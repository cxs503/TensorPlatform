"""Verifier — automatic criteria checking against declared acceptance criteria.

Checks:
  - Numeric values within declared tolerance
  - Structural completeness (required fields present)
  - Comparison against baseline (if provided)
  - Drift classification for any deviations

Zero domain semantics: criteria are specified in the experiment spec, not hardcoded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from tools.drift_classifier import (
    classify_drift_fingerprint,
    DRIFT_BIT_EXACT,
    DRIFT_ULP_LEVEL,
    DRIFT_THRESHOLD_LEVEL,
    DRIFT_STRUCTURAL,
    DRIFT_TIMEOUT_BLOCKED,
)
from .runner import RunResult


@dataclass
class VerificationResult:
    """Result of verification."""
    passed: bool
    verdict: str  # "pass", "drift", "fail", "blocked"
    details: dict[str, Any]
    drift_class: str | None = None
    criteria_checked: int = 0
    criteria_passed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "drift_class": self.drift_class,
            "criteria_checked": self.criteria_checked,
            "criteria_passed": self.criteria_passed,
            "details": self.details,
        }


class Verifier:
    """Verifies experiment outputs against declared acceptance criteria."""

    def __init__(self, declared_tol: float = 0.01):
        self.declared_tol = declared_tol

    def verify(self, run_result: RunResult, baseline: Any = None,
               experiment_spec: dict[str, Any] | None = None) -> VerificationResult:
        """Verify a run result against criteria.

        Args:
            run_result: the output from running the experiment
            baseline: optional baseline output for comparison
            experiment_spec: optional spec with acceptance criteria

        Returns:
            VerificationResult with verdict and details
        """
        # Check for blocked runs
        if run_result.timed_out:
            return VerificationResult(
                passed=False,
                verdict="blocked",
                drift_class=DRIFT_TIMEOUT_BLOCKED,
                details={"reason": "run_timed_out", "duration_sec": run_result.duration_sec},
            )

        if run_result.exit_code is not None and run_result.exit_code != 0:
            return VerificationResult(
                passed=False,
                verdict="blocked",
                drift_class=DRIFT_TIMEOUT_BLOCKED,
                details={"reason": "non_zero_exit", "exit_code": run_result.exit_code,
                         "error": run_result.error},
            )

        if run_result.output is None:
            return VerificationResult(
                passed=False,
                verdict="blocked",
                drift_class=DRIFT_TIMEOUT_BLOCKED,
                details={"reason": "no_output"},
            )

        # Get acceptance criteria from spec
        criteria = self._extract_criteria(experiment_spec)

        # If baseline provided, do drift comparison
        if baseline is not None:
            return self._verify_with_baseline(run_result.output, baseline, criteria)

        # Otherwise, just check structural completeness and criteria
        return self._verify_criteria(run_result.output, criteria)

    def _extract_criteria(self, experiment_spec: dict[str, Any] | None) -> dict[str, Any]:
        """Extract acceptance criteria from experiment spec."""
        if experiment_spec is None:
            return {}
        return experiment_spec.get("acceptance_criteria", {})

    def _verify_with_baseline(self, output: Any, baseline: Any,
                               criteria: dict[str, Any]) -> VerificationResult:
        """Verify output against baseline with drift classification."""
        tol = criteria.get("tolerance", self.declared_tol)

        drift_result = classify_drift_fingerprint(
            baseline, output,
            declared_tol=tol,
            baseline_timed_out=False,
            candidate_timed_out=False,
        )

        drift_class = drift_result["drift_class"]

        if drift_class == DRIFT_BIT_EXACT:
            return VerificationResult(
                passed=True,
                verdict="pass",
                drift_class=drift_class,
                details={"drift_analysis": drift_result},
                criteria_checked=1,
                criteria_passed=1,
            )
        elif drift_class == DRIFT_ULP_LEVEL:
            return VerificationResult(
                passed=True,
                verdict="pass",
                drift_class=drift_class,
                details={"drift_analysis": drift_result},
                criteria_checked=1,
                criteria_passed=1,
            )
        elif drift_class == DRIFT_THRESHOLD_LEVEL:
            # Check if within tolerance
            max_rel = drift_result.get("evidence", {}).get("max_relative_diff", 1.0)
            if max_rel < tol:
                return VerificationResult(
                    passed=True,
                    verdict="pass",
                    drift_class=drift_class,
                    details={"drift_analysis": drift_result},
                    criteria_checked=1,
                    criteria_passed=1,
                )
            else:
                return VerificationResult(
                    passed=False,
                    verdict="drift",
                    drift_class=drift_class,
                    details={"drift_analysis": drift_result},
                    criteria_checked=1,
                    criteria_passed=0,
                )
        elif drift_class == DRIFT_STRUCTURAL:
            return VerificationResult(
                passed=False,
                verdict="fail",
                drift_class=drift_class,
                details={"drift_analysis": drift_result},
                criteria_checked=1,
                criteria_passed=0,
            )
        else:
            return VerificationResult(
                passed=False,
                verdict="blocked",
                drift_class=drift_class,
                details={"drift_analysis": drift_result},
                criteria_checked=1,
                criteria_passed=0,
            )

    def _verify_criteria(self, output: Any, criteria: dict[str, Any]) -> VerificationResult:
        """Verify output against declared criteria (no baseline)."""
        checked = 0
        passed = 0
        details: dict[str, Any] = {}

        # Check required fields
        required_fields = criteria.get("required_fields", [])
        if required_fields and isinstance(output, dict):
            for field_name in required_fields:
                checked += 1
                if field_name in output:
                    passed += 1
                else:
                    details[f"missing_{field_name}"] = True

        # Check numeric constraints
        numeric_constraints = criteria.get("numeric_constraints", {})
        if isinstance(output, dict):
            for field_name, constraint in numeric_constraints.items():
                if field_name in output:
                    checked += 1
                    value = output[field_name]
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        min_val = constraint.get("min")
                        max_val = constraint.get("max")
                        ok = True
                        if min_val is not None and value < min_val:
                            ok = False
                        if max_val is not None and value > max_val:
                            ok = False
                        if ok:
                            passed += 1
                        else:
                            details[f"constraint_violated_{field_name}"] = {
                                "value": value, "constraint": constraint
                            }

        # Check custom predicates
        predicates = criteria.get("predicates", [])
        for pred in predicates:
            checked += 1
            try:
                if isinstance(pred, dict) and "field" in pred and "op" in pred:
                    field_name = pred["field"]
                    op = pred["op"]
                    expected = pred.get("value")
                    if isinstance(output, dict) and field_name in output:
                        actual = output[field_name]
                        if self._eval_predicate(actual, op, expected):
                            passed += 1
                        else:
                            details[f"predicate_failed_{field_name}"] = pred
                    else:
                        details[f"predicate_field_missing_{field_name}"] = True
                else:
                    passed += 1  # unknown predicate format, skip
            except Exception as e:
                details[f"predicate_error"] = str(e)

        verdict = "pass" if passed == checked else "fail"
        if checked == 0:
            # No criteria to check, assume pass if output exists
            verdict = "pass"
            passed = 1
            checked = 1

        return VerificationResult(
            passed=(passed == checked),
            verdict=verdict,
            details=details,
            criteria_checked=checked,
            criteria_passed=passed,
        )

    def _eval_predicate(self, actual: Any, op: str, expected: Any) -> bool:
        """Evaluate a predicate operation."""
        if op == "eq":
            return actual == expected
        elif op == "ne":
            return actual != expected
        elif op == "gt":
            return isinstance(actual, (int, float)) and actual > expected
        elif op == "gte":
            return isinstance(actual, (int, float)) and actual >= expected
        elif op == "lt":
            return isinstance(actual, (int, float)) and actual < expected
        elif op == "lte":
            return isinstance(actual, (int, float)) and actual <= expected
        elif op == "in":
            return actual in expected
        elif op == "not_in":
            return actual not in expected
        elif op == "is_finite":
            return isinstance(actual, (int, float)) and math.isfinite(actual)
        else:
            return True  # unknown op, don't fail
