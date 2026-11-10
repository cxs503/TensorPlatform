"""Five-level capability maturity ladder — data model (p251w2-n03).

**Zero-domain-semantics discipline.**  This package never names a concrete
capability, metric, threshold or domain vocabulary.  The five-level ladder,
transition criteria and evidence bundles arrive exclusively as injected
configuration / metadata (see ``criteria.py`` for the data-driven rule DSL).

The ladder has five ordered levels:

    L1 "Implemented"
    L2 "Tested"
    L3 "Benchmark-verified"
    L4 "Coupled prototype"
    L5 "Engineering-qualified"

A capability moves up the ladder only when machine-checkable evidence
satisfies the transition criteria for the target level.  Every transition
(upgrade or downgrade) is recorded with timestamp, evidence refs,
adjudicator and a rollback point so the state can be restored.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Five-level ladder ─────────────────────────────────────────────────────
MATURITY_LEVELS: Tuple[str, ...] = (
    "Implemented",
    "Tested",
    "Benchmark-verified",
    "Coupled prototype",
    "Engineering-qualified",
)
LEVEL_RANK: Dict[str, int] = {name: i for i, name in enumerate(MATURITY_LEVELS)}
TOP_LEVEL = MATURITY_LEVELS[-1]


def level_rank(level: str) -> int:
    """Rank of ``level`` in the five-level ladder (0 = lowest)."""
    if level not in LEVEL_RANK:
        raise KeyError(f"未登记的成熟度等级 {level!r}（应在 {MATURITY_LEVELS} 内）")
    return LEVEL_RANK[level]


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
@dataclass(frozen=True)
class MaturityLevel:
    """One rung of the five-level ladder."""

    name: str
    rank: int

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "rank": self.rank}


@dataclass
class EvidenceBundle:
    """A set of machine-checkable evidence for one capability at one level.

    ``metrics`` is a flat dict of named quantities (e.g. ``unit_test_pass_rate``,
    ``code_coverage``, ``benchmark_error``, ``convergence_verified``).  The
    names are **data**, not code — a transition criterion references them by
    name and the engine only compares values against thresholds.
    """

    capability_id: str
    level: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    collected_at: str = field(default_factory=now_iso)

    @property
    def digest(self) -> str:
        return digest(
            {
                "capability_id": self.capability_id,
                "level": self.level,
                "metrics": self.metrics,
                "source": self.source,
                "payload": self.payload,
            }
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "metrics": self.metrics,
            "source": self.source,
            "payload": self.payload,
            "collected_at": self.collected_at,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class RollbackPoint:
    """A snapshot of the state before a transition, for rollback.

    ``level_before`` is the maturity level the capability held before the
    transition; ``evidence_digest`` is the content address of the evidence
    that justified the previous level.  Restoring a rollback point returns
    the capability to ``level_before`` and re-links the evidence.
    """

    capability_id: str
    level_before: str
    evidence_digest: str
    created_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level_before": self.level_before,
            "evidence_digest": self.evidence_digest,
            "created_at": self.created_at,
        }


@dataclass
class LevelTransition:
    """One recorded upgrade/downgrade/rollback on the maturity ladder.

    Every transition carries: ``timestamp`` (when it happened), ``evidence_refs``
    (the evidence bundle digests that justified it), ``adjudicator`` (who/what
    decided — ``"machine"`` for the auto-gate, or a named reviewer for the
    independent-review rung), and ``rollback_point`` (the state before the
    transition, so it can be restored).
    """

    transition_id: str
    capability_id: str
    from_level: str
    to_level: str
    timestamp: str = field(default_factory=now_iso)
    evidence_refs: List[str] = field(default_factory=list)
    adjudicator: str = "machine"
    rollback_point: Optional[RollbackPoint] = None
    reason: str = ""

    @property
    def direction(self) -> str:
        """``up`` if the level rose, ``down`` if it fell, ``rollback`` if restored."""
        if self.rollback_point is not None and self.to_level == self.rollback_point.level_before:
            return "rollback"
        return "up" if level_rank(self.to_level) > level_rank(self.from_level) else "down"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "timestamp": self.timestamp,
            "evidence_refs": list(self.evidence_refs),
            "adjudicator": self.adjudicator,
            "rollback_point": self.rollback_point.as_dict() if self.rollback_point else None,
            "reason": self.reason,
            "direction": self.direction,
        }


@dataclass
class CapabilityMaturity:
    """A capability's position on the five-level ladder + its transition history."""

    capability_id: str
    current_level: str = "Implemented"
    history: List[LevelTransition] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=now_iso)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current_level": self.current_level,
            "level_rank": level_rank(self.current_level),
            "history": [t.as_dict() for t in self.history],
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }
