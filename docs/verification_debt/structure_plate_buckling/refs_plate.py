"""Independent reference solutions for rectangular-plate elastic buckling.

Deliberately independent of the ship platform: nothing here calls :8001 and no
platform source is copied.  Two routes are provided.

Route A - exact closed form (simply supported, uniaxial compression)
    von Mises (1898) / Timoshenko & Gere, *Theory of Elastic Stability* 2nd ed.
    eq. (9-6).  For a plate ``a x b x t`` simply supported on all four edges,
    compressed along the ``a`` direction::

        sigma_cr = k * pi^2 * D / (b^2 * t),   D = E t^3 / (12 (1 - nu^2))
        k(alpha) = min_m (m/alpha + alpha/m)^2,   alpha = a / b, m = 1,2,...

    The double-sine series ``w = sum A_mn sin(m pi x/a) sin(n pi y/b)`` is the
    exact eigenbasis for these boundary conditions, so the formula is an exact
    eigenvalue and not a curve fit.  :func:`k_ss_ritz` re-obtains the same value
    from a numerical generalised eigenproblem on that basis, as a check that the
    closed form has been transcribed correctly.

Route B - Timoshenko's simply-supported shear coefficient, plus a numerical
    Galerkin eigenproblem that reproduces it::

        k_s = 5.34 + 4/alpha^2    (alpha >= 1)
        k_s = 4.00 + 5.34/alpha^2 (alpha <  1)

Clamped edges: the platform models clamped plates with the single constant
``k = 7.0``, documented in its own source as "a fixed conservative
simplification, not a ship-class-rule equation".  The only literature anchor
that is independent of a plate aspect-ratio table is its long-plate asymptote
``k -> 6.97`` (Timoshenko & Gere, Table 9 / eq. 9-9), which is used here to
grade that branch as an asymptotic simplification rather than as a defect.
An independent Rayleigh-Ritz clamped solution was attempted for this audit and
discarded: its convergence was not established for ``alpha > 4`` with a
5 x 5 clamped-beam basis, so it is not used for any verdict.

Units: ``a, b, t`` in metres; ``E`` and all stresses in MPa (= 1e6 Pa).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TIMOSHENKO_CLAMPED_ASYMPTOTE = 6.97  # long clamped plate, Timoshenko & Gere Table 9


# ---------------------------------------------------------------- plate factor


def plate_factor(E: float, nu: float, t: float, b: float) -> float:
    """``pi^2 D / (b^2 t)`` in MPa, for E in MPa and geometry in metres.

    This is the "unit stress" that the buckling coefficient multiplies; with
    E = 210000 MPa, b = 1 m, t = 12 mm, nu = 0.3 it equals 27.3312 MPa, so
    ``k * plate_factor`` is directly the critical stress in MPa.
    """
    D = E * t**3 / (12.0 * (1.0 - nu**2))
    return math.pi**2 * D / (b**2 * t)


# ------------------------------------------- simply supported, uniaxial, exact


def k_ss_exact(alpha: float, modes: int = 60) -> tuple[float, int]:
    """Exact coefficient ``k`` and the minimising half-wave count ``m*``."""
    if alpha <= 0:
        raise ValueError("alpha must be positive")
    best_m, best_k = 1, float("inf")
    for m in range(1, modes + 1):
        k = (m / alpha + alpha / m) ** 2
        if k < best_k - 1e-14:
            best_m, best_k = m, k
    return best_k, best_m


def k_ss_ritz(alpha: float, M: int = 10, N: int = 3) -> float:
    """Same coefficient from a sine-basis Galerkin eigenvalue problem.

    For ``w = sum_{m<=M,n<=N} A_mn sin(m pi x/a) sin(n pi y/b)`` the strain
    energy and the work of the edge compression ``N_x`` are diagonal::

        U = D/2 sum A^2 (pi^2/a^2 m^2 + pi^2/b^2 n^2)^2 (ab/4)
        W = N_x/2 sum A^2 (pi^2/a^2 m^2)(ab/4)

    so ``k`` is the smallest generalised eigenvalue of ``(K, G)``.  Must agree
    with :func:`k_ss_exact` to machine precision - that is the self-check.
    """
    ms = np.arange(1, M + 1, dtype=float)
    ns = np.arange(1, N + 1, dtype=float)
    dx = (ms / alpha) ** 2  # (m pi / a)^2 in (pi/b)^2 units
    dy = ns**2  # (n pi / b)^2 in (pi/b)^2 units
    lam = np.array([d_ + e_ for d_ in dx for e_ in dy])
    work = np.array([d_ for d_ in dx for _ in dy])
    return float(np.min(lam**2 / work))


# ------------------------------------------------------------------- shear


def k_shear_ss_exact(alpha: float) -> float:
    """Timoshenko simply-supported pure-shear buckling coefficient."""
    if alpha >= 1.0:
        return 5.34 + 4.0 / alpha**2
    return 4.00 + 5.34 / alpha**2


def k_shear_ss_numeric(alpha: float, M: int = 8, N: int = 8) -> float:
    """Shear coefficient from a sine-basis Galerkin eigenproblem.

    Energy statement: ``D/2 int (lap w)^2 dA = N_xy int w_x w_y dA``.  With
    ``w = sum_{m,n} A_mn sin(m pi x/a) sin(n pi y/b)``

        K_rc = (ab/8) lam_r^2 delta_rc,   lam_r = (m pi/a)^2 + (n pi/b)^2
        G_rc = pi^2 m q Ix(m,p) Iy(n,q),  Ix = int_0^1 cos(m pi xi) sin(p pi xi) dxi

    (the ``1/8`` carries the ``D/2`` of the strain energy and the ``ab/4`` of the
    sine norm).  ``k_s = min positive eigenvalue of K^-1 G`` times ``b^2/pi^2``.
    Reproduces Timoshenko's 9.34 at ``alpha = 1`` to better than 1%.
    """
    xi, wi = _gauss(400)
    ms = np.arange(1, M + 1)
    ns = np.arange(1, N + 1)
    Ix = np.zeros((M + 1, M + 1))
    Iy = np.zeros((N + 1, N + 1))
    for m in ms:
        for p in ms:
            Ix[m, p] = float(np.sum(wi * np.cos(m * math.pi * xi) * np.sin(p * math.pi * xi)))
    for n in ns:
        for q in ns:
            Iy[n, q] = float(np.sum(wi * np.sin(n * math.pi * xi) * np.cos(q * math.pi * xi)))
    a, b = alpha, 1.0
    idx = {(m, n): (m - 1) * N + (n - 1) for m in ms for n in ns}
    ntot = M * N
    K = np.zeros((ntot, ntot))
    G = np.zeros((ntot, ntot))
    for (m, n), r in idx.items():
        lam = (m * math.pi / a) ** 2 + (n * math.pi / b) ** 2
        K[r, r] = 0.125 * a * b * lam**2
    for (m, n), r in idx.items():
        for (p, q), c in idx.items():
            G[r, c] = math.pi**2 * m * q * Ix[m, p] * Iy[n, q]
    eigs = np.linalg.eigvals(np.linalg.solve(G, K))
    pos = []
    for z in eigs:
        if abs(z.imag) < 1e-6 * max(1.0, abs(z)) and z.real > 1e-6:
            pos.append(z.real)
    if not pos:
        raise RuntimeError("no positive eigenvalue for the shear basis")
    return float(min(pos)) * b**2 / math.pi**2


def _gauss(n: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = np.polynomial.legendre.leggauss(n)
    return 0.5 * (x + 1.0), 0.5 * w


# --------------------------------------------------------- stress conversion


def sigma_from_k(k: float, E: float, nu: float, t: float, b: float) -> float:
    """Critical stress in MPa from the buckling coefficient."""
    return k * plate_factor(E, nu, t, b)


def k_from_sigma(sigma: float, E: float, nu: float, t: float, b: float) -> float:
    """Effective buckling coefficient implied by a reported critical stress."""
    return sigma / plate_factor(E, nu, t, b)


def P_cr_kN(sigma_MPa: float, b_m: float, t_m: float) -> float:
    """Edge compressive force at buckling in kN for metre-based geometry.

    ``1 MPa = 1e6 Pa`` so ``P = sigma * b * t`` in newtons is
    ``sigma_MPa * b_m * t_m * 1e3`` in kN.
    """
    return sigma_MPa * b_m * t_m * 1e3


@dataclass(frozen=True)
class Anchor:
    """A literature anchor used for grading."""

    name: str
    value: float
    source: str


SS_ANCHORS = (
    Anchor("k_ss(alpha=0.5)", k_ss_exact(0.5)[0], "Timoshenko & Gere eq. 9-6 (exact)"),
    Anchor("k_ss(alpha=1.0)", k_ss_exact(1.0)[0], "Timoshenko & Gere eq. 9-6 (exact)"),
    Anchor("k_ss(alpha=1.5)", k_ss_exact(1.5)[0], "Timoshenko & Gere eq. 9-6 (exact)"),
    Anchor("k_ss(alpha=2.0)", k_ss_exact(2.0)[0], "Timoshenko & Gere eq. 9-6 (exact)"),
)