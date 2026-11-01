"""
Tests for the independent stability reference and the IMO-gate audit harness.

These tests encode the *reference side* of the verifier (they must pass without
any network access):

  * the exact box-barge section integration must reproduce the wall-sided closed
    form below the deck-edge immersion angle  -> proves the reference chain
  * the closed-form area antiderivative must differentiate back to GZ
  * the IS Code 2008 threshold table must hold the values transcribed from the
    Code (regression guard: a "tidy-up" of the numbers is an audit failure)
  * the criterion evaluator must not confuse GZ at 30 deg with GZ_max
  * the audit summary must be able to flag a false pass
"""
from __future__ import annotations

import math

import pytest

from tools.verifier.box_barge_reference import BoxBarge, wall_sided_check
from tools.verifier.stability_reference import (
    IMO_IS_CODE_2008,
    imo_criteria_from_curve,
    wall_sided_area,
    wall_sided_gz,
)


BARGE = BoxBarge(beam=20.0, draft=8.0, depth=12.0, kg=7.0)


def test_box_barge_initial_stability_matches_closed_form():
    assert BARGE.kb == pytest.approx(4.0)
    assert BARGE.bm == pytest.approx(20.0 ** 2 / (12 * 8.0))
    assert BARGE.gm == pytest.approx(4.0 + 400.0 / 96.0 - 7.0)
    assert BARGE.deck_edge_angle == pytest.approx(math.degrees(math.atan2(8.0, 20.0)))


@pytest.mark.parametrize("phi", [0.0, 2.5, 5.0, 10.0, 15.0, 20.0, 21.0])
def test_section_integration_reproduces_wall_sided_closed_form(phi):
    """Below deck-edge immersion the two independent references must agree."""
    assert phi < BARGE.deck_edge_angle
    exact = BARGE.gz(phi)
    closed = wall_sided_check(BARGE, phi)
    assert exact == pytest.approx(closed, rel=1e-9, abs=1e-12)


def test_wall_sided_antiderivative_differentiates_back_to_gz():
    gm, bm = BARGE.gm, BARGE.bm
    h = 1e-6
    for phi in (5.0, 20.0, 35.0, 50.0):
        d = (wall_sided_area(0.0, phi + h, gm, bm) - wall_sided_area(0.0, phi - h, gm, bm)) / (
            2 * math.radians(h)
        )
        assert d == pytest.approx(wall_sided_gz(phi, gm, bm), rel=1e-6)


def test_area_below_deck_edge_matches_closed_form_integral():
    """Section-integration area (Simpson) vs exact antiderivative area."""
    exact_num = BARGE.area(0.0, 15.0, step=0.05)
    closed = wall_sided_area(0.0, 15.0, BARGE.gm, BARGE.bm)
    assert exact_num == pytest.approx(closed, rel=2e-4)


def test_deck_immersion_reduces_righting_arm():
    """Above the deck edge the exact GZ must fall below the wall-sided curve."""
    phi = 35.0
    assert BARGE.gz(phi) < wall_sided_check(BARGE, phi)


def test_imo_thresholds_transcribed_from_is_code_2008():
    expected = {
        "area_0_30": 0.055,
        "area_0_40": 0.090,
        "area_30_40": 0.030,
        "gz_at_30": 0.200,
        "gm0": 0.150,
        "angle_gz_max": 25.0,
    }
    got = {k: IMO_IS_CODE_2008[k]["threshold"] for k in expected}
    assert got == expected
    # the criteria the Code does NOT contain must not be smuggled in here
    assert "vanishing_angle" not in IMO_IS_CODE_2008


def test_criterion_uses_gz_at_30_not_gz_max():
    """A curve peaking late with a small 30-deg arm must fail 2.2.1.4."""
    curve = [
        {"angle": a, "gz": g}
        for a, g in [(0, 0.0), (10, 0.08), (20, 0.13), (30, 0.15), (40, 0.20),
                     (50, 0.25), (60, 0.30), (70, 0.20), (80, 0.05), (90, 0.0)]
    ]
    res = imo_criteria_from_curve(curve, gm_m=0.5)
    assert res["gz_at_30"]["value"] == pytest.approx(0.15)
    assert res["gz_at_30"]["met"] is False          # 0.15 < 0.20
    assert res["_gz_max"]["angle"] == pytest.approx(60.0)  # GZ_max would have passed


def test_angle_of_max_gz_is_an_angle_not_a_height():
    res = imo_criteria_from_curve(BARGE.gz_curve(step=5.0, max_angle=90.0), BARGE.gm)
    assert 25.0 <= res["angle_gz_max"]["value"] <= 90.0
    assert res["angle_gz_max"]["met"] is True


def test_area_threshold_error_is_detected():
    """0.070 m·rad passes the platform's 0.055 gate but fails IS Code (0.090)."""
    curve = [{"angle": 0, "gz": 0.0}, {"angle": 40, "gz": 0.0}]
    res = imo_criteria_from_curve(curve, gm_m=1.0)
    assert res["area_0_40"]["met"] is False
    assert 0.070 >= 0.055 and 0.070 < IMO_IS_CODE_2008["area_0_40"]["threshold"]


def test_audit_summary_flags_false_pass():
    from tools.verifier.audit_stability_imo import summarise

    fake = {
        "case": {"id": "synthetic"},
        "deviations": {
            "A": {"gm": {"value": 2.5, "ref": 1.0, "rel_err": 1.5}},
            "B": {"gm": {"value": 1.05, "ref": 1.0, "rel_err": 0.05}},
        },
        "gate_A": {"agreement": False},
        "cross_endpoint_diff": {"gm": {"A": 2.5, "B": 1.05, "rel_diff": 0.58}},
    }
    s = summarise([fake])
    assert s["verdict"] == "FAIL"
    assert any(f["quantity"] == "gm" and f["endpoint"] == "A" for f in s["failures"])
    assert any(f["quantity"] == "IMO gate verdict" for f in s["failures"])