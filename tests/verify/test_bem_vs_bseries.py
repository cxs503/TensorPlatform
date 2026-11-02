"""Regression tests for the m1650 verification debt closure:
BEM open-water model (backend/services/propeller_open_water.py) vs the canonical
Wageningen B-series table and vs published KP505 model-test data.

The heavy sweep lives in ``tools/verify/propeller_open_water/bem_vs_bseries.py``
and its artifact ``artifacts/m1650/propeller_bem_vs_bseries/audit.json``.  These
tests keep the run light (a few BEM points, no figures) while pinning:

  * the audit artifact is present and internally consistent,
  * the recorded numbers are reproducible from the current model + reference
    (a silent change to either side fails here),
  * the structural facts the verdict rests on (J0 systematically low, KT slope
    too steep, KQ strictly decreasing, BEM below the actuator-disc bound),
  * explicit gate bookkeeping: every key quantity carries a machine verdict and
    the verdicts follow the 3 % / 15 % gates.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.verify.propeller_open_water import bem_vs_bseries as h

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/m1650/propeller_bem_vs_bseries/audit.json"


@pytest.fixture(scope="module")
def audit():
    if not ARTIFACT.exists():
        pytest.skip("run python3 -m tools.verify.propeller_open_water.bem_vs_bseries first")
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def bem():
    return h.load_bem()


def test_artifact_shape(audit):
    assert audit["mission"] == 1650
    assert len(audit["geometries"]) == 5
    labels = {g["label"] for g in audit["geometries"]}
    assert labels == {"B4-70", "B4-55", "B4-40", "B5-80", "B3-50"}
    for g in audit["geometries"]:
        assert len(g["rows"]) == len(h.J_GRID)
        for key in ("KT_bem", "KT_ref", "KQ_bem", "KQ_ref", "eta_bem", "eta_ref"):
            assert key in g["rows"][0]


def test_recorded_points_reproduce(bem, audit):
    """Every recorded KT/KQ/eta must be reproduced exactly by a fresh evaluation."""
    for g in audit["geometries"]:
        geom = bem.PropellerGeometry(diameter_m=5.0, pitch_ratio=g["P_D"],
                                     blade_area_ratio=g["Ae_Ao"], blade_count=g["Z"])
        for row in g["rows"]:
            p = bem.open_water_point(geom, row["J"])
            assert p["KT"] == pytest.approx(row["KT_bem"], rel=1e-9, abs=1e-12)
            assert p["KQ"] == pytest.approx(row["KQ_bem"], rel=1e-9, abs=1e-12)
            assert p["eta0"] == pytest.approx(row["eta_bem"], rel=1e-9, abs=1e-12)


def test_reference_side_reproduces(audit):
    from tools.verify.propeller_open_water import reference_bseries as ref

    for g in audit["geometries"]:
        for row in g["rows"]:
            c = ref.open_water_coefficients(row["J"], g["P_D"], g["Ae_Ao"], g["Z"])
            assert c["KT"] == pytest.approx(row["KT_ref"], rel=1e-12, abs=1e-12)
            assert c["KQ"] == pytest.approx(row["KQ_ref"], rel=1e-12, abs=1e-12)


def test_reference_anchored_to_experiment(audit):
    """Reference A must stay close to the measured KP505 data, otherwise the whole
    comparison would be graded against a broken yardstick."""
    s = audit["kp505"]["summary"]
    assert s["KT_ref_mean_abs_err"] < 20.0
    assert s["KQ_ref_mean_abs_err"] < 20.0
    # the model under test is not better than the reference at KT (documented finding)
    assert s["KT_bem_mean_abs_err"] > s["KT_ref_mean_abs_err"]


def test_J0_systematically_low(audit):
    """Finding: the BEM zero-thrust ratio is ~0.993*(P/D) while the table gives
    1.06-1.11*(P/D) -> J0 is LOW by 6-11 %, contradicting the heuristic
    '0.85-0.9*(P/D), model 10-15 % high' quoted in the 1393 report."""
    for g in audit["geometries"]:
        assert -15.0 < g["J0"]["err_pct"] < -5.0
        assert 0.98 < g["J0"]["bem"] / g["P_D"] < 1.00
        assert 1.05 < g["J0"]["ref"] / g["P_D"] < 1.12


def test_kt_slope_too_steep(audit):
    """KT slope magnitude exceeds the table's everywhere -> KT high at low J."""
    for label, sl in audit["kt_slope"].items():
        assert sl["ratio_pct"] > 100.0, label
        assert sl["slope_bem"] < 0.0 and sl["slope_ref"] < 0.0


