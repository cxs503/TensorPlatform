"""Verification tests: exact box hydrostatics reference + audited engine snapshot.

Mission m1597.  The reference module :mod:`services.box_hydrostatics` is the
measuring stick used to audit the platform stability engine
(``POST /api/v1/calculations/stability``).  If the measuring stick is wrong the
whole audit is worthless, so these tests pin it against closed-form results:

* ``KB = T/2`` and ``BM = B²/(12·T)`` for a rectangular barge;
* the polygon-clip solver reproduces the wall-sided identity
  ``GZ = (GM + ½·BM·tan²φ)·sinφ`` to 1e-9 m inside its validity window;
* the GZ slope at the origin equals GM (metacentric definition);
* the immersed-area solver conserves displacement at every heel angle;
* Simpson areas match a fine trapezoid reference;
* the frozen engine snapshot still exhibits the documented defects
  (KB/T = 2/3 at Cb = 1, 15° branch jump, 60° vanishing-angle censoring) — this
  test is the tripwire that forces a re-audit once the engine is fixed.
"""
from __future__ import annotations

import math

import pytest

from services import box_hydrostatics as ref
from services import stability_engine_audit_snapshot as eng

L, B, T, D, KG = 60.0, 12.0, 3.0, 6.0, 4.7


# ---------------------------------------------------------------------------
# Reference hydrostatics
# ---------------------------------------------------------------------------

def test_box_hydrostatics_closed_form():
    h = ref.box_hydrostatics(L, B, T)
    assert h["displacement_m3"] == pytest.approx(L * B * T)
    assert h["kb_m"] == pytest.approx(T / 2.0)
    assert h["bm_m"] == pytest.approx(B ** 2 / (12.0 * T))
    assert h["km_m"] == pytest.approx(T / 2.0 + B ** 2 / (12.0 * T))


def test_wallsided_regime_limits():
    bilge, deck = ref.wallsided_regime_limits_deg(B, T, D)
    assert bilge == pytest.approx(math.degrees(math.atan2(2 * T, B)))
    assert deck == pytest.approx(math.degrees(math.atan2(D - T, B / 2)))
    assert min(bilge, deck) == pytest.approx(26.565, abs=1e-3)


def test_reference_matches_wallsided_identity_inside_regime():
    """Inside the wall-sided window the polygon solver must equal the closed form."""
    h = ref.box_hydrostatics(L, B, T)
    gm = h["kb_m"] + h["bm_m"] - KG
    limit = min(ref.wallsided_regime_limits_deg(B, T, D))
    for i in range(1, int(2 * limit) + 1):
        phi = 0.5 * i
        assert ref.box_gz(phi, B, T, D, KG) == pytest.approx(
            ref.wallsided_gz(phi, gm, h["bm_m"]), abs=1e-9)


def test_gz_slope_at_origin_equals_gm():
    h = ref.box_hydrostatics(L, B, T)
    gm = h["kb_m"] + h["bm_m"] - KG
    eps = 1e-6
    slope = (ref.box_gz(eps, B, T, D, KG) - ref.box_gz(-eps, B, T, D, KG)) / (2 * math.radians(eps))
    assert slope == pytest.approx(gm, rel=1e-6)


def test_gz_is_antisymmetric():
    for phi in (2.0, 17.0, 45.0, 80.0):
        assert ref.box_gz(-phi, B, T, D, KG) == pytest.approx(-ref.box_gz(phi, B, T, D, KG), abs=1e-12)


def test_immersed_area_conserves_displacement():
    for phi in (0.0, 5.0, 12.5, 26.565, 40.0, 70.0, 110.0):
        area, _, _ = ref.immersed_section(B, T, D, phi)
        assert area == pytest.approx(B * T, rel=1e-12)
        assert 0.0 < area < B * D


def test_upright_centroid_is_half_draft():
    area, yb, zb = ref.immersed_section(B, T, D, 0.0)
    assert area == pytest.approx(B * T, rel=1e-13)
    assert yb == pytest.approx(0.0, abs=1e-12)
    assert zb == pytest.approx(T / 2.0, rel=1e-10)


