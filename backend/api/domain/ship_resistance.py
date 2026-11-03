"""
Canonical pure-function resistance kernel shared by the public API and benchmark validator.

Model ID: suboff_axisymmetric_empirical_v1

This is deliberately a small empirical model, NOT a general merchant-ship
resistance method. It combines an optional supplied wetted surface (otherwise
an axisymmetric-body estimate), ITTC-1957 friction, an empirical SUBOFF-like
residual coefficient, and an optional viscous form factor (1+k).

A geometry value supplied by a caller is not automatically verified here.
The returned validity status is an applicability warning, not proof of
experimental validation.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

G = 9.81
DEFAULT_WATER_DENSITY = 998.0
# M8（#583 步骤 3）：验收判据值随域载荷发布（经 seam 下发，判决点零硬编码）。
PASS_TOLERANCE_PCT = 15.0
DEFAULT_KINEMATIC_VISCOSITY = 1.004e-6
MODEL_ID = "suboff_axisymmetric_empirical_v1"
MODEL_NAME = "ITTC-1957 friction + empirical residual (axisymmetric-body fit)"


def reynolds_number(speed: float, length: float, nu: float = DEFAULT_KINEMATIC_VISCOSITY) -> float:
    """Re = V * L / nu."""
    if speed <= 0 or length <= 0 or nu <= 0:
        raise ValueError("speed, length and kinematic viscosity must be positive")
    return speed * length / nu


def froude_number(speed: float, length: float, g: float = G) -> float:
    """Fn = V / sqrt(g * L)."""
    if speed <= 0 or length <= 0 or g <= 0:
        raise ValueError("speed, length and gravity must be positive")
    return speed / math.sqrt(g * length)


def ittc57_cf(re: float) -> float:
    """ITTC-1957 correlation line Cf = 0.075 / (log10(Re) - 2)^2."""
    if re <= 0:
        raise ValueError("Reynolds number must be positive")
    denom = math.log10(re) - 2.0
    if denom <= 0:
        raise ValueError("Reynolds number too small for ITTC-1957 (log10(Re) <= 2)")
    return 0.075 / denom ** 2


def schoenherr_cf(re: float, tol: float = 1e-14, max_iter: int = 100) -> float:
    """Schoenherr (1932) average friction line, solved by deterministic bisection."""
    if re <= 0:
        raise ValueError("Reynolds number must be positive")

    def resid(cf: float) -> float:
        return 0.242 / math.sqrt(cf) - math.log10(re * cf)

    lo, hi = 1e-6, 0.02
    f_lo = resid(lo)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = resid(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def wetted_surface_axisym(beam: float, length: float, cb: float) -> float:
    """Legacy axisymmetric-body wetted-surface estimate; not suitable for all hulls."""
    if beam <= 0 or length <= 0 or not (0.0 < cb <= 1.0):
        raise ValueError("beam and length must be positive and Cb must be in (0, 1]")
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def residual_coefficient(fr: float, cb: float, fn_floor: float = 0.05) -> float:
    """Empirical residual-resistance coefficient; SUBOFF-like fit without published provenance."""
    return 0.002 * (1.0 + 2.0 * cb) * fr ** 2.5 if fr > fn_floor else 0.0


def resistance_breakdown(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed: float,
    rho: float = DEFAULT_WATER_DENSITY,
    nu: float = DEFAULT_KINEMATIC_VISCOSITY,
    wetted_surface_m2: Optional[float] = None,
    form_factor: float = 1.0,
) -> Dict[str, object]:
    """Compute the canonical resistance breakdown used by API and benchmark paths.

    wetted_surface_m2 should only be supplied when its geometry/provenance has
    been checked by the caller. form_factor is the conventional (1+k) factor.
    """
    if min(length, beam, draft, speed, rho, nu) <= 0:
        raise ValueError("speed, dimensions, density and viscosity must be positive")
    if not (0.3 < cb < 1.0):
        raise ValueError(f"Cb={cb} out of physical range (0.3, 1.0)")
    if not (1.0 <= form_factor <= 1.5):
        raise ValueError("form_factor must be in [1.0, 1.5]")
    if wetted_surface_m2 is not None and wetted_surface_m2 <= 0:
        raise ValueError("wetted_surface_m2 must be positive when supplied")

    if wetted_surface_m2 is None:
        s_wet = wetted_surface_axisym(beam, length, cb)
        surface_source = "empirical_axisymmetric_estimate"
    else:
        s_wet = float(wetted_surface_m2)
        surface_source = "caller_supplied_geometry_value"

    re = reynolds_number(speed, length, nu)
    fn = froude_number(speed, length)
    cf = ittc57_cf(re)
    cr = residual_coefficient(fn, cb)
    q = 0.5 * rho * s_wet * speed ** 2

    rf = q * cf
    rform = (form_factor - 1.0) * rf
    rr = q * cr
    rt = rf + rform + rr
    ct = rt / q

    validity_notes: List[str] = [
        "Empirical residual fit is SUBOFF-like and has no published provenance in this repository.",
        "A caller-supplied wetted surface is not independently validated by this function.",
        "Passing software checks does not establish physical validation.",
    ]
    if cb >= 0.6:
        validity_notes.append(f"Cb={cb:.4f} is outside the documented fine/axisymmetric-body fit range (Cb < 0.6).")
    if fn > 0.4:
        validity_notes.append(f"Fn={fn:.4f} exceeds the documented empirical-fit range (Fn <= 0.4).")

    return {
        "model_id": MODEL_ID,
        "method": MODEL_NAME,
        "validity_status": "unvalidated_empirical_estimate",
        "validity_notes": validity_notes,
        "surface_source": surface_source,
        "wetted_surface_m2": s_wet,
        "reynolds": re,
        "reynolds_number": re,
        "froude": fn,
        "froude_number": fn,
        "cf": cf,
        "cr": cr,
        "ct": ct,
        "form_factor": form_factor,
        "rf_n": rf,
        "frictional_resistance_n": rf,
        "form_resistance_n": rform,
        "rr_n": rr,
        "residual_resistance_n": rr,
        "rt_n": rt,
        "total_resistance_n": rt,
    }


def ct_from_rt(rt: float, rho: float, s_wet: float, speed: float) -> float:
    """Ct = Rt / (0.5 * rho * S * V^2) — the coefficient identity used for audits."""
    q = 0.5 * rho * s_wet * speed ** 2
    if q <= 0:
        raise ValueError("Dynamic pressure must be positive")
    return rt / q


def displacement_from_cb(length: float, beam: float, draft: float, cb: float,
                         rho: float = DEFAULT_WATER_DENSITY) -> float:
    """Displacement in tonnes from the block coefficient (nabla = Cb * L * B * T)."""
    if min(length, beam, draft, rho) <= 0:
        raise ValueError("dimensions and density must be positive")
    if not (0.0 < cb <= 1.0):
        raise ValueError("cb must be in (0, 1]")
    return cb * length * beam * draft * rho / 1000.0
