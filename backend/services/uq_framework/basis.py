"""Per-marginal orthonormal polynomial bases (generalised polynomial chaos).

Why not one fixed basis
-----------------------
Fitting a chaos expansion in a single "isoprobabilistic uniform" coordinate
(``xi = 2F(x) - 1``, Legendre basis) is mathematically valid for *every*
continuous family, but it converges badly when the inverse CDF is not smooth:
for a normal input ``ndtri`` has a logarithmic endpoint singularity, so a
polynomial in ``xi`` is a poor polynomial in ``x``.  Measured on a *linear*
response with one normal input, the uniform-coordinate fit only reached
``R^2 = 0.94`` -- an unacceptable surrogate error for a framework whose whole
point is that the cheap leg is scored against Monte Carlo.

The textbook fix is generalised polynomial chaos: expand in polynomials that
are orthonormal with respect to *each* marginal's own measure,

    E[psi_k(X_j) psi_l(X_j)] = delta_kl,

so that the product basis is orthonormal under the joint measure and the
closed-form identities ``mean = c_0``, ``Var = sum_{alpha != 0} c_alpha^2``
stay valid *exactly*, for mixed families too.  Families implemented:

``normal``      probabilists' Hermite ``He_k(z)/sqrt(k!)`` with ``z = (x-mu)/sigma``;
``uniform``     Legendre ``sqrt(2k+1) P_k(z)`` with ``z`` mapped onto [-1, 1];
``triangular``  monic three-term recurrence (Stieltjes) against the exact
                triangular measure of ``z`` on [-1, 1]; the raw moments are
                evaluated by two-piece Gauss-Legendre quadrature, which is
                exact for the polynomial degrees used here (a 40-node rule
                integrates degree <= 79 exactly; the density is piecewise
                linear, so the integral splits into two polynomial pieces).

Polynomial responses (linear, quadratic, interactions) are then reproduced to
machine precision by a finite-order expansion -- which is what makes the
``PCE vs MC < 5%`` criterion a test of the *implementation* rather than of the
truncation order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

import numpy as np

from .marginals import InputDistribution

__all__ = ["MarginalBasis", "basis_for", "legendre_coefficients", "triangular_std_moments"]


def _legendre_p(z: np.ndarray, degree: int) -> List[np.ndarray]:
    """``P_0..P_degree`` on ``z`` (three-term recurrence, no scipy needed)."""
    z = np.asarray(z, dtype=float)
    out = [np.ones_like(z)]
    if degree >= 1:
        out.append(z.copy())
    for k in range(1, degree):
        out.append(((2 * k + 1) * z * out[k] - k * out[k - 1]) / (k + 1))
    return out[: degree + 1]


def _hermite_he(z: np.ndarray, degree: int) -> List[np.ndarray]:
    """Probabilists' ``He_0..He_degree`` on ``z`` (``He_{k+1} = z He_k - k He_{k-1}``)."""
    z = np.asarray(z, dtype=float)
    out = [np.ones_like(z)]
    if degree >= 1:
        out.append(z.copy())
    for k in range(1, degree):
        out.append(z * out[k] - k * out[k - 1])
    return out[: degree + 1]


def triangular_std_moments(lower: float, mode: float, upper: float, n_max: int) -> np.ndarray:
    """Exact ``E[z^n]`` for ``z = 2(x-a)/(b-a) - 1`` when ``x ~ Triangular(a, c, b)``.

    In the standardised variable the triangular density is piecewise linear with
    unit peak at ``zc``: ``g(z) = (z+1)/(zc+1)`` on ``[-1, zc]`` and
    ``g(z) = (1-z)/(1-zc)`` on ``[zc, 1]``.  Each piece is integrated with a
    40-node Gauss-Legendre rule, which is exact for ``z^n * (linear)`` up to
    ``n = 78`` -- far above the polynomial orders used by the chaos fits, so the
    moments (and hence the orthonormal basis) are exact, not quadrature-noisy.
    """
    a, c, b = float(lower), float(mode), float(upper)
    span = b - a
    zc = 2.0 * (c - a) / span - 1.0
    nodes, weights = np.polynomial.legendre.leggauss(40)
    moments = np.zeros(int(n_max) + 1)
    pieces = []
    if zc > -1.0:
        pieces.append((-1.0, zc, lambda z, zc=zc: (z + 1.0) / (zc + 1.0)))
    if zc < 1.0:
        pieces.append((zc, 1.0, lambda z, zc=zc: (1.0 - z) / (1.0 - zc)))
    for lo, hi, dens in pieces:
        half = 0.5 * (hi - lo)
        mid = 0.5 * (hi + lo)
        zz = mid + half * nodes
        g = dens(zz)
        for n in range(moments.size):
            moments[n] += half * float(np.sum(weights * zz ** n * g))
    if moments[0] <= 0:
        raise ValueError("degenerate triangular moments")
    return moments / moments[0]


