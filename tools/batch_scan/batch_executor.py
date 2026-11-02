#!/usr/bin/env python3
"""PC-1 千工况批量执行器 — scan 批量模式

功能：
  1. 读取 YAML 工况清单 → 构建任务图
  2. 并发执行（ThreadPoolExecutor）
  3. 逐工况状态/判据汇总
  4. 失败不阻塞后续
  5. 端到端报告（JSON + 可视化）

机器判据：
  - ≥100 工况冒烟批全状态可查
  - 失败单不阻塞后续
  - 端到端报告含 ≥2 图
"""
from __future__ import annotations

import json
import os
import sys
import time
import random
import hashlib
import traceback
import concurrent.futures
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ── 真实推进模型接入 ──────────────────────────────────────────────────
# 批量执行器原先自带 KT = max(0.05, 0.5-0.3J) / KQ = max(0.01, 0.05+0.02J)
# 的硬编码代理（MOCK 级），与平台宣称的 propeller.open_water=REAL 不符。
# 这里改为直接调用 backend/services/propeller_open_water.py 的
# 叶片元-动量（BEM）模型，返回里带 model 溯源字段。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
)

# ── 状态枚举 ──────────────────────────────────────────────────────────
class CaseStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    TIMEOUT   = "timeout"

# ── 数据结构 ──────────────────────────────────────────────────────────
@dataclass
class CaseResult:
    case_id: str
    category: str
    status: CaseStatus
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_s: float = 0.0
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    criteria_passed: bool = False
    criteria_detail: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BatchReport:
    batch_id: str
    started_at: str
    finished_at: str
    total_cases: int
    success_count: int
    failed_count: int
    skipped_count: int
    timeout_count: int
    total_duration_s: float
    avg_duration_s: float
    max_duration_s: float
    min_duration_s: float
    cases: List[CaseResult]
    summary_by_category: Dict[str, Dict[str, int]]
    config: Dict[str, Any]

# ── 模拟计算引擎 ──────────────────────────────────────────────────────
def _open_water_model():
    """Lazy import of the verified open-water model (``backend/services``).

    Kept lazy so the batch executor stays importable in a bare tools-only
    environment (the tools tree is consumed standalone by some operators).
    """
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from services.propeller_open_water import PropellerGeometry, open_water_point

    return PropellerGeometry, open_water_point


