"""Data model for the capability evidence auto-upgrade pipeline (P218-N01).

**Zero-domain-semantics discipline.**  This package never names a concrete
capability, metric, threshold or domain vocabulary.  Capability slots,
acceptance criteria and evidence items arrive exclusively as injected
configuration / metadata (see ``config/capability_upgrade/registry.example.json``
for a synthetic example and ``tools/capability_upgrade/ledger_adapter.py`` for a
real-ledger adapter that lives in *data*, not in code).

The model has three layers:

``CapabilitySlot``
    A capability position with an evidence level.  Levels are ordered by
    :data:`LEVEL_RANK`; ``WITHHELD`` is the honest default when nothing has been
    proven yet.

``EvidenceItem``
    An immutable, content-addressed record of one piece of evidence.  Every item
    carries a ``source`` block (path / locator / sha256) so that a level can be
    traced back to a concrete case, test node id or reference value.

``Criterion``
    A machine-checkable acceptance rule bound to a capability.  Criteria are
    data (JSON), never code branches.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Evidence levels ────────────────────────────────────────────────────────
LEVELS: Tuple[str, ...] = (
    "NOT_SUPPORTED",
    "STUB",
    "MOCK",
    "WITHHELD",
    "REAL",
)
LEVEL_RANK: Dict[str, int] = {lv: i for i, lv in enumerate(LEVELS)}


def level_rank(level: str) -> int:
    """Rank of ``level``; unknown levels are treated as the weakest (0)."""
    return LEVEL_RANK.get(str(level).upper(), 0)


def now_iso() -> str:
    """UTC timestamp with microsecond resolution (ordering-safe)."""
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialisation (sorted keys, no whitespace drift)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any) -> str:
    """SHA-256 of the canonical JSON form — the content address of a record."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ── Records ────────────────────────────────────────────────────────────────
@dataclass
class CapabilitySlot:
    """A capability position whose evidence level is under adjudication."""

    capability_id: str
    label: str = ""
    initial_level: str = "WITHHELD"
    current_level: str = "WITHHELD"
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "initial_level": self.initial_level,
            "current_level": self.current_level,
            "level_rank": level_rank(self.current_level),
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }


@dataclass
class EvidenceItem:
    """One immutable piece of evidence, bound to exactly one capability."""

    evidence_id: str
    capability_id: str
    kind: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    observed_level: Optional[str] = None
    source: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    ingested_at: str = field(default_factory=now_iso)

    @property
    def digest(self) -> str:
        return digest(
            {
                "evidence_id": self.evidence_id,
                "capability_id": self.capability_id,
                "kind": self.kind,
                "metrics": self.metrics,
                "observed_level": self.observed_level,
                "source": self.source,
                "payload": self.payload,
            }
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "capability_id": self.capability_id,
            "kind": self.kind,
            "metrics": self.metrics,
            "observed_level": self.observed_level,
            "source": self.source,
            "payload": self.payload,
            "ingested_at": self.ingested_at,
            "digest": self.digest,
        }


@dataclass
class Criterion:
    """A machine-checkable acceptance rule (pure data)."""

    criterion_id: str
    capability_id: str
    spec: Dict[str, Any]
    target_level: str = "REAL"
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "capability_id": self.capability_id,
            "target_level": self.target_level,
            "description": self.description,
            "spec": self.spec,
            "digest": digest(self.spec),
        }


@dataclass
class CriterionVerdict:
    """Outcome of evaluating one criterion against a capability's evidence."""

    criterion_id: str
    passed: bool
    matched_evidence_ids: List[str] = field(default_factory=list)
    target_level: str = "REAL"
    failures: List[Dict[str, Any]] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "passed": self.passed,
            "matched_evidence_ids": self.matched_evidence_ids,
            "target_level": self.target_level,
            "failures": self.failures,
            "checks": self.checks,
        }