"""IMO IS Code 2008 intact-stability criteria -- single source of truth.

Context (m1642 audit)
---------------------
Before this module the platform carried **two independent implementations** of
the same regulatory check, and they disagreed:

``services/ship_performance/icing/stability.py``  (LoadedCondition.stability_criteria)
    5 items: GM0, A(0-30), GZ(30), GZ_max angle, A(30-40) -- **A(0-40) >= 0.09
    m rad is missing**, so a curve that keeps area to 30 deg but loses it
    between 30 and 40 deg (exactly what deck-edge immersion after ice accretion
    does) is reported as a *pass*.

``services/ice_integration/nodes_downstream.py``  (imo_criteria)
    6 items including **GZ_max >= 0.25 m**, which is *not* an IS Code 2008
    A.2.2 requirement, while the A.2.2.4 angle-of-maximum-GZ clause is not
    gated at all.  A compliant ship with GZ_max = 0.24 m is reported as a
    *fail*.

The two defects act in opposite directions (false pass / false fail), so the
disagreement cannot be dismissed as a naming convention.  Both call sites now
delegate here, so there is one numeric set to audit.

The numeric set implemented (IS Code 2008 Part A, 2.2):

======================  ==========================================  ==========
clause                  requirement                                 threshold
======================  ==========================================  ==========
2.2.1 (first)           A(0 .. 30 deg)                              >= 0.055
2.2.1 (second)          A(0 .. 40 deg) or A(0 .. theta_f)           >= 0.090
2.2.2                   A(30 .. 40 deg) or A(30 .. theta_f)         >= 0.030
2.2.3                   GZ at 30 deg                                >= 0.200 m
2.2.4                   heel angle of GZ_max                        >= 25 deg
2.2.5                   initial GM0                                 >= 0.150 m
======================  ==========================================  ==========

Areas are integrated in **m rad** (heels converted with ``math.radians``); an
``m deg`` implementation would read ``pi/180`` = 1.75 % of the true value and
fail every criterion, which the anchor suite in
``tools/verification/stability_criteria_anchors.py`` checks explicitly against
the closed-form antiderivative of the wall-sided GZ curve.

Out of scope (deliberately not silently gated): weather criterion 2.3, passenger
crowding, grain, and the ``theta_f`` down-flooding angle itself -- callers that
know a flooding angle may pass ``theta_f_deg`` and the 40 deg window is capped;
callers that do not are evaluated at 40 deg, which is the optimistic branch and
is reported as such (``theta_f_deg = None``).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

STANDARD = "IMO IS Code 2008 Part A 2.2 (Res. MSC.267(85))"

#: Requirement thresholds, in the order of the Code.
AREA_0_30_MIN = 0.055   # m rad -- A.2.2.1 (first clause)
AREA_0_40_MIN = 0.090   # m rad -- A.2.2.1 (second clause)
AREA_30_40_MIN = 0.030  # m rad -- A.2.2.2
GZ_AT_30_MIN = 0.200    # m     -- A.2.2.3
GZ_MAX_ANGLE_MIN = 25.0  # deg  -- A.2.2.4
GM0_MIN = 0.150         # m     -- A.2.2.5

TOL = 1e-12

Point = Tuple[float, float]  # (heel_deg, gz_m)


# ---------------------------------------------------------------------------
# curve handling
# ---------------------------------------------------------------------------
def normalise_curve(curve: Iterable[Any]) -> List[Point]:
    """Accept ``(heel_deg, gz_m)`` pairs, ``{"heel_deg":..,"gz_m":..}`` dicts.

    Returns an ascending list of ``(heel_deg, gz_m)`` tuples.  Raises
    ``ValueError`` on an empty curve or on non-finite / non-monotonic heels,
    because silently sorting garbage would hide a broken producer.
    """
    pts: List[Point] = []
    for item in curve:
        if isinstance(item, Mapping):
            heel = item.get("heel_deg", item.get("phi_deg", item.get("heel")))
            gz = item.get("gz_m", item.get("gz_numeric", item.get("gz")))
        elif hasattr(item, "heel_deg") and hasattr(item, "gz_m"):
            heel, gz = item.heel_deg, item.gz_m  # type: ignore[attr-defined]
        else:
            heel, gz = item
        heel = float(heel)
        gz = float(gz)
        if not (math.isfinite(heel) and math.isfinite(gz)):
            raise ValueError(f"non-finite GZ curve point: heel={heel}, gz={gz}")
        pts.append((heel, gz))
    if len(pts) < 2:
        raise ValueError("GZ curve needs at least two points")
    pts.sort(key=lambda p: p[0])
    for (x0, _), (x1, _) in zip(pts, pts[1:]):
        if x1 - x0 <= 1e-12:
            raise ValueError(f"duplicate heel angle in GZ curve: {x1} deg")
    return pts


def gz_at(points: Sequence[Point], angle_deg: float) -> float:
    """Righting lever at ``angle_deg`` by linear interpolation (clamped)."""
    if angle_deg <= points[0][0]:
        return points[0][1]
    if angle_deg >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= angle_deg <= x1:
            if x1 - x0 <= 0.0:
                return y0
            return y0 + (y1 - y0) * (angle_deg - x0) / (x1 - x0)
    return points[-1][1]


def area_under_gz(points: Sequence[Point], a0_deg: float, a1_deg: float) -> float:
    """Trapezoidal area under the GZ curve between two heels, in **m rad**.

    Segment endpoints are clipped by linear interpolation, so a grid that does
    not land exactly on ``a0``/``a1`` (e.g. 2.5 deg spacing) introduces no
    truncation bias -- only the usual O(h^2) trapezoid error on the interior,
    which the anchor suite measures against the closed-form antiderivative.
    """
    if a1_deg <= a0_deg:
        return 0.0
    a0_deg = max(a0_deg, points[0][0])
    a1_deg = min(a1_deg, points[-1][0])
    if a1_deg <= a0_deg:
        return 0.0

    clipped: List[Point] = [(a0_deg, gz_at(points, a0_deg))]
    for x, y in points:
        if a0_deg < x < a1_deg:
            clipped.append((x, y))
    clipped.append((a1_deg, gz_at(points, a1_deg)))

    total = 0.0
    for (x0, y0), (x1, y1) in zip(clipped, clipped[1:]):
        total += 0.5 * (y0 + y1) * (math.radians(x1) - math.radians(x0))
    return total


# ---------------------------------------------------------------------------
# criteria evaluation
# ---------------------------------------------------------------------------
def _entry(value: float, required: Optional[float], reference: str,
           description: str, applicable: bool = True,
           gating: bool = True) -> Dict[str, Any]:
    passed = True if not applicable or required is None else value >= required - TOL
    return {
        "value": float(value),
        "required": None if required is None else float(required),
        "pass": bool(passed),
        "applicable": bool(applicable),
        "gating": bool(gating and applicable and required is not None),
        "reference": reference,
        "description": description,
        "margin": None if required is None else float(value - required),
    }


def evaluate_is_code_2008(
    curve: Iterable[Any],
    gm0_m: float,
    theta_f_deg: Optional[float] = None,
) -> Dict[str, Any]:
    """Evaluate IS Code 2008 Part A 2.2 on a GZ curve.

    Parameters
    ----------
    curve
        Sampled GZ curve: pairs / dicts / objects, see :func:`normalise_curve`.
        Must span at least 0..40 deg for the full area criteria (the range is
        clipped to the supplied data; a short curve is *reported*, not silently
        extrapolated).
    gm0_m
        Initial metacentric height (already corrected for free surface).
    theta_f_deg
        Optional angle of flooding; when < 40 deg the 40 deg-area clauses are
        evaluated at ``theta_f`` instead of 40 deg, as the Code prescribes.

    Returns
    -------
    dict with ``criteria`` (per-clause value/required/pass/reference/margin),
    ``informational`` (quantities worth reporting that are *not* Code gates),
    ``all_pass``, ``min_margin`` and the effective evaluation window.
    """
    pts = normalise_curve(curve)
    top = pts[-1][0]
    theta_40 = 40.0 if theta_f_deg is None else min(40.0, float(theta_f_deg))

    a_0_30 = area_under_gz(pts, 0.0, 30.0)
    a_0_40 = area_under_gz(pts, 0.0, theta_40)
    a_30_40 = area_under_gz(pts, 30.0, theta_40)
    gz30 = gz_at(pts, 30.0)

    peak_heel, peak_gz = max(pts, key=lambda p: p[1])

    criteria: Dict[str, Dict[str, Any]] = {
        "area_0_30_m_rad": _entry(
            a_0_30, AREA_0_30_MIN, "IS Code 2008 A.2.2.1",
            "area under GZ curve to 30 deg"),
        "area_0_40_m_rad": _entry(
            a_0_40, AREA_0_40_MIN, "IS Code 2008 A.2.2.1",
            f"area under GZ curve to 40 deg (evaluated to {theta_40:g} deg)"),
        "area_30_40_m_rad": _entry(
            a_30_40, AREA_30_40_MIN, "IS Code 2008 A.2.2.2",
            f"area between 30 and 40 deg (evaluated to {theta_40:g} deg)",
            applicable=theta_40 > 30.0 + 1e-9),
        "gz_at_30_m": _entry(
            gz30, GZ_AT_30_MIN, "IS Code 2008 A.2.2.3",
            "righting lever at 30 deg heel"),
        "angle_of_max_gz_deg": _entry(
            peak_heel, GZ_MAX_ANGLE_MIN, "IS Code 2008 A.2.2.4",
            "heel angle at which GZ reaches its maximum"),
        "gm0_m": _entry(
            float(gm0_m), GM0_MIN, "IS Code 2008 A.2.2.5",
            "initial metacentric height (free-surface corrected)"),
    }

    gating = {k: v for k, v in criteria.items() if v["gating"]}
    all_pass = all(v["pass"] for v in gating.values())
    margins = {k: v["margin"] for k, v in gating.items()}
    min_margin = min(margins.values()) if margins else float("nan")

    notes: List[str] = []
    if theta_f_deg is None:
        notes.append(
            "theta_f not supplied: the 40 deg area clauses were evaluated at "
            "40 deg (optimistic branch of A.2.2.1 / A.2.2.2)")
    if top < 40.0 - 1e-9:
        notes.append(
            f"GZ curve ends at {top:g} deg; area criteria clipped to the "
            "supplied range and therefore NOT a full Code verdict")
    if theta_40 <= 30.0 + 1e-9:
        notes.append("theta_f <= 30 deg: A.2.2.2 (30-40 deg area) is not applicable")

    return {
        "standard": STANDARD,
        "criteria": criteria,
        "informational": {
            "gz_max_m": float(peak_gz),
            "gz_max_angle_deg": float(peak_heel),
            "curve_range_deg": (float(pts[0][0]), float(top)),
            "num_points": len(pts),
        },
        "gating_keys": sorted(gating),
        "margins": margins,
        "min_margin": float(min_margin),
        "all_pass": bool(all_pass),
        "theta_f_deg": None if theta_f_deg is None else float(theta_f_deg),
        "theta_40_deg": float(theta_40),
        "notes": notes,
    }