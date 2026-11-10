"""Plain / Latin-hypercube Monte Carlo propagation with interval estimates.

The framework keeps Monte Carlo as the **reference estimator**: whatever the
chaos surrogate reports is scored against MC on the same problem, so the cheap
method can never silently become the definition of truth.

Reported per output: mean and variance (Bessel corrected), the requested
quantiles, a percentile-bootstrap interval for the mean, and the *number of
model evaluations* -- the quantity the cost claim is scored on (wall clock is
also returned, but only as a diagnostic; the speed-up criterion uses counts).

Sobol' shares are estimated with the complement design (``A``, ``B`` and ``M_u``
= ``A`` in coordinates ``u``, ``B`` elsewhere):

    G_u = E[y_A y_{M_u}] - f0^2 = sum_{w subseteq u, w != {}} V_w,

Mobius-inverted to exact-order shares.  Estimation happens in the canonical
unit box of :mod:`.marginals`, which is legitimate because the shares are
invariant under the (monotone) isoprobabilistic map.  With
``max_order = d`` the shares are *exact* order-decomposed and
``sum(S) = 1`` becomes a checkable identity rather than an assumption.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np

from .marginals import InputDistribution
from .pce import latin_hypercube

__all__ = ["run_monte_carlo", "sobol_complement", "call_in_unit_box"]


def call_in_unit_box(
    func: Callable[..., Any],
    dists: Sequence[InputDistribution],
    U: np.ndarray,
    call_style: str = "kwargs",
) -> np.ndarray:
    """Evaluate ``func`` on unit-box samples by mapping to physical space."""
    X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(dists)])
    if call_style == "array":
        y = func(X)
    elif call_style == "kwargs":
        y = func(**{m.name: X[:, j] for j, m in enumerate(dists)})
    else:
        raise ValueError(f"unknown call_style {call_style!r}")
    y = np.asarray(y, dtype=float).ravel()
    if y.size != U.shape[0]:
        raise ValueError(f"model returned {y.size} values for {U.shape[0]} samples")
    return y


def run_monte_carlo(
    func: Callable[..., Any],
    dists: Sequence[InputDistribution],
    n: int = 1000,
    seed: int = 0,
    design: str = "lhs",
    quantiles: Sequence[float] = (0.05, 0.5, 0.95),
    ci_level: float = 0.95,
    n_boot: int = 2000,
    call_style: str = "kwargs",
    sobol: bool = False,
    sobol_n_base: int = 256,
    sobol_max_order: int | None = None,
) -> Dict[str, Any]:
    """Monte Carlo propagation of a black-box response."""
    if n < 10:
        raise ValueError("n must be >= 10 for a usable variance estimate")
    d = len(dists)
    design = str(design).lower()
    if design not in ("mc", "lhs"):
        raise ValueError("design must be 'mc' or 'lhs'")
    rng = np.random.default_rng(seed)
    t0 = time.perf_counter()
    U = latin_hypercube(n, d, seed) if design == "lhs" else rng.random((int(n), int(d)))
    y = call_in_unit_box(func, dists, U, call_style=call_style)
    wall = time.perf_counter() - t0

    mean = float(np.mean(y))
    var = float(np.var(y, ddof=1))
    std = float(np.sqrt(var))

    # percentile bootstrap for the mean
    boot = np.empty(int(n_boot))
    idx = rng.integers(0, y.size, size=(int(n_boot), y.size))
    boot[:] = y[idx].mean(axis=1)
    alpha = (1.0 - float(ci_level)) / 2.0
    ci = [float(np.quantile(boot, alpha)), float(np.quantile(boot, 1.0 - alpha))]

    q = {f"p{100 * float(p):g}": float(np.quantile(y, p)) for p in quantiles}
    out: Dict[str, Any] = {
        "method": f"monte_carlo[{design}]",
        "n_samples": int(n),
        "n_evals": int(n),
        "seed": int(seed),
        "wall_seconds": float(wall),
        "mean": mean,
        "variance": var,
        "std": std,
        "ci_level": float(ci_level),
        "ci_mean": ci,
        "quantiles": q,
        "skewness": float(np.mean(((y - mean) / std) ** 3)) if std > 0 else 0.0,
    }
    if sobol:
        md = int(sobol_max_order) if sobol_max_order is not None else d
        md = max(1, min(md, d))

        def func_unit(UU: np.ndarray) -> np.ndarray:
            return call_in_unit_box(func, dists, UU, call_style=call_style)

        sob = sobol_complement(func_unit, d, n_base=sobol_n_base, max_order=md, seed=seed + 7)
        out["sobol"] = sob
        out["n_evals"] = int(out["n_evals"] + sob["n_evals"])
    return out


# --------------------------------------------------------------------------- #
# Sobol' by complement design (exact order decomposition)
# --------------------------------------------------------------------------- #
def sobol_complement(
    func_unit: Callable[[np.ndarray], np.ndarray],
    d: int,
    n_base: int = 256,
    max_order: int | None = None,
    seed: int = 0,
    names: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Variance decomposition by the complement (pick-free) design."""
    md = max(1, min(int(max_order if max_order is not None else d), d))
    if n_base < 32:
        raise ValueError("n_base must be >= 32")
    # stratified (Latin-hypercube) base designs: for smooth responses the
    # variance of the complement estimator drops markedly versus iid draws,
    # at identical cost
    A = latin_hypercube(n_base, d, seed)
    B = latin_hypercube(n_base, d, seed + 991)
    yA = np.asarray(func_unit(A), dtype=float).ravel()
    yB = np.asarray(func_unit(B), dtype=float).ravel()
    f0_sq = float(0.5 * (yA.mean() + yB.mean())) ** 2
    var = float(np.var(np.concatenate([yA, yB]), ddof=1))
    if var <= 0:
        raise ValueError("response has zero variance; Sobol' indices undefined")

    evals = 2 * n_base
    G: Dict[Tuple[int, ...], float] = {}
    for size in range(1, md + 1):
        for u in itertools.combinations(range(d), size):
            M = B.copy()
            for j in u:
                M[:, j] = A[:, j]
            G[u] = float(np.mean(yA * np.asarray(func_unit(M), dtype=float).ravel())) - f0_sq
            evals += n_base

    V: Dict[Tuple[int, ...], float] = {}
    for size in range(1, md + 1):
        for u in itertools.combinations(range(d), size):
            tot = 0.0
            for k in range(1, size + 1):
                for w in itertools.combinations(u, k):
                    tot += ((-1) ** (size - k)) * G[w]
            V[u] = tot

    labels = list(names) if names is not None else [f"x{j}" for j in range(d)]
    first = {labels[j]: float(V[(j,)] / var) for j in range(d)}
    total = {}
    for j in range(d):
        s = sum(v for u, v in V.items() if j in u)
        total[labels[j]] = float(s / var)
    closure = float(sum(V.values()) / var)  # == 1 when max_order == d
    return {
        "method": "complement_design",
        "n_base": int(n_base),
        "max_order": int(md),
        "n_evals": int(evals),
        "variance": var,
        "first_order": first,
        "total_order": total,
        "exact_order": {"".join(str(i) for i in u): float(v / var) for u, v in V.items()},
        "closure": closure,
    }