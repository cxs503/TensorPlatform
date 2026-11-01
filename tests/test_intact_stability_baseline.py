"""Verification tests for the exact box-barge intact-stability baseline (QR-INTACT).

These tests validate the *baseline* itself, not the ship platform: the baseline is the
measuring instrument of the audit, so it has to be shown correct against routes that do
not share its numerics (textbook wall-sided closed form, volume conservation, GM slope,
symmetry).  A measuring instrument with a wrong zero silently blesses a wrong platform.

Run:  python3 -m pytest tests/test_intact_stability_baseline.py -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VERIFY = _ROOT / "tools" / "verify"
if str(_VERIFY) not in sys.path:
    sys.path.insert(0, str(_VERIFY))

import intact_stability_baseline as bl  # noqa: E402

# A rectangular barge: the only hull with a closed-form GZ, hence the audit vehicle.
BARGE = {"B": 20.0, "D": 10.0, "T": 6.0, "KG": 5.0}


# ---------------------------------------------------------------------------
# 1. Hydrostatics: closed forms
# ---------------------------------------------------------------------------
def test_box_hydrostatics_closed_form():
    assert bl.box_kb(6.0) == pytest.approx(3.0)
    assert bl.box_bm(20.0, 6.0) == pytest.approx(400.0 / 72.0)
    assert bl.box_gm(20.0, 6.0, 5.0) == pytest.approx(3.0 + 400.0 / 72.0 - 5.0)


def test_flooding_and_bilge_angles():
    # deck edge dips at atan((D-T)/(B/2)); bilge leaves the water at atan(2T/B)
    assert bl.flooding_angle_deg(20.0, 10.0, 6.0) == pytest.approx(math.degrees(math.atan(4.0 / 10.0)))
    assert bl.bilge_emergence_angle_deg(20.0, 6.0) == pytest.approx(math.degrees(math.atan(12.0 / 20.0)))
    assert bl.wall_sided_valid_limit_deg(20.0, 10.0, 6.0) == pytest.approx(
        bl.flooding_angle_deg(20.0, 10.0, 6.0))


# ---------------------------------------------------------------------------
# 2. Independent-route agreement: polygon baseline vs wall-sided closed form
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phi", [0.5, 2.0, 5.0, 10.0, 15.0, 20.0])
def test_exact_matches_wall_sided_within_validity(phi):
    """Two independent derivations must agree to machine precision while wall-sided."""
    B, D, T, KG = BARGE["B"], BARGE["D"], BARGE["T"], BARGE["KG"]
    assert phi <= bl.wall_sided_valid_limit_deg(B, D, T)
    exact = bl.box_gz(B, D, T, KG, phi)
    closed = bl.wall_sided_gz(phi, bl.box_gm(B, T, KG), bl.box_bm(B, T))
    assert exact == pytest.approx(closed, abs=1e-12)


@pytest.mark.parametrize("phi", [30.0, 45.0, 60.0, 80.0])
def test_wall_sided_deviates_beyond_validity_as_expected(phi):
    """Beyond deck immersion the closed form must be *rejected* by the exact model.

    Guards against a baseline that is really just the closed form in disguise: the two
    routes must disagree once the hull is no longer wall-sided.
    """
    B, D, T, KG = BARGE["B"], BARGE["D"], BARGE["T"], BARGE["KG"]
    exact = bl.box_gz(B, D, T, KG, phi)
    closed = bl.wall_sided_gz(phi, bl.box_gm(B, T, KG), bl.box_bm(B, T))
    assert abs(exact - closed) / abs(closed) > 0.02


# ---------------------------------------------------------------------------
# 3. Physical invariants of the section solver
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phi", [0.0, 10.0, 30.0, 60.0, 89.0])
def test_volume_conservation(phi):
    """Displaced section area must stay B*T at every heel angle (no trim, no sinkage)."""
    B, D, T = BARGE["B"], BARGE["D"], BARGE["T"]
    area, _c = bl.box_section_submerged(B, D, T, phi)
    assert area == pytest.approx(B * T, rel=1e-9)


def test_gz_zero_at_upright_and_antisymmetric():
    B, D, T, KG = BARGE["B"], BARGE["D"], BARGE["T"], BARGE["KG"]
    assert bl.box_gz(B, D, T, KG, 0.0) == pytest.approx(0.0, abs=1e-12)
    for phi in (10.0, 25.0, 40.0):
        assert bl.box_gz(B, D, T, KG, phi) == pytest.approx(-bl.box_gz(B, D, T, KG, -phi), abs=1e-9)


def test_initial_slope_equals_gm():
    """dGZ/dphi|_0 = GM is the definition of the metacentric height."""
    B, D, T, KG = BARGE["B"], BARGE["D"], BARGE["T"], BARGE["KG"]
    h = 0.05
    slope = (bl.box_gz(B, D, T, KG, h) - bl.box_gz(B, D, T, KG, -h)) / (2.0 * math.radians(h))
    assert slope == pytest.approx(bl.box_gm(B, T, KG), rel=1e-4)


def test_gz_increases_towards_gzmax_then_falls():
    B, D, T, KG = BARGE["B"], BARGE["D"], BARGE["T"], BARGE["KG"]
    b = bl.barge_baseline(B, D, T, KG, open_top=False)
    gz = b["gz"]
    k = gz.index(max(gz))
    assert k > 0 and k < len(gz) - 1
    assert all(gz[i + 1] > gz[i] for i in range(k))
    assert all(gz[i + 1] < gz[i] for i in range(k, len(gz) - 1))


def test_gzmax_grows_with_freeboard():
    """More freeboard -> larger admissible righting lever (monotone physics)."""
    B, T, KG = BARGE["B"], BARGE["T"], BARGE["KG"]
    vals = [bl.barge_baseline(B, D, T, KG, open_top=True)["gz_max"] for D in (6.3, 8.0, 10.0, 14.0, 20.0)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    ends = [bl.barge_baseline(B, D, T, KG, open_top=True)["vanishing_angle"] for D in (6.3, 10.0, 20.0)]
    assert all(b > a for a, b in zip(ends, ends[1:]))


# ---------------------------------------------------------------------------
# 4. Areas and IMO criteria (IS Code 2008 Part A 2.2/2.3)
# ---------------------------------------------------------------------------
def test_area_integration_matches_analytic_curve():
    """Trapezoid on a fine grid must reproduce the analytic wall-sided integral."""
    B, T, KG = BARGE["B"], BARGE["T"], BARGE["KG"]
    gm, bm = bl.box_gm(B, T, KG), bl.box_bm(B, T)
    angles = [0.1 * i for i in range(0, 201)]  # 0..20 deg at 0.1 deg
    gz = [bl.wall_sided_gz(a, gm, bm) for a in angles]
    num = bl.area_under(angles, gz, 0.0, 20.0)
    # analytic: int_0^P sin*(GM + 0.5*BM*tan^2) dphi
    #   = GM*(1-cos P) + 0.5*BM*(sec P + cos P - 2)
    P = math.radians(20.0)
    ana = gm * (1.0 - math.cos(P)) + 0.5 * bm * (1.0 / math.cos(P) + math.cos(P) - 2.0)
    assert num == pytest.approx(ana, rel=2e-4)


def test_area_zero_when_gz_zero():
    angles = list(range(0, 91))
    assert bl.imo_areas(angles, [0.0] * len(angles))["area_0_40"] == pytest.approx(0.0)


def test_imo_gate_is_falsifiable():
    """The criteria set must be able to fail: a barely-floating barge fails several."""
    B, T, KG = BARGE["B"], BARGE["T"], BARGE["KG"]
    # watertight deck (closed box) -> genuinely sound; open-top with 0.3 m freeboard -> not
    good = bl.barge_baseline(B, 10.0, T, KG, open_top=False)
    bad = bl.barge_baseline(B, 6.3, T, KG, open_top=True)
    assert all(good["imo"].values())
    n_fail = sum(1 for v in bad["imo"].values() if not v)
    assert n_fail >= 4
    assert bad["vanishing_angle"] < 3.0


def test_low_freeboard_curve_terminates_early():
    B, T, KG = BARGE["B"], BARGE["T"], BARGE["KG"]
    b = bl.barge_baseline(B, 6.3, T, KG, open_top=True)
    assert b["flooding_angle_deg"] == pytest.approx(math.degrees(math.atan2(0.3, 10.0)))
    assert b["vanishing_angle"] == pytest.approx(b["flooding_angle_deg"], abs=1e-6)
    assert b["gz_max"] < 0.2  # below the IS Code GZmax floor


def test_vanishing_angle_interpolates_crossing():
    angles = [0, 10, 20, 30]
    gz = [0.0, 0.5, 0.4, -0.2]
    assert bl.vanishing_angle(angles, gz) == pytest.approx(20.0 + 10.0 * (0.4 / 0.6))