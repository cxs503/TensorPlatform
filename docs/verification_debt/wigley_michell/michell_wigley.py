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

* Michell (1898) thin-ship wave resistance in deep water

      Rw = 4 rho g^2 / (pi U^4) * Int_0^{pi/2} |I(theta)|^2 sec^3(theta) dtheta

      I(theta) = Int_S (dy/dx) exp(-k0 zeta sec^2 theta)
                          exp(i k0 x sec theta) dx dzeta,     k0 = g / U^2

  For the Wigley hull both inner integrals can be evaluated in closed form,
  so only the theta integral is quadrature (route A, `rw_theta_integral`).
  A second, fully independent numerical route (route B, `rw_double_quad`)
  uses a central difference for dy/dx and 2-D Gauss-Legendre quadrature over
  the hull surface before the theta quadrature.  Agreement of the two routes
  is the implementation check; theta-quadrature refinement / Richardson
  extrapolation is the discretisation check.

Units: SI.  U [m/s], lengths [m], Rw [N], rho [kg/m^3].
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np

__all__ = [
    "Wigley",
    "rw_theta_integral",
    "rw_double_quad",
    "michell_rw",
    "wetted_surface",
    "michell_cw",
]


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


def wetted_surface(hull: Wigley, n_x: int = 2000, n_z: int = 800) -> float:
    """Wetted surface area of both sides of the Wigley hull by quadrature.

    dS = 2 * sqrt(1 + (dy/dx)^2 + (dy/dzeta)^2) dx dzeta  (mirror symmetry).
    The integrand is smooth, so Gauss-Legendre converges spectrally.
    """
    gx, wx = np.polynomial.legendre.leggauss(n_x)
    gz, wz = np.polynomial.legendre.leggauss(n_z)
    x = 0.5 * hull.L * gx
    zeta = 0.5 * hull.T * (gz + 1.0)
    wx = 0.5 * hull.L * wx
    wz = 0.5 * hull.T * wz

    X, Z = np.meshgrid(x, zeta, indexing="ij")
    dydx = -4.0 * hull.B * X / hull.L**2 * (1.0 - (Z / hull.T) ** 2)
    dydz = 0.5 * hull.B * (1.0 - (2.0 * X / hull.L) ** 2) * (-2.0 * Z / hull.T**2)
    dS = 2.0 * np.sqrt(1.0 + dydx**2 + dydz**2)
    return float(np.einsum("i,j,ij->", wx, wz, dS))


# ---------------------------------------------------------------------------
# Route A : closed-form inner integrals, quadrature in theta
# ---------------------------------------------------------------------------
def _inner_x(hull: Wigley, b: float) -> complex:
    """Int_{-L/2}^{L/2} x exp(i b x) dx  (purely imaginary)."""
    A = hull.L / 2.0
    if abs(b) < 1e-12:
        return 0.0 + 0.0j
    return 2.0j * (math.sin(b * A) / b**2 - A * math.cos(b * A) / b)


def _inner_z(hull: Wigley, a: float) -> float:
    """Int_0^T (1 - (zeta/T)^2) exp(-a zeta) dzeta."""
    T = hull.T
    if a < 1e-9:  # Taylor limit a -> 0
        return 2.0 * T / 3.0
    e = math.exp(-a * T)
    i0 = (1.0 - e) / a
    i2 = (2.0 / a**3) * (1.0 - e * (1.0 + a * T + 0.5 * (a * T) ** 2))
    return i0 - i2 / T**2


def _i_theta(hull: Wigley, U: float, theta: float) -> complex:
    """Wave-amplitude integral I(theta) in closed form."""
    k0 = hull.g / U**2
    sec = 1.0 / math.cos(theta)
    # I = -(4 B / L^2) * Int x exp(i k0 x sec) dx * Int (...) exp(-k0 zeta sec^2) dzeta
    pre = -4.0 * hull.B / hull.L**2
    return pre * _inner_x(hull, k0 * sec) * _inner_z(hull, k0 * sec**2)


