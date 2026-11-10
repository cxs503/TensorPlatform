"""Largest Lyapunov exponent of a coupled discrete evolution (domain-neutral).

A coupled scheme is *stable* when a perturbation does not grow: the largest
Lyapunov exponent of the coupled map has to be ``<= 0`` (zero is the
conservative/marginal case).  This module estimates the spectrum with the
standard Benettin algorithm:

1. propagate ``m`` orthonormal perturbation vectors with the tangent map;
2. re-orthonormalise them every step (Gram–Schmidt) and accumulate
   ``log |R_jj|`` — the local expansion factors;
3. average over the run to get ``λ_j = Σ log|R_jj| / (n · dt)``.

The tangent map is obtained by finite differences of the *map itself*
(``J·q ≈ (F(x + εq) − F(x)) / ε``), so a caller only supplies the evolution —
no analytic Jacobian, no assumption about the integrator.  That is also why the
estimate is reported together with its running series: a single number hides
whether the transient was discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

__all__ = ["LyapunovError", "LyapunovResult", "lyapunov_spectrum", "linear_map_probe"]


class LyapunovError(ValueError):
    """Raised when a spectrum estimate is not well posed."""


@dataclass
class LyapunovResult:
    """Estimated spectrum plus the convergence history of the leading exponent."""

    exponents: np.ndarray
    series: np.ndarray
    n_steps: int
    dt: float
    transient_steps: int
    eps: float

    @property
    def leading(self) -> float:
        return float(self.exponents[0])

    def stable(self, margin: float = 0.0) -> bool:
        """``λ_max <= margin`` — a positive margin requires *strict* stability."""
        return self.leading <= margin

    def to_dict(self) -> Dict[str, object]:
        return {
            "exponents": [float(v) for v in np.atleast_1d(self.exponents)],
            "leading": self.leading,
            "series_tail": [float(v) for v in self.series[-5:]],
            "n_steps": int(self.n_steps),
            "dt": float(self.dt),
            "transient_steps": int(self.transient_steps),
            "eps": float(self.eps),
        }


def lyapunov_spectrum(
    step: Callable[[np.ndarray], np.ndarray],
    x0: Sequence[float],
    *,
    n_steps: int = 4000,
    dt: float = 1.0,
    transient_steps: int = 200,
    eps: float = 1e-7,
    n_exponents: Optional[int] = None,
) -> LyapunovResult:
    """Estimate the Lyapunov spectrum of the map ``step`` from the state ``x0``.

    Parameters
    ----------
    step
        One application of the evolution, ``x_{n+1} = step(x_n)``.
    dt
        Physical time per application; exponents carry units of ``1/time``.
    transient_steps
        Steps discarded before accumulation (settling of the reference orbit).
    eps
        Finite-difference perturbation used to apply the tangent map.
    """
    x = np.asarray(x0, dtype=float).reshape(-1)
    dim = x.size
    m = dim if n_exponents is None else int(n_exponents)
    if not (1 <= m <= dim):
        raise LyapunovError(f"n_exponents must be in [1, {dim}], got {m}")
    if n_steps <= 0 or dt <= 0.0:
        raise LyapunovError("n_steps and dt must be positive")
    if eps <= 0.0:
        raise LyapunovError("eps must be positive")

    Q = np.eye(dim, m)
    sums = np.zeros(m)
    series: List[float] = []
    for n in range(n_steps + transient_steps):
        fx = np.asarray(step(x), dtype=float).reshape(-1)
        if fx.size != dim:
            raise LyapunovError(f"step() changed the state dimension: {fx.size} != {dim}")
        columns = np.empty((dim, m))
        for j in range(m):
            shifted = np.asarray(step(x + eps * Q[:, j]), dtype=float).reshape(-1)
            columns[:, j] = (shifted - fx) / eps
        Q, R = np.linalg.qr(columns)
        diag = np.abs(np.diag(R)).copy()
        diag[diag < np.finfo(float).tiny] = np.finfo(float).tiny
        if n >= transient_steps:
            sums += np.log(diag)
            series.append(float(sums[0] / ((n - transient_steps + 1) * dt)))
        x = fx
    exponents = sums / (n_steps * dt)
    return LyapunovResult(
        exponents=exponents,
        series=np.asarray(series, dtype=float),
        n_steps=n_steps,
        dt=float(dt),
        transient_steps=transient_steps,
        eps=float(eps),
    )


def linear_map_probe(matrix: Sequence[Sequence[float]], dt: float = 1.0) -> np.ndarray:
    """Exact reference spectrum ``log|eig(M)|/dt`` for a linear map.

    Used to validate the finite-difference estimator itself: for a linear map the
    Benettin estimate must reproduce this number, which is the only way to know
    that a ``<= 0`` verdict is a property of the system and not of the estimator.
    """
    m = np.asarray(matrix, dtype=float)
    eig = np.linalg.eigvals(m)
    with np.errstate(divide="ignore"):
        return np.sort(np.log(np.abs(eig)) / dt)[::-1]