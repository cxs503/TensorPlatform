"""IMO MSC.137(76) minimum-manoeuvrability audit of the MMG manoeuvring model.

Verification-debt closure (tp-ver-8).  ``validation.py`` for mission W3-N08
verifies the manoeuvring model *code-to-code* only and honestly withholds an
absolute-accuracy claim.  This suite adds the second, standards-based tier: the
statutory numeric limits of IMO Res. MSC.137(76).

Run with::

    python -m pytest tests/test_maneuvering_imo_msc137_76.py -q
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from physics.ship_env_maneuvering import data as D
from physics.ship_env_maneuvering import imo_criteria as I
from physics.ship_env_maneuvering import mmg as M

VESSEL = D.VESSEL_VLCC
SPEED = 7.717                      # m/s, 15 kn service speed of the reference hull


# ------------------------------------------------------------ the standard ---
def test_criteria_constants_match_the_resolution():
    """The limits are transcription, not tuning: lock them with the citation."""
    c1 = I.CRITERIA["C1_turning_circle"]
    assert c1["advance_over_L_max"] == 4.5
    assert c1["tactical_diameter_over_L_max"] == 5.0
    assert c1["rudder_deg"] == 35.0

    c2 = I.CRITERIA["C2_initial_turning"]
    assert c2["heading_change_deg"] == 10.0
    assert c2["track_distance_over_L_max"] == 2.5

    c3 = I.CRITERIA["C3_zigzag_10_10"]
    assert c3["first_overshoot_max_deg_low_LV"] == 10.0
    assert c3["first_overshoot_max_deg_high_LV"] == 20.0
    assert c3["LV_split_s"] == 2.5

    assert I.CRITERIA["C4_zigzag_20_20"]["first_overshoot_max_deg"] == 25.0
    assert "MSC.137(76)" in I.IMO_RESOLUTION


# ------------------------------------------------------------ L/V branch ----
def test_LV_ratio_uses_metres_over_metres_per_second():
    assert I.length_speed_ratio_s(320.0, 7.717) == pytest.approx(41.467, abs=1e-3)


def test_overshoot_branch_for_a_long_slow_ship_is_the_20deg_branch():
    b = I.overshoot_limit_deg(320.0, 7.717)          # L/V = 41.5 s >> 2.5 s
    assert b["branch"] == "high_LV"
    assert b["limit_deg"] == 20.0


def test_overshoot_branch_for_a_short_fast_ship_is_the_10deg_branch():
    b = I.overshoot_limit_deg(40.0, 20.0)            # L/V = 2.0 s < 2.5 s
    assert b["branch"] == "low_LV"
    assert b["limit_deg"] == 10.0


def test_overshoot_branch_is_continuous_at_the_split():
    """At L/V exactly 2.5 s the high-LV (20 deg) branch applies."""
    b = I.overshoot_limit_deg(50.0, 20.0)            # L/V = 2.5 s exactly
    assert b["LV_s"] == pytest.approx(2.5)
    assert b["branch"] == "high_LV"


# ------------------------------------------------------- check primitives ---
def test_check_helper_treats_value_equal_to_limit_as_a_pass():
    """A value exactly at the limit passes (the limit is inclusive) but must be
    flagged borderline - it is a pass with zero margin."""
    c = I._check("x", 5.0, 5.0, "L", margin_frac=I.BORDERLINE_MARGIN_FRAC)
    assert c["passed"] and c["margin"] == 0.0 and c["borderline"]


def test_check_helper_fails_a_value_above_the_limit():
    c = I._check("x", 5.01, 5.0, "L")
    assert not c["passed"] and c["margin"] < 0.0
    assert c["margin_pct"] == pytest.approx(-0.2, abs=1e-9)


def test_check_helper_flags_a_borderline_pass():
    c = I._check("x", 4.99, 5.0, "L", margin_frac=0.01)   # margin = 0.01*5
    assert c["passed"] and c["borderline"]


def test_check_helper_never_certifies_a_non_finite_metric():
    """A diverged / non-converged simulation must fail, not silently pass."""
    for bad in (float("nan"), float("inf")):
        c = I._check("x", bad, 5.0, "L")
        assert not c["passed"]
        assert "not finite" in c["note"]


# --------------------------------------------------------- criterion units --
def test_turning_circle_check_passes_a_compliant_metric_set():
    res = I.check_turning_circle(
        {"advance_over_L": 3.31, "tactical_diameter_over_L": 2.88}, 320.0)
    assert res["passed"]
    assert {c["check"] for c in res["checks"]} == {
        "advance_over_L", "tactical_diameter_over_L"}


def test_turning_circle_check_fails_an_over_diameter():
    res = I.check_turning_circle(
        {"advance_over_L": 3.31, "tactical_diameter_over_L": 5.6}, 320.0)
    assert not res["passed"]


def test_initial_turning_uses_track_distance_not_time():
    """C2 measures distance run; a synthetic straight-track history pins it."""
    L = 100.0
    hist = {"t": [0.0, 10.0, 20.0], "x": [0.0, 200.0, 240.0],
            "y": [0.0, 0.0, 0.0], "psi_deg": [0.0, 5.0, 10.0]}
    res = I.check_initial_turning(hist, L)           # 240 m / 100 m = 2.4 L
    assert res["passed"]
    assert res["checks"][0]["value"] == pytest.approx(2.4)
    assert res["time_to_heading_s"] == pytest.approx(20.0)


def test_initial_turning_fails_when_the_heading_takes_too_long():
    L = 100.0
    hist = {"t": [0.0, 30.0], "x": [0.0, 300.0], "y": [0.0, 0.0],
            "psi_deg": [0.0, 10.0]}                      # 3.0 L > 2.5 L
    assert not I.check_initial_turning(hist, L)["passed"]


def test_incomplete_zigzag_can_never_be_certified():
    res = I.check_zigzag({"first_overshoot_deg": 5.0, "completed": False},
                         320.0, SPEED, rudder_deg=20.0)
    assert not res["passed"]
    assert "did not complete" in res["note"]


# ------------------------------------------------------------ live model ----
@pytest.fixture(scope="module")
def audit():
    return I.run_imo_compliance(vessel=VESSEL, speed=SPEED)


def test_audit_reports_the_standard_and_withholds_absolute_accuracy(audit):
    assert audit["standard"] == I.IMO_RESOLUTION
    assert audit["absolute_accuracy_claim"] == "WITHHELD"
    assert audit["withheld"], "withheld reasons must be enumerated"


def test_audit_covers_all_four_implemented_criteria(audit):
    for name in ("kvlcc2_reference", "slender_body"):
        assert set(audit["results"][name]["criteria"]) == {
            "C1_turning_circle", "C2_initial_turning",
            "C3_zigzag_10_10", "C4_zigzag_20_20"}


def test_turning_circle_and_initial_turning_are_imo_compliant(audit):
    for name, res in audit["results"].items():
        assert res["criteria"]["C1_turning_circle"]["passed"], name
        assert res["criteria"]["C2_initial_turning"]["passed"], name


def test_zigzag_10_10_is_compliant_on_the_correct_LV_branch(audit):
    for name, res in audit["results"].items():
        c3 = res["criteria"]["C3_zigzag_10_10"]
        assert c3["branch"] == "high_LV", name
        assert c3["checks"][0]["passed"], name


def test_zigzag_20_20_exceeds_the_imo_25deg_limit(audit):
    """Documented finding: the uncalibrated model over-predicts the 20/20
    first overshoot.  Both coefficient sets fail, so the verdict is robust."""
    for name, res in audit["results"].items():
        c4 = res["criteria"]["C4_zigzag_20_20"]
        assert c4["checks"][0]["value"] > 25.0, name
        assert not c4["passed"], name
        assert res["failing"] == ["C4_zigzag_20_20"], name
    assert audit["coefficient_set_robust"] is True
    assert audit["overall_passed"] is False


def test_audit_verdict_is_not_hard_coded(audit):
    """+20 % on the sway/yaw stiffness pair must move the verdict: the tactical
    diameter grows past the 5 L limit, so C1 flips to FAIL.  A checker that
    always returned PASS would survive the audit above but not this one, and
    the flip also quantifies how thin the C1 margin is against the uncalibrated
    derivatives."""
    base = M.KVLCC2_REFERENCE.as_dict()
    stiff = M.MMGCoefficients(**{**base, "name": "stiff_plus20pct",
                                 "Y_v": base["Y_v"] * 1.2,
                                 "N_r": base["N_r"] * 1.2})
    res = I.run_imo_compliance(vessel=VESSEL, speed=SPEED,
                               coefficient_sets={"stiff_plus20pct": stiff})
    c1 = res["results"]["stiff_plus20pct"]["criteria"]["C1_turning_circle"]
    td = c1["checks"][1]["value"]
    assert td > 5.0, f"expected TD/L > 5, got {td}"
    assert not c1["passed"]
    assert "C1_turning_circle" in res["results"]["stiff_plus20pct"]["failing"]
    baseline = audit["results"]["kvlcc2_reference"]["criteria"][
        "C1_turning_circle"]["checks"][1]["value"]
    assert td > baseline


def test_audit_is_reproducible(audit):
    again = I.run_imo_compliance(vessel=VESSEL, speed=SPEED)
    for name in audit["results"]:
        assert (again["results"][name]["raw_metrics"]["turning_circle"]
                ["tactical_diameter_over_L"]
                == audit["results"][name]["raw_metrics"]["turning_circle"]
                ["tactical_diameter_over_L"])