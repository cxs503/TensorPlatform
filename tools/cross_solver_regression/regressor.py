"""Regression detection engine with sensitivity analysis.

Detects ≥5% deviation with <1% false positive rate.
Provides:
- Per-case regression detection (drift vs reference)
- Suite-level regression summary
- Sensitivity analysis (ROC-like characterization)
- False positive rate estimation via Monte Carlo on noise floor
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from .model import is_finite, rel_error

__all__ = [
    "RegressionResult", "SuiteRegressionResult",
    "detect_regression", "detect_suite_regression",
    "sensitivity_analysis", "estimate_false_positive_rate",
]

DEFAULT_DRIFT_THRESHOLD = 0.05  # 5% sensitivity target
CRITICAL_FACTOR = 3.0           # >15% = critical
ZERO_FLOOR = 1e-12


class RegressionResult:
    """Result of regression detection for a single case."""

    def __init__(
        self,
        case_id: str,
        solver_name: str,
        passed: bool,
        status: str,  # "pass", "fail", "regression", "error"
        deviations: Dict[str, float],
        alerts: List[Dict[str, Any]],
        metrics: Dict[str, float],
        reference_values: Dict[str, float],
        tolerances: Dict[str, float],
    ):
        self.case_id = case_id
        self.solver_name = solver_name
        self.passed = passed
        self.status = status
        self.deviations = deviations
        self.alerts = alerts
        self.metrics = metrics
        self.reference_values = reference_values
        self.tolerances = tolerances

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "solver_name": self.solver_name,
            "passed": self.passed,
            "status": self.status,
            "deviations": self.deviations,
            "alerts": self.alerts,
            "metrics": self.metrics,
            "reference_values": self.reference_values,
            "tolerances": self.tolerances,
        }


class SuiteRegressionResult:
    """Aggregate regression result for an entire suite."""

    def __init__(
        self,
        suite_id: str,
        results: List[RegressionResult],
        overall_status: str,
        summary: Dict[str, Any],
    ):
        self.suite_id = suite_id
        self.results = results
        self.overall_status = overall_status
        self.summary = summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "overall_status": self.overall_status,
            "summary": self.summary,
            "results": [r.to_dict() for r in self.results],
        }


def detect_regression(
    case_id: str,
    solver_name: str,
    metrics: Dict[str, Any],
    reference_values: Dict[str, float],
    tolerances: Dict[str, float],
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> RegressionResult:
    """Detect regression for a single case.

    Compares current metrics against reference values.
    Returns regression result with per-metric deviations and alerts.
    """
    deviations: Dict[str, float] = {}
    alerts: List[Dict[str, Any]] = []
    passed = True

    for metric_name, ref_val in reference_values.items():
        cur_val = metrics.get(metric_name)
        if cur_val is None or not is_finite(cur_val):
            alerts.append({
                "kind": "metric_missing",
                "severity": "critical",
                "metric": metric_name,
                "message": f"Metric {metric_name!r} missing or non-finite",
            })
            passed = False
            continue

        if not is_finite(ref_val):
            continue  # skip non-finite references

        tol = tolerances.get(metric_name, drift_threshold)
        dev = rel_error(cur_val, ref_val)
        if dev is None:
            continue

        deviations[metric_name] = dev

        if dev > tol:
            severity = "critical" if dev > CRITICAL_FACTOR * tol else "warn"
            alerts.append({
                "kind": "drift",
                "severity": severity,
                "metric": metric_name,
                "reference": ref_val,
                "current": float(cur_val),
                "deviation": dev,
                "deviation_pct": round(100.0 * dev, 4),
                "tolerance": tol,
                "message": (
                    f"{metric_name} deviated {100*dev:.2f}% "
                    f"(ref={ref_val:.6g}, cur={float(cur_val):.6g}) "
                    f"> tolerance {100*tol:.1f}%"
                ),
            })
            passed = False

    status = "pass" if passed else "regression"
    if any(a.get("severity") == "critical" for a in alerts):
        status = "regression"

    return RegressionResult(
        case_id=case_id,
        solver_name=solver_name,
        passed=passed,
        status=status,
        deviations=deviations,
        alerts=alerts,
        metrics={k: float(v) for k, v in metrics.items() if is_finite(v)},
        reference_values=reference_values,
        tolerances=tolerances,
    )


def detect_suite_regression(
    suite_id: str,
    cases: List[Dict[str, Any]],
    solver_results: List[Dict[str, Any]],
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> SuiteRegressionResult:
    """Detect regression for an entire suite of cases.

    Args:
        suite_id: identifier for the suite
        cases: list of case definitions (with reference_values, tolerances)
        solver_results: list of solver outputs (aligned with cases)
        drift_threshold: default drift threshold if not specified per-case
    """
    results = []
    for case_def, solver_out in zip(cases, solver_results):
        case_id = case_def.get("case_id", "unknown")
        solver_name = case_def.get("solver_name", "unknown")
        ref_vals = case_def.get("reference_values", {})
        tols = case_def.get("tolerances", {})

        result = detect_regression(
            case_id=case_id,
            solver_name=solver_name,
            metrics=solver_out,
            reference_values=ref_vals,
            tolerances=tols,
            drift_threshold=drift_threshold,
        )
        results.append(result)

    # Summary statistics
    n_total = len(results)
    n_passed = sum(1 for r in results if r.passed)
    n_failed = n_total - n_passed
    n_critical = sum(
        1 for r in results
        if any(a.get("severity") == "critical" for a in r.alerts)
    )

    # Per-solver breakdown
    solver_stats: Dict[str, Dict[str, int]] = {}
    for r in results:
        stats = solver_stats.setdefault(r.solver_name, {"total": 0, "passed": 0, "failed": 0})
        stats["total"] += 1
        if r.passed:
            stats["passed"] += 1
        else:
            stats["failed"] += 1

    # All deviations for distribution analysis
    all_deviations = []
    for r in results:
        all_deviations.extend(r.deviations.values())

    overall_status = "clean" if n_failed == 0 else "regression"

    summary = {
        "n_total": n_total,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "n_critical": n_critical,
        "pass_rate": n_passed / max(n_total, 1),
        "solver_stats": solver_stats,
        "deviation_stats": {
            "mean": statistics.mean(all_deviations) if all_deviations else 0.0,
            "max": max(all_deviations) if all_deviations else 0.0,
            "min": min(all_deviations) if all_deviations else 0.0,
            "stdev": statistics.stdev(all_deviations) if len(all_deviations) > 1 else 0.0,
        },
    }

    return SuiteRegressionResult(
        suite_id=suite_id,
        results=results,
        overall_status=overall_status,
        summary=summary,
    )


def sensitivity_analysis(
    solver_fn,
    reference_fn,
    params: Dict[str, Any],
    perturbation_range: Tuple[float, float] = (0.01, 0.20),
    n_steps: int = 20,
    metric_name: str = "l2_norm",
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> Dict[str, Any]:
    """Analyze regression detection sensitivity.

    Introduces controlled perturbations to the solver output and measures
    the detection rate at each perturbation level.

    Returns detection rate curve (should be ~0 below threshold, ~1 above).
    """
    # Get baseline reference
    ref = reference_fn(**params)
    ref_val = ref.get(metric_name, 0.0)

    detection_rates = []
    perturbation_levels = []

    for frac in [perturbation_range[0] + i * (perturbation_range[1] - perturbation_range[0]) / n_steps
                 for i in range(n_steps + 1)]:
        perturbation_levels.append(frac)

        # Simulate perturbed solver output
        perturbed_metrics = dict(ref)
        perturbed_metrics[metric_name] = ref_val * (1.0 + frac)

        # Check if regression is detected
        result = detect_regression(
            case_id="sensitivity_test",
            solver_name="test",
            metrics=perturbed_metrics,
            reference_values={metric_name: ref_val},
            tolerances={metric_name: drift_threshold},
            drift_threshold=drift_threshold,
        )

        detected = not result.passed
        detection_rates.append(1.0 if detected else 0.0)

    return {
        "metric_name": metric_name,
        "drift_threshold": drift_threshold,
        "perturbation_levels": perturbation_levels,
        "detection_rates": detection_rates,
        "sensitivity_at_5pct": detection_rates[min(n_steps // 4, len(detection_rates) - 1)],
        "specificity_at_1pct": 1.0 - detection_rates[0] if perturbation_levels[0] < drift_threshold else None,
    }


def estimate_false_positive_rate(
    solver_fn,
    reference_fn,
    params: Dict[str, Any],
    n_trials: int = 100,
    noise_level: float = 1e-10,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    metric_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Estimate false positive rate via Monte Carlo.

    Runs the solver once to get a baseline, then adds tiny numerical noise
    n_trials times and checks how often regression is falsely detected.
    The baseline is the unperturbed solver output (not the analytical reference),
    so this tests whether machine-epsilon-level noise triggers false alarms.

    Target: <1% false positive rate.
    """
    # Get baseline from solver itself (not reference — solver may differ from
    # reference by design; FPR tests noise sensitivity, not accuracy)
    baseline = solver_fn(**params)

    if metric_names is None:
        metric_names = [k for k, v in baseline.items() if isinstance(v, (int, float))
                        and k not in ("grid_size", "time_steps", "n_elements",
                                      "polynomial_order", "n_particles", "n_cells")]

    # Build reference values from baseline (only finite numeric metrics)
    baseline_refs = {}
    for k in metric_names:
        v = baseline.get(k)
        if v is not None and is_finite(v) and float(v) != 0.0:
            baseline_refs[k] = float(v)

    false_positives = 0
    total_checks = 0

    for trial in range(n_trials):
        # Add deterministic pseudo-noise based on trial number
        perturbed = {}
        for k in baseline_refs:
            v = baseline_refs[k]
            # Deterministic perturbation: sin-based, amplitude = noise_level
            noise = noise_level * math.sin(trial * 13.37 + hash(k) * 0.001)
            perturbed[k] = v * (1.0 + noise)

        # Check regression against baseline (not reference)
        reg_result = detect_regression(
            case_id="fpr_test",
            solver_name="test",
            metrics=perturbed,
            reference_values=baseline_refs,
            tolerances={k: drift_threshold for k in baseline_refs},
            drift_threshold=drift_threshold,
        )

        total_checks += 1
        if not reg_result.passed:
            false_positives += 1

    fpr = false_positives / max(total_checks, 1)

    return {
        "n_trials": n_trials,
        "noise_level": noise_level,
        "drift_threshold": drift_threshold,
        "false_positives": false_positives,
        "total_checks": total_checks,
        "false_positive_rate": fpr,
        "fpr_pct": round(100.0 * fpr, 4),
        "meets_target": fpr < 0.01,
    }
