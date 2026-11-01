"""Tests for the stability input-determinacy audit (tools/verifier/stability_indeterminacy.py).

Covers:
  T1 R1<->R2 agreement: box barge GZ vs wall-sided closed form (rel < 1e-9)
  T2 hydrostatics anchors: box Cb=1 -> KB=T/2, BM=B^2/(12T), deck edge atan(2(D-T)/B)
  T3 Cb construction: mean_x(s) * c_s == cb for every family member (rel < 1e-12)
  T4 knot invariance: extra collinear section knots change nothing (rel < 1e-9)
  T5 volume conservation: solved floatation reproduces the design displacement
  T6 indeterminacy is real: one payload -> spread in GM/GZ_max/area_0_40 above the
     15% acceptance line, and a positive minimax floor
  T7 IMO verdict flip inside a single payload (case B)
  T8 fully determined case (Cb=1) reproduces the #1150 reference GM = 1.1667 m
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stability_indeterminacy import (  # noqa: E402
    Hull,
    audit,
    minimax_floor,
    reference_validation,
    rel_err,
    section_area_coefficient,
    wall_sided_gz,
)


# --- T1 -------------------------------------------------------------------
def test_box_gz_matches_wall_sided_closed_form():
    hull = Hull("box-Cb1", cb=1.0, kg=7.0)
    gm, bm = hull.gm(), hull.bm()
    assert hull.deck_edge_angle_deg() > 21.0
    for a in (2.0, 5.0, 10.0, 15.0, 20.0):
        num = hull.gz(a)
        ref = wall_sided_gz(a, gm, bm)
        assert abs(num - ref) / abs(ref) < 1e-9, (a, num, ref)


# --- T2 -------------------------------------------------------------------
def test_box_hydrostatic_anchors():
    hull = Hull("box-Cb1", cb=1.0, kg=7.0)
    assert hull.kb() == pytest.approx(hull.T / 2.0, rel=1e-9)
    assert hull.bm() == pytest.approx(hull.B ** 2 / (12.0 * hull.T), rel=1e-9)
    deck = math.degrees(math.atan(2.0 * (hull.D - hull.T) / hull.B))
    assert hull.deck_edge_angle_deg() == pytest.approx(deck, rel=1e-6)


# --- T3 -------------------------------------------------------------------
def test_cb_is_reproduced_for_every_member():
    """Cb = mean_x(s) * c_s must come out of the construction, per member."""
    for cb, kg in ((0.5, 4.5), (0.7, 6.0)):
        res = audit(cb, kg)
        assert res["members"]
        for m in res["members"]:
            hull = Hull("h", shape=m["shape"], profile=m["profile"], cb=cb, kg=kg)
            n = hull.n_stations
            mean_s = sum((0.5 if i in (0, n) else 1.0) * hull.s_at(i / n)
                         for i in range(n + 1)) / n
            assert mean_s * hull.cs == pytest.approx(cb, rel=1e-12)


# --- T4 -------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["box", "vee", "trapezoid", "knuckle"])
def test_extra_collinear_knots_change_nothing(shape):
    a = Hull("h", shape=shape, cb=0.5, kg=4.5)
    b = Hull("h", shape=shape, cb=0.5, kg=4.5, extra_knots=17)
    for ang in (0.0, 10.0, 25.0, 40.0):
        assert a.gz(ang) == pytest.approx(b.gz(ang), abs=1e-9)
    assert a.gm() == pytest.approx(b.gm(), rel=1e-12)


# --- T5 -------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["box", "vee", "trapezoid", "knuckle"])
def test_volume_conservation(shape):
    hull = Hull("h", shape=shape, cb=0.5, kg=4.5)
    for ang in (0.0, 12.0, 30.0):
        d, _, _ = hull.solve_floatation(ang)
        v, _, _ = hull._volume_and_centroid(math.radians(ang), d)
        assert v == pytest.approx(hull.displacement_volume, rel=1e-8)


# --- T6 -------------------------------------------------------------------
def test_indeterminacy_exceeds_acceptance_band():
    res = audit(0.7, 6.0)
    spread = res["spread"]
    gm_span = spread["gm_m"]["max"] - spread["gm_m"]["min"]
    assert gm_span > 0.15 * spread["gm_m"]["max"]  # way beyond 15%
    for key in ("gm_m", "gz_max_m", "area_0_40_mrad"):
        floor = res["minimax_floor"][key]["worst_case_rel_err"]
        assert floor > 0.15, (key, floor)


def test_minimax_floor_is_a_true_lower_bound():
    vals = [0.5, 1.0, 3.0]
    best = minimax_floor(vals, n_grid=4001)
    # any answer must be at least as bad as the reported floor
    for q in (0.1, 0.5, 1.0, 2.0, 3.0, 5.0):
        assert max(rel_err(v, q) for v in vals) >= best["worst_case_rel_err"] - 1e-12


# --- T7 -------------------------------------------------------------------
def test_imo_verdict_flips_within_one_payload():
    res = audit(0.7, 6.0)
    assert res["imo_verdict"]["n_pass"] > 0
    assert res["imo_verdict"]["n_fail"] > 0


# --- T8 -------------------------------------------------------------------
def test_fully_determined_case_reproduces_1150_reference():
    res = audit(1.0, 7.0)
    assert len(res["members"]) == 1
    m = res["members"][0]
    assert m["gm_m"] == pytest.approx(1.1666666667, abs=1e-6)
    assert m["kb_m"] == pytest.approx(4.0, abs=1e-6)
    assert m["bm_m"] == pytest.approx(4.1666666667, abs=1e-6)
    assert m["area_0_40_mrad"] == pytest.approx(0.3194, abs=2e-3)


def test_reference_validation_report():
    rv = reference_validation()
    assert rv["max_rel_err"] < 1e-9
    assert rv["gm_m"] == pytest.approx(1.1666666667, abs=1e-6)


def test_section_coefficients():
    assert section_area_coefficient("box") == pytest.approx(1.0, rel=1e-12)
    assert section_area_coefficient("vee") == pytest.approx(0.5, rel=1e-12)
    assert section_area_coefficient("trapezoid") == pytest.approx(0.75, rel=1e-12)
    assert section_area_coefficient("knuckle") == pytest.approx(0.75, rel=1e-12)