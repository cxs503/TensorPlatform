"""Global sensitivity analysis: Sobol indices via PCE and MC-based estimators.

Provides two routes to Sobol indices:
1. **PCE-based** (spectral): exact from the polynomial chaos coefficients
   when the basis is orthonormal. Zero additional model evaluations.
2. **MC-based** (Saltelli/Jansen): sampling-based estimators that work for
   any black-box model, at the cost of O(N·d) additional evaluations.

The PCE route is preferred when a surrogate is available; the MC route is
the fallback for non-intrusive verification or when the PCE is not accurate.

Domain-free: the model is injected via a callable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend.services.pce_uq import PCE, Marginal, latin_hypercube


@dataclass
class SobolResult:
    """Sobol sensitivity indices (first-order and total-order)."""
    first_order: Dict[str, float]
    total_order: Dict[str, float]
    method: str  # "pce_spectral" | "mc_saltelli" | "mc_jansen"
    n_model_evaluations: int
    wall_seconds: float
    variance: float
    sum_first_order: float
    sum_total_order: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "first_order": self.first_order,
            "total_order": self.total_order,
            "method": self.method,
            "n_model_evaluations": self.n_model_evaluations,
            "wall_seconds": self.wall_seconds,
            "variance": self.variance,
            "sum_first_order": self.sum_first_order,
            "sum_total_order": self.sum_total_order,
        }

    def ranking(self, by: str = "total_order") -> List[Tuple[str, float]]:
        """Return input names sorted by sensitivity (descending)."""
        d = self.total_order if by == "total_order" else self.first_order
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)


def sobol_from_pce(pce: PCE) -> SobolResult:
    """Extract Sobol indices from a fitted PCE (spectral route).

    Zero additional model evaluations: the indices are closed-form from
    the orthonormal polynomial chaos coefficients.
    """
    t0 = time.perf_counter()
    s = pce.sobol()
    dt = time.perf_counter() - t0
    return SobolResult(
        first_order=s["first_order"],
        total_order=s["total_order"],
        method="pce_spectral",
        n_model_evaluations=0,  # no additional evaluations
        wall_seconds=dt,
        variance=s["variance"],
        sum_first_order=s["sum_first_order"],
        sum_total_order=s["sum_total_order"],
    )


def sobol_mc_saltelli(
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    n: int = 4096,
    seed: int = 42,
) -> SobolResult:
    """Saltelli (2010) sampling-based Sobol indices.

    Requires O(N·(d+2)) model evaluations for d inputs.
    Uses the Jansen estimator for total-order indices (more stable).

    Parameters
    ----------
    func : callable
        Model function f(**kwargs) -> float.
    marginals : sequence of Marginal
        Input distributions.
    n : int
        Base sample size (power of 2 recommended).
    seed : int
        Random seed.
    """
    t0 = time.perf_counter()
    d = len(marginals)
    names = [m.name for m in marginals]
    rng = np.random.default_rng(int(seed))

    # Generate two independent sample matrices A and B
    A_unit = rng.random((int(n), d))
    B_unit = rng.random((int(n), d))

    # Transform to physical space
    A_phys = np.column_stack([m.from_unit(A_unit[:, j]) for j, m in enumerate(marginals)])
    B_phys = np.column_stack([m.from_unit(B_unit[:, j]) for j, m in enumerate(marginals)])

    # Evaluate model on A and B
    Y_A = _evaluate_batch(func, A_phys, names)
    Y_B = _evaluate_batch(func, B_phys, names)

    # Total evaluations so far: 2*N
    n_eval = 2 * int(n)

    # For each input i, create AB^(i) by replacing column i of A with column i of B
    first_order = {}
    total_order = {}
    f0 = 0.5 * (np.mean(Y_A) + np.mean(Y_B))
    var_total = 0.5 * (np.var(Y_A, ddof=1) + np.var(Y_B, ddof=1))

    for i in range(d):
        AB_i = A_phys.copy()
        AB_i[:, i] = B_phys[:, i]
        Y_AB_i = _evaluate_batch(func, AB_i, names)
        n_eval += int(n)

        # First-order (Saltelli estimator)
        # S_i = (1/N) * sum(Y_B * (Y_AB_i - Y_A)) / Var(Y)
        vi = np.mean(Y_B * (Y_AB_i - Y_A))
        first_order[names[i]] = float(vi / var_total) if var_total > 0 else 0.0

        # Total-order (Jansen estimator)
        # S_Ti = (1/(2N)) * sum((Y_A - Y_AB_i)^2) / Var(Y)
        vti = 0.5 * np.mean((Y_A - Y_AB_i) ** 2)
        total_order[names[i]] = float(vti / var_total) if var_total > 0 else 0.0

    dt = time.perf_counter() - t0
    return SobolResult(
        first_order=first_order,
        total_order=total_order,
        method="mc_saltelli",
        n_model_evaluations=n_eval,
        wall_seconds=dt,
        variance=float(var_total),
        sum_first_order=float(sum(first_order.values())),
        sum_total_order=float(sum(total_order.values())),
    )


def _evaluate_batch(func: Callable, X: np.ndarray, names: Sequence[str]) -> np.ndarray:
    """Evaluate func row-by-row, returning an array of scalars."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    out = np.empty(X.shape[0], dtype=float)
    for i in range(X.shape[0]):
        kwargs = {str(n): float(X[i, j]) for j, n in enumerate(names)}
        val = func(**kwargs)
        out[i] = float(val)
    return out


__all__ = [
    "SobolResult",
    "sobol_from_pce",
    "sobol_mc_saltelli",
]
