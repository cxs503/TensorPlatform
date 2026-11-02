"""Independent thin-ship (Michell) reference solution for the Wigley parabolic hull.

Purpose
-------
The ship-performance platform claims REAL wave resistance for the whole
`Fn` range (`/api/v1/resistance/predict` -> `wave_resistance`).  There is no
analytic reference in the repository against which that claim can be checked,
so this module builds one:

* Wigley parabolic hull, half-breadth

      y(x, z) = B/2 * (1 - (2x/L)^2) * (1 - (z/T)^2),
      x in [-L/2, L/2],  z in [0, T] measured downward from the waterline,

  for which the displacement and block coefficient are available in closed
  form ( 4 L B T / 9  and  4/9 ), so the geometry gate (Cb/L/B) can be closed
  without any fitted data.

* Michell (1898) thin-ship wave resistance in deep water, written in the
  dimensionally explicit form used below (`I` in m^2 for one hull side):

      Rw = 8 rho g^2 / (pi U^2) * Int_0^{pi/2} |I(theta)|^2 sec^3(theta) dtheta

      I(theta) = Int_S (dy/dx) exp(-k0 zeta sec^2 theta)
                          exp(i k0 x sec theta) dx dzeta,     k0 = g / U^2

  The factor 8 = 4 (Michell) x 2 (both sides); the two-sided Kochin function
  would instead be 2 rho g^2 / (pi U^2) with |I_2side|^2 = 4 |I|^2.

Two independent routes, so an implementation error cannot hide:

  route A  `rw_theta_integral` - closed-form inner integrals (exact in x, zeta),
           Gauss-Legendre quadrature in theta only.
  route B  `rw_double_quad`    - numerical dy/dx (central difference of
           `half_breadth`, i.e. the geometry function is *not* differentiated
           analytically) + 2-D Gauss-Legendre quadrature over the hull surface,
           then a coarse theta quadrature.

Route A is vectorised (milliseconds); route B is deliberately slow/dumb
(seconds).  Their agreement is the implementation check; theta refinement /
Richardson extrapolation is the discretisation check; the classical
Cw ~ 4.4e-3 hump of the Wigley hull near Fn = 0.30 and the exp(-c/Fn^2) decay
at low Fn are the physics checks (see `tests/test_verification_debt_wigley.py`).

Units: SI.  U [m/s], lengths [m], Rw [N], rho [kg/m^3].
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Tuple

import numpy as np

__all__ = [
    "Wigley",
    "rw_theta_integral",
    "rw_double_quad",
    "michell_rw",
    "michell_rw_reference",
    "michell_rw_converged",
    "michell_cw",
    "wetted_surface",
    "leggauss",
]


@lru_cache(maxsize=64)
def leggauss(n: int) -> Tuple[np.ndarray, np.ndarray]:
    """Cached Gauss-Legendre rule.

    `numpy.polynomial.legendre.leggauss` is O(n^2) with a large constant
    (n=800 costs ~10 s), which is what made the original audit hang: the old
    default was 2000 x 800 nodes.  All spectra here converge to 1e-9 with a few
    hundred nodes, so the cache is enough.
    """
    return np.polynomial.legendre.leggauss(n)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Wigley:
    """Wigley parabolic hull (L/B = 10, B/T = 1.6 are the classical ratios)."""

    L: float = 100.0
    B: float = 10.0
    T: float = 6.25
    rho: float = 1025.0
    g: float = 9.81

    # -- closed-form geometry ------------------------------------------------
    @property
    def volume(self) -> float:
        """Analytic displacement volume 4 L B T / 9."""
        return 4.0 * self.L * self.B * self.T / 9.0

    @property
    def cb(self) -> float:
        """Analytic block coefficient 4/9 = 0.4444..."""
        return 4.0 / 9.0

    @property
    def displacement_t(self) -> float:
        return self.volume * self.rho / 1000.0

    @property
    def waterplane_area(self) -> float:
        """Exact waterplane area 2 L B / 3 (Cwp = 2/3)."""
        return 2.0 * self.L * self.B / 3.0

    def half_breadth(self, x: float, zeta: float) -> float:
        """y(x, zeta); zeta is depth below the waterline (zeta=0 at WL)."""
        if abs(x) > self.L / 2.0 or zeta < 0.0 or zeta > self.T:
            return 0.0
        return 0.5 * self.B * (1.0 - (2.0 * x / self.L) ** 2) * (
            1.0 - (zeta / self.T) ** 2
        )

    def dydx(self, x: float, zeta: float) -> float:
        """Analytic dy/dx (used by route A)."""
        return -4.0 * self.B * x / self.L**2 * (1.0 - (zeta / self.T) ** 2)


def wetted_surface(hull: Wigley, n_x: int = 240, n_z: int = 120) -> float:
    """Wetted surface area of both sides of the Wigley hull by quadrature.

    dS = 2 * sqrt(1 + (dy/dx)^2 + (dy/dzeta)^2) dx dzeta  (mirror symmetry).
    The integrand is analytic, so Gauss-Legendre converges spectrally:
    240 x 120 nodes already agree with 480 x 240 to < 1e-9 relative.
    """
    gx, wx = leggauss(n_x)
    gz, wz = leggauss(n_z)
    x = 0.5 * hull.L * gx
    wx = 0.5 * hull.L * wx
    zeta = 0.5 * hull.T * (gz + 1.0)
    wz = 0.5 * hull.T * wz

    X, Z = np.meshgrid(x, zeta, indexing="ij")
    dydx = -4.0 * hull.B * X / hull.L**2 * (1.0 - (Z / hull.T) ** 2)
    dydz = 0.5 * hull.B * (1.0 - (2.0 * X / hull.L) ** 2) * (-2.0 * Z / hull.T**2)
    dS = 2.0 * np.sqrt(1.0 + dydx**2 + dydz**2)
    return float(np.einsum("i,j,ij->", wx, wz, dS))


# ---------------------------------------------------------------------------
# Shared: theta truncation of the (Gaussian) integrand
# ---------------------------------------------------------------------------
def _theta_grid(hull: Wigley, U: float, n_theta: int) -> Tuple[np.ndarray, np.ndarray]:
    """Gauss-Legendre nodes/weights on [0, theta_max].

    |I|^2 carries the factor exp(-2 k0 T sec^2 theta); truncating where that
    exponent reaches -60 makes the omitted tail < 1e-26 of the peak value.
    """
    k0 = hull.g / U**2
    arg = k0 * hull.T / 30.0  # exp(-2 k0 T sec^2) = exp(-60) at theta_max
    cos_t = math.sqrt(min(1.0, arg)) if arg > 1e-300 else 1.0
    theta_max = math.acos(cos_t)
    gt, wt = leggauss(n_theta)
    return 0.5 * theta_max * (gt + 1.0), 0.5 * theta_max * wt


# ---------------------------------------------------------------------------
# Route A : closed-form inner integrals, quadrature in theta
# ---------------------------------------------------------------------------
def _inner_x(hull: Wigley, b):
    """Int_{-L/2}^{L/2} x exp(i b x) dx  (purely imaginary)."""
    A = hull.L / 2.0
    b = np.asarray(b, dtype=float)
    safe = np.where(np.abs(b) < 1e-12, 1.0, b)
    out = 2.0j * (np.sin(safe * A) / safe**2 - A * np.cos(safe * A) / safe)
    return np.where(np.abs(b) < 1e-12, 0.0 + 0.0j, out)


def _inner_z(hull: Wigley, a):
    """Int_0^T (1 - (zeta/T)^2) exp(-a zeta) dzeta."""
    T = hull.T
    a = np.asarray(a, dtype=float)
    safe = np.where(a < 1e-6, 1.0, a)
    e = np.exp(-safe * T)
    i0 = (1.0 - e) / safe
    i2 = (2.0 / safe**3) * (1.0 - e * (1.0 + safe * T + 0.5 * (safe * T) ** 2))
    out = i0 - i2 / T**2
    limit = 2.0 * T / 3.0 - a * T**2 / 4.0  # Taylor in a -> 0
    return np.where(a < 1e-6, limit, out)


def _i_theta(hull: Wigley, U: float, theta):
    """Wave-amplitude integral I(theta), closed form (one hull side)."""
    k0 = hull.g / U**2
    sec = 1.0 / np.cos(theta)
    pre = -4.0 * hull.B / hull.L**2
    return pre * _inner_x(hull, k0 * sec) * _inner_z(hull, k0 * sec**2)


def rw_theta_integral(hull: Wigley, U: float, n_theta: int = 400) -> float:
    """Michell wave resistance [N], closed-form inner integrals (route A)."""
    thetas, weights = _theta_grid(hull, U, n_theta)
    i_th = _i_theta(hull, U, thetas)
    acc = float(np.sum(weights * np.abs(i_th) ** 2 / np.cos(thetas) ** 3))
    return 8.0 * hull.rho * hull.g**2 / (math.pi * U**2) * acc


def rw_theta_integral_scalar(hull: Wigley, U: float, n_theta: int = 400) -> float:
    """Scalar reference implementation of route A (regression companion).

    Kept to prove that the vectorised path above has no phase/limit mistake;
    it is the original code path (python loop over theta, scalar limits).
    """
    k0 = hull.g / U**2
    cos_t = math.sqrt(min(1.0, k0 * hull.T / 30.0))
    theta_max = math.acos(cos_t)
    gt, wt = leggauss(n_theta)
    acc = 0.0
    for g_i, w_i in zip(gt, wt):
        th = float(0.5 * theta_max * (g_i + 1.0))
        a = k0 / math.cos(th) ** 2
        b = k0 / math.cos(th)
        if a < 1e-9:
            iz = 2.0 * hull.T / 3.0
        else:
            e = math.exp(-a * hull.T)
            iz = (1.0 - e) / a - (2.0 / a**3) * (
                1.0 - e * (1.0 + a * hull.T + 0.5 * (a * hull.T) ** 2)
            ) / hull.T**2
        if abs(b) < 1e-12:
            ix = 0.0 + 0.0j
        else:
            A = hull.L / 2.0
            ix = 2.0j * (math.sin(b * A) / b**2 - A * math.cos(b * A) / b)
        i_th = -4.0 * hull.B / hull.L**2 * ix * iz
        acc += 0.5 * theta_max * w_i * abs(i_th) ** 2 / math.cos(th) ** 3
    return 8.0 * hull.rho * hull.g**2 / (math.pi * U**2) * acc


# ---------------------------------------------------------------------------
# Route B : numerical dy/dx (central difference) + 2-D Gauss-Legendre
# ---------------------------------------------------------------------------
def rw_double_quad(
    hull: Wigley,
    U: float,
    n_x: int = 160,
    n_z: int = 100,
    n_theta: int = 120,
    h_rel: float = 1e-4,
) -> float:
    """Same Michell integral via brute-force surface quadrature (check route).

    dy/dx is a central difference of `half_breadth` (no analytic derivative),
    the surface integral is a 2-D Gauss-Legendre rule and theta is integrated
    with a much coarser rule than route A, so the two routes share only the
    printed Michell prefactor.
    """
    gx, wx = leggauss(n_x)
    gz, wz = leggauss(n_z)
    x = 0.5 * hull.L * gx
    wx = 0.5 * hull.L * wx
    zeta = 0.5 * hull.T * (gz + 1.0)
    wz = 0.5 * hull.T * wz
    X, Z = np.meshgrid(x, zeta, indexing="ij")
    dA = np.outer(wx, wz)

    h = h_rel * hull.L
    dydx = np.array(
        [
            [
                (hull.half_breadth(float(xi) + h, float(zi))
                 - hull.half_breadth(float(xi) - h, float(zi))) / (2.0 * h)
                for zi in Z[i]
            ]
            for i, xi in enumerate(X[:, 0])
        ]
    )

    thetas, weights = _theta_grid(hull, U, n_theta)
    acc = 0.0
    k0 = hull.g / U**2
    for th, w in zip(thetas, weights):
        th = float(th)
        sec = 1.0 / math.cos(th)
        kern = np.exp(-k0 * Z * sec**2) * np.exp(1j * k0 * X * sec)
        i_one_side = complex(np.sum(dA * dydx * kern))
        i_both = 2.0 * i_one_side  # mirror symmetry
        acc += float(w) * abs(i_both) ** 2 / math.cos(th) ** 3
    # |I_both|^2 = 4 |I_one_side|^2 -> prefactor 8/4 = 2
    return 2.0 * hull.rho * hull.g**2 / (math.pi * U**2) * acc


# ---------------------------------------------------------------------------
# Public convenience wrappers
# ---------------------------------------------------------------------------
def michell_rw(hull: Wigley, U: float, n_theta: int = 400) -> float:
    """Michell wave resistance [N] (route A)."""
    return rw_theta_integral(hull, U, n_theta=n_theta)


def michell_rw_converged(
    hull: Wigley, U: float, n_theta: int = 400, ratio: int = 2
) -> Tuple[float, float]:
    """Richardson-extrapolated Rw and the relative refinement increment.

    Returns (Rw_extrapolated, |Rw(n) - Rw(2n)| / Rw(2n)).
    """
    r1 = rw_theta_integral(hull, U, n_theta=n_theta)
    r2 = rw_theta_integral(hull, U, n_theta=ratio * n_theta)
    delta = abs(r1 - r2) / max(abs(r2), 1e-30)
    rw = r2 + (r2 - r1) / (2.0**4 - 1.0)  # conservative p=4 (GL is spectral)
    return rw, delta


def michell_rw_reference(hull: Wigley, Fn: float) -> float:
    """Convenience: Rw at a given Froude number Fn = U / sqrt(g L)."""
    return michell_rw(hull, Fn * math.sqrt(hull.g * hull.L))


def michell_cw(hull: Wigley, U: float, s_ref: float | None = None) -> float:
    """Wave resistance coefficient based on 0.5 rho S U^2 (S = wetted area)."""
    s = wetted_surface(hull) if s_ref is None else s_ref
    return michell_rw(hull, U) / (0.5 * hull.rho * s * U**2)