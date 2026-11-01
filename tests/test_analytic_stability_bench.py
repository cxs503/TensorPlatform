"""Tests for the analytic intact-stability benchmark harness (m1532).

Two layers:
  1. hermetic  — the analytic reference itself is checked against an
     independent numerical method (polygon clipping + bisection) and against
     textbook values, so a silent error in the reference cannot pass;
  2. live      — the platform API is probed and the *measured* deviations are
     pinned, so any change in the platform's GZ/KB behaviour is caught.  The
     "should be within 3 %" assertions are xfail-marked for the currently known
     defects: once the platform is fixed they flip to XPASS (strict=False).
"""
from __future__ import annotations

import math
import os
import sys
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.analytic_bench import reference as R  # noqa: E402
from tools.analytic_bench import stability_audit as A  # noqa: E402

CASE1 = R.BoxBarge(length=100.0, beam=20.0, draft=8.0, depth=14.0, kg=6.0)
CASE2 = R.BoxBarge(length=80.0, beam=24.0, draft=6.0, depth=12.0, kg=5.0)
API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")


# ---------------------------------------------------------------------------
# 1. Hermetic reference checks
# ---------------------------------------------------------------------------


def test_box_hydrostatics_closed_form():
    assert CASE1.kb == pytest.approx(4.0)
    assert CASE1.bm == pytest.approx(400.0 / 96.0)
    assert CASE1.gm == pytest.approx(4.0 + 400.0 / 96.0 - 6.0)
    # box: KB is exactly T/2 and the Morrish approximation must reproduce it
    assert R.morrish_kb(CASE1.draft, 1.0, 1.0) == pytest.approx(CASE1.draft / 2.0)


@pytest.mark.parametrize("hull", [CASE1, CASE2])
def test_wall_sided_limit_is_min_of_opening_angles(hull):
    assert hull.wall_sided_limit_deg == pytest.approx(
        min(hull.phi_deck_deg, hull.phi_bilge_deg))
    assert 0.0 < hull.wall_sided_limit_deg < 60.0


@pytest.mark.parametrize("hull", [CASE1, CASE2])
@pytest.mark.parametrize("phi", [0.0, 5.0, 10.0, 15.0, 20.0, 25.0])
def test_closed_form_matches_independent_wedge_integration(hull, phi):
    """Closed-form GZ vs polygon-clipping integration of the heeled section."""
    closed = hull.gz_wall_sided(phi)
    numeric = hull.gz_numeric(phi)
    assert numeric == pytest.approx(closed, rel=1e-6, abs=1e-9)


def test_numeric_gz_zero_upright_and_monotone_early():
    assert CASE1.gz_numeric(0.0) == pytest.approx(0.0, abs=1e-9)
    vals = [CASE1.gz_numeric(p) for p in (0.0, 5.0, 10.0, 15.0, 20.0)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_numeric_small_angle_slope_equals_gm():
    """dGZ/dphi at the origin must equal the metacentric height GM."""
    eps = 1e-4
    slope = (CASE1.gz_numeric(eps) - CASE1.gz_numeric(0.0)) / math.radians(eps)
    assert slope == pytest.approx(CASE1.gm, rel=1e-4)


def test_published_ittc57_value():
    # ITTC-1957: Cf = 0.075/(log10(Re)-2)^2 -> 0.0020833 at Re = 1e8
    assert R.ittc57_cf(1e8) == pytest.approx(0.075 / 36.0, rel=1e-12)


def test_curve_summary_reference_self_consistency():
    angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    gz = [CASE1.gz_wall_sided(p) for p in angles]
    s = R.curve_summary(angles, gz)
    assert s["gz_max"] == pytest.approx(gz[-1])          # still rising at 30 deg
    assert s["area_0_30"] > 0.0
    assert s["vanishing_angle"] == 0.0                    # no crossing in window


# ---------------------------------------------------------------------------
# 2. Live platform audit (regression pins + xfail'd accuracy requirements)
# ---------------------------------------------------------------------------


def _api_alive() -> bool:
    try:
        urllib.request.urlopen(f"{API}/openapi.json", timeout=3).read(64)
        return True
    except Exception:
        return False


live = pytest.mark.skipif(not _api_alive(), reason=f"platform API {API} unreachable")


@pytest.fixture(scope="module")
def case1_audit():
    return A.audit_case(API, CASE1)


@live
def test_itti57_cf_platform_matches_analytic():
    """PASS control: the harness must report a <3 % error on an exact identity."""
    ctrl = A.friction_control(API)
    assert abs(ctrl["err_pct"]) < A.TOL_OK
    assert ctrl["verdict"] == "PASS"


@live
def test_kb_regression_pin(case1_audit):
    """Regression pin: KB of a box barge is T/2 = 4.0 m; platform reports ~5.33 m."""
    kb = case1_audit["endpoint_A_calculations_stability"]["factors_subset"]["KB"]
    assert kb["err_pct"] == pytest.approx(33.3, abs=3.0)


@live
@pytest.mark.xfail(strict=False, reason="known defect m1532-A: KB = T(1-Cb/3), "
                                       "2/3*T instead of T/2 for a box barge")
def test_kb_within_3pct(case1_audit):
    kb = case1_audit["endpoint_A_calculations_stability"]["factors_subset"]["KB"]
    assert abs(kb["err_pct"]) <= A.TOL_OK


@live
def test_gz_pointwise_regression_pin(case1_audit):
    """GZ(30 deg) error is reported as ~-22 % (sin^2 vs tan^2 large-angle term)."""
    rows = {r["angle_deg"]: r for r in case1_audit["endpoint_B_stability_gz_curve"]["pointwise"]}
    assert rows[30.0]["err_pct"] == pytest.approx(-21.9, abs=3.0)
    assert rows[5.0]["err_pct"] == pytest.approx(+18.1, abs=3.0)


@live
@pytest.mark.xfail(strict=False, reason="known defect m1532-B: gz-curve KN drops the "
                                       "0.5*BM*tan^2(phi)*sin(phi) term")
def test_gz_pointwise_within_3pct(case1_audit):
    for r in case1_audit["endpoint_B_stability_gz_curve"]["pointwise"]:
        if r["reference_defined"]:
            assert abs(r["err_pct"]) <= A.TOL_OK


@live
def test_audit_artifacts_written(tmp_path):
    rc = A.main(["--api", API, "--out", str(tmp_path)])
    assert rc == 0
    for name in ("stability_audit.json", "stability_audit.md", "fig_stability_audit.png"):
        p = tmp_path / name
        assert p.exists() and p.stat().st_size > 0