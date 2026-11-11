#!/usr/bin/env python3
"""Descriptive statistics used by the metrics-tool verification chain.

Deliberately dependency-free (pure ``math``/``list``): the acceptance numbers of
this rig must be reproducible on a bare interpreter and must not depend on a
numpy version.  ``tests/metrics_verify/test_stats.py`` pins the estimators
against numpy, which is where the cross-implementation evidence comes from.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence


class StatsError(ValueError):
    """Raised when a statistic is requested over an empty / malformed sample."""


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile (identical to ``numpy.percentile`` default).

    ``q`` is a fraction in [0, 1].  Empty sample -> ``None`` (never a fake 0.0,
    which would silently *pass* a latency ceiling).
    """
    if not 0.0 <= q <= 1.0:
        raise StatsError(f"percentile q must be within [0, 1], got {q!r}")
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    pos = q * (len(data) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return data[lo] + (data[hi] - data[lo]) * frac


def mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return math.fsum(float(v) for v in values) / len(values)


def variance(values: Sequence[float], ddof: int = 0) -> Optional[float]:
    """Population (``ddof=0``) or sample (``ddof=1``) variance."""
    n = len(values)
    if n == 0 or n - ddof <= 0:
        return None
    mu = mean(values)
    assert mu is not None
    ss = math.fsum((float(v) - mu) ** 2 for v in values)
    return ss / (n - ddof)


def stdev(values: Sequence[float], ddof: int = 0) -> Optional[float]:
    var = variance(values, ddof=ddof)
    return None if var is None else math.sqrt(var)


def summarize(values: Iterable[float], quantiles: Sequence[float] = (0.5, 0.9, 0.99)) -> Dict[str, Any]:
    """One-sample summary: n / mean / var / stdev / requested quantiles / min / max."""
    data: List[float] = [float(v) for v in values]
    out: Dict[str, Any] = {
        "n": len(data),
        "mean": mean(data),
        "var": variance(data),
        "stdev": stdev(data),
        "min": min(data) if data else None,
        "max": max(data) if data else None,
    }
    for q in quantiles:
        out[f"p{int(round(q * 100))}"] = percentile(data, q)
    return out


def rel_error(measured: Optional[float], reference: Optional[float]) -> Optional[float]:
    """``|measured - reference| / |reference|``; ``None`` when not computable."""
    if measured is None or reference is None:
        return None
    if reference == 0:
        return 0.0 if measured == 0 else None
    return abs(measured - reference) / abs(reference)


def abs_error(measured: Optional[float], reference: Optional[float]) -> Optional[float]:
    if measured is None or reference is None:
        return None
    return abs(measured - reference)


def lognormal_quantile(median: float, sigma: float, q: float) -> float:
    """Analytic lognormal quantile: ``median * exp(sigma * Phi^-1(q))``.

    Used as the *ground truth* for the latency estimator: the simulator injects a
    lognormal delay, the collector measures it, and the measured p-quantile is
    compared to this closed-form value (no fitting, no circularity).
    """
    if not 0.0 < q < 1.0:
        raise StatsError(f"lognormal quantile q must be in (0, 1), got {q!r}")
    # Acklam-free approach: use math.erf inversion via bisection (deterministic,
    # ~1e-12 accurate, and dependency free).
    lo, hi = -10.0, 10.0
    target = q
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        cdf = 0.5 * (1.0 + math.erf(mid / math.sqrt(2.0)))
        if cdf < target:
            lo = mid
        else:
            hi = mid
    z = 0.5 * (lo + hi)
    return median * math.exp(sigma * z)


def seeded_rng(seed: int) -> random.Random:
    """Single deterministic entry point for every simulated stream."""
    return random.Random(int(seed))