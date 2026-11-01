"""
Independent stability / IMO intact-stability reference implementation (VERIFIER side).

Purpose
-------
This module holds *reference* values that are derived independently of any
platform implementation, so that the platform's merged stability capability
(``POST /api/v1/calculations/stability``) can be audited against literature
rather than against itself.

Two independent sources are encoded here:

1. **Wall-sided ship, closed form** (Biran, *Ship Hydrostatics and Stability*,
   2nd ed., §5.4; also Rawson & Tupper §8):
        GZ(phi) = sin(phi) * (GM + 0.5 * BM * tan^2(phi))
   This is exact for a vessel with vertical topsides (a box-shaped barge in the
   range where the deck edge is not immersed), i.e. a genuine analytical
   reference for a pontoon/barge geometry.

   The area under the curve has a closed-form antiderivative:
        INT sin(phi) (GM + 0.5 BM tan^2 phi) dphi
          = -GM cos(phi) + 0.5 BM (sec(phi) + cos(phi)) + C
   so areas between arbitrary heel angles are evaluated exactly, with no
   quadrature and hence no shared numerical error with the platform's
   trapezoid rule.

2. **IMO Intact Stability Code 2008** (IMO Res. MSC.267(85), Part A,
   Chapter 2, §2.2.1 "General criteria"), which the platform's IMO gate must
   implement verbatim:

        .1  area under GZ up to 30 deg           >= 0.055 m.rad
        .2  area under GZ up to 40 deg (or theta_f) >= 0.090 m.rad
        .3  area between 30 deg and 40 deg       >= 0.030 m.rad
        .4  GZ at 30 deg                         >= 0.200 m
        .5  angle of maximum GZ                  >= 25 deg
        .6  initial metacentric height GM0       >= 0.150 m

   Note on .4: the requirement is on the righting arm **at 30 deg**, *not* on
   the maximum GZ. A curve with GZ_max = 0.30 m at 60 deg and GZ(30 deg) =
   0.15 m does NOT satisfy .4. Substituting GZ_max is a classic false-pass.

   §2.2 contains no general "vanishing angle >= 55 deg" criterion. Such a
   requirement belongs to other rule sets (e.g. class-society rules for
   specific ship types) and, if implemented, must be attributed.

Everything in this module is pure ``math`` — no numpy, no platform imports —
so it can run inside the isolated verifier process and is safe to use as a
held-out oracle.
"""
from __future__ import annotations

import math
from typing import Iterable

# ---------------------------------------------------------------------------
# 1. Wall-sided closed form
# ---------------------------------------------------------------------------


def wall_sided_gz(phi_deg: float, gm_m: float, bm_m: float) -> float:
    """Righting arm of a wall-sided ship at heel angle ``phi_deg`` [m].

    Exact for vertical topsides (box barge / pontoon), valid while the deck
    edge is not immersed and the section remains "wall-sided".

    GZ = sin(phi) * (GM + 0.5 * BM * tan^2(phi))
    """
    phi = math.radians(phi_deg)
    return math.sin(phi) * (gm_m + 0.5 * bm_m * math.tan(phi) ** 2)


def _wall_sided_area_antiderivative(phi_deg: float, gm_m: float, bm_m: float) -> float:
    """Antiderivative F(phi) of GZ(phi) with respect to phi in radians.

    F(phi) = -GM*cos(phi) + 0.5*BM*(sec(phi) + cos(phi))
    (verified by differentiation; sec(phi) term comes from sin*tan^2 =
     sin*sec^2 - sin and d(sec)/dphi = sec*tan).
    """
    phi = math.radians(phi_deg)
    return -gm_m * math.cos(phi) + 0.5 * bm_m * (1.0 / math.cos(phi) + math.cos(phi))


def wall_sided_area(phi1_deg: float, phi2_deg: float, gm_m: float, bm_m: float) -> float:
    """Exact area under a wall-sided GZ curve between two heel angles [m.rad]."""
    if phi2_deg == phi1_deg:
        return 0.0
    lo, hi = sorted((phi1_deg, phi2_deg))
    a = _wall_sided_area_antiderivative(hi, gm_m, bm_m) - _wall_sided_area_antiderivative(lo, gm_m, bm_m)
    return a if phi2_deg > phi1_deg else -a


def barge_gm_bm(beam_m: float, draft_m: float, kg_m: float,
                cb: float = 1.0, cm: float = 1.0) -> tuple[float, float, float]:
    """KB, BM, GM of a rectangular pontoon (box barge) — closed form.

    KB = T/2,  BM = I_wl / nabla = (L*B^3/12) / (L*B*T) = B^2 / (12*T)
    GM = KB + BM - KG
    """
    kb = draft_m / 2.0
    bm = beam_m ** 2 / (12.0 * draft_m)
    gm = kb + bm - kg_m
    return kb, bm, gm


# ---------------------------------------------------------------------------
# 2. IMO IS Code 2008 general criteria (Part A, Chapter 2, §2.2.1)
# ---------------------------------------------------------------------------