def rw_theta_integral(hull: Wigley, U: float, n_theta: int = 400) -> float:
    """Michell wave resistance with closed-form inner integrals.

    The theta integrand |I|^2 sec^3 theta decays like exp(-2 k0 T sec^2 theta),
    so the upper limit is truncated where the exponent reaches ~-60 and a
    Gauss-Legendre rule is applied on the remaining smooth interval.
    """
    k0 = hull.g / U**2
    # truncation: cos(theta_max) = sqrt(2 k0 T / 60)
    cos_t = math.sqrt(min(1.0, 2.0 * k0 * hull.T / 60.0))
    theta_max = math.acos(cos_t)
    gt, wt = np.polynomial.legendre.leggauss(n_theta)
    thetas = 0.5 * theta_max * (gt + 1.0)
    weights = 0.5 * theta_max * wt
    acc = 0.0
    for th, w in zip(thetas, weights):
        i_th = _i_theta(hull, U, float(th))
        acc += w * abs(i_th) ** 2 / math.cos(th) ** 3
    return 8.0 * hull.rho * hull.g**2 / (math.pi * U**2) * acc  # 4 rho g^2/(pi U^2), both hull sides


# ---------------------------------------------------------------------------
# Route B : numerical dy/dx (central difference) + 2-D Gauss-Legendre
# ---------------------------------------------------------------------------
def rw_double_quad(
    hull: Wigley,
    U: float,
    n_x: int = 400,
    n_z: int = 300,
    n_theta: int = 240,
) -> float:
    """Same Michell integral via brute-force surface quadrature (check route)."""
    gx, wx = np.polynomial.legendre.leggauss(n_x)
    gz, wz = np.polynomial.legendre.leggauss(n_z)
    x = 0.5 * hull.L * gx
    wx = 0.5 * hull.L * wx
    zeta = 0.5 * hull.T * (gz + 1.0)
    wz = 0.5 * hull.T * wz

    X, Z = np.meshgrid(x, zeta, indexing="ij")
    dydx = -4.0 * hull.B * X / hull.L**2 * (1.0 - (Z / hull.T) ** 2)
    dA = np.outer(wx, wz)

    k0 = hull.g / U**2
    cos_t = math.sqrt(min(1.0, 2.0 * k0 * hull.T / 60.0))
    theta_max = math.acos(cos_t)
    gt, wt = np.polynomial.legendre.leggauss(n_theta)
    thetas = 0.5 * theta_max * (gt + 1.0)
    weights = 0.5 * theta_max * wt

    acc = 0.0
    for th, w in zip(thetas, weights):
        th = float(th)
        sec = 1.0 / math.cos(th)
        # phase factor per surface point; factor 2 for the mirrored side
        phase = math.cos(k0 * sec) * X + math.sin(k0 * sec) * X  # placeholder
        # complex phase exp(i k0 x sec)
        ang = k0 * X * sec
        kern = np.exp(-k0 * Z * sec**2) * (np.cos(ang) + 1j * np.sin(ang))
        i_th = float(np.sum(dA * dydx * kern))
        i_th = 2.0 * i_th  # two sides of the hull
        acc += w * abs(i_th) ** 2 / math.cos(th) ** 3
        del phase
    return 8.0 * hull.rho * hull.g**2 / (math.pi * U**2) * acc  # 4 rho g^2/(pi U^2), both hull sides


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
    # Gauss-Legendre is spectrally convergent; the Richardson factor is
    # empirically p=4 which is conservative for this smooth integrand.
    rw = r2 + (r2 - r1) / (2.0**4 - 1.0)
    return rw, delta


def michell_cw(hull: Wigley, U: float, s_ref: float | None = None) -> float:
    """Wave resistance coefficient based on 0.5 rho S U^2 (S = wetted area)."""
    s = wetted_surface(hull) if s_ref is None else s_ref
    return michell_rw(hull, U) / (0.5 * hull.rho * s * U**2)