"""Report Generator — paper-level report generation with audit chain.

Generates structured reports containing:
  - Experiment metadata and preregistration info
  - Run results and timing
  - Verification verdict and drift analysis
  - Comparison with baseline (if available)
  - Audit chain (SHA-256 fingerprints at each stage)

Zero domain semantics: report structure is generic, domain data injected via artifacts.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner import RunResult
from .verifier import VerificationResult


@dataclass
class ReportPayload:
    """A generated report payload."""
    report_id: str
    experiment_id: str
    content: dict[str, Any]
    artifacts: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "experiment_id": self.experiment_id,
            "content": self.content,
            "artifacts": self.artifacts,
            "generated_at": self.generated_at,
        }


class ReportGenerator:
    """Generates paper-level experiment reports."""

    def __init__(self, output_dir: str = "artifacts/experiments"):
        self.output_dir = output_dir

    def generate(self, experiment_id: str, prereg: dict[str, Any],
                 run_result: RunResult, verification: VerificationResult,
                 baseline: Any = None) -> ReportPayload:
        """Generate a full report for an experiment.

        Args:
            experiment_id: unique experiment identifier
            prereg: preregistration record
            run_result: execution result
            verification: verification result
            baseline: optional baseline for comparison

        Returns:
            ReportPayload with content and artifact paths
        """
        report_id = self._compute_report_id(experiment_id, prereg, run_result, verification)

        content = {
            "title": f"Experiment Report: {experiment_id}",
            "experiment_id": experiment_id,
            "report_id": report_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": {
                "preregistration": self._section_preregistration(prereg),
                "execution": self._section_execution(run_result),
                "verification": self._section_verification(verification),
                "baseline_comparison": self._section_baseline_comparison(baseline, run_result.output),
                "audit_chain": self._section_audit_chain(prereg, run_result, verification),
            },
            "summary": self._summary(verification, run_result),
        }

        # Write report to disk
        artifacts = self._write_report(experiment_id, report_id, content)

        return ReportPayload(
            report_id=report_id,
            experiment_id=experiment_id,
            content=content,
            artifacts=artifacts,
            generated_at=content["generated_at"],
        )

    def _compute_report_id(self, experiment_id: str, prereg: dict[str, Any],
                           run_result: RunResult, verification: VerificationResult) -> str:
        """Compute deterministic report ID from inputs."""
        data = {
            "experiment_id": experiment_id,
            "spec_sha256": prereg.get("spec_sha256", ""),
            "run_exit_code": run_result.exit_code,
            "run_timed_out": run_result.timed_out,
            "verdict": verification.verdict,
        }
        blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _section_preregistration(self, prereg: dict[str, Any]) -> dict[str, Any]:
        return {
            "experiment_id": prereg.get("experiment_id"),
            "spec_sha256": prereg.get("spec_sha256"),
            "preregistered_at": prereg.get("preregistered_at"),
            "schema_validated": prereg.get("schema_validated", False),
        }

    def _section_execution(self, run_result: RunResult) -> dict[str, Any]:
        return {
            "exit_code": run_result.exit_code,
            "timed_out": run_result.timed_out,
            "duration_sec": run_result.duration_sec,
            "error": run_result.error,
            "has_output": run_result.output is not None,
        }

    def _section_verification(self, verification: VerificationResult) -> dict[str, Any]:
        return {
            "passed": verification.passed,
            "verdict": verification.verdict,
            "drift_class": verification.drift_class,
            "criteria_checked": verification.criteria_checked,
            "criteria_passed": verification.criteria_passed,
        }

    def _section_baseline_comparison(self, baseline: Any, output: Any) -> dict[str, Any]:
        if baseline is None:
            return {"available": False}

        from tools.drift_classifier import classify_drift_fingerprint
        drift = classify_drift_fingerprint(baseline, output)

        return {
            "available": True,
            "drift_class": drift["drift_class"],
            "fingerprint_baseline": drift["fingerprint_baseline"],
            "fingerprint_output": drift["fingerprint_candidate"],
        }

    def _section_audit_chain(self, prereg: dict[str, Any],
                              run_result: RunResult,
                              verification: VerificationResult) -> list[dict[str, str]]:
        chain = []

        # Preregistration hash
        if prereg.get("spec_sha256"):
            chain.append({
                "stage": "preregistration",
                "sha256": prereg["spec_sha256"],
                "description": "Experiment spec SHA-256",
            })

        # Output hash
        if run_result.output is not None:
            try:
                output_json = json.dumps(run_result.output, sort_keys=True, separators=(",", ":"))
                output_sha = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
                chain.append({
                    "stage": "output",
                    "sha256": output_sha,
                    "description": "Run output SHA-256",
                })
            except (TypeError, ValueError):
                pass

        # Verification hash
        verif_json = json.dumps(verification.to_dict(), sort_keys=True, separators=(",", ":"))
        verif_sha = hashlib.sha256(verif_json.encode("utf-8")).hexdigest()
        chain.append({
            "stage": "verification",
            "sha256": verif_sha,
            "description": "Verification result SHA-256",
        })

        return chain

    def _summary(self, verification: VerificationResult,
                 run_result: RunResult) -> dict[str, Any]:
        return {
            "verdict": verification.verdict,
            "passed": verification.passed,
            "drift_class": verification.drift_class,
            "duration_sec": run_result.duration_sec,
        }

    def _write_report(self, experiment_id: str, report_id: str,
                      content: dict[str, Any]) -> list[str]:
        """Write report to disk and return artifact paths."""
        out_dir = Path(self.output_dir) / experiment_id
        out_dir.mkdir(parents=True, exist_ok=True)

        artifacts = []

        # Main report JSON
        report_path = out_dir / f"report_{report_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2, sort_keys=True)
        artifacts.append(str(report_path))

        # Summary JSON
        summary_path = out_dir / f"summary_{report_id}.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(content.get("summary", {}), f, ensure_ascii=False, indent=2)
        artifacts.append(str(summary_path))

        return artifacts