def legendre_coefficients(moments: Sequence[float], degree: int) -> List[np.ndarray]:
    """Orthonormal polynomials by the Stieltjes three-term recurrence.

    ``coefficients[k][i]`` is the coefficient of ``z**i`` in ``psi_k``; the
    inner product ``<p, q> = sum_ij p_i q_j m_{i+j}`` uses the raw moments
    ``m`` of the standardised variable, so no quadrature error enters once the
    moments are exact.
    """
    m = np.asarray(moments, dtype=float)

    def inner(p: np.ndarray, q: np.ndarray) -> float:
        if len(p) + len(q) - 1 > m.size:
            raise ValueError("moments vector too short for the requested inner product")
        return float(sum(p[i] * q[j] * m[i + j]
                         for i in range(len(p)) for j in range(len(q))))

    def pad(v: np.ndarray, n: int) -> np.ndarray:
        return np.concatenate([v, np.zeros(n - len(v))]) if len(v) < n else v

    pis: List[np.ndarray] = [np.array([1.0])]
    norms: List[float] = [inner(pis[0], pis[0])]
    out: List[np.ndarray] = [pis[0] / np.sqrt(norms[0])]
    for k in range(degree):
        n_len = len(pis[k]) + 1
        x_pi = np.concatenate([[0.0], pis[k]])            # z * pi_k
        alpha = inner(x_pi, pis[k]) / norms[k]
        nxt = x_pi - pad(alpha * pis[k], n_len)
        if k > 0:
            beta = norms[k] / norms[k - 1]
            nxt = nxt - pad(beta * pis[k - 1], n_len)
        nk = inner(nxt, nxt)
        if not nk > 0:
            raise ValueError(f"degenerate measure: recurrence collapsed at order {k + 1}")
        pis.append(nxt)
        norms.append(nk)
        out.append(nxt / np.sqrt(nk))
    return out


@dataclass(frozen=True)
class MarginalBasis:
    """Orthonormal polynomial basis of one uncertain input."""

    dist: InputDistribution
    degree: int

    # -- internal ----------------------------------------------------------
    def _standardised(self, x: np.ndarray) -> np.ndarray:
        d = self.dist
        x = np.asarray(x, dtype=float)
        if d.family == "normal":
            return (x - d.params["mean"]) / d.params["std"]
        a, b = d.params["lower"], d.params["upper"]
        return 2.0 * (x - a) / (b - a) - 1.0

    def _raw(self, z: np.ndarray) -> List[np.ndarray]:
        d = self.dist
        if d.family == "normal":
            hs = _hermite_he(z, self.degree)
            return [hs[k] / np.sqrt(float(math.factorial(k))) for k in range(self.degree + 1)]
        if d.family == "uniform":
            ps = _legendre_p(z, self.degree)
            return [np.sqrt(2.0 * k + 1.0) * ps[k] for k in range(self.degree + 1)]
        # triangular: Stieltjes basis against the exact measure of z
        mom = triangular_std_moments(d.params["lower"], d.params["mode"], d.params["upper"],
                                     2 * self.degree + 2)
        coefs = legendre_coefficients(mom, self.degree)
        zz = np.asarray(z, dtype=float)
        return [sum(c[i] * zz ** i for i in range(len(c))) for c in coefs]

    def values(self, x: np.ndarray, degree: int | None = None) -> np.ndarray:
        """``(n, degree+1)`` array of ``psi_0..psi_degree`` evaluated at ``x``."""
        deg = self.degree if degree is None else int(degree)
        z = self._standardised(np.asarray(x, dtype=float).ravel())
        cols = self._raw(z)
        return np.column_stack(cols[: deg + 1])

    @property
    def family(self) -> str:
        return self.dist.family


def basis_for(dist: InputDistribution, degree: int) -> MarginalBasis:
    return MarginalBasis(dist=dist, degree=int(degree))