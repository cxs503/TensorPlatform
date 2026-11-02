"""
Dependency-free resistance core for the ship-performance platform.

The FastAPI endpoint in ``api/calculations.py`` is a thin wrapper around
:func:`solve_resistance`.  Keeping the physics here means the algorithm can be
audited and unit-tested without importing the web application (and therefore
without a database driver), which is what the m1609 verification mission needs.

Model
-----
    Re  = V L / nu
    Cf  = 0.075 / (log10(Re) - 2)^2                 ITTC-1957 friction line
    Cr  = 0.002 (1 + 2 Cb) Fn^2.5,  Fn > 0.05       semi-empirical residual fit
    Ct  = Cf + Cr
    Rt  = 0.5 rho S Ct V^2

Validity envelope: the residual fit was calibrated on DARPA SUBOFF
(axisymmetric slender body) data at Fr <= 0.62.  Full-form merchant hulls are
outside the envelope; that fact is reported by :func:`validity_verdict` instead
of being silently extrapolated.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

try:  # package import (api.domain.resistance_core)
    from .hull_geometry import (
        PUBLISHED_GEOMETRY,
        ittc57_cf,
        plausibility_band,
        resolve_geometry,
        validate_hull_geometry,
    )
except ImportError:  # standalone / path-loaded import
    from hull_geometry import (  # type: ignore
        PUBLISHED_GEOMETRY,
        ittc57_cf,
        plausibility_band,
        resolve_geometry,
        validate_hull_geometry,
    )

VALIDITY_FROUDE_MAX = 0.62
VALIDITY_FORMS = ("axisymmetric",)


class GeometryError(ValueError):
    """Raised when the hull geometry fails verification."""

    def __init__(self, hull: str, checks):
        self.hull = hull
        self.checks = checks
        super().__init__(f"geometry verification failed for {hull}: {checks}")


def validity_verdict(hull: str, froude_number: float) -> Dict[str, Any]:
    """Is this (hull, Fr) point inside the calibrated envelope?"""
    form = PUBLISHED_GEOMETRY[hull]["form"]
    inside = form in VALIDITY_FORMS and froude_number <= VALIDITY_FROUDE_MAX
    reasons = []
    if form not in VALIDITY_FORMS:
        reasons.append(
            f"hull form '{form}' is outside the envelope (fit calibrated on axisymmetric bodies)"
        )
    if froude_number > VALIDITY_FROUDE_MAX:
        reasons.append(
            f"Fr={froude_number:.3f} > {VALIDITY_FROUDE_MAX}: the Fn^2.5 residual "
            "fit over-predicts beyond the calibrated range"
        )
    return {
        "in_envelope": inside,
        "forms": list(VALIDITY_FORMS),
        "froude_max": VALIDITY_FROUDE_MAX,
        "reasons": reasons,
    }


def solve_resistance(
    hull: str,
    speed_ms: float,
    overrides: Optional[Dict[str, Any]] = None,
    rho: float = 998.0,
    nu: float = 1.004e-6,
    require_geometry_gate: bool = True,
) -> Dict[str, Any]:
    """Solve resistance for one (hull, speed) point and return a plain dict.

    ``overrides`` may supply ``length``/``beam``/``draft``/``cb``/
    ``wetted_surface_m2``; anything omitted is taken from the verified published
    table.  Raises :class:`GeometryError` when the gate fails, so an unverified
    hull can never produce a force.
    """
    if hull not in PUBLISHED_GEOMETRY:
        raise KeyError(f"No verified geometry for hull '{hull}'. Available: {sorted(PUBLISHED_GEOMETRY)}")
    if speed_ms <= 0:
        raise ValueError(f"speed_ms must be positive, got {speed_ms}")
    if rho <= 0 or nu <= 0:
        raise ValueError("rho and nu must be positive")

    gate = validate_hull_geometry(hull)
    if require_geometry_gate and not gate["ok"]:
        raise GeometryError(hull, [c for c in gate["checks"] if not c.get("ok")])

    geom = resolve_geometry(hull, overrides)
    L = geom["length"]
    Cb = geom["cb"]
    S = geom["wetted_surface_m2"]

    if not (0.3 < Cb < 1.0):
        raise ValueError(f"Cb={Cb} out of physical range [0.3, 1.0]")

    re = speed_ms * L / nu
    cf = ittc57_cf(re)
    fn = speed_ms / math.sqrt(9.81 * L)
    cr = 0.002 * (1.0 + 2.0 * Cb) * fn ** 2.5 if fn > 0.05 else 0.0
    ct = cf + cr

    q = 0.5 * rho * S * speed_ms ** 2
    verdict = validity_verdict(hull, fn)
    return {
        "ship_type": hull,
        "speed_ms": speed_ms,
        "total_resistance_n": round(q * ct, 4),
        "frictional_resistance_n": round(q * cf, 4),
        "residual_resistance_n": round(q * cr, 4),
        "ct": ct,
        "cf": cf,
        "cr": cr,
        "wetted_surface_m2": round(S, 4),
        "reynolds_number": re,
        "froude_number": fn,
        "method": "ITTC-57 friction + semi-empirical Froude fit (submarine-calibrated)",
        "geometry": {
            "gate_ok": gate["ok"],
            "form": geom["form"],
            "cb": round(Cb, 4),
            "cb_source": geom["cb_source"],
            "s_source": geom["s_source"],
            "checks": gate["checks"],
            "derived": gate["derived"],
        },
        "validity": verdict,
        "self_plausibility": plausibility_band(hull, speed_ms, ct, nu),
    }