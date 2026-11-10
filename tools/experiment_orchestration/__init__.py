"""Experiment Orchestration Pipeline — prereg → run → verify → report.

Domain-agnostic experiment lifecycle automation with:
  - Preregistration (JSON schema validation + SHA-256 freeze)
  - Run dispatch (local/cluster with timeout handling)
  - Verification (automatic criteria checking + drift classification)
  - Report generation (paper-level with audit chain)

Zero domain semantics: all domain data injected via API/config/artifacts.
"""
from __future__ import annotations

from .pipeline import ExperimentPipeline, PipelineConfig, PipelineResult
from .preregistration import Preregistration, freeze_spec, validate_experiment_spec
from .runner import ExperimentRunner, RunResult
from .verifier import Verifier, VerificationResult
from .reporter import ReportGenerator, ReportPayload

__all__ = [
    "ExperimentPipeline",
    "PipelineConfig",
    "PipelineResult",
    "Preregistration",
    "freeze_spec",
    "validate_experiment_spec",
    "ExperimentRunner",
    "RunResult",
    "Verifier",
    "VerificationResult",
    "ReportGenerator",
    "ReportPayload",
]
