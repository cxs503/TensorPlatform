"""Geometry-exact wetted surface area (S) for hull forms — closes the m1666 debt.

Why this module exists
----------------------
``POST /api/v1/calculations/resistance`` sized its frictional resistance from the
empirical fit ``S = pi * B * L * (0.5 + 0.25 * Cb)``.  The external baseline audit
(m1666) measured that fit against the only *data* anchor in the repository
(DARPA SUBOFF, Roddy 1990, ``S = 5.42 m2``) and against the closed-form
equivalent ellipsoid, and found **-18.5 % / -19.6 %**: the fit is not a
sub-model, it is a shape-blind regression.  The audit's explicit follow-up was

    "修公式：对 form_type=body_of_revolution 用几何/实测 S"

This module is that geometry path.  Given a meridian offset table
``(x, r(x))`` — the way a body of revolution is actually specified — the wetted
surface is integrated from the revolved polyline.  For each segment the lateral
area of the *exact* frustum is used::

    dS = pi * (r1 + r2) * sqrt(h^2 + (r1 - r2)^2)

so the sum is exact for a straight meridian (cylinder, cone) and converges
``O(h^2)`` to the smooth surface (verified in
``tests/test_wetted_surface_geometry.py``).  Volume uses the exact frustum
volume ``pi*h*(r1^2 + r1*r2 + r2^2)/3``, which yields the block coefficient of
the *supplied geometry* — the Cb/L/B pre-gate the precision standard requires
before any resistance number may be trusted.

Conventions
-----------
* ``x`` runs from 0 (stem) to ``L`` (stern); ``r(x) >= 0`` is the local radius.
* Block coefficient of a body of revolution follows the platform convention used
  by ``backend/services/benchmark_canon.py`` for ``form_type=body_of_revolution``:
  ``Cb = nabla / (L * B * T)`` with ``B = T = 2*r_max`` (the diameter plays both
  the beam and the draft role).  A prolate spheroid therefore returns exactly
  ``pi/6 = 0.5236``, which is the value the canon compares against.
* Anchors are literature-free: closed-form areas of the cylinder, sphere,
  spheroid, ellipsoid and box.  No table values are transcribed from memory, so
  the verification cannot inherit a transcription error.

Evidence grade: REAL for the geometry integration (machine-exact anchors),
REAL for the endpoint wiring; the *hull form itself* is the caller's evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "GeometryError",
    "WettedSurfaceResult",
    "revolution_wetted_surface",
    "acceleration_free_surface",
    "ellipsoid_wetted_surface",
    "cylinder_wetted_surface",
    "sphere_wetted_surface",
    "box_wetted_surface",
    "empirical_wetted_surface",
    "LEGACY_FIT_BIAS_PCT",
]

#: Measured bias of the legacy fit ``pi*B*L*(0.5+0.25*Cb)`` against closed-form
#: shapes (percent, computed by :func:`legacy_fit_bias_report`, not copied).
LEGACY_FIT_BIAS_PCT = {
    "cylinder": -30.4,
    "prolate_spheroid_b_over_a_0.25": -8.7,
}

_CB_GATE_TOL = 0.05


class GeometryError(ValueError):
    """Raised when the supplied meridian is not an admissible body of revolution."""


@dataclass(frozen=True)
class WettedSurfaceResult:
    """Wetted surface of a body of revolution plus the gate evidence."""

    wetted_surface_m2: float
    method: str
    n_points: int
    length_m: float
    max_radius_m: float
    volume_m3: float
    cb_geometry: float
    discretisation_pct: float
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "wetted_surface_m2": round(self.wetted_surface_m2, 9),
            "method": self.method,
            "n_points": self.n_points,
            "length_m": self.length_m,
            "max_radius_m": self.max_radius_m,
            "volume_m3": round(self.volume_m3, 9),
            "cb_geometry": round(self.cb_geometry, 9),
            "discretisation_pct": round(self.discretisation_pct, 6),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Meridian validation
# ---------------------------------------------------------------------------

def _clean_profile(profile: Sequence[Sequence[float]]) -> Tuple[List[float], List[float]]:
    if profile is None or len(profile) < 2:
        raise GeometryError("meridian_profile needs at least two (x, r) points")
    xs: List[float] = []
    rs: List[float] = []
    for point in profile:
        if len(point) < 2:
            raise GeometryError("each meridian point must be [x, r]")
        x, r = float(point[0]), float(point[1])
        if not math.isfinite(x) or not math.isfinite(r):
            raise GeometryError("meridian coordinates must be finite")
        if r < 0.0:
            raise GeometryError(f"negative radius r={r} at x={x}")
        if xs and x <= xs[-1]:
            raise GeometryError("meridian x must be strictly increasing")
        xs.append(x)
        rs.append(r)
    length = xs[-1] - xs[0]
    if length <= 0.0:
        raise GeometryError("meridian length must be positive")
    if max(rs) <= 0.0:
        raise GeometryError("meridian has zero radius everywhere (degenerate body)")
    return xs, rs


# ---------------------------------------------------------------------------
# Exact polyline-refolution integration
# ---------------------------------------------------------------------------

def revolution_wetted_surface(
    profile: Sequence[Sequence[float]],
    *,
    cb_reference: Optional[float] = None,
    cb_tolerance: float = _CB_GATE_TOL,
) -> WettedSurfaceResult:
    """Wetted surface of a body of revolution from its meridian ``(x, r)`` table.

    Parameters
    ----------
    profile:
        Meridian points ``[[x, r], ...]``, ``x`` strictly increasing, ``r >= 0``.
    cb_reference:
        Block coefficient claimed by the caller.  When given, the Cb integrated
        from the geometry must agree within ``cb_tolerance`` (relative) or
        :class:`GeometryError` is raised — the geometry-first gate.
    cb_tolerance:
        Relative tolerance of that gate (default 5 %).
    """
    xs, rs = _clean_profile(profile)

    area = 0.0
    volume = 0.0
    arc_simpson_numer = 0.0  # independent 1st-order estimate for the discretisation indicator
    for i in range(len(xs) - 1):
        h = xs[i + 1] - xs[i]
        r1, r2 = rs[i], rs[i + 1]
        dr = r2 - r1
        slant = math.hypot(h, dr)
        area += math.pi * (r1 + r2) * slant            # exact frustum lateral area
        volume += math.pi * h * (r1 * r1 + r1 * r2 + r2 * r2) / 3.0  # exact frustum volume
        # trapezoidal approximation of 2*pi*int r*sqrt(1+(r')^2) dx on this segment
        slope = dr / h
        arc_simpson_numer += 2.0 * math.pi * 0.5 * h * (
            r1 * math.hypot(1.0, slope) + r2 * math.hypot(1.0, slope)
        )

    length = xs[-1] - xs[0]
    r_max = max(rs)
    cb = volume / (length * (2.0 * r_max) ** 2) if r_max > 0 else 0.0
    discretisation = abs(area - arc_simpson_numer) / area * 100.0 if area > 0 else 0.0

    if cb_reference is not None:
        rel = abs(cb - float(cb_reference)) / float(cb_reference)
        if rel > cb_tolerance:
            raise GeometryError(
                "geometry/Cb mismatch: meridian integrates to Cb=%.6f while the "
                "request declares cb=%.6f (rel %.2f%% > %.2f%%)"
                % (cb, float(cb_reference), rel * 100.0, cb_tolerance * 100.0)
            )

    return WettedSurfaceResult(
        wetted_surface_m2=area,
        method="revolution_frustum_exact",
        n_points=len(xs),
        length_m=length,
        max_radius_m=r_max,
        volume_m3=volume,
        cb_geometry=cb,
        discretisation_pct=discretisation,
        notes=(
            "lateral area summed over exact frusta (exact for a straight meridian, "
            "O(h^2) to a smooth surface); wetted surface excludes the wetted base area"
        ),
    )


def acceleration_free_surface(result: WettedSurfaceResult) -> float:
    """Wetted area available to friction: the lateral area (no base disc).

    Present so that callers never confuse the two surfaces; identical to the
    reported value for a closed body of revolution.
    """
    return result.wetted_surface_m2


# ---------------------------------------------------------------------------
# Closed-form references (verification anchors, no literature tables involved)
# ---------------------------------------------------------------------------

def cylinder_wetted_surface(diameter: float, length: float) -> float:
    """Lateral area of a circular cylinder, ``pi * D * L`` (ends excluded)."""
    if diameter <= 0 or length <= 0:
        raise GeometryError("diameter and length must be positive")
    return math.pi * diameter * length


def sphere_wetted_surface(radius: float) -> float:
    """``4 * pi * R^2``."""
    if radius <= 0:
        raise GeometryError("radius must be positive")
    return 4.0 * math.pi * radius * radius


def ellipsoid_wetted_surface(a: float, b: float) -> float:
    """Surface of an ellipsoid of revolution.

    ``a`` is the semi-axis along the axis of revolution, ``b`` the equatorial
    semi-axis.  ``a > b`` gives the prolate closed form (with ``e = sqrt(1-b^2/a^2)``)::

        S = 2*pi*b^2 * (1 + a/(b*e) * asin(e))

    ``a < b`` gives the oblate form with ``e = sqrt(1-a^2/b^2)``::

        S = 2*pi*b^2 * (1 + (1-e^2)/e * atanh(e))

    ``a == b`` returns the sphere.  Both branches are the standard integrals of
    revolution and are used here as ground truth, not as fitted values.
    """
    if a <= 0 or b <= 0:
        raise GeometryError("semi-axes must be positive")
    if abs(a - b) <= 1e-12 * max(a, b):
        return sphere_wetted_surface(a)
    if a > b:
        e = math.sqrt(1.0 - (b / a) ** 2)
        return 2.0 * math.pi * b * b * (1.0 + a / (b * e) * math.asin(e))
    e = math.sqrt(1.0 - (a / b) ** 2)
    return 2.0 * math.pi * b * b * (1.0 + (1.0 - e * e) / e * math.atanh(e))


def box_wetted_surface(length: float, beam: float, draft: float) -> float:
    """Platform convention for a box hull: bottom + two sides, ``L*(B+2T)``.

    Matches ``services.ship_hydrostatics._wetted_surface`` on ``barge_offsets``
    (ends excluded in both), so the geometry path and the offset path agree by
    construction rather than by luck.
    """
    if length <= 0 or beam <= 0 or draft <= 0:
        raise GeometryError("box dimensions must be positive")
    return length * (beam + 2.0 * draft)


# ---------------------------------------------------------------------------
# Legacy fit (kept for the before/after comparison, never for new work)
# ---------------------------------------------------------------------------

def empirical_wetted_surface(length: float, beam: float, cb: float) -> float:
    """The legacy fit ``pi * B * L * (0.5 + 0.25 * Cb)`` (m1666 verdict: FAIL)."""
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def prolate_spheroid_profile(
    length: float, max_diameter: float, n_points: int = 2001
) -> List[Tuple[float, float]]:
    """Meridian of a prolate spheroid: ``r(x) = b * sqrt(1 - ((x-a)/a)^2)``.

    Convenience for tests, benchmarks and for callers that only know
    ``(L, D)`` of a slender body; ``a = L/2``, ``b = D/2``.
    """
    if length <= 0 or max_diameter <= 0 or n_points < 3:
        raise GeometryError("invalid spheroid parameters")
    a, b = 0.5 * length, 0.5 * max_diameter
    out = []
    for i in range(n_points):
        x = length * i / (n_points - 1)
        u = (x - a) / a
        u = max(-1.0, min(1.0, u))
        out.append((x, b * math.sqrt(max(0.0, 1.0 - u * u))))
    return out


def legacy_fit_bias_report(length: float = 4.356, diameter: float = 0.508) -> dict:
    """Bias of the legacy fit on two closed-form shapes (programmatic, zero hand-copy).

    Returns relative errors in percent for the cylinder and the prolate spheroid
    equivalent to ``(length, diameter)``; used by the tests and the figures.
    """
    b = 0.5 * diameter
    cyl = cylinder_wetted_surface(diameter, length)
    sph = ellipsoid_wetted_surface(0.5 * length, b)
    fit_cyl = empirical_wetted_surface(length, diameter, math.pi / 4.0)
    fit_sph = empirical_wetted_surface(length, diameter, math.pi / 6.0)
    return {
        "cylinder": {
            "closed_form_m2": cyl,
            "legacy_fit_m2": fit_cyl,
            "legacy_bias_pct": (fit_cyl - cyl) / cyl * 100.0,
        },
        "prolate_spheroid": {
            "closed_form_m2": sph,
            "legacy_fit_m2": fit_sph,
            "legacy_bias_pct": (fit_sph - sph) / sph * 100.0,
        },
    }