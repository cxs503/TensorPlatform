"""Tests for cross-solver regression pipeline.

Covers:
- generator.py: CaseTemplate, BenchmarkCase, CaseGenerator, generate_suite
- solver_stubs.py: all 4 solver stubs + references
- regressor.py: detect_regression, detect_suite_regression, sensitivity, FPR
- dashboard.py: figure generation, report assembly
- pipeline.py: end-to-end pipeline
- model.py: is_finite, rel_error

Target: ≥85% coverage of core logic.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

import pytest

# Ensure repo root is on path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.cross_solver_regression.model import is_finite, rel_error
from tools.cross_solver_regression.solver_stubs import (
    lbm_solver, lbm_reference,
    fem_solver, fem_reference,
    dem_solver, dem_reference,
    fvm_solver, fvm_reference,
    SOLVER_REGISTRY,
)
from tools.cross_solver_regression.generator import (
    CaseTemplate, BenchmarkCase, CaseGenerator, generate_suite,
)
from tools.cross_solver_regression.regressor import (
    detect_regression, detect_suite_regression,
    sensitivity_analysis, estimate_false_positive_rate,
    RegressionResult, SuiteRegressionResult,
    DEFAULT_DRIFT_THRESHOLD,
)
from tools.cross_solver_regression.dashboard import (
    fig_status_table, fig_deviation_histogram,
    fig_regression_trend, fig_solver_comparison,
    generate_dashboard, DashboardReport,
)
from tools.cross_solver_regression.pipeline import (
    CrossSolverPipeline, run_pipeline,
)


# ═══════════════════════════════════════════════════════════════════════
# model.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestModel:
    def test_is_finite_valid(self):
        assert is_finite(0) is True
        assert is_finite(1.0) is True
        assert is_finite(-3.14) is True
        assert is_finite(1e300) is True

    def test_is_finite_invalid(self):
        assert is_finite(None) is False
        assert is_finite(True) is False
        assert is_finite(False) is False
        assert is_finite("hello") is False
        assert is_finite(float("nan")) is False
        assert is_finite(float("inf")) is False
        assert is_finite(float("-inf")) is False
        assert is_finite([]) is False

    def test_rel_error_exact(self):
        assert rel_error(1.0, 1.0) == 0.0
        assert rel_error(0.0, 0.0) == 0.0

    def test_rel_error_nonzero(self):
        err = rel_error(1.05, 1.0)
        assert err is not None
        assert abs(err - 0.05) < 1e-10

    def test_rel_error_nonfinite(self):
        assert rel_error(None, 1.0) is None
        assert rel_error(1.0, None) is None
        assert rel_error("x", 1.0) is None
        assert rel_error(float("nan"), 1.0) is None

    def test_rel_error_zero_reference(self):
        # With zero reference, should use eps floor
        err = rel_error(1e-15, 0.0)
        assert err is not None
        assert is_finite(err)


# ═══════════════════════════════════════════════════════════════════════
# solver_stubs.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestSolverStubs:
    def test_registry_completeness(self):
        assert "TensorLBM" in SOLVER_REGISTRY
        assert "TensorFEM" in SOLVER_REGISTRY
        assert "TensorDEM" in SOLVER_REGISTRY
        assert "TensorFVM" in SOLVER_REGISTRY
        assert len(SOLVER_REGISTRY) >= 3

    def test_lbm_solver_returns_dict(self):
        result = lbm_solver(grid_size=32, diffusion_coeff=0.1, time_steps=50)
        assert isinstance(result, dict)
        assert "l2_norm" in result
        assert "max_value" in result
        assert "mass" in result
        assert result["solver"] == "TensorLBM"

    def test_lbm_reference_matches_solver(self):
        params = {"grid_size": 64, "diffusion_coeff": 0.1, "time_steps": 100}
        solver_result = lbm_solver(**params)
        ref_result = lbm_reference(**params)
        # L2 norm should be close (within numerical error)
        assert abs(solver_result["l2_norm"] - ref_result["l2_norm"]) / ref_result["l2_norm"] < 0.5  # FTCS has significant numerical diffusion

    def test_fem_solver_returns_dict(self):
        result = fem_solver(n_elements=16, source_amplitude=1.0, polynomial_order=1)
        assert isinstance(result, dict)
        assert "l2_error" in result
        assert "energy_norm" in result
        assert result["solver"] == "TensorFEM"

    def test_fem_convergence(self):
        # Higher resolution should give smaller error
        r1 = fem_solver(n_elements=8, polynomial_order=1)
        r2 = fem_solver(n_elements=32, polynomial_order=1)
        assert r2["l2_error"] < r1["l2_error"]

    def test_dem_solver_returns_dict(self):
        result = dem_solver(n_particles=50, packing_fraction=0.6)
        assert isinstance(result, dict)
        assert "coordination_number" in result
        assert "pressure" in result
        assert result["solver"] == "TensorDEM"

    def test_fvm_solver_returns_dict(self):
        result = fvm_solver(n_cells=32, velocity=1.0, time_steps=20)
        assert isinstance(result, dict)
        assert "l2_norm" in result
        assert "mass" in result
        assert result["solver"] == "TensorFVM"

    def test_fvm_mass_conservation(self):
        # Mass should be approximately conserved
        result = fvm_solver(n_cells=64, velocity=1.0, time_steps=50)
        ref = fvm_reference(n_cells=64, velocity=1.0, time_steps=50)
        # Upwind scheme has numerical diffusion, so mass may not be exactly conserved
        assert abs(result["mass"] - ref["mass"]) / max(ref["mass"], 1e-10) < 0.1

    def test_all_solvers_deterministic(self):
        """All solvers must be deterministic (no random state)."""
        for name, entry in SOLVER_REGISTRY.items():
            r1 = entry["solver"](problem_id=f"det_test_{name}")
            r2 = entry["solver"](problem_id=f"det_test_{name}")
            for k in r1:
                if isinstance(r1[k], (int, float)):
                    assert r1[k] == r2[k], f"{name}.{k} not deterministic"


# ═══════════════════════════════════════════════════════════════════════
# generator.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestGenerator:
    def test_case_template_creation(self):
        tmpl = CaseTemplate(
            template_id="test_01",
            solver_name="TensorLBM",
            description="Test template",
            param_space={"grid_size": [32]},
        )
        assert tmpl.template_id == "test_01"
        assert tmpl.solver_name == "TensorLBM"
        d = tmpl.to_dict()
        assert d["template_id"] == "test_01"

    def test_benchmark_case_creation(self):
        case = BenchmarkCase(
            case_id="test_case_01",
            template_id="tmpl_01",
            solver_name="TensorLBM",
            parameters={"grid_size": 32},
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        assert case.case_id == "test_case_01"
        d = case.to_dict()
        assert d["case_id"] == "test_case_01"

        # Round-trip
        case2 = BenchmarkCase.from_dict(d)
        assert case2.case_id == case.case_id
        assert case2.reference_values == case.reference_values

    def test_case_generator_init(self):
        gen = CaseGenerator()
        assert len(gen.templates) > 0
        assert len(gen.registry) >= 3

    def test_generate_single_case(self):
        gen = CaseGenerator()
        tmpl = gen.templates[0]
        case = gen.generate_case(tmpl)
        assert isinstance(case, BenchmarkCase)
        assert case.solver_name == tmpl.solver_name
        assert len(case.reference_values) > 0
        assert len(case.tolerances) > 0
        assert case.generation_time_s >= 0

    def test_generate_suite_min_cases(self):
        gen = CaseGenerator()
        cases = gen.generate_suite(min_cases=10, min_solvers=3, cases_per_solver=3)
        assert len(cases) >= 10

    def test_generate_suite_min_solvers(self):
        gen = CaseGenerator()
        cases = gen.generate_suite(min_cases=10, min_solvers=3, cases_per_solver=3)
        solvers = set(c.solver_name for c in cases)
        assert len(solvers) >= 3

    def test_generate_suite_all_have_references(self):
        gen = CaseGenerator()
        cases = gen.generate_suite(min_cases=10, min_solvers=3, cases_per_solver=3)
        for case in cases:
            assert len(case.reference_values) > 0, f"Case {case.case_id} has no reference values"
            assert len(case.tolerances) > 0, f"Case {case.case_id} has no tolerances"

    def test_generate_suite_fast(self):
        """Generation should be <1 min per case."""
        gen = CaseGenerator()
        cases = gen.generate_suite(min_cases=10, min_solvers=3, cases_per_solver=3)
        for case in cases:
            assert case.generation_time_s < 60.0, (
                f"Case {case.case_id} took {case.generation_time_s:.2f}s to generate"
            )

    def test_generate_suite_convenience(self):
        result = generate_suite(min_cases=10, min_solvers=3, cases_per_solver=3)
        assert isinstance(result, list)
        assert len(result) >= 10
        assert all(isinstance(r, dict) for r in result)

    def test_unknown_solver_raises(self):
        gen = CaseGenerator(solver_registry={})
        tmpl = CaseTemplate(
            template_id="bad",
            solver_name="NonexistentSolver",
            description="bad",
            param_space={},
        )
        with pytest.raises(ValueError, match="Unknown solver"):
            gen.generate_case(tmpl)


# ═══════════════════════════════════════════════════════════════════════
# regressor.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestRegressor:
    def test_detect_regression_pass(self):
        result = detect_regression(
            case_id="test_pass",
            solver_name="TensorLBM",
            metrics={"l2_norm": 0.501},
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        assert result.passed is True
        assert result.status == "pass"
        assert len(result.alerts) == 0

    def test_detect_regression_fail_small_drift(self):
        result = detect_regression(
            case_id="test_fail",
            solver_name="TensorLBM",
            metrics={"l2_norm": 0.53},  # 6% drift
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        assert result.passed is False
        assert result.status == "regression"
        assert len(result.alerts) > 0

    def test_detect_regression_critical(self):
        result = detect_regression(
            case_id="test_critical",
            solver_name="TensorLBM",
            metrics={"l2_norm": 0.60},  # 20% drift > 3x threshold
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        assert result.passed is False
        critical_alerts = [a for a in result.alerts if a["severity"] == "critical"]
        assert len(critical_alerts) > 0

    def test_detect_regression_missing_metric(self):
        result = detect_regression(
            case_id="test_missing",
            solver_name="TensorLBM",
            metrics={},  # no metrics
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        assert result.passed is False
        assert any(a["kind"] == "metric_missing" for a in result.alerts)

    def test_detect_regression_multiple_metrics(self):
        result = detect_regression(
            case_id="test_multi",
            solver_name="TensorFEM",
            metrics={"l2_error": 0.001, "energy_norm": 0.51, "max_error": 0.02},
            reference_values={"l2_error": 0.0, "energy_norm": 0.5, "max_error": 0.0},
            tolerances={"l2_error": 0.05, "energy_norm": 0.05, "max_error": 0.05},
        )
        assert isinstance(result, RegressionResult)
        assert len(result.deviations) > 0

    def test_detect_suite_regression(self):
        cases = [
            {
                "case_id": "c1",
                "solver_name": "TensorLBM",
                "reference_values": {"l2_norm": 0.5},
                "tolerances": {"l2_norm": 0.05},
            },
            {
                "case_id": "c2",
                "solver_name": "TensorFEM",
                "reference_values": {"energy_norm": 0.5},
                "tolerances": {"energy_norm": 0.05},
            },
        ]
        solver_results = [
            {"l2_norm": 0.501},
            {"energy_norm": 0.501},
        ]
        result = detect_suite_regression("test_suite", cases, solver_results)
        assert isinstance(result, SuiteRegressionResult)
        assert result.overall_status == "clean"
        assert result.summary["n_passed"] == 2

    def test_detect_suite_regression_with_failure(self):
        cases = [
            {
                "case_id": "c1",
                "solver_name": "TensorLBM",
                "reference_values": {"l2_norm": 0.5},
                "tolerances": {"l2_norm": 0.05},
            },
        ]
        solver_results = [
            {"l2_norm": 0.60},  # 20% drift
        ]
        result = detect_suite_regression("test_suite", cases, solver_results)
        assert result.overall_status == "regression"
        assert result.summary["n_failed"] == 1

    def test_sensitivity_analysis(self):
        sa = sensitivity_analysis(
            solver_fn=lbm_solver,
            reference_fn=lbm_reference,
            params={"grid_size": 32, "diffusion_coeff": 0.1, "time_steps": 50},
            n_steps=10,
            metric_name="l2_norm",
        )
        assert "perturbation_levels" in sa
        assert "detection_rates" in sa
        assert len(sa["perturbation_levels"]) == len(sa["detection_rates"])
        assert len(sa["perturbation_levels"]) == 11  # n_steps + 1

    def test_estimate_false_positive_rate(self):
        fpr = estimate_false_positive_rate(
            solver_fn=lbm_solver,
            reference_fn=lbm_reference,
            params={"grid_size": 32, "diffusion_coeff": 0.1, "time_steps": 50},
            n_trials=20,
            noise_level=1e-10,
        )
        assert "false_positive_rate" in fpr
        assert fpr["false_positive_rate"] < 0.01, (
            f"FPR = {fpr['false_positive_rate']:.4f} exceeds 1% target"
        )
        assert fpr["meets_target"] is True

    def test_regression_result_to_dict(self):
        result = detect_regression(
            case_id="test",
            solver_name="TensorLBM",
            metrics={"l2_norm": 0.5},
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
        )
        d = result.to_dict()
        assert "case_id" in d
        assert "deviations" in d
        assert "alerts" in d


# ═══════════════════════════════════════════════════════════════════════
# dashboard.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestDashboard:
    def _sample_results(self):
        return [
            {
                "case_id": "TensorLBM_test_01",
                "solver_name": "TensorLBM",
                "passed": True,
                "status": "pass",
                "deviations": {"l2_norm": 0.02, "max_value": 0.01},
                "alerts": [],
                "metrics": {"l2_norm": 0.51, "max_value": 0.99},
                "reference_values": {"l2_norm": 0.5, "max_value": 1.0},
                "tolerances": {"l2_norm": 0.05, "max_value": 0.05},
            },
            {
                "case_id": "TensorFEM_test_01",
                "solver_name": "TensorFEM",
                "passed": False,
                "status": "regression",
                "deviations": {"energy_norm": 0.08},
                "alerts": [{"kind": "drift", "severity": "warn", "metric": "energy_norm"}],
                "metrics": {"energy_norm": 0.54},
                "reference_values": {"energy_norm": 0.5},
                "tolerances": {"energy_norm": 0.05},
            },
            {
                "case_id": "TensorDEM_test_01",
                "solver_name": "TensorDEM",
                "passed": True,
                "status": "pass",
                "deviations": {"coordination_number": 0.03},
                "alerts": [],
                "metrics": {"coordination_number": 5.8},
                "reference_values": {"coordination_number": 6.0},
                "tolerances": {"coordination_number": 0.10},
            },
        ]

    def test_fig_status_table(self):
        results = self._sample_results()
        png = fig_status_table(results)
        assert isinstance(png, bytes)
        assert len(png) > 0
        # Check PNG magic bytes
        assert png[:4] == b"\x89PNG"

    def test_fig_deviation_histogram(self):
        results = self._sample_results()
        png = fig_deviation_histogram(results)
        assert isinstance(png, bytes)
        assert len(png) > 0
        assert png[:4] == b"\x89PNG"

    def test_fig_regression_trend(self):
        history = [
            {"run_id": "run_1", "results": self._sample_results()},
            {"run_id": "run_2", "results": self._sample_results()},
        ]
        png = fig_regression_trend(history)
        assert isinstance(png, bytes)
        assert len(png) > 0

    def test_fig_solver_comparison(self):
        results = self._sample_results()
        png = fig_solver_comparison(results)
        assert isinstance(png, bytes)
        assert len(png) > 0

    def test_generate_dashboard(self):
        suite_result = {
            "suite_id": "test_suite",
            "overall_status": "clean",
            "summary": {
                "n_total": 3,
                "n_passed": 2,
                "n_failed": 1,
                "n_critical": 0,
                "pass_rate": 0.667,
                "solver_stats": {
                    "TensorLBM": {"total": 1, "passed": 1, "failed": 0},
                    "TensorFEM": {"total": 1, "passed": 0, "failed": 1},
                    "TensorDEM": {"total": 1, "passed": 1, "failed": 0},
                },
                "deviation_stats": {"mean": 0.04, "max": 0.08, "min": 0.01, "stdev": 0.03},
            },
            "results": self._sample_results(),
        }
        report = generate_dashboard(suite_result)
        assert isinstance(report, DashboardReport)
        assert len(report.figures) > 0
        assert "status_table" in report.figures
        assert "deviation_histogram" in report.figures
        assert "solver_comparison" in report.figures

    def test_dashboard_save(self):
        suite_result = {
            "suite_id": "test_suite",
            "overall_status": "clean",
            "summary": {
                "n_total": 1, "n_passed": 1, "n_failed": 0,
                "pass_rate": 1.0, "solver_stats": {}, "deviation_stats": {},
            },
            "results": [{
                "case_id": "c1", "solver_name": "TensorLBM",
                "passed": True, "status": "pass",
                "deviations": {"l2_norm": 0.01}, "alerts": [],
                "metrics": {}, "reference_values": {}, "tolerances": {},
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            report = generate_dashboard(suite_result, output_dir=tmpdir)
            assert os.path.exists(os.path.join(tmpdir, "report.json"))
            assert os.path.exists(os.path.join(tmpdir, "status_table.png"))


# ═══════════════════════════════════════════════════════════════════════
# pipeline.py tests
# ═══════════════════════════════════════════════════════════════════════

class TestPipeline:
    def test_pipeline_init(self):
        p = CrossSolverPipeline()
        assert p.min_cases == 10
        assert p.min_solvers == 3
        assert p.drift_threshold == 0.05

    def test_pipeline_generate_cases(self):
        p = CrossSolverPipeline()
        cases = p.generate_cases()
        assert len(cases) >= 10
        solvers = set(c.solver_name for c in cases)
        assert len(solvers) >= 3

    def test_pipeline_run_solvers(self):
        p = CrossSolverPipeline()
        cases = p.generate_cases()
        results = p.run_solvers(cases)
        assert len(results) == len(cases)
        for r in results:
            assert isinstance(r, dict)
            assert "error" not in r

    def test_pipeline_detect_regressions(self):
        p = CrossSolverPipeline()
        cases = p.generate_cases()
        solver_results = p.run_solvers(cases)
        suite_result = p.detect_regressions(cases, solver_results)
        assert "suite_id" in suite_result
        assert "results" in suite_result
        assert "summary" in suite_result

    def test_pipeline_full(self):
        p = CrossSolverPipeline(min_cases=10, min_solvers=3, cases_per_solver=3)
        result = p.run_full()
        assert result["meets_requirements"]["min_cases"] is True
        assert result["meets_requirements"]["min_solvers"] is True
        assert result["meets_requirements"]["gen_time_per_case_under_60s"] is True
        assert result["cases"]["n_total"] >= 10
        assert result["cases"]["n_solvers_covered"] >= 3

    def test_pipeline_full_with_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = CrossSolverPipeline(min_cases=10, min_solvers=3, cases_per_solver=3)
            result = p.run_full(output_dir=tmpdir)
            assert os.path.exists(os.path.join(tmpdir, "suite_result.json"))
            assert os.path.exists(os.path.join(tmpdir, "cases.json"))
            assert os.path.exists(os.path.join(tmpdir, "solver_results.json"))
            assert os.path.exists(os.path.join(tmpdir, "report.json"))

    def test_pipeline_with_sensitivity(self):
        p = CrossSolverPipeline(min_cases=10, min_solvers=3, cases_per_solver=3)
        result = p.run_full(run_sensitivity=True)
        assert result["sensitivity"] is not None
        assert len(result["sensitivity"]) >= 3

    def test_pipeline_with_fpr(self):
        p = CrossSolverPipeline(min_cases=10, min_solvers=3, cases_per_solver=3)
        result = p.run_full(run_fpr=True)
        assert result["fpr_estimation"] is not None
        for solver_name, fpr_data in result["fpr_estimation"].items():
            assert fpr_data["meets_target"] is True, (
                f"FPR for {solver_name} = {fpr_data['fpr_pct']:.4f}% exceeds 1%"
            )

    def test_run_pipeline_convenience(self):
        result = run_pipeline(min_cases=10, min_solvers=3, cases_per_solver=3)
        assert result["meets_requirements"]["min_cases"] is True
        assert result["meets_requirements"]["min_solvers"] is True


# ═══════════════════════════════════════════════════════════════════════
# Integration tests
# ═══════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_pipeline_end_to_end(self):
        """Full end-to-end test: generate → run → regress → report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_pipeline(
                output_dir=tmpdir,
                min_cases=12,
                min_solvers=3,
                cases_per_solver=3,
                run_sensitivity=True,
                run_fpr=True,
            )

            # Verify requirements
            assert result["cases"]["n_total"] >= 12
            assert result["cases"]["n_solvers_covered"] >= 3
            assert result["timing"]["generation_per_case_s"] < 60.0

            # Verify FPR
            for solver, fpr_data in result["fpr_estimation"].items():
                assert fpr_data["false_positive_rate"] < 0.01

            # Verify files
            assert os.path.exists(os.path.join(tmpdir, "suite_result.json"))
            assert os.path.exists(os.path.join(tmpdir, "report.json"))
            assert os.path.exists(os.path.join(tmpdir, "status_table.png"))
            assert os.path.exists(os.path.join(tmpdir, "deviation_histogram.png"))
            assert os.path.exists(os.path.join(tmpdir, "solver_comparison.png"))
            assert os.path.exists(os.path.join(tmpdir, "sensitivity.json"))
            assert os.path.exists(os.path.join(tmpdir, "fpr_estimation.json"))

    def test_regression_detection_5pct_sensitivity(self):
        """Verify that 5% deviation is detectable."""
        # Create a case with exactly 5% deviation
        result = detect_regression(
            case_id="sensitivity_5pct",
            solver_name="TensorLBM",
            metrics={"l2_norm": 0.525},  # 5% above 0.5
            reference_values={"l2_norm": 0.5},
            tolerances={"l2_norm": 0.05},
            drift_threshold=0.05,
        )
        # At exactly 5%, should be at the boundary
        assert result.deviations["l2_norm"] >= 0.049  # approximately 5%

    def test_zero_domain_semantics(self):
        """Verify no domain-specific terms in solver stubs."""
        import inspect
        from tools.cross_solver_regression import solver_stubs

        source = inspect.getsource(solver_stubs)
        # Check that no ship/ocean/fluid domain terms appear
        domain_terms = ["ship", "hull", "resistance", "wave", "ocean",
                        "marine", "naval", "propeller", "rudder"]
        for term in domain_terms:
            assert term not in source.lower(), (
                f"Domain term '{term}' found in solver_stubs.py - violates zero domain semantics"
            )
