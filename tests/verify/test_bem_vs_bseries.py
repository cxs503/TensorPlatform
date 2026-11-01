"""Verification gate for the BEM open-water model's literature comparison (m1638).

Two jobs:

1. **Anti-fabrication gate** — every headline number in
   ``artifacts/m1638/bem_vs_bseries/audit.json`` is re-derived here from the
   model and the reference regression, so the artefact cannot silently go stale
   or be hand-edited.
2. **Regression gate** — the measured error budget is frozen with tolerances, so
   a future change to ``backend/services/propeller_open_water.py`` that degrades
   (or, happily, improves) the accuracy against literature fails loudly instead
   of quietly re-labelling the capability.

Evidence levels: the model under test is REAL (first-principles strip theory) and
the reference is a published regression (Oosterveld & van Oossanen 1975).  The
verdict below is a *negative* one on purpose: it is an honest FAIL of the 15 %
quantitative bar, and the test asserts that the report keeps saying so unless the
physics actually improves.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import backend.services.propeller_open_water as bem_mod
from backend.services.propeller_open_water import PropellerGeometry, open_water_point
from tools.verify.propeller_open_water import bem_vs_bseries as bvb
from tools.verify.propeller_open_water import reference_bseries as ref

AUDIT = Path("artifacts/m1638/bem_vs_bseries/audit.json")
B4_70 = {"name": "B4-70", "P_D": 1.00, "Ae_Ao": 0.70, "Z": 4}


@pytest.fixture(scope="module")
def audit():
    assert AUDIT.exists(), f"missing artefact {AUDIT}; run python3 -m tools.verify.propeller_open_water.bem_vs_bseries"
    return json.loads(AUDIT.read_text())


# ---------------------------------------------------------------------------
# 1. artefact integrity: recompute, never trust the JSON
# ---------------------------------------------------------------------------
def test_artifact_curves_match_recomputation(audit):
    rows = next(c for c in audit["comparisons"] if c["geometry"]["name"] == "B4-70")["rows"]
    geom = bvb.make_geometry(B4_70)
    ref_curve = ref.open_water_coefficients
    for row in rows[::5]:
        J = row["J"]
        bem = open_water_point(geom, J)
        r = ref_curve(J, B4_70["P_D"], B4_70["Ae_Ao"], B4_70["Z"])
        assert bem["KT"] == pytest.approx(row["KT_bem"], rel=0, abs=1e-9)
        assert bem["KQ"] == pytest.approx(row["KQ_bem"], rel=0, abs=1e-9)
        assert r["KT"] == pytest.approx(row["KT_ref"], rel=0, abs=1e-12)
        assert r["KQ"] == pytest.approx(row["KQ_ref"], rel=0, abs=1e-12)


def test_artifact_error_statistics_reproduce(audit):
    """Recompute the working-band summary of one family from the stored rows."""
    block = next(c for c in audit["comparisons"] if c["geometry"]["name"] == "B4-70")
    lo, hi = bvb.WORKING_RANGE
    errs = [abs(r["e_KT_pct"]) for r in block["rows"] if lo <= r["J_over_J0"] <= hi]
    assert errs
    assert block["summary"]["e_KT_pct"]["working_max_abs"] == pytest.approx(max(errs), abs=1e-9)
    assert block["summary"]["e_KT_pct"]["working_mean_abs"] == pytest.approx(
        sum(errs) / len(errs), abs=1e-9)


def test_kp505_statistics_reproduce(audit):
    kp = audit["kp505"]
    rows = kp["rows"]
    for key, floor_key in (("e_KT_bem_pct", None), ("e_KQ_ref_pct", None),
                           ("e_KT_bem_pct", "KT_exp")):
        sel = rows if floor_key is None else [r for r in rows if r[floor_key] >= bvb.KT_FLOOR]
        vals = [abs(r[key]) for r in sel]
        bucket = kp["stats"] if floor_key is None else kp["stats_above_kt_floor"]
        assert bucket[key]["max_abs"] == pytest.approx(max(vals), abs=1e-9)
        assert bucket[key]["n"] == len(vals)


# ---------------------------------------------------------------------------
# 2. physics invariants that must hold whatever the accuracy
# ---------------------------------------------------------------------------
def test_kq_decreases_with_advance_ratio():
    """A propeller's torque coefficient must fall with J (the legacy surrogate
    this module replaced did the opposite)."""
    geom = bvb.make_geometry(B4_70)
    kqs = [open_water_point(geom, J)["KQ"] for J in (0.0, 0.25, 0.5, 0.75)]
    assert all(a > b for a, b in zip(kqs, kqs[1:]))


def test_zero_thrust_advance_ratio_exists_and_matches_artifact(audit):
    geom = bvb.make_geometry(B4_70)
    j0 = bvb._bem_zero_thrust(geom)
    assert 0.5 < j0 < 2.0
    assert abs(open_water_point(geom, j0)["KT"]) < 1e-3
    stored = next(c for c in audit["comparisons"] if c["geometry"]["name"] == "B4-70")["J0_bem"]
    assert j0 == pytest.approx(stored, abs=1e-6)


def test_efficiency_below_actuator_disc_bound():
    geom = bvb.make_geometry(B4_70)
    j0 = bvb._bem_zero_thrust(geom)
    for frac in (0.3, 0.5, 0.7):
        p = open_water_point(geom, frac * j0)
        assert p["KT"] > 0.0
        assert p["eta0"] < p["eta_ideal"] < 1.0


def test_sensitivity_study_does_not_pollute_module_constants():
    cd0, slope = bem_mod.CD0, bem_mod.CL_SLOPE
    bvb.sensitivity_b4_70()
    assert (bem_mod.CD0, bem_mod.CL_SLOPE) == (cd0, slope)


# ---------------------------------------------------------------------------
# 3. frozen error budget + verdict
# ---------------------------------------------------------------------------
def test_error_budget_frozen_against_literature(audit):
    """Frozen worst-case errors in the working band (max over the five families).

    Tolerances are 0.5 percentage points: large enough for grid/platform noise,
    small enough that any real modelling change trips the gate.
    """
    worst = audit["verdict"]["worst_working_max_abs_pct_vs_regression"]
    assert worst["e_KT_pct"] == pytest.approx(45.96, abs=0.5)
    assert worst["e_KQ_pct"] == pytest.approx(31.73, abs=0.5)
    assert worst["e_eta_pct"] == pytest.approx(20.14, abs=0.5)


def test_integrated_quantities_pass_the_bar(audit):
    """Peak efficiency and zero-thrust J are the numbers a design study quotes;
    these *do* stay inside 15 % — the model is right about shape, wrong about
    absolute loading."""
    integ = audit["verdict"]["integrated_checks"]
    assert integ["peak_eta_pass"] is True
    assert integ["J0_pass"] is True
    assert integ["peak_eta_max_abs_pct"] < 15.0
    assert integ["J0_max_abs_pct"] < 15.0


def test_verdict_stays_honest(audit):
    """If the numbers ever improve past the bar, the verdict must flip; while they
    do not, the artefact must keep declaring the FAIL and naming the quantities."""
    verdict = audit["verdict"]
    assert verdict["accept_bar_pct"] == 15.0
    assert verdict["quantitative_vs_literature"] is False
    assert set(verdict["failing_quantities"]) == {"e_KT_pct", "e_KQ_pct", "e_eta_pct"}
    assert verdict["n_geometry_pass_all_metrics"] == 0
    assert verdict["n_geometry"] == 5
    assert "trend" in verdict["grade"].lower()


def test_no_single_calibration_constant_removes_the_bias(audit):
    """The bias changes sign across families (B5-80 +41 % vs B4-40 -11 %), so a
    global gain cannot repair it; the sensitivity study must show that a 10 %
    lift-slope change cannot bring the worst family inside 15 %."""
    variants = audit["sensitivity_b4_70"]["variants"]
    best_worst = min(
        max(abs(r["e_KT_pct"]) for r in rows) for rows in variants.values())
    assert best_worst > 15.0, "a constant retune would now suffice - re-verify the verdict"
    biases = [c["bias_design_band"]["KT_bem_over_ref"] for c in audit["comparisons"]]
    assert min(biases) < 1.0 < max(biases)


def test_model_family_bias_table_complete(audit):
    for c in audit["comparisons"]:
        b = c["bias_design_band"]
        assert set(b) == {"KT_bem_over_ref", "KQ_bem_over_ref", "eta_bem_over_ref"}
        assert all(not math.isnan(v) and v > 0 for v in b.values())

# ---------------------------------------------------------------------------
# 4. batch-wrapper wiring (the red test this mission also closed)
# ---------------------------------------------------------------------------
def _batch_engine():
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "tools", "batch_scan"))
    from batch_executor import ShipCalculationEngine  # noqa: E402
    return ShipCalculationEngine


def test_batch_wrapper_serves_the_physics_model_verbatim():
    """The batch scan path must return the BEM numbers themselves, not a
    re-rounded surrogate that could drift away from the audited model."""
    engine = _batch_engine()
    params = {"diameter_m": 5.0, "pitch_ratio": 1.0, "blade_area_ratio": 0.70,
              "blade_count": 4, "rpm": 100.0, "advance_speed_kn": 12.0}
    out = engine.calc_propeller(params)
    assert out["model"] == "bem-blade-element-momentum/v1"
    geom = bvb.make_geometry(B4_70)
    J = 12.0 * 0.5144 / (100.0 / 60.0 * 5.0)
    ref_point = open_water_point(geom, J)
    assert out["advance_coefficient"] == pytest.approx(J, abs=1e-4)
    assert out["thrust_coefficient"] == pytest.approx(ref_point["KT"], abs=1e-4)
    assert out["torque_coefficient"] == pytest.approx(ref_point["KQ"], abs=1e-4)
    assert out["efficiency"] == pytest.approx(ref_point["eta0"], abs=1e-4)
    assert out["torque_knm"] == pytest.approx(ref_point["torque_nm"] / 1000.0, abs=1e-4)


def test_batch_wrapper_reports_windmill_branch_instead_of_clamping():
    """Beyond the zero-thrust advance ratio the propeller brakes: thrust and
    torque go negative.  The legacy surrogate clamped KT >= 0.05 / KQ >= 0.01 and
    an efficiency plateau at 0.30, which made a windmilling propeller look like a
    propelling one.  The physical model must not resurrect that clamp."""
    engine = _batch_engine()
    params = {"diameter_m": 5.0, "pitch_ratio": 1.0, "blade_area_ratio": 0.70,
              "blade_count": 4, "rpm": 100.0, "advance_speed_kn": 18.0}
    out = engine.calc_propeller(params)
    assert out["thrust_coefficient"] < 0.0
    assert out["efficiency"] == 0.0
