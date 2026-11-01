"""**已降级为 facade**：参考数据一致性判定的对外兼容层（m1561 六分支裁决）。

架构裁决
--------
平台曾并行存在三套"参考数据一致性判定"实现：

===========================  ==========================================  ==============
实现                          检查族                                       裁决结果
===========================  ==========================================  ==============
``services/benchmark_integrity``  C1–C5（Fr/Δ/Rt/摩擦/形体带，带严重度梯）   **正典**
``services/benchmark_dataset_audit``  I1–I3（Ct-Rt/Fr/摩擦下限）               降级为本文件
``api/domain/ship_benchmark``     无物理，仅编排                               保留（消费者）
===========================  ==========================================  ==============

裁决依据：C1–C5 是真超集（多出排水量闭合与形体带，且用 PASS<WITHHELD<WARN<FAIL
严重度梯而非三值），并且带独立测试；I1–I3 是本文件的祖先实现，无独立测试、且把密度
写死成单一常量（**这正是 SUBOFF 用海水密度 1025 报出 95.0 N 的根因**）。
因此本模块**不再自带任何物理**，全部委托 :mod:`services.benchmark_integrity`，
只保留 API 层依赖的 ``CONSISTENT / PARTIAL / INCONSISTENT`` 三值口径与导出名。
归档说明见 ``docs/superseded/m1402_dataset_audit.md``。

三值口径（与历史 API 契约一致）
------------------------------
``CONSISTENT``   所有适用的闭合检查 PASS           → 该行可作判据
``PARTIAL``      无 FAIL 但有 WITHHELD（缺量）      → 值不可复核，不出精度判决
``INCONSISTENT`` 至少一项 FAIL                      → 记录自相矛盾，值作废

密度溯源（m1561 修正）
----------------------
行内带 ``reference_density_kg_m3`` / ``reference_kinematic_viscosity_m2_s`` 时以其为准
（正典 v3.0 每行都带）；缺失时回落到平台物理核的淡水物性
``ship_resistance.DEFAULT_WATER_DENSITY``，与 ``calc.resistance`` 同介质。
"""
from __future__ import annotations

import warnings
from typing import Any, Dict, Iterable, List, Optional

from services.benchmark_integrity import (
    FAIL,
    PASS,
    WARN,
    WITHHELD,
    audit_case as _integrity_audit_case,
)
from services.ship_resistance import DEFAULT_KINEMATIC_VISCOSITY, DEFAULT_WATER_DENSITY

CONSISTENT = "CONSISTENT"
PARTIAL = "PARTIAL"
INCONSISTENT = "INCONSISTENT"

#: 证据闸门用的恒等式容差：1 %（诊断用途可放宽到 15 %）。
IDENTITY_TOL = 0.01
FROUDE_TOL = 0.02

#: 行缺几何块时的兜底船长（保留历史常量，供外部脚本导入）。
DEFAULT_HULL_LENGTH = {
    "SUBOFF": 4.356,
    "DTMB-5415": 5.720,
    "KVLCC2": 320.0,
}

_DEPRECATION = (
    "services.benchmark_dataset_audit 已降级为 facade：物理全部委托 "
    "services.benchmark_integrity（m1561 正典）。新代码请直接用 benchmark_integrity。"
)


def _with_fallback_fluid(case: Dict[str, Any]) -> Dict[str, Any]:
    """行内无物性字段时补上物理核的淡水物性（旧行为），有则原样尊重。"""
    if case.get("reference_density_kg_m3") is not None:
        return case
    row = dict(case)
    row.setdefault("reference_density_kg_m3", DEFAULT_WATER_DENSITY)
    row.setdefault("reference_kinematic_viscosity_m2_s", DEFAULT_KINEMATIC_VISCOSITY)
    return row


def assess_case(case: Dict[str, Any], nu: Optional[float] = None,
                rho: Optional[float] = None, identity_tol: float = IDENTITY_TOL
                ) -> Dict[str, Any]:
    """单行判定，返回历史 API 口径的三值状态 + 失败项清单。"""
    row = _with_fallback_fluid(case)
    audit = _integrity_audit_case(row, rho=rho, nu=nu, identity_tol=identity_tol)
    checks = audit["checks"]
    failed = [c["check"] for c in checks if c["status"] == FAIL]
    not_run = [c["check"] for c in checks if c["status"] == WITHHELD]
    warned = [c["check"] for c in checks if c["status"] == WARN]
    if failed:
        status = INCONSISTENT
    elif not_run:
        status = PARTIAL
    else:
        status = CONSISTENT
    return {
        "case_id": audit.get("case_id"),
        "ship_type": audit.get("ship_type"),
        "speed_ms": row.get("speed_ms", 0.0),
        "experimental_ct": row.get("experimental_ct"),
        "experimental_rt_n": row.get("experimental_rt_n"),
        "reference_density_kg_m3": row.get("reference_density_kg_m3"),
        "status": status,
        "verdict": audit.get("verdict"),
        "withheld": status != CONSISTENT,
        "failed_checks": failed,
        "checks_not_run": not_run,
        "warned_checks": warned,
        "checks": checks,
        "source_citation": row.get("source_citation", ""),
    }


def assess_dataset(cases: Iterable[Dict[str, Any]], nu: Optional[float] = None,
                   rho: Optional[float] = None, identity_tol: float = IDENTITY_TOL
                   ) -> Dict[str, Any]:
    """整个参考数据集的判定与汇总。"""
    rows = [assess_case(c, nu=nu, rho=rho, identity_tol=identity_tol) for c in cases]
    by_status: Dict[str, int] = {CONSISTENT: 0, PARTIAL: 0, INCONSISTENT: 0}
    by_ship: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_status[r["status"]] += 1
        bucket = by_ship.setdefault(r["ship_type"],
                                    {CONSISTENT: 0, PARTIAL: 0, INCONSISTENT: 0})
        bucket[r["status"]] += 1
    total = len(rows)
    return {
        "total_cases": total,
        "status_counts": by_status,
        "status_by_ship_type": by_ship,
        "verified_cases": by_status[CONSISTENT],
        "withheld_cases": total - by_status[CONSISTENT],
        "verifiable_accuracy_claim": by_status[CONSISTENT] > 0,
        "tolerances": {
            "ct_rt_identity_pct": IDENTITY_TOL * 100,
            "froude_identity_pct": FROUDE_TOL * 100,
        },
        "delegated_to": "services.benchmark_integrity",
        "cases": rows,
    }


def load_and_assess(data: Dict[str, Any], nu: Optional[float] = None,
                    rho: Optional[float] = None) -> Dict[str, Any]:
    """对已加载的 benchmark_reference.json 字典做判定（API 层入口）。"""
    summary = assess_dataset(data.get("cases", []), nu=nu, rho=rho)
    summary["dataset_description"] = data.get("description", "")
    summary["dataset_version"] = data.get("version")
    return summary


def case_status_map(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """按 case_id 索引判定结果，供 API 层 O(1) 查询。"""
    return {row["case_id"]: row for row in summary.get("cases", [])}


def is_finite_number(value: Optional[float]) -> bool:
    if value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def warn_deprecated() -> None:
    """显式提示调用方迁移（脚本/工具用，import 时不打扰）。"""
    warnings.warn(_DEPRECATION, DeprecationWarning, stacklevel=2)


__all__ = [
    "CONSISTENT", "PARTIAL", "INCONSISTENT", "IDENTITY_TOL", "FROUDE_TOL",
    "DEFAULT_HULL_LENGTH", "assess_case", "assess_dataset", "load_and_assess",
    "case_status_map", "is_finite_number", "warn_deprecated",
]