"""Regression tests for the hydrostatics / GZ-area discretisation certification.

Guards ``examples/verification/hydrostatics_gci_study.py``: the analytic anchors
it relies on, the observed orders of accuracy it reports, and the production-grid
error margins it certifies.  Pure CPU, sub-second.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_STUDY = os.path.join(REPO, "examples", "verification", "hydrostatics_gci_study.py")


def _load_study():
    spec = importlib.util.spec_from_file_location("hydrostatics_gci_study", _STUDY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def study():
    return _load_study()


@pytest.fixture(scope="module")
def results(study):
    barge = study.run_case(
        "barge",
        lambda n_stations, n_waterlines: study.barge_offsets(
            n_stations=n_stations, n_waterlines=n_waterlines, **study.BARGE
        ),
        study.barge_analytic(),
        None,
    )
    wsurf = study.wigley_wetted_surface_reference()
    wigley = study.run_case(
        "wigley",
        lambda n_stations, n_waterlines: study.wigley_offsets(
            n_stations=n_stations, n_waterlines=n_waterlines, **study.WIGLEY
        ),
        study.wigley_analytic(),
        wsurf,
    )
    heel = study.run_heel_study()
    return {"barge": barge, "wigley": wigley, "heel": heel, "wsurf_ref": wsurf}


# ---------------------------------------------------------------------------
# Closed-form anchors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["volume_m3", "cb", "kb_m", "bm_t_m", "waterplane_area_m2", "wetted_surface_m2"],
)
def test_barge_matches_closed_form(results, key):
    """A rectangular barge is integrated exactly whenever Simpson's rule applies."""
    q = results["barge"]["quantities"][key]
    assert q["reference_kind"] == "closed_form"
    assert q["rel_error_finest_vs_reference_pct"] is not None
    assert q["rel_error_finest_vs_reference_pct"] < 1e-9, (key, q["values"][-1], q["analytic"])


def test_barge_lcb_is_symmetric_zero(results):
    q = results["barge"]["quantities"]["lcb_m"]
    assert q["abs_error_finest"] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("key", ["volume_m3", "cb", "kb_m", "waterplane_area_m2"])
def test_wigley_polynomial_quantities_exact(results, key):
    q = results["wigley"]["quantities"][key]
    assert q["rel_error_finest_vs_reference_pct"] < 1e-9, (key, q["rel_error_finest_vs_reference_pct"])


def test_wigley_bm_t_analytic_formula(study, results):
    """BM_T of the Wigley hull closes to 3 B^2 / (35 T)."""
    B, T = study.WIGLEY["beam"], study.WIGLEY["draft"]
    q = results["wigley"]["quantities"]["bm_t_m"]
    assert q["analytic"] == pytest.approx(3.0 * B * B / (35.0 * T), rel=1e-14)
    assert q["rel_error_finest_vs_reference_pct"] < 1e-5


# ---------------------------------------------------------------------------
# Observed order of accuracy (verification, not validation)
# ---------------------------------------------------------------------------


def test_wigley_bm_t_observed_order_is_fourth(results):
    p = results["wigley"]["quantities"]["bm_t_m"]["observed_order"]
    assert p == pytest.approx(4.0, abs=0.5), p


def test_wigley_wetted_surface_is_second_order(results):
    """The girth is a waterline polyline -> quadratic convergence, not Simpson's 4."""
    q = results["wigley"]["quantities"]["wetted_surface_m2"]
    assert q["reference_kind"] == "adaptive_quadrature"
    assert q["observed_order"] == pytest.approx(2.0, abs=0.4), q["observed_order"]


def test_wigley_wetted_surface_errors_decrease_monotonically(study, results):
    q = results["wigley"]["quantities"]["wetted_surface_m2"]
    ref = results["wsurf_ref"]
    errs = [abs(v - ref) for v in q["values"]]
    assert all(b <= a * (1.0 + 1e-12) for a, b in zip(errs, errs[1:])), errs


def test_production_grid_wetted_surface_error_margin(results):
    """n_stations=81 / n_waterlines=41 (the module default) must be < 0.01 %."""
    q = results["wigley"]["quantities"]["wetted_surface_m2"]
    assert q["rel_error_production_vs_reference_pct"] < 1e-2


# ---------------------------------------------------------------------------
# Heel-step (trapezoidal) quadrature of the GZ area
# ---------------------------------------------------------------------------


def test_heel_closed_form_matches_analytic_formula(study, results):
    heel = results["heel"]
    analytic = study.wallsided_gz_area_analytic(30.0, heel["gm"], heel["bm"])
    assert heel["analytic"] == pytest.approx(analytic, rel=1e-14)
    # GM must be positive for the case to be an engineering-relevant stability check
    assert heel["gm"] > 0.0


def test_heel_area_error_decreases_with_step(results):
    rows = results["heel"]["grid_rows"]
    errs = [r["rel_err_vs_analytic_pct"] for r in rows]
    assert all(b < a for a, b in zip(errs, errs[1:])), errs


def test_heel_production_step_five_degrees_is_negligible(results):
    """src/ship_perf/hydrostatics.py samples the heel range in 5 deg steps."""
    row = next(r for r in results["heel"]["grid_rows"] if r["step_deg"] == 5.0)
    assert row["rel_err_vs_analytic_pct"] < 2e-2, row


def test_heel_ten_degree_step_margin_is_documented(results):
    """At 10 deg the trapezoid error is ~2.5 % -- inside the 3 % gate but with
    almost no margin, which is exactly why the report recommends <= 5 deg."""
    row = next(r for r in results["heel"]["grid_rows"] if r["step_deg"] == 10.0)
    assert 1.0 < row["rel_err_vs_analytic_pct"] < 3.0, row


def test_heel_observed_order(results):
    p = results["heel"]["observed_order"]
    assert p == pytest.approx(4.0, abs=0.6), p


# ---------------------------------------------------------------------------
# Gate semantics
# ---------------------------------------------------------------------------


def test_gate_reports_pass_for_shipped_resolution(results):
    """Every certified quantity stays inside the 3 % gate at production grids."""
    for case in ("barge", "wigley"):
        for key, q in results[case]["quantities"].items():
            pe = q.get("rel_error_production_vs_reference_pct")
            if pe is not None:
                assert pe <= 3.0, (case, key, pe)
    assert results["heel"]["grid_rows"][0]["rel_err_vs_analytic_pct"] <= 3.0


def test_analytic_heel_area_formula_matches_numeric_integral(study):
    """Independent check of the closed form itself by quadrature of GZ(phi)."""
    from scipy.integrate import quad

    GM, BM = 0.6666666666666666, 4.166666666666667

    def gz(phi_rad):
        return math.sin(phi_rad) * (GM + 0.5 * BM * math.tan(phi_rad) ** 2)

    num, _ = quad(gz, 0.0, math.radians(30.0), limit=200, epsabs=1e-15, epsrel=1e-14)
    closed = study.wallsided_gz_area_analytic(30.0, GM, BM)
    assert closed == pytest.approx(num, rel=1e-9)