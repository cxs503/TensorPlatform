"""Ship resistance core — dependency-free physics (no FastAPI, no database).

Kept separate from ``api/calculations.py`` so the physics can be unit-tested and
audited in environments where the API dependency chain (psycopg2, cryptography)
is unavailable, and so the benchmark validator can reuse the same coefficient
extraction instead of re-implementing it.

Conventions: SI units, fresh water at 20 C by default (rho = 998 kg/m^3,
nu = 1.004e-6 m^2/s).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

G = 9.81
RHO_FRESH = 998.0
NU_FRESH = 1.004e-6


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship correlation line (flat-plate friction coefficient)."""
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def compute_resistance(
    *,
    speed_ms: float,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    water_density: float = RHO_FRESH,
    kinematic_viscosity: float = NU_FRESH,
) -> Dict[str, float]:
    """ITTC-1957 friction + empirical residual resistance for a slender hull.

    ``ct`` is the quantity downstream validation compares, and it is returned
    together with the wetted surface used to obtain it, so that
    Rt = 0.5 rho S V^2 ct holds exactly by construction.

    Raises:
        ValueError: block coefficient outside [0.3, 1.0] or non-positive inputs.
    """
    if not (0.3 < cb < 1.0):
        raise ValueError(f"Cb={cb} out of physical range [0.3, 1.0]")
    if length <= 0 or beam <= 0 or draft <= 0 or speed_ms <= 0:
        raise ValueError("Dimensions and speed must be positive")

    # Wetted surface area (approximation for a body of revolution)
    s_wet = math.pi * beam * length * (0.5 + 0.25 * cb)

    re = speed_ms * length / kinematic_viscosity
    cf = ittc57_cf(re)
    rf = 0.5 * water_density * s_wet * cf * speed_ms ** 2

    froude = speed_ms / math.sqrt(G * length)
    cr = 0.002 * (1 + 2.0 * cb) * froude ** 2.5 if froude > 0.05 else 0.0
    rr = 0.5 * water_density * s_wet * cr * speed_ms ** 2

    rt = rf + rr
    ct = rt / (0.5 * water_density * s_wet * speed_ms ** 2)

    return {
        "wetted_surface_m2": s_wet,
        "reynolds_number": re,
        "froude_number": froude,
        "cf": cf,
        "cr": cr,
        "ct": ct,
        "frictional_resistance_n": rf,
        "residual_resistance_n": rr,
        "total_resistance_n": rt,
    }


def geometry_from_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Hull geometry for one benchmark case, taken from the case record itself.

    Reading the geometry from the case (rather than from a per-ship-type preset)
    keeps one hull's dimensions from being combined with another case's
    reference coefficient. Bodies of revolution legitimately have no beam/draft
    of their own, so the diameter stands in for both.
    """
    geom = case.get("geometry", {})

    def pick(*keys) -> Optional[float]:
        for key in keys:
            if geom.get(key) is not None:
                return float(geom[key])
        return None

    length = pick("length_m", "length")
    beam = pick("beam_m", "diameter_m", "beam")
    draft = pick("draft_m", "diameter_m", "draft")

    cb = geom.get("cb")
    if cb is None and length and beam and draft:
        # Derive Cb from the displaced volume when the record does not state it.
        mass = geom.get("displacement_kg")
        if mass is None and geom.get("displacement_t") is not None:
            mass = geom["displacement_t"] * 1000.0
        if mass:
            rho = 1025.0 if length > 50 else RHO_FRESH
            cb = (mass / rho) / (length * beam * draft)

    disp = geom.get("displacement_kg")
    if disp is None and geom.get("displacement_t") is not None:
        disp = geom["displacement_t"] * 1000.0

    return {
        "length": length or 0.0,
        "beam": beam or 0.0,
        "draft": draft or 0.0,
        "cb": float(cb) if cb is not None else 0.55,
        "displacement": float(disp) if disp is not None else 0.0,
        "wetted_surface_m2": geom.get("wetted_surface_m2"),
    }


def extract_ct(
    calc_data: Dict[str, Any],
    geometry: Dict[str, Any],
    speed_ms: float,
    rho: float = RHO_FRESH,
) -> Optional[float]:
    """Dimensionless total-resistance coefficient from a resistance response.

    Prefers the service's own ``ct``; otherwise reconstructs it from the reported
    force using the wetted surface of the response, the case record, or the
    geometry formula, in that order. Without this fallback a validator comparing
    ``ct`` against a service that only publishes a force would score 0.0 against
    every reference and report a universal 100 % error.
    """
    ct = calc_data.get("ct")
    if ct:
        return float(ct)

    rt = calc_data.get("rt")
    if rt is None:
        rt = calc_data.get("total_resistance_n")
    if not rt or not speed_ms:
        return None

    s_wet = calc_data.get("wetted_surface") or calc_data.get("wetted_surface_m2")
    if not s_wet:
        s_wet = geometry.get("wetted_surface_m2")
    if not s_wet:
        length = geometry.get("length") or 0.0
        beam = geometry.get("beam") or 0.0
        cb = geometry.get("cb", 0.55)
        if not (length and beam):
            return None
        s_wet = math.pi * beam * length * (0.5 + 0.25 * cb)

    return float(rt) / (0.5 * rho * float(s_wet) * speed_ms ** 2)