"""End-to-end pipeline: generate → run → regress → report.

Orchestrates the full cross-solver regression detection workflow:
1. Generate benchmark cases from templates (≥10 cases, ≥3 solvers)
2. Run solver stubs to get current metrics
3. Detect regressions against reference values
4. Generate dashboard report with figures
5. Save all artifacts

Performance target: <1 minute per case for generation.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .generator import CaseGenerator, BenchmarkCase
from .solver_stubs import SOLVER_REGISTRY
from .regressor import (
    detect_regression, detect_suite_regression,
    sensitivity_analysis, estimate_false_positive_rate,
    DEFAULT_DRIFT_THRESHOLD,
)
from .dashboard import generate_dashboard, DashboardReport

__all__ = ["CrossSolverPipeline", "run_pipeline"]


class CrossSolverPipeline:
    """Full cross-solver regression detection pipeline."""

    def __init__(
        self,
        solver_registry: Optional[Dict] = None,
        drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
        min_cases: int = 10,
        min_solvers: int = 3,
        cases_per_solver: int = 3,
    ):
        self.registry = solver_registry or SOLVER_REGISTRY
        self.drift_threshold = drift_threshold
        self.min_cases = min_cases
        self.min_solvers = min_solvers
        self.cases_per_solver = cases_per_solver
        self.generator = CaseGenerator(self.registry)

    def generate_cases(self) -> List[BenchmarkCase]:
        """Step 1: Generate benchmark cases from templates."""
        return self.generator.generate_suite(
            min_cases=self.min_cases,
            min_solvers=self.min_solvers,
            cases_per_solver=self.cases_per_solver,
        )

    def run_solvers(self, cases: List[BenchmarkCase]) -> List[Dict[str, Any]]:
        """Step 2: Run solver stubs for each case."""
        results = []
        for case in cases:
            solver_entry = self.registry.get(case.solver_name)
            if solver_entry is None:
                results.append({"error": f"Unknown solver: {case.solver_name}"})
                continue

            solver_fn = solver_entry["solver"]
            try:
                result = solver_fn(**case.parameters)
                results.append(result)
            except Exception as e:
                results.append({"error": str(e)})

        return results

    def detect_regressions(
        self,
        cases: List[BenchmarkCase],
        solver_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Step 3: Detect regressions."""
        case_dicts = [c.to_dict() for c in cases]
        suite_result = detect_suite_regression(
            suite_id=f"cross_solver_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            cases=case_dicts,
            solver_results=solver_results,
            drift_threshold=self.drift_threshold,
        )
        return suite_result.to_dict()

    def generate_report(
        self,
        suite_result: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> DashboardReport:
        """Step 4: Generate dashboard report."""
        return generate_dashboard(suite_result, history)

    def run_full(
        self,
        output_dir: Optional[str] = None,
        run_sensitivity: bool = False,
        run_fpr: bool = False,
    ) -> Dict[str, Any]:
        """Run the full pipeline end-to-end.

        Returns comprehensive result dict with all artifacts.
        """
        t_start = time.time()

        # Step 1: Generate cases
        t_gen_start = time.time()
        cases = self.generate_cases()
        gen_time = time.time() - t_gen_start
        gen_time_per_case = gen_time / max(len(cases), 1)

        # Step 2: Run solvers
        t_run_start = time.time()
        solver_results = self.run_solvers(cases)
        run_time = time.time() - t_run_start

        # Step 3: Detect regressions
        t_reg_start = time.time()
        suite_result = self.detect_regressions(cases, solver_results)
        reg_time = time.time() - t_reg_start

        # Step 4: Generate report
        t_report_start = time.time()
        report = self.generate_report(suite_result)
        report_time = time.time() - t_report_start

        total_time = time.time() - t_start

        # Optional: sensitivity analysis
        sensitivity_results = {}
        if run_sensitivity:
            # Map solver to a good metric for sensitivity testing
            solver_metric_map = {
                "TensorLBM": "l2_norm",
                "TensorFEM": "energy_norm",
                "TensorDEM": "coordination_number",
                "TensorFVM": "l2_norm",
            }
            for solver_name, entry in self.registry.items():
                solver_cases = [c for c in cases if c.solver_name == solver_name]
                if solver_cases:
                    case = solver_cases[0]
                    metric = solver_metric_map.get(solver_name, "l2_norm")
                    sa = sensitivity_analysis(
                        solver_fn=entry["solver"],
                        reference_fn=entry["reference"],
                        params=case.parameters,
                        drift_threshold=self.drift_threshold,
                        metric_name=metric,
                    )
                    sensitivity_results[solver_name] = sa

        # Optional: false positive rate estimation
        fpr_results = {}
        if run_fpr:
            for solver_name, entry in self.registry.items():
                solver_cases = [c for c in cases if c.solver_name == solver_name]
                if solver_cases:
                    case = solver_cases[0]
                    fpr = estimate_false_positive_rate(
                        solver_fn=entry["solver"],
                        reference_fn=entry["reference"],
                        params=case.parameters,
                        drift_threshold=self.drift_threshold,
                    )
                    fpr_results[solver_name] = fpr

        # Save artifacts
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

            # Save suite result
            with open(os.path.join(output_dir, "suite_result.json"), "w") as f:
                json.dump(suite_result, f, indent=2, default=str)

            # Save cases
            with open(os.path.join(output_dir, "cases.json"), "w") as f:
                json.dump([c.to_dict() for c in cases], f, indent=2, default=str)

            # Save solver results
            with open(os.path.join(output_dir, "solver_results.json"), "w") as f:
                json.dump(solver_results, f, indent=2, default=str)

            # Save report
            report.save(output_dir)

            # Save sensitivity/FPR if computed
            if sensitivity_results:
                with open(os.path.join(output_dir, "sensitivity.json"), "w") as f:
                    json.dump(sensitivity_results, f, indent=2, default=str)
            if fpr_results:
                with open(os.path.join(output_dir, "fpr_estimation.json"), "w") as f:
                    json.dump(fpr_results, f, indent=2, default=str)

        # Build comprehensive result
        result = {
            "pipeline_version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "drift_threshold": self.drift_threshold,
                "min_cases": self.min_cases,
                "min_solvers": self.min_solvers,
                "cases_per_solver": self.cases_per_solver,
                "n_solvers_registered": len(self.registry),
            },
            "timing": {
                "total_s": round(total_time, 4),
                "generation_s": round(gen_time, 4),
                "generation_per_case_s": round(gen_time_per_case, 6),
                "solver_run_s": round(run_time, 4),
                "regression_detection_s": round(reg_time, 4),
                "report_generation_s": round(report_time, 4),
            },
            "cases": {
                "n_total": len(cases),
                "n_solvers_covered": len(set(c.solver_name for c in cases)),
                "solvers": list(set(c.solver_name for c in cases)),
                "cases": [c.to_dict() for c in cases],
            },
            "suite_result": suite_result,
            "report": report.to_dict(),
            "sensitivity": sensitivity_results or None,
            "fpr_estimation": fpr_results or None,
            "meets_requirements": {
                "min_cases": len(cases) >= self.min_cases,
                "min_solvers": len(set(c.solver_name for c in cases)) >= self.min_solvers,
                "gen_time_per_case_under_60s": gen_time_per_case < 60.0,
            },
        }

        return result


def run_pipeline(
    output_dir: Optional[str] = None,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    min_cases: int = 10,
    min_solvers: int = 3,
    cases_per_solver: int = 3,
    run_sensitivity: bool = False,
    run_fpr: bool = False,
) -> Dict[str, Any]:
    """Convenience function: run the full pipeline."""
    pipeline = CrossSolverPipeline(
        drift_threshold=drift_threshold,
        min_cases=min_cases,
        min_solvers=min_solvers,
        cases_per_solver=cases_per_solver,
    )
    return pipeline.run_full(
        output_dir=output_dir,
        run_sensitivity=run_sensitivity,
        run_fpr=run_fpr,
    )
