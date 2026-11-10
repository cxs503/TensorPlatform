"""cycle_baseline.stats —— 纯统计层（无 IO，可单测）。

复用原则：百分位实现不另写一份，直接借 ``tools/campaign_throughput.percentile_linear``
（线性插值，与 PostgreSQL ``percentile_cont`` 同构），避免"第二份真相"。
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from tools.campaign_throughput import percentile_linear


def pcts(values: Iterable[Optional[float]], qs: Sequence[float] = (0.5, 0.9, 0.99)) -> dict:
    """p50/p90/p99 + n；空集全 None（不伪造 0）。"""
    xs = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    out: dict = {"n": len(xs)}
    for q in qs:
        out[f"p{int(round(q * 100))}"] = percentile_linear(xs, q) if xs else None
    return out


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in values if v is not None]
    return (sum(xs) / len(xs)) if xs else None


def jain_index(xs: Sequence[float]) -> Optional[float]:
    """J(x) = (Σx)² / (n Σx²)；n<1 或 Σx²=0 → None。"""
    vals = [float(x) for x in xs]
    n = len(vals)
    if n < 1:
        return None
    s = sum(vals)
    sq = sum(v * v for v in vals)
    if sq == 0:
        return None
    return (s * s) / (n * sq)


def gini(xs: Sequence[float]) -> Optional[float]:
    """平均绝对差形式的 Gini；μ=0 → 0.0（degenerate，调用方应同时记录标记）。"""
    vals = [float(x) for x in xs]
    n = len(vals)
    if n < 1:
        return None
    mu = sum(vals) / n
    if mu == 0:
        return 0.0
    total = 0.0
    for i in range(n):
        for j in range(n):
            total += abs(vals[i] - vals[j])
    return total / (2.0 * n * n * mu)


def is_degenerate(xs: Sequence[float]) -> bool:
    vals = [float(x) for x in xs]
    return bool(vals) and sum(vals) == 0


def gini_from_weights(weights: Sequence[float]) -> Optional[float]:
    """理论份额向量 ρ_i = w_i/Σw 的 Gini（与实测份额 Gini 对比用）。"""
    w = [float(x) for x in weights]
    s = sum(w)
    if s == 0:
        return None
    return gini([x / s for x in w])


def jain_from_weights(weights: Sequence[float]) -> Optional[float]:
    """理论份额向量上的 Jain：J(ρ) = (Σρ)²/(n Σρ²) = 1/(n Σρ²) 当 Σρ=1。"""
    w = [float(x) for x in weights]
    s = sum(w)
    if s == 0:
        return None
    return jain_index([x / s for x in w])


def rel_err(measured: Optional[float], theory: Optional[float],
            abs_floor: float = 1e-3) -> Optional[float]:
    """相对误差；theory 接近 0 时退化为绝对误差（避免除零放大）。"""
    if measured is None or theory is None:
        return None
    if abs(theory) <= abs_floor:
        return abs(measured - theory)
    return abs(measured - theory) / abs(theory)


def grade_segment(coverage: Optional[float], coverage_min: float) -> str:
    """覆盖率门（判据 d）：不足 → WITHHELD，绝不四舍五入放行。"""
    if coverage is None:
        return "WITHHELD"
    return "MEASURED" if coverage >= coverage_min else "WITHHELD"