def test_efficiency_is_the_tightest_quantity(audit):
    """eta_max is the best-agreeing quantity (mean < 10 %, max < 15 %), while
    pointwise KT/KQ exceed the 15 % acceptable gate -> that asymmetry is exactly
    what the report claims, so it is pinned here."""
    v = audit["verdict"]
    assert v["eta_max"]["mean_abs_pct"] < 10.0
    assert v["eta_max"]["max_abs_pct"] < 15.0
    assert v["band"]["KT_pointwise"]["mean_abs_pct"] > 15.0
    assert v["band"]["KQ_pointwise"]["mean_abs_pct"] > 15.0


def test_verdict_bookkeeping(audit):
    v = audit["verdict"]
    for scope in ("band", "all_points"):
        for key in ("KT_pointwise", "KQ_pointwise", "eta_pointwise"):
            d = v[scope][key]
            for stat, vk in (("mean_abs_pct", "verdict_mean_abs_pct"),
                             ("max_abs_pct", "verdict_max_abs_pct")):
                m = d[stat]
                verdict = d[vk]
                if m < h.GATE_PUBLISH:
                    assert verdict.startswith("PASS <3")
                elif m < h.GATE_ACCEPT:
                    assert verdict.startswith("PASS <15")
                else:
                    assert verdict.startswith("FAIL")
    for key in ("J0", "J_opt", "eta_max"):
        assert "verdict_mean_abs_pct" in v[key]
        assert "verdict_max_abs_pct" in v[key]


def test_load_band_excludes_zero_thrust_points(audit):
    band = audit["verdict"]["load_band"]
    assert band["n_points"] < 5 * len(h.J_GRID)
    assert band["excluded_J"], "some points must fall outside the load band"
    # band membership is per point, not per J: a J that is near zero thrust for a
    # low-pitch propeller can still carry load for a high-pitch one.
    for g in audit["geometries"]:
        for row in g["rows"]:
            in_band = h.in_load_band(row)
            if not in_band:
                assert (row["KT_ref"] < h.LOAD_KT_MIN
                        or row["KQ_ref"] < h.LOAD_KQ_MIN
                        or row["KT_bem"] * row["KT_ref"] <= 0.0
                        or row["KQ_bem"] * row["KQ_ref"] <= 0.0)


def test_physics_rails(bem, audit):
    """KQ falls with J for both models; the BEM never beats the actuator-disc bound."""
    mono = audit["monotonicity"]
    assert mono["KQ_strictly_decreasing_bem"] is True
    assert mono["KQ_strictly_decreasing_ref"] is True
    assert mono["KT_strictly_decreasing_bem"] is True
    assert mono["KT_strictly_decreasing_ref"] is True

    geom = bem.PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                 blade_area_ratio=0.70, blade_count=4)
    for J in (0.2, 0.4, 0.6):
        p = bem.open_water_point(geom, J)
        bound = bem.ideal_efficiency_bound(p["KT"], J, geom)
        assert 0.0 < p["eta0"] < bound


def test_section_model_docstring_drift_recorded(audit):
    """Known documentation drift in the model under test, recorded (not hidden):
    docstring says 0.008 + 0.9 a^2 clamped [0.006, 0.08]; code uses
    CD0 + 1.0 a^2 clamped [0.008, 0.10]."""
    d = audit["section_docstring_drift"]
    assert d["cd_code"] != pytest.approx(d["cd_per_docstring"], rel=1e-6)
    assert d["docstring_clamp"] == [0.006, 0.08]
    assert d["code_clamp"] == [0.008, 0.10]


def test_kp505_table_monotone(audit):
    """The experimental anchor itself must be physically consistent."""
    rows = audit["kp505"]["rows"]
    assert [r["J"] for r in rows] == sorted(r["J"] for r in rows)
    for a, b in zip(rows, rows[1:]):
        assert b["KT_exp"] < a["KT_exp"]
        assert b["KQ_exp"] < a["KQ_exp"]
    assert math.isclose(rows[-1]["J"], 1.0)