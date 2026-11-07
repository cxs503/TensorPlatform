"""Closed-form reference formulas used as analytic oracles.

Every function here is an *independent* statement of a physical identity or
closed-form law, transcribed from the primary source named in the docstring.
The audit compares the platform's answer against these instead of against the
platform's own bookkeeping, which is what makes the check a falsification test
rather than a self-consistency echo.
"""
from __future__ import annotations

import math

G = 9.81                 # m/s^2, value used by the platform Fr identity (verified)
G_STANDARD = 9.80665     # m/s^2, ISO 80000-3 standard gravity
KNOT = 1852.0 / 3600.0


def ittc57_cf(re: float) -> float:
    """ITTC-1957 line: Cf = 0.075 / (log10 Re - 2)^2  (ITTC 7.5-02-02-01)."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def froude_number(v_ms: float, length_m: float) -> float:
    return v_ms / math.sqrt(G * length_m)


def reynolds_number(v_ms: float, length_m: float, nu: float) -> float:
    return v_ms * length_m / nu


def effective_power_kw(resistance_kn: float, v_ms: float) -> float:
    """P_E = R_T * V  (R in kN, V in m/s -> kW)."""
    return resistance_kn * v_ms


def total_resistance_kn(ct: float, rho: float, v_ms: float, s_m2: float) -> float:
    """R_T = C_T * 0.5 * rho * V^2 * S  (N -> kN)."""
    return ct * 0.5 * rho * v_ms ** 2 * s_m2 / 1000.0


def kb_wall_sided(t: float, cb: float, cw: float) -> float:
    """KB = T (5/6 - Cb/(3 Cw)) — wall-sided / box-ship hydrostatics."""
    return t * (5.0 / 6.0 - cb / (3.0 * cw))


def bm_wall_sided(cw: float, b: float, cb: float, t: float) -> float:
    """BM = Cw B^2 / (12 Cb T) — waterplane second moment over displaced volume."""
    return cw * b * b / (12.0 * cb * t)


def gm_identity(kb: float, bm: float, kg: float) -> float:
    """GM = KB + BM - KG (metacentric stability identity)."""
    return kb + bm - kg


def advance_ratio(v_ms: float, wake_fraction: float, n_rps: float, d_m: float) -> float:
    """J = V (1 - w) / (n D)."""
    return v_ms * (1.0 - wake_fraction) / (n_rps * d_m)


def eta_open_water(j: float, kt: float, kq: float) -> float:
    """eta_0 = J KT / (2 pi KQ) — open-water efficiency definition."""
    return j * kt / (2.0 * math.pi * kq)


def eta_ideal_from_ct(ct_loading: float) -> float:
    """Actuator-disk (Lanchester-Glauert) ideal efficiency eta_i = 2/(1+sqrt(1+C_T))."""
    return 2.0 / (1.0 + math.sqrt(1.0 + ct_loading))


def deep_water_wavelength(tp: float, g: float = G_STANDARD) -> float:
    """Dispersion: lambda = g T^2 / (2 pi) for deep-water waves.

    Uses standard gravity: the audited endpoint derives lambda_p with
    g = 9.80665, one order of magnitude tighter than the 4-6 digit print
    precision, so the choice is observable.
    """
    return g * tp * tp / (2.0 * math.pi)


def rel_error(observed: float, expected: float) -> float:
    """Symmetric-free relative error; tolerant to a zero expected value."""
    denom = max(abs(expected), 1e-12)
    return abs(observed - expected) / denom