"""Data model for the evidence debt compiler (P519-N01).

Zero-domain-semantics discipline: no concrete domain vocabulary appears in code.
All domain-specific terms arrive via injected configuration.

The model tracks the lifecycle of an evidence debt:
  ReportText → ExtractedDebt → ClassifiedDebt → VerificationTask → VerificationResult → LevelUpdate
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_digest(obj: Any) -> str:
    """SHA-256 content address for deduplication / idempotency."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class BaselineType(str, Enum):
    """Classification of missing baseline for a WITHHELD declaration."""
    ANALYTICAL_SOLUTION = "analytical_solution"
    EXPERIMENTAL = "experimental"
    MESH_CONVERGENCE = "mesh_convergence"
    PHYSICAL_CONSISTENCY = "physical_consistency"
    CROSS_VALIDATION = "cross_validation"
    UNKNOWN = "unknown"


class DebtStatus(str, Enum):
    """Lifecycle status of an evidence debt."""
    EXTRACTED = "extracted"
    CLASSIFIED = "classified"
    TASK_GENERATED = "task_generated"
    VERIFICATION_RUNNING = "verification_running"
    VERIFIED = "verified"
    FAILED = "failed"
    UPGRADED = "upgraded"
    REMAINING_WITHHELD = "remaining_withheld"


class VerificationOutcome(str, Enum):
    """Outcome of a verification task execution."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class ExtractedDebt:
    """A WITHHELD declaration extracted from a report text."""
    debt_id: str
    capability_id: str
    source_report_id: str
    declaration_text: str
    context: Dict[str, Any] = field(default_factory=dict)
    extracted_at: str = field(default_factory=now_iso)

    @property
    def digest(self) -> str:
        return content_digest({
            "capability_id": self.capability_id,
            "source_report_id": self.source_report_id,
            "declaration_text": self.declaration_text,
        })

    def as_dict(self) -> Dict[str, Any]:
        return {
            "debt_id": self.debt_id,
            "capability_id": self.capability_id,
            "source_report_id": self.source_report_id,
            "declaration_text": self.declaration_text,
            "context": self.context,
            "extracted_at": self.extracted_at,
            "digest": self.digest,
        }


@dataclass
class ClassifiedDebt:
    """A debt with classified missing baseline type."""
    debt_id: str
    capability_id: str
    baseline_type: BaselineType
    confidence: float = 1.0
    rationale: str = ""
    classification_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "debt_id": self.debt_id,
            "capability_id": self.capability_id,
            "baseline_type": self.baseline_type.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "classification_at": self.classification_at,
        }


@dataclass
class VerificationTask:
    """A generated verification task specification."""
    task_id: str
    debt_id: str
    capability_id: str
    baseline_type: BaselineType
    spec: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "debt_id": self.debt_id,
            "capability_id": self.capability_id,
            "baseline_type": self.baseline_type.value,
            "spec": self.spec,
            "created_at": self.created_at,
        }


@dataclass
class VerificationResult:
    """Result of executing a verification task."""
    task_id: str
    debt_id: str
    capability_id: str
    outcome: VerificationOutcome
    metrics: Dict[str, Any] = field(default_factory=dict)
    evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    executed_at: str = field(default_factory=now_iso)
    error_message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "debt_id": self.debt_id,
            "capability_id": self.capability_id,
            "outcome": self.outcome.value,
            "metrics": self.metrics,
            "evidence_items": self.evidence_items,
            "executed_at": self.executed_at,
            "error_message": self.error_message,
        }


@dataclass
class LevelUpdate:
    """Record of an evidence level update decision."""
    capability_id: str
    debt_id: str
    before_level: str
    after_level: str
    reason: str
    updated_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "debt_id": self.debt_id,
            "before_level": self.before_level,
            "after_level": self.after_level,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }


@dataclass
class PipelineKPI:
    """Key performance indicators for the upgrade pipeline."""
    n_withheld_initial: int = 0
    n_upgraded: int = 0
    n_failed: int = 0
    n_remaining: int = 0
    closure_rate: float = 0.0
    pipeline_run_id: str = ""
    started_at: str = field(default_factory=now_iso)
    finished_at: str = ""

    def compute(self) -> None:
        """Compute closure_rate and validate invariant."""
        total = self.n_upgraded + self.n_failed + self.n_remaining
        if self.n_withheld_initial > 0:
            self.closure_rate = self.n_upgraded / self.n_withheld_initial
        else:
            self.closure_rate = 0.0
        # Invariant check
        if total != self.n_withheld_initial:
            raise ValueError(
                f"KPI invariant violated: {self.n_upgraded}+{self.n_failed}+"
                f"{self.n_remaining}={total} != {self.n_withheld_initial}"
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_withheld_initial": self.n_withheld_initial,
            "n_upgraded": self.n_upgraded,
            "n_failed": self.n_failed,
            "n_remaining": self.n_remaining,
            "closure_rate": round(self.closure_rate, 6),
            "pipeline_run_id": self.pipeline_run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