IMO_IS_CODE_2008 = {
    "area_0_30": {
        "requirement": "area under GZ up to 30 deg >= 0.055 m.rad",
        "threshold": 0.055,
        "unit": "m.rad",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.1",
    },
    "area_0_40": {
        "requirement": "area under GZ up to 40 deg (or theta_f) >= 0.090 m.rad",
        "threshold": 0.090,
        "unit": "m.rad",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.2",
    },
    "area_30_40": {
        "requirement": "area between 30 deg and 40 deg >= 0.030 m.rad",
        "threshold": 0.030,
        "unit": "m.rad",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.3",
    },
    "gz_at_30": {
        "requirement": "GZ at 30 deg >= 0.200 m",
        "threshold": 0.200,
        "unit": "m",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.4",
    },
    "gm0": {
        "requirement": "initial metacentric height GM0 >= 0.150 m",
        "threshold": 0.150,
        "unit": "m",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.6",
    },
    "angle_gz_max": {
        "requirement": "angle of maximum GZ >= 25 deg",
        "threshold": 25.0,
        "unit": "deg",
        "ref": "IS Code 2008 Part A Ch.2 para 2.2.1.5",
    },
}


def gz_at(gz_curve: Iterable[dict], target_deg: float, tol_deg: float = 1e-6) -> float:
    """Interpolate GZ at ``target_deg`` from a curve [{angle, gz}, ...].

    Linear interpolation is used; if ``target_deg`` coincides with a sample the
    sample value is returned exactly. Raises if the target is outside the range
    (the reference never extrapolates).
    """
    pts = sorted(((float(p["angle"]), float(p["gz"])) for p in gz_curve), key=lambda t: t[0])
    if not pts:
        raise ValueError("empty GZ curve")
    for a, g in pts:
        if abs(a - target_deg) <= tol_deg:
            return g
    for (a1, g1), (a2, g2) in zip(pts, pts[1:]):
        if a1 <= target_deg <= a2:
            w = (target_deg - a1) / (a2 - a1)
            return g1 + w * (g2 - g1)
    raise ValueError(f"heel angle {target_deg} outside curve range [{pts[0][0]}, {pts[-1][0]}]")


def _interp_pts(pts, target_deg: float) -> float:
    """Linear interpolation on a sorted list of (angle, gz) tuples."""
    for a, g in pts:
        if abs(a - target_deg) <= 1e-9:
            return g
    for (a1, g1), (a2, g2) in zip(pts, pts[1:]):
        if a1 <= target_deg <= a2:
            w = (target_deg - a1) / (a2 - a1)
            return g1 + w * (g2 - g1)
    raise ValueError(f"heel angle {target_deg} outside curve range")


def imo_criteria_from_curve(gz_curve: Iterable[dict], gm_m: float) -> dict:
    """Evaluate IS Code 2008 §2.2.1 criteria on a *given* GZ curve.

    Areas are integrated with the trapezoid rule on the supplied samples, which
    matches how any implementation would integrate the same samples; the
    reference differs from the platform only in the **thresholds and the
    criterion definitions**, which is exactly what is being audited.

    Returns {criterion_key: {"value": float, "threshold": float, "met": bool,
                             "requirement": str, "ref": str, "passed_name": str}}.
    """
    pts = sorted(((float(p["angle"]), float(p["gz"])) for p in gz_curve), key=lambda t: t[0])

    def area(a0: float, a1: float) -> float:
        acc = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            # clip segment to [a0, a1]
            lo, hi = max(x0, a0), min(x1, a1)
            if hi <= lo:
                continue
            ylo = y0 + (y1 - y0) * (lo - x0) / (x1 - x0) if x1 != x0 else y0
            yhi = y0 + (y1 - y0) * (hi - x0) / (x1 - x0) if x1 != x0 else y1
            acc += 0.5 * (ylo + yhi) * math.radians(hi - lo)
        return acc

    gz_max_angle, gz_max = max(pts, key=lambda t: t[1])
    values = {
        "area_0_30": area(0.0, 30.0),
        "area_0_40": area(0.0, 40.0),
        "area_30_40": area(30.0, 40.0),
        "gz_at_30": _interp_pts(pts, 30.0),
        "gm0": float(gm_m),
        "angle_gz_max": gz_max_angle,
    }
    values["angle_gz_max"] = float(gz_max_angle)
    out = {}
    for key, spec in IMO_IS_CODE_2008.items():
        v = values[key]
        out[key] = {
            "value": v,
            "threshold": spec["threshold"],
            "met": v >= spec["threshold"],
            "requirement": spec["requirement"],
            "ref": spec["ref"],
            "unit": spec["unit"],
        }
    out["_gz_max"] = {"value": gz_max, "angle": gz_max_angle}
    return out


# ---------------------------------------------------------------------------
# 3. Attribution of platform criterion keys -> IS Code keys
# ---------------------------------------------------------------------------

#: Mapping documented criteria semantics to the canonical IS Code keys.
#: ``None`` means "no such criterion in IS Code 2008 Part A §2.2".
PLATFORM_KEY_MAP = {
    "GM >= 0.15m": "gm0",
    "GZmax >= 0.20m": "gz_at_30",      # semantically WRONG: IS Code 2.2.1.4 is GZ@30
    "φ_GZmax >= 25°": "angle_gz_max",
    "Area_30_40 >= 0.03 m·rad": "area_30_40",
    "Area_0_40 >= 0.055 m·rad": "area_0_40",  # threshold WRONG: 0.090 m.rad
    "Vanishing angle >= 55°": None,            # not in IS Code 2008 §2.2
}