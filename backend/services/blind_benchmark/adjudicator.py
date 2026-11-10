"""
Blind Adjudicator — Decrypt sealed datasets and score predictions.

The adjudicator is the ONLY component with access to the decryption key.
It:
  1. Decrypts the sealed ground truth dataset
  2. Loads the prediction artifact
  3. Computes scoring metrics (error, residual, etc.)
  4. Generates a comprehensive scoring report
  5. Determines PASS/FAIL based on predeclared thresholds

This is the "opening the envelope" step in the blind evaluation protocol.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .crypto import unseal_data
from .sealer import DatasetSealer, SealManifest
from .submission import PredictionSubmitter, PredictionArtifact


@dataclass
class CaseScore:
    """Scoring result for a single case."""
    case_id: str
    predicted: Dict[str, Any]
    reference: Dict[str, Any]
    errors: Dict[str, float]           # quantity -> absolute error
    relative_errors: Dict[str, float]  # quantity -> relative error (%)
    passed: bool
    fail_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "predicted": self.predicted,
            "reference": self.reference,
            "errors": self.errors,
            "relative_errors": self.relative_errors,
            "passed": self.passed,
            "fail_reason": self.fail_reason,
        }


@dataclass
class ScoringReport:
    """Comprehensive scoring report for a submission."""
    report_id: str
    dataset_id: str
    dataset_name: str
    submission_id: str
    solver_name: str
    solver_version: str
    adjudicate_timestamp: float
    adjudicate_timestamp_iso: str
    scoring_rules: Dict[str, Any]      # thresholds, tolerances
    case_scores: List[CaseScore]
    overall_passed: bool
    overall_summary: Dict[str, Any]    # aggregate statistics
    provenance: Dict[str, Any]         # full audit trail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "submission_id": self.submission_id,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
            "adjudicate_timestamp": self.adjudicate_timestamp,
            "adjudicate_timestamp_iso": self.adjudicate_timestamp_iso,
            "scoring_rules": self.scoring_rules,
            "case_scores": [cs.to_dict() for cs in self.case_scores],
            "overall_passed": self.overall_passed,
            "overall_summary": self.overall_summary,
            "provenance": self.provenance,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class BlindAdjudicator:
    """
    Decrypts sealed datasets and scores predictions.

    Usage:
        adjudicator = BlindAdjudicator(
            sealer=sealer,
            submitter=submitter,
            report_dir="/path/to/reports"
        )
        report = adjudicator.adjudicate(
            submission_id="xyz789",
            scoring_rules={"max_relative_error_pct": 15.0}
        )
    """

    def __init__(
        self,
        sealer: DatasetSealer,
        submitter: PredictionSubmitter,
        report_dir: str | Path,
    ):
        """
        Args:
            sealer: DatasetSealer with access to decryption key
            submitter: PredictionSubmitter to load submissions
            report_dir: directory to store scoring reports
        """
        self.sealer = sealer
        self.submitter = submitter
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def adjudicate(
        self,
        submission_id: str,
        scoring_rules: Optional[Dict[str, Any]] = None,
    ) -> ScoringReport:
        """
        Adjudicate a submission: decrypt ground truth, score predictions.

        Args:
            submission_id: which submission to adjudicate
            scoring_rules: dict with thresholds (e.g., max_relative_error_pct)

        Returns:
            ScoringReport with detailed results
        """
        # Default scoring rules
        if scoring_rules is None:
            scoring_rules = {"max_relative_error_pct": 15.0}

        # Load submission
        artifact = self.submitter.load_submission(submission_id)

        # Load and decrypt sealed dataset
        envelope, manifest = self.sealer.load_sealed(artifact.dataset_id)
        plaintext = unseal_data(envelope, self.sealer.master_key)
        ground_truth = json.loads(plaintext.decode("utf-8"))

        # Score each case
        case_scores = []
        for case_id, predicted_values in artifact.predictions.items():
            reference_values = ground_truth.get("cases", {}).get(case_id, {})
            if not reference_values:
                # Case not found in ground truth
                case_scores.append(CaseScore(
                    case_id=case_id,
                    predicted=predicted_values,
                    reference={},
                    errors={},
                    relative_errors={},
                    passed=False,
                    fail_reason=f"Case {case_id} not found in ground truth",
                ))
                continue

            # Compute errors
            errors = {}
            relative_errors = {}
            for quantity, pred_val in predicted_values.items():
                ref_val = reference_values.get(quantity)
                if ref_val is None:
                    continue
                abs_error = abs(pred_val - ref_val)
                errors[quantity] = abs_error
                if ref_val != 0:
                    rel_error = 100.0 * abs_error / abs(ref_val)
                    relative_errors[quantity] = rel_error

            # Determine pass/fail for this case
            max_rel_error = max(relative_errors.values()) if relative_errors else 0.0
            threshold = scoring_rules.get("max_relative_error_pct", 15.0)
            passed = max_rel_error <= threshold

            case_scores.append(CaseScore(
                case_id=case_id,
                predicted=predicted_values,
                reference=reference_values,
                errors=errors,
                relative_errors=relative_errors,
                passed=passed,
                fail_reason=None if passed else f"Max relative error {max_rel_error:.2f}% > {threshold}%",
            ))

        # Overall pass/fail
        overall_passed = all(cs.passed for cs in case_scores)

        # Aggregate statistics
        all_rel_errors = []
        for cs in case_scores:
            all_rel_errors.extend(cs.relative_errors.values())
        overall_summary = {
            "total_cases": len(case_scores),
            "passed_cases": sum(1 for cs in case_scores if cs.passed),
            "failed_cases": sum(1 for cs in case_scores if not cs.passed),
            "mean_relative_error_pct": sum(all_rel_errors) / len(all_rel_errors) if all_rel_errors else 0.0,
            "max_relative_error_pct": max(all_rel_errors) if all_rel_errors else 0.0,
            "min_relative_error_pct": min(all_rel_errors) if all_rel_errors else 0.0,
        }

        # Generate report ID
        report_id = hashlib.sha256(
            f"{submission_id}:{artifact.predictions_hash}:{time.time()}".encode()
        ).hexdigest()[:16]

        # Provenance
        provenance = {
            "dataset_manifest": manifest.to_dict(),
            "submission_artifact": {
                "submission_id": artifact.submission_id,
                "predictions_hash": artifact.predictions_hash,
                "submit_timestamp_iso": artifact.submit_timestamp_iso,
            },
            "ground_truth_hash": manifest.plaintext_sha256,
            "adjudication_engine": "backend/services/blind_benchmark/adjudicator.py",
        }

        report = ScoringReport(
            report_id=report_id,
            dataset_id=artifact.dataset_id,
            dataset_name=manifest.dataset_name,
            submission_id=submission_id,
            solver_name=artifact.solver_name,
            solver_version=artifact.solver_version,
            adjudicate_timestamp=time.time(),
            adjudicate_timestamp_iso=datetime.utcnow().isoformat() + "Z",
            scoring_rules=scoring_rules,
            case_scores=case_scores,
            overall_passed=overall_passed,
            overall_summary=overall_summary,
            provenance=provenance,
        )

        # Store report
        report_path = self.report_dir / f"{report_id}.report.json"
        report_path.write_text(report.to_json(), encoding="utf-8")

        return report

    def load_report(self, report_id: str) -> ScoringReport:
        """Load a scoring report."""
        report_path = self.report_dir / f"{report_id}.report.json"

        if not report_path.exists():
            raise FileNotFoundError(f"Report not found: {report_path}")

        report_dict = json.loads(report_path.read_text(encoding="utf-8"))
        case_scores = [CaseScore(**cs) for cs in report_dict.pop("case_scores")]
        report = ScoringReport(**report_dict, case_scores=case_scores)
        return report
