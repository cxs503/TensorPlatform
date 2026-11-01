"""IS Code 2008 criteria: single-source-of-truth + divergence regressions (m1642).

The audit found two platform implementations of the same regulatory check that
disagreed in *opposite* directions (one omitted A(0-40) >= 0.09 m rad -> false
pass; the other gated a non-existent GZ_max >= 0.25 m -> false fail).  Both now
delegate to ``services.stability_criteria``; these tests pin the numeric set,
the area integrator and both regression cases.

Run: pytest tests/test_stability_criteria_is_code.py -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.stability_criteria import (  # noqa: E402
    AREA_0_30_MIN, AREA_0_40_MIN, AREA_30_40_MIN, GZ_AT_30_MIN, GZ_MAX_ANGLE_MIN,
    GM0_MIN, area_under_gz, evaluate_is_code_2008, gz_at,
)
from services.ship_performance import icing as ice  # noqa: E402


def wall_sided(gm: float, bm: float, phi_deg: float) -> float:
    phi = math.radians(phi_deg)
    return gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)


def wall_sided_area(gm: float, bm: float, phi_deg: float) -> float:
    phi = math.radians(phi_deg)
    return gm * (1.0 - math.cos(phi)) + 0.5 * bm * (1.0 / math.cos(phi) + math.cos(phi) - 2.0)


def declining_curve() -> list:
    """GZ peaks at 30 deg then erodes: passes A(0-30) but fails A(0-40)."""
    pts = []
    for k in range(181):
        phi = k * 0.5
        if phi <= 30.0:
            gz = 0.42 * math.sin(math.radians(phi))
        elif phi <= 40.0:
            gz = 0.21 - 0.005 * (phi - 30.0)
        else:
            gz = 0.16 * (90.0 - phi) / 50.0
        pts.append((phi, gz))
    return pts


def compliant_low_gzmax_curve() -> list:
    """All six IS Code clauses pass with GZ_max = 0.235 m (< the legacy 0.25)."""
    pts = []
    for k in range(181):
        phi = k * 0.5
        if phi <= 40.0:
            gz = 0.235 * math.sin(math.radians(phi) * (math.pi / 2) / math.radians(40.0))
        else:
            gz = 0.1175 * (90.0 - phi) / 50.0
        pts.append((phi, gz))
    return pts


# ---------------------------------------------------------------------------
# numeric set of the Code
# ---------------------------------------------------------------------------
def test_thresholds_match_is_code_2008():
    assert (AREA_0_30_MIN, AREA_0_40_MIN, AREA_30_40_MIN) == (0.055, 0.090, 0.030)
    assert (GZ_AT_30_MIN, GZ_MAX_ANGLE_MIN, GM0_MIN) == (0.200, 25.0, 0.150)


def test_criteria_keys_and_references():
    res = evaluate_is_code_2008(declining_curve(), 0.42)
    assert set(res["criteria"]) == {
        "area_0_30_m_rad", "area_0_40_m_rad", "area_30_40_m_rad",
        "gz_at_30_m", "angle_of_max_gz_deg", "gm0_m",
    }
    for name, entry in res["criteria"].items():
        assert entry["reference"].startswith("IS Code 2008 A.2.2")
        assert isinstance(entry["pass"], bool)
    # gz_max is reported but is not a Code gate
    assert "gz_max_m" in res["informational"]
    assert "gz_max_m" not in res["criteria"]


# ---------------------------------------------------------------------------
# integrator anchors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("gm,bm,phi", [(1.0, 3.0, 30.0), (0.5, 6.0, 40.0), (0.2, 1.0, 10.0)])
def test_area_matches_closed_form_antiderivative(gm, bm, phi):
    """Trapezoid error must stay small AND shrink as O(h^2) when h halves.

    The 0.5 deg tolerance is 1e-3 (not 1e-4): with BM = 6 m the wall-sided arm
    grows like tan^2, so the 40 deg window is curvature-limited -- the
    Richardson ratio below is the real anchor, the absolute bound only guards
    against a broken integrator or a deg/rad slip.
    """
    def err(step: float) -> float:
        pts = [(k * step, wall_sided(gm, bm, k * step)) for k in range(int(round(180 / step)) + 1)]
        return abs(area_under_gz(pts, 0.0, phi) - wall_sided_area(gm, bm, phi)) / wall_sided_area(gm, bm, phi)

    assert err(0.5) < 1e-3
    assert math.log2(err(1.0) / err(0.5)) > 1.7      # second-order convergence


def test_area_is_m_rad_not_m_deg():
    gm, bm = 1.0, 3.0
    pts = [(k * 0.5, wall_sided(gm, bm, k * 0.5)) for k in range(181)]
    ratio = area_under_gz(pts, 0.0, 40.0) / wall_sided_area(gm, bm, 40.0)
    assert abs(ratio - 1.0) < 1e-4          # an m*deg implementation would read pi/180


def test_area_additivity_and_boundary_interpolation():
    pts = declining_curve()
    assert abs(area_under_gz(pts, 0.0, 40.0)
               - area_under_gz(pts, 0.0, 30.0)
               - area_under_gz(pts, 30.0, 40.0)) < 1e-12
    # a 2.5 deg grid must not truncate the window: interpolation clips exactly
    coarse = [(k * 2.5, gz_at(pts, k * 2.5)) for k in range(37)]
    assert abs(area_under_gz(coarse, 0.0, 40.0) - area_under_gz(pts, 0.0, 40.0)) < 5e-3


def test_theta_f_caps_the_forty_degree_window():
    curve = compliant_low_gzmax_curve()
    full = evaluate_is_code_2008(curve, 0.168)
    capped = evaluate_is_code_2008(curve, 0.168, theta_f_deg=35.0)
    assert full["theta_40_deg"] == 40.0
    assert capped["theta_40_deg"] == 35.0
    assert capped["criteria"]["area_0_40_m_rad"]["value"] < full["criteria"]["area_0_40_m_rad"]["value"]
    assert any("theta_f" in n for n in full["notes"])


# ---------------------------------------------------------------------------
# producer anchor: exact section integration vs wall-sided closed form
# ---------------------------------------------------------------------------
def test_platform_gz_reproduces_wall_sided_below_deck_edge():
    vessel = ice.preset_polar_fishing_vessel()
    cond = ice.LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    gm, bm = cond.gm_loaded, vessel.hull.bm_at_draft(cond.draft_loaded)
    limit = math.degrees(math.atan(2.0 * min(cond.draft_loaded,
                                             vessel.hull.depth - cond.draft_loaded)
                                    / vessel.hull.beam))
    for phi in (2.0, 5.0, 10.0, 15.0, limit - 1.0):
        rel = abs(cond.gz(phi) - wall_sided(gm, bm, phi)) / wall_sided(gm, bm, phi)
        assert rel < 1e-6, f"heel {phi}: rel err {rel:.2e}"
    h = 1e-4
    assert abs((cond.gz(h) - cond.gz(-h)) / (2 * math.radians(h)) - gm) / gm < 1e-6


# ---------------------------------------------------------------------------
# regression: the two legacy verdict defects (false PASS / false FAIL)
# ---------------------------------------------------------------------------
def test_legacy_icing_set_would_have_passed_an_illegal_curve():
    res = evaluate_is_code_2008(declining_curve(), 0.42)
    c = res["criteria"]
    legacy_keys = ("area_0_30_m_rad", "gz_at_30_m", "angle_of_max_gz_deg", "area_30_40_m_rad")
    assert all(c[k]["pass"] for k in legacy_keys)      # the 5-item set says OK
    assert c["gm0_m"]["pass"]
    assert c["area_0_40_m_rad"]["value"] < AREA_0_40_MIN
    assert c["area_0_40_m_rad"]["pass"] is False
    assert res["all_pass"] is False                    # corrected verdict


def test_compliant_ship_with_gz_max_below_quarter_metre_passes():
    res = evaluate_is_code_2008(compliant_low_gzmax_curve(), 0.168)
    assert res["informational"]["gz_max_m"] < 0.25
    assert all(v["pass"] for v in res["criteria"].values())
    assert res["all_pass"] is True


def test_icing_stability_criteria_reports_the_formerly_missing_clause():
    vessel = ice.preset_polar_fishing_vessel()
    cond = ice.LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    crit = cond.stability_criteria()
    assert "area_0_40" in crit
    assert crit["area_0_40"]["limit"] == AREA_0_40_MIN
    assert crit["all_pass"]["value"] == all(
        crit[k]["pass"] for k in
        ("gm_initial", "area_0_30", "area_0_40", "gz_at_30", "heel_at_max_gz", "area_30_40"))


def test_pipeline_and_icing_agree_on_the_same_curve():
    """Same GZ curve, two call sites, one verdict."""
    import numpy as np
    from services.ice_integration import nodes_downstream as nd

    vessel = ice.preset_polar_fishing_vessel()
    cond = ice.LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    phis = list(np.arange(0.0, 60.1, 0.5))
    pipe = nd.imo_criteria(
        {"phi_deg": phis, "gz_numeric": [cond.gz(p) for p in phis],
         "GM0": cond.gm_loaded}, cond.gm_loaded)
    icing_verdict = cond.stability_criteria()["all_pass"]["value"]
    assert pipe["passed"] == icing_verdict
    # every gating check in the pipeline names a real Code clause
    for key, entry in pipe["checks"].items():
        if entry["required"] is not None:
            assert "IS Code 2008" in entry["reference"]
    assert pipe["checks"]["gz_max_m"]["required"] is None
    assert "angle_of_max_gz_deg" in pipe["checks"]