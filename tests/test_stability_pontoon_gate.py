"""Offline regression tests for the pontoon intact-stability verification gate.

The tests are deliberately network-free: they pin
  * the exactness of the closed-form reference (so the *reference* can never
    drift silently), and
  * the behaviour of the root-cause detector used on live platform output, via
    synthetic platform responses that reproduce either a correct KM or the
    KB-double-count defect found on 2026-11-02.

Live probing lives in ``docs/verification_debt/stability_pontoon/stability_gate.py``
and is run explicitly against :8001; it is not part of the unit-test suite so the
suite stays deterministic on a busy host.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "docs" / "verification_debt" / "stability_pontoon"
sys.path.insert(0, str(DIR))
import refs  # noqa: E402

L, B, T, KG = 100.0, 20.0, 8.0, 3.0


def test_pontoon_hydrostatics_are_exact():
    h = refs.pontoon_analytic(L, B, T, KG)
    assert h["volume_m3"] == pytest.approx(L * B * T)
    assert h["KB"] == pytest.approx(T / 2)
    assert h["BM"] == pytest.approx(B ** 2 / (12 * T))      # 4.1667 m
    assert h["KM"] == pytest.approx(h["KB"] + h["BM"])       # 8.1667 m
    assert h["GM"] == pytest.approx(h["KM"] - KG)            # 5.1667 m
    # deck at D = 2T -> deck edge immersed at atan(2T/B) = 38.66 deg
    assert h["deck_edge_deg"] == pytest.approx(math.degrees(math.atan(2 * T / B)))
    assert h["deck_edge_deg"] == pytest.approx(38.6598, abs=1e-3)


def test_bm_scaling_matches_thin_ship_limit():
    """BM must scale as B^2/T: doubling B quadruples BM, doubling T halves it."""
    bm = lambda b, t: refs.pontoon_analytic(L, b, t, KG)["BM"]
    assert bm(2 * B, T) / bm(B, T) == pytest.approx(4.0)
    assert bm(B, 2 * T) / bm(B, T) == pytest.approx(0.5)


def test_gz_small_angle_limit_is_gm():
    h = refs.pontoon_analytic(L, B, T, KG)
    for phi in (0.5, 1.0, 2.0):
        gz = refs.gz_wall_sided(phi, h["GM"], h["BM"])
        assert gz / math.sin(math.radians(phi)) == pytest.approx(h["GM"], rel=1e-3)
    assert refs.gz_wall_sided(0.0, h["GM"], h["BM"]) == 0.0


def test_gz_curve_stops_at_deck_edge():
    h = refs.pontoon_analytic(L, B, T, KG)
    curve = refs.gz_curve(L, B, T, KG, step=1.0)
    assert max(c["phi_deg"] for c in curve) <= h["deck_edge_deg"] + 1e-9
    assert max(c["phi_deg"] for c in curve) > h["deck_edge_deg"] - 1.0
    assert all(c["GZ"] >= 0.0 for c in curve)


def test_area_under_gz_is_positive_and_grid_independent():
    a1 = refs.area_under(refs.gz_curve(L, B, T, KG, step=1.0), 30.0)
    a2 = refs.area_under(refs.gz_curve(L, B, T, KG, step=0.25), 30.0)
    assert a1 > 0 and a2 > 0
    assert a1 == pytest.approx(a2, rel=2e-2)  # trapezoid on a smooth integrand


# ---------------------------------------------------------------------------
# root-cause detector: GM_platform = GM_analytic + T/2  <=>  KB counted twice
# ---------------------------------------------------------------------------
def detector_residual(gm_platform: float, gm_analytic: float, t: float) -> float:
    return abs(gm_platform - gm_analytic - t / 2.0)


@pytest.mark.parametrize("b,t", [(10.0, 4.0), (20.0, 8.0), (30.0, 12.0)])
def test_detector_flags_kb_double_count(b, t):
    h = refs.pontoon_analytic(L, b, t, KG)
    defective_gm = h["KM"] + t / 2.0 - KG          # what :8001 returned on 2026-11-02
    assert detector_residual(defective_gm, h["GM"], t) < 1e-9
    assert abs((defective_gm - h["GM"]) / h["GM"]) > 0.15   # >15% => unacceptable


@pytest.mark.parametrize("b,t", [(10.0, 4.0), (20.0, 8.0), (30.0, 12.0)])
def test_detector_clears_a_correct_platform(b, t):
    """A correct KM leaves residual ~ T/2, i.e. the double-count signature is absent."""
    h = refs.pontoon_analytic(L, b, t, KG)
    for corrected_gm in (h["GM"], h["GM"] * 1.01):   # exact, and within a 1% band
        residual = detector_residual(corrected_gm, h["GM"], t)
        assert abs(residual - t / 2.0) < 0.1         # ~T/2 => no defect
    # the real defect drives the residual to zero instead
    assert detector_residual(h["GM"] + t / 2.0, h["GM"], t) < 1e-9
