"""
Resistance physics core (single source of truth).

Pure Python, no HTTP/no DB: callable from API endpoints, benchmark validation and
offline audit scripts alike. Implements the platform's simplified
Holtrop-Mennen residual-resistance model with the ITTC-1957 friction line.

Validity envelope (documented honestly, see docs/verification/):
  * wetted-surface estimate ``S = pi * B * L * (0.5 + 0.25 * Cb)`` is an
    AXISYMMETRIC-body approximation (calibrated on DARPA SUBOFF); it over-predicts
    S for displacement hulls (KVLCC2: ~+52% vs the published 27 000 m^2).
  * residual resistance ``Cr = 0.002 * (1 + 2 Cb) * Fn**2.5`` is an empirical curve
    fit for fine submarine-like bodies (Cb < 0.6). For merchant hulls (Cb > 0.65)
    and for Fn > 0.4 it is outside its calibration range.
"""
from __future__ import annotations

import math
from typing import Dict

G = 9.81
DEFAULT_RHO = 998.0          # kg/m^3, fresh water
DEFAULT_NU = 1.004e-6        # m^2/s, fresh water at ~20 C


def froude_number(speed_ms: float, length: float) -> float:
    """Froude number based on length on the waterline."""
    if length <= 0:
        raise ValueError("length must be positive")
    return speed_ms / math.sqrt(G * length)


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship correlation line: Cf = 0.075 / (log10(Re) - 2)^2."""
    if reynolds <= 0:
        raise ValueError("reynolds must be positive")
    denom = math.log10(reynolds) - 2.0
    if denom <= 0:
        return 0.0
    return 0.075 / denom ** 2


def axisym_wetted_surface(length: float, beam: float, cb: float) -> float:
    """Axisymmetric-body wetted-surface estimate used by the platform (SUBOFF-calibrated)."""
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def resistance_core(
    speed_ms: float,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    rho: float = DEFAULT_RHO,
    nu: float = DEFAULT_NU,
    wetted_surface: float | None = None,
) -> Dict[str, float]:
    """
    Total resistance of a hull at a given speed.

    Parameters mirror POST /api/v1/calculations/resistance. ``wetted_surface``
    overrides the built-in axisymmetric estimate when a measured/supplied value
    is available (used by the benchmark validator for SUBOFF).

    Returns dict with: wetted_surface_m2, reynolds_number, froude_number, cf,
    cr, frictional_resistance_n, residual_resistance_n, total_resistance_n, ct.
    """
    if speed_ms <= 0 or length <= 0 or beam <= 0 or draft <= 0:
        raise ValueError("speed and main dimensions must be positive")
    if not (0.3 < cb < 1.0):
        raise ValueError(f"Cb={cb} out of physical range (0.3, 1.0)")

    S = wetted_surface if (wetted_surface and wetted_surface > 0) else axisym_wetted_surface(length, beam, cb)
    Re = speed_ms * length / nu
    Cf = ittc57_cf(Re)
    Fn = froude_number(speed_ms, length)

    q = 0.5 * rho * S * speed_ms ** 2       # dynamic-pressure-times-area [N]
    Rf = q * Cf
    Cr = 0.002 * (1.0 + 2.0 * cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = q * Cr
    Rt = Rf + Rr

    return {
        "wetted_surface_m2": S,
        "reynolds_number": Re,
        "froude_number": Fn,
        "cf": Cf,
        "cr": Cr,
        "frictional_resistance_n": Rf,
        "residual_resistance_n": Rr,
        "total_resistance_n": Rt,
        "ct": Rt / q if q > 0 else 0.0,
    }


def ct_from_rt(rt: float, rho: float, wetted_surface: float, speed_ms: float) -> float:
    """Back out the total resistance coefficient from a dimensional resistance."""
    q = 0.5 * rho * wetted_surface * speed_ms ** 2
    if q <= 0:
        return 0.0
    return rt / q