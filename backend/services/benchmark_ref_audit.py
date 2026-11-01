"""
基准参考值溯源与自洽性审计（纯标准库，无重计算）。

背景
----
`backend/data/benchmark_reference.json` 是平台阻力/基准验证链路上唯一的外部判据
（12 算例：SUBOFF ×4、DTMB-5415 ×4、KVLCC2 ×4）。该文件被
`api/domain/ship_benchmark.py` 与 `services/ship_proxy.py` 直接当"实验参考值"使用，
但文件内部字段从未被机器校验过：同一算例里的 Ct / Rt / Fn / Δ / Cb 可以互相矛盾
（例如 SUBOFF-01 的 Ct=0.0038 与 Rt=12.5 N 差 7.4 倍）。

本模块把"参考值能不能当判据"变成可机读的三条恒等式检查：

  I1  Fn   = V / sqrt(g L)                      容差 ±2%
  I2  Ct   = Rt / (0.5 rho V^2 S)               容差 ±15%（S 为估计值时不放宽原理，单列 estimate 标记）
  I3  Δ    = rho Cb L B T                       容差 ±5%

缺字段 → WITHHELD（不猜、不静默外推，符合平台 honest-FAIL 纪律）。
任何一个算例只要有一条 FAIL，其参考值就不能当外部基准（INCONSISTENT），
调用方（benchmark_validate）必须据此把判定降级为"无判据可依"，而不是报一个
看起来很像精度的误差百分比。

方位说明：本模块只做**审计与派生**，不改任何文献值；需要湿表面积时用公开回归
Denny–Mumford (S = 1.7 L T + ∇ / T) 估计并显式标注 estimate=True。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.80665  # m/s^2
RHO_FRESH = 998.5  # kg/m^3 模型尺度（淡水）
RHO_SEA = 1025.0  # kg/m^3 实船尺度（海水）

# 恒等式容差（判据阈值，与平台「>15% 不可接受」精度标准对齐）
TOL_FN = 0.02
TOL_CT = 0.15
TOL_MASS = 0.05

# 尺度分界：L >= 100 m 视为实船（海水），否则按模型（淡水）
REAL_SHIP_LENGTH_M = 100.0

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"

CONSISTENT = "CONSISTENT"
INCONSISTENT = "INCONSISTENT"


def density_for(length_m: float) -> float:
    """按尺度选水密度：实船海水 / 模型淡水。"""
    return RHO_SEA if length_m >= REAL_SHIP_LENGTH_M else RHO_FRESH


def estimate_wetted_surface(length: float, beam: float, draft: float, cb: float) -> float:
    """Denny–Mumford 湿表面积回归 S = 1.7 L T + ∇ / T，∇ = Cb L B T。

    仅用于「数据集缺 S」时的交叉核对，返回值必须由调用方标注 estimate=True。
    """
    if min(length, beam, draft, cb) <= 0:
        raise ValueError("estimate_wetted_surface: all inputs must be > 0")
    volume = cb * length * beam * draft
    return 1.7 * length * draft + volume / draft


def derive_ct_from_rt(rt_n: float, speed_ms: float, s_m2: float, rho: float) -> float:
    """Ct = Rt / (0.5 rho V^2 S)。供平台返回只有 Rt（无 Ct）时派生系数。"""
    if speed_ms <= 0 or s_m2 <= 0 or rho <= 0:
        raise ValueError("derive_ct_from_rt: speed, area and density must be > 0")
    return rt_n / (0.5 * rho * speed_ms * speed_ms * s_m2)


def _rel(a: float, b: float) -> float:
    """相对偏差 |a-b| / |b|（b 为定义值）。"""
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b)


def _check(name: str, declared: float, recomputed: float, tol: float, formula: str,
           estimate: bool = False) -> Dict[str, Any]:
    rel = _rel(declared, recomputed)
    return {
        "identity": name,
        "formula": formula,
        "declared": None if declared is None else float(declared),
        "recomputed": None if recomputed is None else float(recomputed),
        "rel_diff": None if not math.isfinite(rel) else round(rel, 6),
        "tolerance": tol,
        "estimated_input": estimate,
        "status": PASS if rel <= tol else FAIL,
    }


def _withheld(name: str, formula: str, reason: str) -> Dict[str, Any]:
    return {"identity": name, "formula": formula, "declared": None, "recomputed": None,
            "rel_diff": None, "tolerance": None, "estimated_input": False,
            "status": WITHHELD, "reason": reason}


def _geom(case: Dict[str, Any]) -> Dict[str, float]:
    g = case.get("geometry") or {}
    L = g.get("length_m")
    B = g.get("beam_m", g.get("diameter_m"))
    T = g.get("draft_m", g.get("diameter_m"))
    return {
        "length": L,
        "beam": B,
        "draft": T,
        "cb": g.get("cb", case.get("cb")),
        "wetted_surface": g.get("wetted_surface_m2"),
        "displacement": g.get("displacement_kg", g.get("displacement_t")),
    }


def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """对单个基准算例跑三条恒等式检查，返回机读结论。"""
    checks: List[Dict[str, Any]] = []
    geo = _geom(case)
    L, V = geo["length"], case.get("speed_ms")
    rho = density_for(L) if L else RHO_FRESH

    # I1: Froude 数
    fn_declared = case.get("froude_number")
    if L and V and fn_declared is not None:
        checks.append(_check("I1_froude", float(fn_declared), V / math.sqrt(G * L),
                             TOL_FN, "Fn = V / sqrt(g L)"))
    else:
        checks.append(_withheld("I1_froude", "Fn = V / sqrt(g L)",
                                "缺 froude_number / speed_ms / length_m"))

    # I2: Ct <-> Rt 一致性
    ct, rt = case.get("experimental_ct"), case.get("experimental_rt_n")
    S, est = geo["wetted_surface"], False
    if S is None and all(v for v in (geo["beam"], geo["draft"], geo["cb"])):
        S = estimate_wetted_surface(L, geo["beam"], geo["draft"], geo["cb"])
        est = True
    if ct is not None and rt is not None and S and V:
        checks.append(_check("I2_ct_from_rt", float(ct), derive_ct_from_rt(rt, V, S, rho),
                             TOL_CT, "Ct = Rt / (0.5 rho V^2 S)", estimate=est))
    else:
        checks.append(_withheld("I2_ct_from_rt", "Ct = Rt / (0.5 rho V^2 S)",
                                "缺 Ct / Rt / 可估计湿表面积 / 航速"))

    # I3: 质量排水量 <-> Cb L B T
    disp = geo["displacement"]
    if disp is not None and all(v for v in (L, geo["beam"], geo["draft"], geo["cb"])):
        mass = rho * geo["cb"] * L * geo["beam"] * geo["draft"]
        declared_mass = disp * 1000.0 if case.get("geometry", {}).get("displacement_t") else disp
        checks.append(_check("I3_displacement", declared_mass, mass, TOL_MASS,
                             "Delta = rho Cb L B T"))
    else:
        checks.append(_withheld("I3_displacement", "Delta = rho Cb L B T",
                                "缺排水量 / Cb / L / B / T"))

    fails = [c for c in checks if c["status"] == FAIL]
    status = INCONSISTENT if fails else CONSISTENT
    reasons = []
    for c in fails:
        reasons.append(f"{c['identity']} 偏差 {c['rel_diff']*100:.1f}% > 容差 {c['tolerance']*100:.0f}%")
    if not fails and any(c["status"] == WITHHELD for c in checks):
        reasons.append("部分恒等式缺字段，未判废但不足以背书")

    return {
        "case_id": case.get("case_id"),
        "ship_type": case.get("ship_type"),
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "source_citation": (case.get("source_citation") or "")[:120],
    }


def audit_dataset(data: Dict[str, Any]) -> Dict[str, Any]:
    """审计整个基准数据集；baseline_usable 只在 0 判废时成立。"""
    cases = data.get("cases", [])
    audited = [audit_case(c) for c in cases]
    n_inc = sum(1 for a in audited if a["status"] == INCONSISTENT)
    n_con = sum(1 for a in audited if a["status"] == CONSISTENT)
    withheld_only = [a["case_id"] for a in audited
                     if a["status"] == CONSISTENT and any(c["status"] == WITHHELD for c in a["checks"])]
    return {
        "description": data.get("description", ""),
        "total_cases": len(audited),
        "consistent": n_con,
        "inconsistent": n_inc,
        "partial_withheld": withheld_only,
        "baseline_usable": n_inc == 0 and len(audited) > 0,
        "verdict": "DATASET_REJECTED_AS_EXTERNAL_BASELINE" if n_inc else "DATASET_ACCEPTED",
        "cases": audited,
    }


def audit_file(path: Optional[str] = None) -> Dict[str, Any]:
    """默认审计仓库内基准数据集。"""
    p = Path(path) if path else (
        Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json")
    with open(p, "r") as f:
        return audit_dataset(json.load(f))


# ---------------------------------------------------------------------------
# 参考值容差纪律：>15% 不可接受（平台精度标准），高容差不得记为 PASS
# ---------------------------------------------------------------------------
ACCEPTABLE_ERROR_PCT = 15.0


def tolerance_is_acceptable(tolerance_pct: float) -> bool:
    """容差本身是否满足平台精度标准。SUBOFF 的 300% 容差属于豁免，不是合格。"""
    return float(tolerance_pct) <= ACCEPTABLE_ERROR_PCT


if __name__ == "__main__":  # 轻量 CLI：python -m services.benchmark_ref_audit
    import sys
    result = audit_file(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"},
                     ensure_ascii=False, indent=1))
    for c in result["cases"]:
        print(f"{c['case_id']:<13} {c['status']:<13} " +
              " ".join(f"{x['identity'].split('_')[0]}={x['status']}" for x in c["checks"]))