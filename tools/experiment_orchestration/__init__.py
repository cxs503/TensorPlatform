"""Experiment Orchestration Pipeline — prereg → run → verify → report.

Domain-agnostic experiment lifecycle automation with:
  - Preregistration (JSON schema validation + SHA-256 freeze)
  - Run dispatch (local/cluster with timeout handling)
  - Verification (automatic criteria checking + drift classification)
  - Report generation (paper-level with audit chain)
  - Reproducibility verification (multi-run consistency)
  - Capability ledger integration (evidence tracking)
  - Synthetic domain fixtures (zero domain semantics)

Zero domain semantics: all domain data injected via API/config/artifacts.
"""
from __future__ import annotations

from .pipeline import ExperimentPipeline, PipelineConfig, PipelineResult
from .preregistration import Preregistration, freeze_spec, validate_experiment_spec
from .runner import ExperimentRunner, RunResult
from .verifier import Verifier, VerificationResult
from .reporter import ReportGenerator, ReportPayload
from .reproducibility import ReproducibilityResult, verify_reproducibility, deterministic_computation
from .capability_integration import CapabilityLedger_Integrator, CapabilityUpdate
from .synthetic_fixtures import (
    get_synthetic_experiment_1,
    get_synthetic_experiment_2,
    get_synthetic_experiment_3,
    get_synthetic_schema,
    save_synthetic_fixtures,
)

__all__ = [
    # Core pipeline
    "ExperimentPipeline",
    "PipelineConfig",
    "PipelineResult",
    # Preregistration
    "Preregistration",
    "freeze_spec",
    "validate_experiment_spec",
    # Runner
    "ExperimentRunner",
    "RunResult",
    # Verifier
    "Verifier",
    "VerificationResult",
    # Reporter
    "ReportGenerator",
    "ReportPayload",
    # Reproducibility
    "ReproducibilityResult",
    "verify_reproducibility",
    "deterministic_computation",
    # Capability integration
    "CapabilityLedger_Integrator",
    "CapabilityUpdate",
    # Synthetic fixtures
    "get_synthetic_experiment_1",
    "get_synthetic_experiment_2",
    "get_synthetic_experiment_3",
    "get_synthetic_schema",
    "save_synthetic_fixtures",
]