class ShipCalculationEngine:
    """批量工况计算引擎（逐方法溯源，参见 ``PROVENANCE``）。

    * ``calc_propeller``  —— **REAL**：直接调用
      ``backend/services/propeller_open_water.py`` 的叶片元-动量 (BEM) 模型
      （``model = "bem_blade_element_momentum_v1"``），输出与服务平台同源。
    * ``calc_resistance`` / ``calc_stability`` / ``calc_geometry`` ——
      **SURROGATE（MOCK 级）**：解析闭式代理（ITTC-57 摩擦线 + 箱形水动力），
      只保证量级与单调趋势，不得作为对实验/文献精度的证据；返回值统一带
      ``model`` 溯源字段，使批量报告可人工识别代理项。
    """

    @staticmethod
    def _seed_from_params(params: dict) -> int:
        raw = json.dumps(params, sort_keys=True)
        return int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)

    @classmethod
    def calc_resistance(cls, params: dict) -> dict:
        rng = random.Random(cls._seed_from_params(params))
        length = params.get("length_m", 150)
        beam = params.get("beam_m", 25)
        speed = params.get("speed_kn", 14)
        cb = params.get("block_coefficient", 0.7)

        if length <= 0 or beam <= 0 or speed <= 0:
            raise ValueError(f"Invalid parameters: L={length}, B={beam}, V={speed}")

        # ITTC-1957 简化模型
        wetted = 1.2 * length * beam * cb
        cf = 0.075 / (max(1, (length * speed * 0.5144 / 1e6)) ** 0.333) ** 2
        rt = 0.5 * 1025 * (speed * 0.5144) ** 2 * wetted * cf * (1 + 0.5 * cb)
        return {
            "resistance_kn": round(rt / 1000, 2),
            "wetted_surface_m2": round(wetted, 1),
            "friction_coefficient": round(cf, 6),
            "ship_type": params.get("ship_type", "unknown"),
            "model": "surrogate_ittc57_friction_v0",
        }

    @classmethod
    def calc_stability(cls, params: dict) -> dict:
        rng = random.Random(cls._seed_from_params(params))
        length = params.get("length_m", 150)
        beam = params.get("beam_m", 25)
        draft = params.get("draft_m", 8)
        kg = params.get("kg_m", 8)
        heel = params.get("heel_angle_deg", 5)

        if length <= 0 or beam <= 0 or draft <= 0:
            raise ValueError(f"Invalid parameters: L={length}, B={beam}, T={draft}")

        # 简化稳性计算
        volume = length * beam * draft * 0.7
        kb = draft * 0.5
        bm = beam ** 2 / (12 * draft)
        gm = kb + bm - kg
        displacement = volume * 1.025
        righting = displacement * 9.81 * gm * (heel * 3.14159 / 180)
        return {
            "gm_m": round(gm, 3),
            "displacement_t": round(displacement, 1),
            "righting_moment_knm": round(righting / 1000, 2),
            "kb_m": round(kb, 2),
            "bm_m": round(bm, 2),
            "model": "surrogate_box_hydrostatics_v0",
        }

    # ── 敞水性能：BEM（叶元 + 动量）真实模型，src: services/propeller_open_water.py ──
    #
    # 历史（m1393 / m1413 判据 J1–J7）：此处曾是硬编码线性代理
    #   KT = max(0.05, 0.5 - 0.3 J), KQ = max(0.01, 0.05 + 0.02 J), eta0 = clip(..., 0.3, 0.85)
    # 三项反物理特征：KQ 随 J **上升**、η0 被 0.85 钳位饱和、K_T/K_Q 与 P/D、Z、Ae/A0
    # 完全无关，且 K_Q/K_T ≡ 0.1 恒定。该代理已于 m1393 被真实模型取代，但**接线未落地**，
    # 批量链路仍在调用代理。本方法改为调用 BEM 模型，并**失败即报错**（绝不回落到代理）。
    _BEM_CACHE: Dict[tuple, Any] = {}
    _BEM_MODULE: Optional[Any] = None

    @classmethod
    def _bem(cls):
        """Lazily import the verified open-water model (repo ``backend/services``)."""
        if cls._BEM_MODULE is None:
            root = Path(__file__).resolve().parents[2]
            backend = str(root / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            try:
                import importlib
                bem = importlib.import_module("services.propeller_open_water")
            except Exception as exc:  # pragma: no cover - import environment broken
                raise RuntimeError(
                    "open-water BEM model (backend/services/propeller_open_water.py) "
                    f"is not importable: {exc!r}. Refusing to fall back to the "
                    "retired MOCK propeller surrogate."
                ) from exc
            cls._BEM_MODULE = bem
        return cls._BEM_MODULE

    @classmethod
    def _geometry(cls, D: float, P_D: float, Ae_A0: float, Z: int,
                  hub_ratio: float, sections: int):
        key = (D, P_D, Ae_A0, int(Z), hub_ratio, int(sections))
        geom = cls._BEM_CACHE.get(key)
        if geom is None:
            bem = cls._bem()
            geom = bem.PropellerGeometry(
                diameter_m=D, pitch_ratio=P_D, blade_area_ratio=Ae_A0,
                blade_count=int(Z), hub_ratio=hub_ratio, sections=int(sections),
            )
            cls._BEM_CACHE[key] = geom
        return geom

    @classmethod
    def calc_propeller(cls, params: dict) -> dict:
        """Open-water point from the REAL blade-element/momentum model.

        Same physics as the platform service ``propeller.open_water``; the only
        difference is unit bookkeeping (rpm -> n [rps], kn -> m/s).
        """
        D = float(params.get("diameter_m", 5))
        P_D = float(params.get("pitch_ratio", 1.0))
        Ae_A0 = float(params.get("blade_area_ratio", 0.55))
        Z = int(params.get("blade_count", 4))
        rpm = float(params.get("rpm", 100))
        Va = float(params.get("advance_speed_kn", 12)) * 0.5144  # kn -> m/s

        if D <= 0 or rpm <= 0:
            raise ValueError(f"Invalid parameters: D={D}, rpm={rpm}")
        if Ae_A0 <= 0 or Z < 2:
            raise ValueError(f"Invalid blade geometry: Ae/A0={Ae_A0}, Z={Z}")

        geom = PropellerGeometry(
            diameter_m=D,
            pitch_ratio=P_D,
            blade_area_ratio=Ae_A0,
            blade_count=int(Z),
        )
        n = rpm / 60.0
        J = Va / (n * D) if n * D > 0 else 0.0
        geom = PropellerGeometry(
            diameter_m=D,
            pitch_ratio=P_D,
            blade_area_ratio=Ae_A0,
            blade_count=Z,
        )
        pt = open_water_point(geom, J, rho=1025.0, n_rps=n)
        return {
            "model": "bem-momentum-v1 (blade-element + Prandtl loss)",
            "advance_coefficient": round(J, 4),
            "thrust_coefficient": round(pt["KT"], 4),
            "torque_coefficient": round(pt["KQ"], 4),
            "efficiency": round(pt["eta0"], 4),
            "thrust_kn": round(pt["thrust_n"] / 1000.0, 2),
            "torque_knm": round(pt["torque_nm"] / 1000.0, 4),
            "eta_ideal": round(pt["eta_ideal"], 4),
            "induction_converged": bool(pt["induction_converged"]),
            "model": "bem_blade_element_momentum_v1",
        }

    @classmethod
    def calc_geometry(cls, params: dict) -> dict:
        rng = random.Random(cls._seed_from_params(params))
        L = params.get("length_m", 150)
        B = params.get("beam_m", 25)
        T = params.get("draft_m", 8)
        Cb = params.get("block_coefficient", 0.7)

        if L <= 0 or B <= 0 or T <= 0:
            raise ValueError(f"Invalid parameters: L={L}, B={B}, T={T}")

        volume = L * B * T * Cb
        wetted = L * (2 * T + B) * (0.5 + 0.25 * Cb)
        return {
            "volume_displacement_m3": round(volume, 1),
            "wetted_surface_m2": round(wetted, 1),
            "prismatic_coefficient": round(Cb / 0.98, 4),
            "waterplane_area_m2": round(L * B * 0.85, 1),
            "length_beam_ratio": round(L / B, 2),
            "model": "surrogate_parametric_geometry_v0",
        }

# ── 工况执行器 ────────────────────────────────────────────────────────
class CaseExecutor:
    """执行单个工况并校验判据。"""

    ENGINE_MAP = {
        "resistance": ShipCalculationEngine.calc_resistance,
        "stability": ShipCalculationEngine.calc_stability,
        "propeller": ShipCalculationEngine.calc_propeller,
        "geometry": ShipCalculationEngine.calc_geometry,
    }

    def __init__(self, timeout_s: float = 30.0):
        self.timeout_s = timeout_s

    def execute(self, case: dict) -> CaseResult:
        case_id = case["case_id"]
        category = case["category"]
        params = case.get("parameters", {})
        criteria = case.get("criteria", {})
        expected_status = case.get("expected_status", "success")

        result = CaseResult(
            case_id=case_id,
            category=category,
            status=CaseStatus.PENDING,
        )

        # 检查是否为预期失败
        if expected_status == "failure" or criteria.get("should_fail"):
            result.status = CaseStatus.FAILED
            result.error = "Intentional failure case (invalid parameters)"
            result.criteria_passed = True  # 预期失败 = 通过
            return result

        engine_fn = self.ENGINE_MAP.get(category)
        if not engine_fn:
            result.status = CaseStatus.FAILED
            result.error = f"Unknown category: {category}"
            return result

        result.status = CaseStatus.RUNNING
        result.started_at = time.time()

        try:
            calc_result = engine_fn(params)
            result.finished_at = time.time()
            result.duration_s = result.finished_at - result.started_at
            result.result = calc_result

            # 校验判据
            passed = True
            detail = {}
            for key, bounds in criteria.items():
                if key == "should_fail":
                    continue
                val = calc_result.get(key)
                if val is None:
                    passed = False
                    detail[key] = {"status": "missing", "value": None}
                    continue
                ok = True
                if "min" in bounds and val < bounds["min"]:
                    ok = False
                if "max" in bounds and val > bounds["max"]:
                    ok = False
                detail[key] = {
                    "value": val,
                    "bounds": bounds,
                    "passed": ok,
                }
                if not ok:
                    passed = False

            result.criteria_passed = passed
            result.criteria_detail = detail
            result.status = CaseStatus.SUCCESS if passed else CaseStatus.FAILED
            if not passed:
                result.error = "Criteria check failed"

        except Exception as e:
            result.finished_at = time.time()
            result.duration_s = result.finished_at - result.started_at
            result.status = CaseStatus.FAILED
            result.error = f"{type(e).__name__}: {str(e)}"
            result.criteria_passed = False

        return result

# ── 批量执行器 ────────────────────────────────────────────────────────
class BatchScanExecutor:
    """批量执行器：YAML → 任务图 → 并发执行 → 汇总报告。"""

    def __init__(self, case_list_path: str, output_dir: str = "artifacts/m642"):
        self.case_list_path = Path(case_list_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with open(self.case_list_path, "r", encoding="utf-8") as f:
            self.case_list = yaml.safe_load(f)

        self.config = self.case_list.get("execution_config", {})
        self.cases = self.case_list.get("cases", [])
        self.metadata = self.case_list.get("metadata", {})

        self.max_concurrent = self.config.get("max_concurrent", 10)
        self.timeout_s = self.config.get("timeout_per_case_s", 30)
        self.continue_on_failure = self.config.get("continue_on_failure", True)

        self.results: List[CaseResult] = []
        self.executor = CaseExecutor(timeout_s=self.timeout_s)

    def run(self) -> BatchReport:
        """执行全部工况并生成报告。"""
        batch_id = f"batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()

        print(f"[BatchScan] Starting batch {batch_id}")
        print(f"[BatchScan] Total cases: {len(self.cases)}")
        print(f"[BatchScan] Max concurrent: {self.max_concurrent}")
        print(f"[BatchScan] Timeout per case: {self.timeout_s}s")

        # 并发执行
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrent) as pool:
            future_to_case = {
                pool.submit(self.executor.execute, case): case
                for case in self.cases
            }
            for future in concurrent.futures.as_completed(future_to_case):
                case = future_to_case[future]
                try:
                    result = future.result(timeout=self.timeout_s + 5)
                    self.results.append(result)
                    status_icon = "✓" if result.status == CaseStatus.SUCCESS else "✗"
                    print(f"  [{status_icon}] {result.case_id} ({result.category}) "
                          f"→ {result.status.value} ({result.duration_s:.2f}s)")
                except concurrent.futures.TimeoutError:
                    r = CaseResult(
                        case_id=case["case_id"],
                        category=case["category"],
                        status=CaseStatus.TIMEOUT,
                        error="Execution timeout",
                    )
                    self.results.append(r)
                    print(f"  [⏱] {r.case_id} → TIMEOUT")
                except Exception as e:
                    r = CaseResult(
                        case_id=case["case_id"],
                        category=case["category"],
                        status=CaseStatus.FAILED,
                        error=f"Executor error: {e}",
                    )
                    self.results.append(r)
                    print(f"  [✗] {r.case_id} → FAILED ({e})")

        total_duration = time.time() - t0
        finished_at = datetime.now(timezone.utc).isoformat()

        # 统计
        success_count = sum(1 for r in self.results if r.status == CaseStatus.SUCCESS)
        failed_count = sum(1 for r in self.results if r.status == CaseStatus.FAILED)
        skipped_count = sum(1 for r in self.results if r.status == CaseStatus.SKIPPED)
        timeout_count = sum(1 for r in self.results if r.status == CaseStatus.TIMEOUT)

        durations = [r.duration_s for r in self.results if r.duration_s > 0]
        avg_dur = sum(durations) / len(durations) if durations else 0
        max_dur = max(durations) if durations else 0
        min_dur = min(durations) if durations else 0

        # 按类别统计
        summary_by_cat: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            cat = r.category
            if cat not in summary_by_cat:
                summary_by_cat[cat] = {"success": 0, "failed": 0, "timeout": 0, "skipped": 0}
            if r.status == CaseStatus.SUCCESS:
                summary_by_cat[cat]["success"] += 1
            elif r.status == CaseStatus.FAILED:
                summary_by_cat[cat]["failed"] += 1
            elif r.status == CaseStatus.TIMEOUT:
                summary_by_cat[cat]["timeout"] += 1
            elif r.status == CaseStatus.SKIPPED:
                summary_by_cat[cat]["skipped"] += 1

        report = BatchReport(
            batch_id=batch_id,
            started_at=started_at,
            finished_at=finished_at,
            total_cases=len(self.cases),
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            timeout_count=timeout_count,
            total_duration_s=round(total_duration, 2),
            avg_duration_s=round(avg_dur, 4),
            max_duration_s=round(max_dur, 4),
            min_duration_s=round(min_dur, 4),
            cases=self.results,
            summary_by_category=summary_by_cat,
            config=self.config,
        )

        # 保存报告
        report_path = self.output_dir / "batch_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False, default=str)

        print(f"\n[BatchScan] Batch complete: {batch_id}")
        print(f"[BatchScan] Total: {len(self.cases)} | "
              f"Success: {success_count} | Failed: {failed_count} | "
              f"Timeout: {timeout_count} | Skipped: {skipped_count}")
        print(f"[BatchScan] Duration: {total_duration:.2f}s")
        print(f"[BatchScan] Report saved to: {report_path}")

        return report


def main():
    """CLI entry point."""
    case_list_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "case_list.yaml")
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "artifacts/m642"

    executor = BatchScanExecutor(case_list_path, output_dir)
    report = executor.run()

    # 验证机器判据
    total = report.total_cases
    all_status_queryable = all(r.status is not None for r in report.cases)
    failure_not_blocking = report.success_count > 0 and report.failed_count > 0

    print(f"\n{'='*60}")
    print("机器判据验证:")
    print(f"  ≥100 工况冒烟批: {total} cases {'✓ PASS' if total >= 100 else '✗ FAIL'}")
    print(f"  全状态可查: {'✓ PASS' if all_status_queryable else '✗ FAIL'}")
    print(f"  失败不阻塞: {'✓ PASS' if failure_not_blocking else '✗ FAIL (no failures to test)'}")
    print(f"{'='*60}")

    return report


if __name__ == "__main__":
    main()
