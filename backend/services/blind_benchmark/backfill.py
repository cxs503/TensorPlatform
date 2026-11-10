"""
Benchmark Backfill — Write adjudication results to guide.benchmark_cases.

After adjudication, the results are automatically written to the guide tracker
with full provenance (dataset hash, submission hash, report hash, timestamps).

This creates an immutable audit trail from dataset sealing → prediction → scoring.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adjudicator import ScoringReport


@dataclass
class BenchmarkCaseEntry:
    """A single entry in guide.benchmark_cases."""
    case_id: str
    benchmark_name: str
    solver_name: str
    solver_version: str
    verdict: str                       # PASS | FAIL | WITHHELD
    scoring_report_id: str
    dataset_id: str
    submission_id: str
    dataset_hash: str                  # sha256 of sealed dataset
    submission_hash: str               # sha256 of predictions
    report_hash: str                   # sha256 of report
    metrics: Dict[str, Any]            # key metrics (error, etc.)
    timestamp: float
    timestamp_iso: str
    provenance: Dict[str, Any]         # full audit trail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "benchmark_name": self.benchmark_name,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
            "verdict": self.verdict,
            "scoring_report_id": self.scoring_report_id,
            "dataset_id": self.dataset_id,
            "submission_id": self.submission_id,
            "dataset_hash": self.dataset_hash,
            "submission_hash": self.submission_hash,
            "report_hash": self.report_hash,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
            "timestamp_iso": self.timestamp_iso,
            "provenance": self.provenance,
        }


class BenchmarkBackfill:
    """
    Writes adjudication results to guide.benchmark_cases with full provenance.

    Usage:
        backfill = BenchmarkBackfill(guide_dir="/path/to/guide")
        entries = backfill.backfill_report(report)
    """

    def __init__(self, guide_dir: str | Path):
        """
        Args:
            guide_dir: directory containing guide.benchmark_cases.json
        """
        self.guide_dir = Path(guide_dir)
        self.guide_dir.mkdir(parents=True, exist_ok=True)
        self.guide_file = self.guide_dir / "benchmark_cases.json"

    def _load_guide(self) -> Dict[str, Any]:
        """Load existing guide file or create new one."""
        if self.guide_file.exists():
            return json.loads(self.guide_file.read_text(encoding="utf-8"))
        return {
            "version": "1.0",
            "description": "Blind benchmark adjudication results with full provenance",
            "entries": [],
        }

    def _save_guide(self, guide: Dict[str, Any]) -> None:
        """Save guide file."""
        self.guide_file.write_text(
            json.dumps(guide, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def backfill_report(self, report: ScoringReport) -> List[BenchmarkCaseEntry]:
        """
        Backfill a scoring report into guide.benchmark_cases.

        Args:
            report: ScoringReport from adjudication

        Returns:
            List of BenchmarkCaseEntry objects created
        """
        guide = self._load_guide()

        # Compute report hash
        report_json = report.to_json()
        report_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()

        # Extract provenance
        dataset_hash = report.provenance.get("ground_truth_hash", "")
        submission_hash = report.provenance.get("submission_artifact", {}).get("predictions_hash", "")

        entries = []
        for case_score in report.case_scores:
            # Generate case entry ID
            case_entry_id = hashlib.sha256(
                f"{report.report_id}:{case_score.case_id}:{time.time()}".encode()
            ).hexdigest()[:16]

            # Determine verdict
            verdict = "PASS" if case_score.passed else "FAIL"

            # Extract metrics
            metrics = {
                "errors": case_score.errors,
                "relative_errors": case_score.errors,
                "max_relative_error_pct": max(case_score.relative_errors.values()) if case_score.relative_errors else 0.0,
                "mean_relative_error_pct": sum(case_score.relative_errors.values()) / len(case_score.relative_errors) if case_score.relative_errors else 0.0,
            }

            entry = BenchmarkCaseEntry(
                case_id=case_entry_id,
                benchmark_name=report.dataset_name,
                solver_name=report.solver_name,
                solver_version=report.solver_version,
                verdict=verdict,
                scoring_report_id=report.report_id,
                dataset_id=report.dataset_id,
                submission_id=report.submission_id,
                dataset_hash=dataset_hash,
                submission_hash=submission_hash,
                report_hash=report_hash,
                metrics=metrics,
                timestamp=time.time(),
                timestamp_iso=datetime.utcnow().isoformat() + "Z",
                provenance={
                    "dataset_manifest": report.provenance.get("dataset_manifest"),
                    "submission_artifact": report.provenance.get("submission_artifact"),
                    "adjudication_engine": report.provenance.get("adjudication_engine"),
                    "case_id_in_dataset": case_score.case_id,
                },
            )

            entries.append(entry)
            guide["entries"].append(entry.to_dict())

        # Save updated guide
        self._save_guide(guide)

        return entries

    def load_guide(self) -> Dict[str, Any]:
        """Load the guide file."""
        return self._load_guide()

    def query_entries(
        self,
        solver_name: Optional[str] = None,
        verdict: Optional[str] = None,
        benchmark_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query guide entries with optional filters.

        Args:
            solver_name: filter by solver
            verdict: filter by verdict (PASS/FAIL)
            benchmark_name: filter by benchmark

        Returns:
            List of matching entries
        """
        guide = self._load_guide()
        entries = guide.get("entries", [])

        if solver_name:
            entries = [e for e in entries if e.get("solver_name") == solver_name]
        if verdict:
            entries = [e for e in entries if e.get("verdict") == verdict]
        if benchmark_name:
            entries = [e for e in entries if e.get("benchmark_name") == benchmark_name]

        return entries