def test_simpson_area_matches_fine_trapezoid():
    step = 0.25
    angles = [step * i for i in range(0, int(121 / step) + 1)]
    gz = ref.gz_curve(angles, B, T, D, KG)
    for lo, hi in ((0.0, 30.0), (30.0, 40.0), (0.0, 40.0)):
        simpson = ref.gz_area(angles, gz, lo, hi)
        idx = [(lo <= a <= hi) for a in angles]
        a_sel = [a for a, k in zip(angles, idx) if k]
        g_sel = [g for g, k in zip(gz, idx) if k]
        trap = sum(0.5 * (g_sel[i] + g_sel[i + 1]) * math.radians(a_sel[i + 1] - a_sel[i])
                   for i in range(len(g_sel) - 1))
        # Simpson is O(h⁴) vs the O(h²) trapezoid on the same 0.25° grid;
        # 1e-4 relative agreement is the discretisation floor, not a formula gap.
        assert simpson == pytest.approx(trap, rel=1e-4)


def test_vanishing_angle_is_a_zero_of_the_exact_curve():
    v = ref.vanishing_angle_deg(B, T, D, KG)
    assert 0.0 < v < 120.0
    assert ref.box_gz(v, B, T, D, KG) == pytest.approx(0.0, abs=1e-6)
    assert ref.box_gz(v - 1.0, B, T, D, KG) > 0.0


def test_vanishing_angle_censoring_is_reported_as_cap():
    """A very stiff barge never capsizes below the cap → the cap is returned."""
    assert ref.vanishing_angle_deg(18.0, 2.0, 20.0, 3.0, max_angle=120.0) == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# Audited engine snapshot still carries the documented defects
# ---------------------------------------------------------------------------

def test_engine_kb_overestimates_box_by_one_third():
    """Engine KB = T(1 − Cb/3); the exact box value is T/2."""
    assert eng.engine_kb(3.0, 1.0) == pytest.approx(2.0)
    assert eng.engine_kb(3.0, 1.0) / ref.box_hydrostatics(L, B, T)["kb_m"] - 1.0 == pytest.approx(1 / 3, abs=1e-12)


def test_engine_bm_uses_cwp_squared():
    """Engine inflates the waterplane inertia by one extra Cwp factor."""
    for cb in (0.7, 0.85, 1.0):
        cwp = 0.75 + 0.25 * cb
        bm_engine = eng.engine_bm(B, T, cb)
        bm_ref = cwp * B ** 2 / (12.0 * T * cb)
        assert bm_engine / bm_ref == pytest.approx(cwp, rel=1e-12)


def test_engine_gz_branch_discontinuity_at_15deg():
    gm, bm = 0.8, 4.0
    jump = eng.engine_gz(15.0, gm, bm) - eng.engine_gz(14.999, gm, bm)
    assert jump > 0.03  # ≈ 0.5·BM·sin³15° ≈ 0.035 m


def test_engine_gz_underestimates_large_heel_vs_wallsided():
    gm, bm = 0.8, 4.0
    for phi in (30.0, 40.0, 50.0):
        assert eng.engine_gz(phi, gm, bm) < ref.wallsided_gz(phi, gm, bm)


def test_engine_vanishing_angle_is_grid_capped():
    kb, bm = eng.engine_kb(T, 1.0), eng.engine_bm(B, T, 1.0)
    for kg in (4.0, 4.7, 5.2):
        curve = eng.engine_curve(kg, kb, bm)
        assert curve["gm"] > 0.0
        assert curve["vanishing_angle"] == pytest.approx(60.0)


def test_engine_reproduces_deployed_endpoint_low_angle_anchor():
    """Case A: the deployed endpoint returns KB=2.0 m, BM=4.0 m, GM=1.3 m and
    GZ(40°)=GZmax=1.367 m.  The snapshot reproduces exactly those anchors."""
    kb, bm = eng.engine_kb(T, 1.0), eng.engine_bm(B, T, 1.0)
    assert kb == pytest.approx(2.0)
    assert bm == pytest.approx(4.0)
    curve = eng.engine_curve(4.7, kb, bm)
    assert curve["gm"] == pytest.approx(1.3)
    assert eng.engine_gz(40.0, curve["gm"], bm) == pytest.approx(1.367, abs=5e-3)


def test_snapshot_high_angle_branch_is_not_reproduced():
    """Honest scope limit: the deployed high-angle branch is a different build.

    The endpoint reports GZmax=1.367 m at 40° and then a *decreasing* curve,
    while the transcribed formula keeps growing to 2.425 m at 60°; therefore the
    deployed 60° sample (and the areas that depend on it) cannot be reproduced
    from the shipped source.  Only the ≤40° anchors are verified here.
    """
    kb, bm = eng.engine_kb(T, 1.0), eng.engine_bm(B, T, 1.0)
    curve = eng.engine_curve(4.7, kb, bm)
    assert eng.engine_gz(60.0, curve["gm"], bm) == pytest.approx(2.4249, abs=1e-3)
    assert eng.engine_gz(60.0, curve["gm"], bm) > 1.367