"""把合成域内核登记成探针，并生成**确定性**的基准用例（seed 固定、可复算）。"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..validators import ProbeRegistry
from . import synthetic_kernels as K

#: 基准用例生成种子（写进工件，任何复算必须用同一种子）
TRIAL_SEED = 20261110


def build_registry() -> ProbeRegistry:
    reg = ProbeRegistry()
    reg.register("synth.linear.upgraded", K.IMPLEMENTATION_PROVIDER, "implementation",
                 K.cholesky_solve, "Cholesky 分解 + 前代/回代")
    reg.register("synth.linear.baseline", K.IMPLEMENTATION_PROVIDER, "baseline",
                 K.baseline_cholesky_solve, "对角元近似（旧 MOCK 实现）")
    reg.register("lapack.linear.solve", K.NUMPY_PROVIDER, "reference",
                 K.ref_linear_solve, "LAPACK 通用 LU 求解")

    reg.register("synth.normal.upgraded", K.IMPLEMENTATION_PROVIDER, "implementation",
                 K.normal_ppf, "CDF 有界根搜索")
    reg.register("synth.normal.baseline", K.IMPLEMENTATION_PROVIDER, "baseline",
                 K.baseline_normal_ppf, "恒等 ±1.96（旧 STUB 实现）")
    reg.register("mpmath.normal.ppf", K.MPMATH_PROVIDER, "reference",
                 K.ref_normal_ppf, "mpmath 60dps 二分求逆")

    reg.register("synth.simpson.upgraded", K.IMPLEMENTATION_PROVIDER, "implementation",
                 K.adaptive_simpson, "自适应 Simpson + Richardson")
    reg.register("synth.simpson.baseline", K.IMPLEMENTATION_PROVIDER, "baseline",
                 K.baseline_adaptive_simpson, "端点梯形（旧 MOCK 实现）")
    reg.register("mpmath.simpson.quad", K.MPMATH_PROVIDER, "reference",
                 K.ref_adaptive_simpson, "mpmath.quad tanh-sinh")

    reg.register("synth.mean.kahan", K.IMPLEMENTATION_PROVIDER, "implementation",
                 K.baseline_mean_reference, "Kahan 补偿求和均值（已是 REAL，负控）")
    reg.register("fractions.mean.exact", "python.fractions(exact-rational)", "reference",
                 K.ref_exact_mean, "Fraction 精确有理均值")
    return reg


def _spd_matrices(n_cases: int = 5) -> List[Dict[str, Any]]:
    """确定性生成 SPD 用例：A = RᵀR + n·I（条件数可控），b = RNG 向量。"""
    rng = np.random.default_rng(TRIAL_SEED)
    cases: List[Dict[str, Any]] = []
    for i, n in enumerate([3, 4, 5, 6, 8][:n_cases]):
        R = rng.normal(size=(n, n))
        A = R.T @ R + n * np.eye(n)
        b = rng.normal(size=n)
        cases.append({"case_id": f"spd_{n}x{n}", "args": [A.tolist(), b.tolist()]})
    return cases


def _normal_cases() -> List[Dict[str, Any]]:
    return [{"case_id": f"p={p}", "args": [p]}
            for p in (0.001, 0.025, 0.5, 0.8413, 0.975, 0.999)]


def _simpson_cases() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in ("exp_decay", "lorentz", "sqrt_bump"):
        for a, b in ((0.0, 1.0), (0.0, 5.0)):
            out.append({"case_id": f"{key}[{a},{b}]", "args": [key, a, b]})
    return out


def _mean_cases() -> List[Dict[str, Any]]:
    return [
        {"case_id": "decimals_1", "args": [["1.1", "2.2", "3.3", "4.4"]]},
        {"case_id": "large_offset", "args": [["1e9", "1", "1", "1"]]},
        {"case_id": "mixed_sign", "args": [["-2.5", "0", "2.5", "0.125"]]},
    ]


def build_trial_specs() -> List[Dict[str, Any]]:
    """三项升级试验 + 一项负控试验（全部含旧实现基线，用于「升级确有改进」判据）。"""
    return [
        {"capability_id": "synth.linalg.cholesky_solve",
         "implementation": "synth.linear.upgraded",
         "baseline": "synth.linear.baseline",
         "reference": "lapack.linear.solve",
         "cases": _spd_matrices()},
        {"capability_id": "synth.stats.normal_ppf",
         "implementation": "synth.normal.upgraded",
         "baseline": "synth.normal.baseline",
         "reference": "mpmath.normal.ppf",
         "cases": _normal_cases()},
        {"capability_id": "synth.integrate.adaptive_simpson",
         "implementation": "synth.simpson.upgraded",
         "baseline": "synth.simpson.baseline",
         "reference": "mpmath.simpson.quad",
         "cases": _simpson_cases()},
        {"capability_id": "synth.stats.mean_reference",
         "implementation": "synth.mean.kahan",
         "reference": "fractions.mean.exact",
         "cases": _mean_cases()},
    ]


def trial_audit_table(path: Any = None) -> Any:
    """把 trial register JSON 读成 AuditTable（与真实台账同一数据模型）。"""
    import json
    from pathlib import Path

    from ..model import AuditRow, AuditTable

    p = Path(path) if path else Path(__file__).with_name("trial_register.json")
    payload = json.loads(p.read_text(encoding="utf-8"))
    rows = [AuditRow(cap_id=c["capability_id"], declared=c["declared_level"],
                     reconciled=c["reconciled_level"], divergence="OK",
                     domain=c.get("domain", ""))
            for c in payload["capabilities"]]
    return AuditTable(rows=rows, counted=len(rows), source_path=str(p),
                      contract={"schema_version": payload["schema_version"]})
