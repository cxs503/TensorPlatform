"""Analytic reference values used by the ship-platform verification audit.

Every number produced here is either a closed form (exact) or a converged
quadrature with a documented discretisation error, so the audit compares the
platform against references that do not depend on the platform itself.

References
----------
* Wigley parabolic hull: y = B/2 (1-(2x/L)^2)(1-(z/T)^2)  ->  Cb = 4/9,
  displacement = 4 L B T / 9 (exact, Wigley 1942; standard thin-ship test hull).
* ITTC-1957 model-ship correlation line: Cf = 0.075/(log10 Re - 2)^2 (exact).
* Rectangular pontoon (wall-sided) hydrostatics: KB = T/2,
  BM = B^2/(12 T), GZ(phi) = sin(phi) (GM + BM/2 tan^2 phi)  (exact while the
  deck edge stays immersed / the bilge stays submerged).
* Wageningen B-series kinematics: J = Va/(nD), KT = T/(rho n^2 D^4),
  KQ = Q/(rho n^2 D^5), eta_o = J KT/(2 pi KQ), P_D = 2 pi n Q (exact).
"""

from __future__ import annotations

import math

G = 9.81
KNOT = 0.5144444444444445  # m/s per knot


# ---------------------------------------------------------------------------
# Wigley parabolic hull
# ---------------------------------------------------------------------------
def wigley_cb() -> float:
    """Exact block coefficient of the Wigley parabolic hull."""
    return 4.0 / 9.0


def wigley_volume(L: float, B: float, T: float) -> float:
    """Exact displacement volume 4 L B T / 9 [m^3]."""
    return 4.0 * L * B * T / 9.0


def wigley_wetted_surface(L: float, B: float, T: float) -> float:
    """Wetted surface area by spectral quadrature (import from michell_wigley)."""
    from michell_wigley import Wigley, wetted_surface

    return wetted_surface(Wigley(L=L, B=B, T=T))


# ---------------------------------------------------------------------------
# ITTC-57 friction line
# ---------------------------------------------------------------------------
def ittc57_cf(re: float) -> float:
    """ITTC-1957 friction coefficient."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def schoenherr_cf(re: float, tol: float = 1e-12) -> float:
    """Schoenherr friction coefficient (implicit, solved by bisection).

    0.242 / sqrt(Cf) = log10(Re * Cf)
    """
    lo, hi = 1e-5, 0.02
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f = 0.242 / math.sqrt(mid) - math.log10(re * mid)
        # f decreases with Cf at high Re -> bracket by sign
        if (0.242 / math.sqrt(lo) - math.log10(re * lo)) * f <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Rectangular pontoon (wall-sided) hydrostatics and stability
# ---------------------------------------------------------------------------
def barge_hydrostatics(B: float, T: float, KG: float) -> dict:
    """Exact pontoon hydrostatics (Cb = Cm = Cwp = 1)."""
    KB = 0.5 * T
    BM = B**2 / (12.0 * T)
    KM = KB + BM
    GM = KM - KG
    return {"KB": KB, "BM": BM, "KM": KM, "GM": GM}


def barge_gz(B: float, T: float, KG: float, phi_deg: float) -> float:
    """Exact wall-sided righting arm of a rectangular pontoon [m]."""
    h = barge_hydrostatics(B, T, KG)
    phi = math.radians(phi_deg)
    return math.sin(phi) * (h["GM"] + 0.5 * h["BM"] * math.tan(phi) ** 2)


def barge_vanishing_angle(B: float, T: float, KG: float) -> float:
    """Heel angle [deg] where GZ -> 0 (tan^2 phi = -2 GM / BM)."""
    h = barge_hydrostatics(B, T, KG)
    if h["GM"] >= 0:
        return 90.0
    return math.degrees(math.atan(math.sqrt(-2.0 * h["GM"] / h["BM"])))


def area_under_curve(angles_deg, values) -> float:
    """Trapezoidal integral of values(angles) in m*rad."""
    acc = 0.0
    for i in range(1, len(angles_deg)):
        dphi = math.radians(angles_deg[i] - angles_deg[i - 1])
        acc += 0.5 * (values[i] + values[i - 1]) * dphi
    return acc


# ---------------------------------------------------------------------------
# Propeller algebra
# ---------------------------------------------------------------------------
def advance_coefficient(va: float, n: float, D: float) -> float:
    return va / (n * D)


def kt_from_thrust(thrust: float, rho: float, n: float, D: float) -> float:
    return thrust / (rho * n**2 * D**4)


def kq_from_torque(torque: float, rho: float, n: float, D: float) -> float:
    return torque / (rho * n**2 * D**5)


def eta_open_water(J: float, KT: float, KQ: float) -> float:
    if KQ <= 0:
        return 0.0
    return J * KT / (2.0 * math.pi * KQ)


def delivered_power(torque: float, n: float) -> float:
    """2 pi n Q [W]."""
    return 2.0 * math.pi * n * torque


# ---------------------------------------------------------------------------
# Dispersion / hydrostatic helpers
# ---------------------------------------------------------------------------
def fn_of_speed(U: float, L: float) -> float:
    return U / math.sqrt(G * L)


def re_of_speed(U: float, L: float, nu: float = 1.19e-6) -> float:
    return U * L / nu


def rel_error(x: float, ref: float) -> float:
    if ref == 0:
        return float("nan")
    return (x - ref) / ref