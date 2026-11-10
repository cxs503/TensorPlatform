"""
Adversarial Fixtures — Verify that the adjudicator correctly identifies FAIL.

Injects known-bad predictions to test that:
  1. The adjudicator detects errors above threshold
  2. The scoring report correctly marks cases as FAIL
  3. The overall verdict is FAIL when any case fails

This is the "red team" component of the blind benchmark system.
"""

import json
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .adjudicator import BlindAdjudicator, ScoringReport
from .submission import PredictionSubmitter


@dataclass
class AdversarialFixture:
    """Result of an adversarial test."""
    fixture_id: str
    fixture_type: str                  # "gross_error", "systematic_bias", "random_noise"
    submission_id: str
    expected_fail: bool
    actual_fail: bool
    test_passed: bool                  # did the adjudicator behave as expected?
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "fixture_type": self.fixture_type,
            "submission_id": self.submission_id,
            "expected_fail": self.expected_fail,
            "actual_fail": self.actual_fail,
            "test_passed": self.test_passed,
            "details": self.details,
        }


def inject_adversarial(
    adjudicator: BlindAdjudicator,
    submitter: PredictionSubmitter,
    dataset_id: str,
    ground_truth: Dict[str, Any],
    fixture_type: str = "gross_error",
    seed: Optional[int] = None,
) -> AdversarialFixture:
    """
    Inject adversarial predictions and verify adjudicator detects them.

    Args:
        adjudicator: BlindAdjudicator instance
        submitter: PredictionSubmitter instance
        dataset_id: which dataset to attack
        ground_truth: the unsealed ground truth (for generating bad predictions)
        fixture_type: type of adversarial attack
        seed: random seed for reproducibility

    Returns:
        AdversarialFixture with test results
    """
    if seed is not None:
        random.seed(seed)

    cases = ground_truth.get("cases", {})
    if not cases:
        raise ValueError("Ground truth has no cases")

    # Generate adversarial predictions based on fixture type
    adversarial_predictions = {}

    if fixture_type == "gross_error":
        # Predictions with 50-100% error (should definitely FAIL)
        for case_id, ref_values in cases.items():
            pred_values = {}
            for quantity, ref_val in ref_values.items():
                if isinstance(ref_val, (int, float)):
                    # Add 50-100% error
                    error_factor = 1.5 + random.random() * 0.5
                    pred_values[quantity] = ref_val * error_factor
                else:
                    pred_values[quantity] = ref_val
            adversarial_predictions[case_id] = pred_values

    elif fixture_type == "systematic_bias":
        # All predictions biased high by 20% (should FAIL if threshold < 20%)
        for case_id, ref_values in cases.items():
            pred_values = {}
            for quantity, ref_val in ref_values.items():
                if isinstance(ref_val, (int, float)):
                    pred_values[quantity] = ref_val * 1.20
                else:
                    pred_values[quantity] = ref_val
            adversarial_predictions[case_id] = pred_values

    elif fixture_type == "random_noise":
        # Random noise with std=10% (may pass or fail depending on threshold)
        for case_id, ref_values in cases.items():
            pred_values = {}
            for quantity, ref_val in ref_values.items():
                if isinstance(ref_val, (int, float)):
                    noise = random.gauss(0, 0.10 * abs(ref_val))
                    pred_values[quantity] = ref_val + noise
                else:
                    pred_values[quantity] = ref_val
            adversarial_predictions[case_id] = pred_values

    elif fixture_type == "perfect_prediction":
        # Perfect predictions (should PASS)
        adversarial_predictions = cases.copy()

    else:
        raise ValueError(f"Unknown fixture type: {fixture_type}")

    # Submit adversarial predictions
    artifact = submitter.submit(
        dataset_id=dataset_id,
        solver_name=f"adversarial_{fixture_type}",
        solver_version="v1.0",
        predictions=adversarial_predictions,
        metadata={"fixture_type": fixture_type, "seed": seed},
    )

    # Adjudicate
    report = adjudicator.adjudicate(
        submission_id=artifact.submission_id,
        scoring_rules={"max_relative_error_pct": 15.0},
    )

    # Determine expected vs actual
    expected_fail = fixture_type != "perfect_prediction"
    actual_fail = not report.overall_passed

    # Test passes if adjudicator behaved as expected
    test_passed = (expected_fail == actual_fail)

    fixture = AdversarialFixture(
        fixture_id=f"adv_{artifact.submission_id}",
        fixture_type=fixture_type,
        submission_id=artifact.submission_id,
        expected_fail=expected_fail,
        actual_fail=actual_fail,
        test_passed=test_passed,
        details={
            "report_id": report.report_id,
            "overall_passed": report.overall_passed,
            "overall_summary": report.overall_summary,
            "failed_cases": [cs.case_id for cs in report.case_scores if not cs.passed],
        },
    )

    return fixture


def run_adversarial_suite(
    adjudicator: BlindAdjudicator,
    submitter: PredictionSubmitter,
    dataset_id: str,
    ground_truth: Dict[str, Any],
) -> List[AdversarialFixture]:
    """
    Run a suite of adversarial fixtures to verify adjudicator correctness.

    Tests:
      1. gross_error: 50-100% error → should FAIL
      2. systematic_bias: 20% bias → should FAIL (threshold=15%)
      3. perfect_prediction: exact match → should PASS

    Returns:
        List of AdversarialFixture results
    """
    fixtures = []

    # Test 1: Gross error (should FAIL)
    fixtures.append(inject_adversarial(
        adjudicator, submitter, dataset_id, ground_truth,
        fixture_type="gross_error", seed=42
    ))

    # Test 2: Systematic bias (should FAIL)
    fixtures.append(inject_adversarial(
        adjudicator, submitter, dataset_id, ground_truth,
        fixture_type="systematic_bias", seed=43
    ))

    # Test 3: Perfect prediction (should PASS)
    fixtures.append(inject_adversarial(
        adjudicator, submitter, dataset_id, ground_truth,
        fixture_type="perfect_prediction", seed=44
    ))

    return fixtures
