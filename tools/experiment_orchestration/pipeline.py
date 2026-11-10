"""Experiment Orchestration Pipeline — prereg → run → verify → report.

Domain-agnostic experiment lifecycle automation:
  1. **preregistration**: JSON schema validation + sha256 freeze of experiment spec
  2. **run**: dispatch to cluster via run_cluster semantics
  3. **verify**: automatic criteria checking against declared acceptance criteria
  4. **report**: paper-level report generation with figures and audit chain

Zero domain semantics: all domain data injected via API/config/artifacts.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .preregistration import Preregistration, validate_experiment_spec
from .runner import ExperimentRunner, RunResult
from .verifier import Verifier, VerificationResult
from .reporter import ReportGenerator, ReportPayload


@dataclass
class PipelineConfig:
    """Configuration for the orchestration pipeline."""
    output_dir: str = "artifacts/experiments"
    schema_path: str | None = None
    max_retries: int = 2
    retry_delay_sec: float = 1.0
    timeout_sec: int = 3600
    declared_tol: float = 0.01


@dataclass
class PipelineResult:
    """Result of a full pipeline execution."""
    experiment_id: str
    prereg: dict[str, Any] = field(default_factory=dict)
    run_result: RunResult | None = None
    verification: VerificationResult | None = None
    report: ReportPayload | None = None
    status: str = "pending"
    errors: list[str] = field(default_factory=list)
    timestamps: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "status": self.status,
            "prereg": self.prereg,
            "run_result": self.run_result.to_dict() if self.run_result else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "report": self.report.to_dict() if self.report else None,
            "errors": self.errors,
            "timestamps": self.timestamps,
        }


class ExperimentPipeline:
    """Full experiment lifecycle: prereg -> run -> verify -> report."""

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or PipelineConfig()
        self.prereg = Preregistration(schema_path=self.config.schema_path)
        self.runner = ExperimentRunner(timeout_sec=self.config.timeout_sec)
        self.verifier = Verifier(declared_tol=self.config.declared_tol)
        self.reporter = ReportGenerator(output_dir=self.config.output_dir)

    def execute(self, experiment_spec: dict[str, Any],
                baseline: Any = None,
                run_fn: Callable | None = None) -> PipelineResult:
        """Execute the full pipeline for an experiment.

        Args:
            experiment_spec: JSON-serializable experiment specification
            baseline: baseline output for comparison (optional)
            run_fn: custom run function (optional, uses default runner if None)

        Returns:
            PipelineResult with all stages populated
        """
        result = PipelineResult(
            experiment_id=experiment_spec.get("experiment_id", "unknown"),
            status="pending",
        )

        # Stage 1: Preregistration
        try:
            result.prereg = self._preregister(experiment_spec)
            result.status = "preregistered"
            result.timestamps["preregistered"] = _now_iso()
        except Exception as e:
            result.errors.append(f"Preregistration failed: {e}")
            result.status = "failed"
            result.timestamps["failed"] = _now_iso()
            return result

        # Stage 2: Run
        try:
            if run_fn is not None:
                run_output = run_fn(experiment_spec)
                result.run_result = RunResult(
                    output=run_output,
                    exit_code=0,
                    timed_out=False,
                    duration_sec=0,
                )
            else:
                result.run_result = self.runner.run(experiment_spec)
            result.status = "running"
            result.timestamps["run_completed"] = _now_iso()
        except Exception as e:
            result.errors.append(f"Run failed: {e}")
            result.run_result = RunResult(
                output=None, exit_code=1, timed_out=False, duration_sec=0,
                error=str(e),
            )
            result.status = "failed"
            result.timestamps["failed"] = _now_iso()
            return result

        # Stage 3: Verify
        try:
            result.verification = self.verifier.verify(
                result.run_result, baseline, experiment_spec
            )
            result.status = "verified"
            result.timestamps["verified"] = _now_iso()
        except Exception as e:
            result.errors.append(f"Verification failed: {e}")
            result.verification = VerificationResult(
                passed=False,
                verdict="error",
                details={"error": str(e)},
            )
            result.status = "failed"
            result.timestamps["failed"] = _now_iso()
            return result

        # Stage 4: Report
        try:
            result.report = self.reporter.generate(
                experiment_id=result.experiment_id,
                prereg=result.prereg,
                run_result=result.run_result,
                verification=result.verification,
                baseline=baseline,
            )
            result.status = "reported"
            result.timestamps["reported"] = _now_iso()
        except Exception as e:
            result.errors.append(f"Report generation failed: {e}")
            result.status = "verified"
            result.timestamps["report_failed"] = _now_iso()

        return result

    def _preregister(self, experiment_spec: dict[str, Any]) -> dict[str, Any]:
        """Stage 1: validate and freeze experiment spec."""
        if self.config.schema_path:
            errors = validate_experiment_spec(experiment_spec, self.config.schema_path)
            if errors:
                raise ValueError(f"Schema validation failed: {errors}")

        spec_json = json.dumps(experiment_spec, sort_keys=True, separators=(",", ":"))
        spec_sha256 = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()

        prereg = {
            "experiment_id": experiment_spec.get("experiment_id", "unknown"),
            "spec_sha256": spec_sha256,
            "spec": experiment_spec,
            "preregistered_at": _now_iso(),
            "schema_validated": self.config.schema_path is not None,
        }
        return prereg


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
