"""IMO minimum-manoeuvrability compliance (resolution MSC.137(76)).

Why this module exists
----------------------
``validation.py`` closes mission W3-N08 with *code-to-code* verification only:
an independent damped-Newton equilibrium solve, an RK4 time-step refinement and
a coefficient-set sensitivity band.  Its absolute-accuracy claim is honestly
``WITHHELD``, because no ice-basin / EFD manoeuvring data exist in the worktree.

There is, however, a second and quite different reference tier that needs no
experimental data at all: the **statutory** minimum manoeuvrability of
IMO resolution MSC.137(76) "Standards for Ship Manoeuvrability" (adopted
2002-12-04, applicable to ships of 100 m length and over).  That resolution
states purely numeric pass limits, so a manoeuvring model can be audited
against it completely offline.  Until now the platform never did.

The four criteria encoded here are, verbatim in substance:

C1  Turning circle (35 deg rudder)
      * advance          <= 4.5 L
      * tactical diameter<= 5.0 L

C2  Initial turning ability (10 deg rudder)
      * the ship shall not have travelled more than 2.5 L by the time the
        heading has changed by 10 deg.

C3  10 deg / 10 deg zigzag
      * first overshoot angle <= 10 deg when L/V < 2.5 s
      * first overshoot angle <= 20 deg when L/V >= 2.5 s

C4  20 deg / 20 deg zigzag
      * first overshoot angle <= 25 deg

The ``L/V`` split in C3 is the classic trap: ``L`` in metres over ``V`` in m/s
gives seconds, and for a 320 m VLCC at 7.717 m/s that is 41.5 s, i.e. the
*20 deg* branch.  Applying the 10 deg branch by mistake would fail a compliant
ship.  :func:`overshoot_limit_deg` implements the branch explicitly and
:func:`check_zigzag` reports which branch was used.

What this module does NOT claim
-------------------------------
Passing MSC.137(76) is a statement about *this* MMG model under *its* stated
coefficient set; it is not a statement about the real ship.  The standards are
semi-absolute (published numeric thresholds), so an error bar versus model
tests is still withheld - see ``withheld`` in :func:`run_imo_compliance`.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from . import data as D
from . import mmg as M

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
IMO_RESOLUTION = "IMO Res. MSC.137(76), adopted 2002-12-04"

CRITERIA = {
    "C1_turning_circle": {
        "rudder_deg": 35.0,
        "advance_over_L_max": 4.5,
        "tactical_diameter_over_L_max": 5.0,
    },
    "C2_initial_turning": {
        "rudder_deg": 10.0,
        "heading_change_deg": 10.0,
        "track_distance_over_L_max": 2.5,
    },
    "C3_zigzag_10_10": {
        "rudder_deg": 10.0,
        "heading_over_overshoot_deg": 10.0,
        "first_overshoot_max_deg_low_LV": 10.0,
        "first_overshoot_max_deg_high_LV": 20.0,
        "LV_split_s": 2.5,
    },
    "C4_zigzag_20_20": {
        "rudder_deg": 20.0,
        "heading_over_overshoot_deg": 20.0,
        "first_overshoot_max_deg": 25.0,
    },
}

PROVENANCE = (
    f"{IMO_RESOLUTION}: {CRITERIA}. Thresholds transcribed from the "
    "resolution text (offline constants); no experimental baseline is involved, "
    "so this tier is available without network access, unlike the EFD "
    "comparisons withheld in validation.py."
)

# Support tolerance: a metric that equals the limit to within this fraction of L
# is reported as borderline rather than silently passing.
BORDERLINE_MARGIN_FRAC = 0.01


# ---------------------------------------------------------------------------
# Branch logic
# ---------------------------------------------------------------------------
def length_speed_ratio_s(length_m: float, speed_ms: float) -> float:
    """``L/V`` in seconds, the parameter that selects the C3 overshoot branch."""
    if length_m <= 0.0 or speed_ms <= 0.0:
        raise ValueError("length and speed must be positive")
    return length_m / speed_ms


def overshoot_limit_deg(length_m: float, speed_ms: float) -> Dict[str, object]:
    """First-overshoot limit for the 10/10 zigzag, with the branch made explicit."""
    lv = length_speed_ratio_s(length_m, speed_ms)
    split = CRITERIA["C3_zigzag_10_10"]["LV_split_s"]
    if lv < split:
        limit = CRITERIA["C3_zigzag_10_10"]["first_overshoot_max_deg_low_LV"]
        branch = "low_LV"
    else:
        limit = CRITERIA["C3_zigzag_10_10"]["first_overshoot_max_deg_high_LV"]
        branch = "high_LV"
    return {"LV_s": lv, "limit_deg": limit, "branch": branch, "split_s": split}


# ---------------------------------------------------------------------------
# Low-level check helper
# ---------------------------------------------------------------------------
def _check(name: str, value: float, limit: float, unit: str, *,
           lower_is_better: bool = True, margin_frac: float = 0.0,
           note: str = "") -> Dict[str, object]:
    """One pass/fail item.  ``margin`` is signed headroom in the metric unit."""
    finite = isinstance(value, (int, float)) and math.isfinite(value)
    if not finite:
        return {"check": name, "value": value, "limit": limit, "unit": unit,
                "margin": float("nan"), "margin_pct": float("nan"),
                "passed": False, "borderline": False,
                "note": (note + " | value not finite, cannot certify").strip(" |")}
    margin = (limit - value) if lower_is_better else (value - limit)
    passed = margin >= 0.0
    borderline = passed and margin <= abs(limit) * margin_frac
    return {
        "check": name,
        "value": value,
        "limit": limit,
        "unit": unit,
        "margin": margin,
        "margin_pct": (margin / abs(limit) * 100.0) if limit else float("nan"),
        "passed": passed,
        "borderline": borderline,
        "note": note,
    }


# ---------------------------------------------------------------------------
# C1 / C2 / C3 / C4
# ---------------------------------------------------------------------------
def check_turning_circle(metrics: Dict[str, object], length_m: float) -> Dict[str, object]:
    """C1: advance <= 4.5 L and tactical diameter <= 5 L."""
    c = CRITERIA["C1_turning_circle"]
    checks = [
        _check("advance_over_L", float(metrics["advance_over_L"]),
               c["advance_over_L_max"], "L", margin_frac=BORDERLINE_MARGIN_FRAC),
        _check("tactical_diameter_over_L",
               float(metrics["tactical_diameter_over_L"]),
               c["tactical_diameter_over_L_max"], "L",
               margin_frac=BORDERLINE_MARGIN_FRAC),
    ]
    return {"criterion": "C1_turning_circle", "rudder_deg": c["rudder_deg"],
            "checks": checks, "passed": all(x["passed"] for x in checks)}


def check_initial_turning(history: Dict[str, Sequence[float]], length_m: float,
                          heading_change_deg: Optional[float] = None
                          ) -> Dict[str, object]:
    """C2: 10 deg of heading must not cost more than 2.5 L of track distance."""
    c = CRITERIA["C2_initial_turning"]
    target = (heading_change_deg if heading_change_deg is not None
              else c["heading_change_deg"])
    dist = float("nan")
    t_reach = float("nan")
    for i, psi in enumerate(history["psi_deg"]):
        if psi >= target:
            t_reach = float(history["t"][i])
            dist = math.hypot(float(history["x"][i]), float(history["y"][i]))
            break
    ratio = dist / length_m if math.isfinite(dist) else float("nan")
    chk = _check(f"track_distance_over_L_at_heading_{target:g}deg", ratio,
                 c["track_distance_over_L_max"], "L",
                 margin_frac=BORDERLINE_MARGIN_FRAC)
    return {"criterion": "C2_initial_turning", "rudder_deg": c["rudder_deg"],
            "time_to_heading_s": t_reach, "track_distance_m": dist,
            "checks": [chk], "passed": chk["passed"]}


def check_zigzag(metrics: Dict[str, object], length_m: float, speed_ms: float,
                 rudder_deg: float = 10.0) -> Dict[str, object]:
    """C3 (10/10) or C4 (20/20) on the first overshoot angle."""
    first = metrics.get("first_overshoot_deg", float("nan"))
    if rudder_deg < 15.0:
        crit = "C3_zigzag_10_10"
        branch = overshoot_limit_deg(length_m, speed_ms)
        limit = float(branch["limit_deg"])
        chk = _check(f"first_overshoot_deg_{rudder_deg:g}_{rudder_deg:g}",
                     float(first), limit, "deg",
                     margin_frac=BORDERLINE_MARGIN_FRAC,
                     note=f"L/V = {branch['LV_s']:.2f} s -> {branch['branch']} "
                          f"branch (split {branch['split_s']:g} s)")
        out = {"criterion": crit, "rudder_deg": rudder_deg,
               "LV_s": branch["LV_s"], "branch": branch["branch"],
               "checks": [chk], "passed": chk["passed"]}
    else:
        crit = "C4_zigzag_20_20"
        limit = float(CRITERIA["C4_zigzag_20_20"]["first_overshoot_max_deg"])
        chk = _check(f"first_overshoot_deg_{rudder_deg:g}_{rudder_deg:g}",
                     float(first), limit, "deg",
                     margin_frac=BORDERLINE_MARGIN_FRAC)
        out = {"criterion": crit, "rudder_deg": rudder_deg,
               "checks": [chk], "passed": chk["passed"]}
    out["completed"] = bool(metrics.get("completed", False))
    out["second_overshoot_deg"] = metrics.get("second_overshoot_deg")
    # an incomplete manoeuvre cannot be certified, whatever the members said
    if not out["completed"]:
        out["passed"] = False
        out["note"] = "manoeuvre did not complete within t_max"
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def run_imo_compliance(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    coefficient_sets: Optional[Dict[str, M.MMGCoefficients]] = None,
) -> Dict[str, object]:
    """Full MSC.137(76) audit, repeated over one or more coefficient sets.

    ``speed`` is in m/s (7.717 m/s = 15 kn service speed of the reference hull).
    """
    sets = coefficient_sets or {
        "kvlcc2_reference": M.KVLCC2_REFERENCE,
        "slender_body": M.slender_body_coefficients(vessel),
    }
    results: Dict[str, object] = {}
    for name, cset in sets.items():
        turning = M.simulate_turning_circle(vessel, coef=cset, speed=speed,
                                            rudder_deg=35.0, t_max=2000.0)
        zz10 = M.simulate_zigzag(vessel, coef=cset, speed=speed, rudder_deg=10.0,
                                 heading_deg=10.0, t_max=2000.0)
        zz20 = M.simulate_zigzag(vessel, coef=cset, speed=speed, rudder_deg=20.0,
                                 heading_deg=20.0, t_max=3000.0)
        c1 = check_turning_circle(turning.metrics, vessel.L)
        c2 = check_initial_turning(zz10.history, vessel.L)
        c3 = check_zigzag(zz10.metrics, vessel.L, speed, rudder_deg=10.0)
        c4 = check_zigzag(zz20.metrics, vessel.L, speed, rudder_deg=20.0)
        criteria = {c1["criterion"]: c1, c2["criterion"]: c2,
                    c3["criterion"]: c3, c4["criterion"]: c4}
        results[name] = {
            "criteria": criteria,
            "passed": all(v["passed"] for v in criteria.values()),
            "failing": [k for k, v in criteria.items() if not v["passed"]],
            "raw_metrics": {
                "turning_circle": turning.metrics,
                "zigzag_10_10": zz10.metrics,
                "zigzag_20_20": zz20.metrics,
                "initial_turning_time_s": c2["time_to_heading_s"],
                "initial_turning_track_over_L": c2["checks"][0]["value"],
            },
        }
    # A verdict is robust only if every coefficient set agrees.
    verdicts = {k: bool(v["passed"]) for k, v in results.items()}
    robust = len(set(verdicts.values())) == 1
    return {
        "standard": IMO_RESOLUTION,
        "provenance": PROVENANCE,
        "vessel": {"name": vessel.name, "L_m": vessel.L,
                   "particulars": vessel.particulars()},
        "speed_ms": speed,
        "speed_kn": speed / D.KNOT,
        "LV_s": length_speed_ratio_s(vessel.L, speed),
        "coefficient_sets": list(sets.keys()),
        "results": results,
        "verdicts": verdicts,
        "coefficient_set_robust": robust,
        "overall_passed": all(verdicts.values()),
        "absolute_accuracy_claim": "WITHHELD",
        "withheld": [
            "No comparison against model tests / sea trials: the worktree has no "
            "EFD manoeuvring data and general network access is blocked. Passing "
            "MSC.137(76) certifies the model's own output against published "
            "numeric limits, not the real ship.",
            "C4 (20/20 zigzag) is reported as FAILED for this coefficient set; "
            "that is a model-capability finding, not a claim that a real VLCC "
            "of these particulars would fail the standard.",
            "Stopping ability (MSC.137(76) C5) and course keeping (C6) are not "
            "implemented in mmg.py and therefore not audited here.",
        ],
    }