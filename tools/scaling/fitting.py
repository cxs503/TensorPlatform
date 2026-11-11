"""Amdahl / Gustafson fits with bootstrap confidence intervals.

Amdahl's law (fixed problem size)::

    A(n) = 1 / (s + (1 - s) / n)

Gustafson's law (fixed time, scaled problem size)::

    G(n) = s_g + (1 - s_g) * n        # scaled speedup from weak-scaling data

Fitting is done on a deterministic dense grid followed by a golden-section
refinement.  A gradient optimiser is deliberately avoided: for a (near) flat
objective -- exactly what the pseudo-parallel negative control produces -- it
wanders, while a grid reports the boundary ``s -> 1`` which is the physically
meaningful answer.

Confidence intervals come from a percentile bootstrap over the measurement
replicates: the per-core-count timing vector is resampled with replacement, the
median is re-taken (robust to a single slow run), the speedup re-derived against
the resampled ``n = 1`` median, and the fit repeated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

__all__ = ["amdahl_speedup", "gustafson_speedup", "fit_amdahl", "fit_gustafson", "r2_score"]

_GRID = np.linspace(0.0, 1.0, 1001)


def amdahl_speedup(n: np.ndarray, s: float) -> np.ndarray:
    n = np.asarray(n, dtype=float)
    return 1.0 / (s + (1.0 - s) / n)


def gustafson_speedup(n: np.ndarray, s: float) -> np.ndarray:
    n = np.asarray(n, dtype=float)
    return s + (1.0 - s) * n


def r2_score(observed: Sequence[float], predicted: Sequence[float]) -> float:
    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    ss_res = float(np.sum((obs - pred) ** 2))
    ss_tot = float(np.sum((obs - np.mean(obs)) ** 2))
    if ss_tot <= 0.0:
        return 1.0 if ss_res <= 0.0 else 0.0
    return 1.0 - ss_res / ss_tot


def _fit_s_grid(n: np.ndarray, y: np.ndarray, grid: np.ndarray = _GRID) -> float:
    """Minimise SSE over s in [0, 1] on a dense grid, then refine locally.

    The grid sweep is vectorised (shape ``(len(grid), len(n))``): the bootstrap
    calls this thousands of times, so a Python loop over ``s`` is far too slow.
    """
    pred = 1.0 / (grid[:, None] + (1.0 - grid[:, None]) / np.asarray(n, dtype=float)[None, :])
    sse = np.sum((pred - np.asarray(y, dtype=float)[None, :]) ** 2, axis=1)
    i = int(np.argmin(sse))
    lo = float(grid[max(i - 1, 0)])
    hi = float(grid[min(i + 1, grid.size - 1)])
    if hi <= lo:
        return float(grid[i])
    # golden-section refinement inside the bracketing cell
    gr = (np.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c = b - gr * (b - a)
    d = a + gr * (b - a)

    def f(s):
        return float(np.sum((amdahl_speedup(n, s) - y) ** 2))

    for _ in range(60):
        if f(c) < f(d):
            b = d
        else:
            a = c
        c = b - gr * (b - a)
        d = a + gr * (b - a)
    return float((a + b) / 2.0)


@dataclass
class AmdahlFit:
    s: float
    r2: float
    n: List[int]
    speedup: List[float]
    predicted: List[float]
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    ci_level: float = 0.95
    bootstrap: int = 0
    degenerate: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "s": self.s,
            "r2": self.r2,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "ci_level": self.ci_level,
            "bootstrap_samples": self.bootstrap,
            "degenerate_flat_objective": self.degenerate,
            "note": self.note,
            "n": self.n,
            "speedup": self.speedup,
            "predicted": self.predicted,
        }


def fit_amdahl(
    times: Dict[int, Sequence[float]],
    *,
    n_boot: int = 1000,
    seed: int = 20261111,
) -> AmdahlFit:
    """Fit ``s`` from raw per-core-count timing replicates.

    ``times`` maps ``n_cores -> [repeat_1, repeat_2, ...]`` seconds.
    Speedup is defined against the ``n = 1`` median of the same resample.
    """
    ns = sorted(int(k) for k in times)
    if 1 not in ns:
        raise ValueError("fit_amdahl needs an n=1 baseline")
    arr = {k: np.asarray(times[k], dtype=float) for k in ns}
    med = {k: float(np.median(arr[k])) for k in ns}
    base = med[1]
    n_vec = np.asarray(ns, dtype=float)
    sp = np.asarray([base / med[k] for k in ns], dtype=float)
    s_hat = _fit_s_grid(n_vec, sp)
    pred = amdahl_speedup(n_vec, s_hat)
    fit = AmdahlFit(
        s=s_hat,
        r2=r2_score(sp, pred),
        n=ns,
        speedup=[float(x) for x in sp],
        predicted=[float(x) for x in pred],
    )

    # A flat objective means the data cannot separate s from 1 (pseudo-parallel):
    # flag it instead of pretending to a precise estimate.
    sse_at = {s: float(np.sum((amdahl_speedup(n_vec, s) - sp) ** 2)) for s in (0.0, 0.5, 0.9, 1.0)}
    span = max(sse_at.values()) - min(sse_at.values())
    fit.degenerate = span < 1e-9 * max(1.0, float(np.sum(sp**2)))

    if n_boot > 0:
        rng = np.random.default_rng(seed)
        boot = []
        for _ in range(n_boot):
            med_b = {}
            for k in ns:
                x = arr[k]
                med_b[k] = float(np.median(x[rng.integers(0, x.size, x.size)]))
            base_b = med_b[1]
            sp_b = np.asarray([base_b / med_b[k] for k in ns], dtype=float)
            boot.append(_fit_s_grid(n_vec, sp_b))
        alpha = (1.0 - fit.ci_level) / 2.0
        fit.ci_low = float(np.quantile(boot, alpha))
        fit.ci_high = float(np.quantile(boot, 1.0 - alpha))
        fit.bootstrap = n_boot
    if fit.degenerate:
        fit.note = (
            "objective flat in s: measurement is consistent with a fully serial "
            "workload (s -> 1); CI bounds are the meaningful statistic"
        )
    return fit


def fit_gustafson(weak_efficiency: Dict[int, float], *, baseline_n: int = 1) -> Dict[str, object]:
    """Fit Gustafson's ``s_g`` from weak-scaling efficiency.

    ``G(n) = n * E_weak(n)``; the model ``G = s_g + (1 - s_g) n`` is linear in
    ``s_g`` so the least-squares solution is closed form.
    """
    ns = np.asarray(sorted(int(k) for k in weak_efficiency if int(k) >= baseline_n), dtype=float)
    g = np.asarray([float(weak_efficiency[int(n)]) * n for n in ns], dtype=float)
    x = 1.0 - ns
    denom = float(np.sum(x**2))
    s_g = float(np.sum((g - ns) * x) / denom) if denom > 0 else 0.0
    # Physical bound: a serial fraction cannot be negative nor exceed 1.
    s_g = float(min(max(s_g, 0.0), 1.0))
    pred = gustafson_speedup(ns, s_g)
    return {
        "s_gustafson": s_g,
        "r2": r2_score(g, pred),
        "n": [int(v) for v in ns],
        "scaled_speedup": [float(v) for v in g],
        "predicted": [float(v) for v in pred],
    }