"""Five-level maturity auto-upgrade gate — the engine (p251w2-n03).

The engine is the *only* place where a capability's maturity level changes.
It is deterministic: given the same evidence and criteria it must return the
same verdict, whether triggered by an evidence-ingest event or a manual
re-judge (C2 consistency requirement).

Adjudication rule
-----------------
1. A capability starts at ``Implemented`` (L1).
2. To move up one rung, the evidence must satisfy **all** checks of the
   transition criteria for that rung (conjunction — one FAIL is a FAIL).
3. A transition that passes is recorded with: timestamp, evidence refs,
   adjudicator, and a **rollback point** (the state before the transition).
4. A transition that fails is recorded as ``rejected`` — the capability
   **stays** at its current level (never silently upgraded).
5. Downgrades are explicit: they happen when new evidence *contradicts* the
   current level's justification (e.g. a test that was passing now fails,
   or an integrity check on the evidence fails).  A downgrade also records
   a rollback point.
6. ``rollback`` restores a capability to a previous level using a recorded
   rollback point — the audit trail keeps both the original transition and
   the rollback.

Every transition is append-only in ``history``; nothing is ever deleted.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .criteria import (
    DEFAULT_CRITERIA, TransitionCheck, TransitionCriteria, evaluate_transition,
)
from .model import (
    MATURITY_LEVELS, LEVEL_RANK, CapabilityMaturity, EvidenceBundle,
    LevelTransition, RollbackPoint, level_rank, now_iso,
)

#: Verdict / outcome constants
UPGRADED = "upgraded"
DOWNGRADED = "downgraded"
HELD = "held"
ROLLED_BACK = "rolled_back"
REJECTED = "rejected"


@dataclass(frozen=True)
class TransitionVerdict:
    """Outcome of evaluating one transition criteria against a capability's evidence."""

    capability_id: str
    from_level: str
    to_level: str
    passed: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "passed": self.passed,
            "checks": self.checks,
            "failures": self.failures,
            "reason": self.reason,
        }


def _transition_key(from_rank: int, to_rank: int) -> str:
    """Registry key for a transition: ``L{from+1}_TO_L{to+1}`` (1-based rung numbers)."""
    return f"L{from_rank + 1}_TO_L{to_rank + 1}"


class MaturityLadderEngine:
    """Deterministic, auditable five-level maturity gate."""

    def __init__(
        self,
        criteria_registry: Optional[Mapping[str, TransitionCriteria]] = None,
    ) -> None:
        self.criteria = dict(criteria_registry or DEFAULT_CRITERIA)
        self._capabilities: Dict[str, CapabilityMaturity] = {}
        self._transitions: List[LevelTransition] = []
        self._rollback_points: Dict[str, List[RollbackPoint]] = {}

    # ── Capability registration ───────────────────────────────────────────
    def register(self, capability_id: str, initial_level: str = "Implemented",
                 metadata: Optional[Mapping[str, Any]] = None) -> CapabilityMaturity:
        """Register a capability at its initial maturity level (default L1)."""
        if capability_id in self._capabilities:
            raise KeyError(f"capability already registered: {capability_id}")
        if initial_level not in LEVEL_RANK:
            raise ValueError(f"unknown initial level {initial_level!r}")
        cap = CapabilityMaturity(
            capability_id=capability_id,
            current_level=initial_level,
            metadata=dict(metadata or {}),
        )
        self._capabilities[capability_id] = cap
        return cap

    def get(self, capability_id: str) -> CapabilityMaturity:
        if capability_id not in self._capabilities:
            raise KeyError(f"unknown capability: {capability_id}")
        return self._capabilities[capability_id]

    def level(self, capability_id: str) -> str:
        return self.get(capability_id).current_level

    # ── Transition criteria ───────────────────────────────────────────────
    def _next_criteria(self, capability_id: str) -> Optional[TransitionCriteria]:
        """The transition criteria for the next rung up from the current level."""
        cur = self.level(capability_id)
        cur_rank = level_rank(cur)
        if cur_rank >= len(MATURITY_LEVELS) - 1:
            return None  # already at top
        key = _transition_key(cur_rank, cur_rank + 1)
        return self.criteria.get(key)

    # ── Evaluation ─────────────────────────────────────────────────────────
    def evaluate(
        self,
        capability_id: str,
        evidence: EvidenceBundle,
        target_level: Optional[str] = None,
    ) -> TransitionVerdict:
        """Evaluate whether ``evidence`` satisfies the transition to ``target_level``.

        If ``target_level`` is None, the next rung up from the current level is
        used.  Returns a :class:`TransitionVerdict` — the engine does **not**
        change the level here; call :meth:`upgrade` to apply.
        """
        cap = self.get(capability_id)
        cur = cap.current_level
        if target_level is None:
            crit = self._next_criteria(capability_id)
            if crit is None:
                return TransitionVerdict(
                    capability_id=capability_id, from_level=cur, to_level=cur,
                    passed=False, reason="already at top level",
                )
            target_level = crit.to_level
        else:
            if target_level not in LEVEL_RANK:
                raise ValueError(f"unknown target level {target_level!r}")
            if level_rank(target_level) <= level_rank(cur):
                return TransitionVerdict(
                    capability_id=capability_id, from_level=cur, to_level=target_level,
                    passed=False, reason=f"target {target_level!r} not above current {cur!r}",
                )
            # Only single-rung transitions are supported by the default
            # registry; a custom registry may define multi-rung jumps.
            key = _transition_key(level_rank(cur), level_rank(target_level))
            crit = self.criteria.get(key)
            if crit is None:
                return TransitionVerdict(
                    capability_id=capability_id, from_level=cur, to_level=target_level,
                    passed=False, reason=f"no transition criteria for {cur}→{target_level}",
                )

        result = crit.evaluate(evidence.metrics)
        return TransitionVerdict(
            capability_id=capability_id,
            from_level=cur,
            to_level=target_level,
            passed=result["passed"],
            checks=result["checks"],
            failures=result["failures"],
            reason="all checks passed" if result["passed"] else
                   "; ".join(f.get("error", f["check"]) for f in result["failures"]),
        )

    # ── Upgrade ────────────────────────────────────────────────────────────
    def upgrade(
        self,
        capability_id: str,
        evidence: EvidenceBundle,
        adjudicator: str = "machine",
    ) -> LevelTransition:
        """Attempt to upgrade a capability one rung.  Records a transition + rollback point.

        Returns the :class:`LevelTransition` record.  If the transition fails,
        the capability **stays** at its current level and the rejection is
        recorded in ``history`` (honest FAIL — never silently upgraded).
        """
        cap = self.get(capability_id)
        verdict = self.evaluate(capability_id, evidence)
        if not verdict.passed:
            # Record the rejection (honest FAIL) — level unchanged.
            transition = LevelTransition(
                transition_id=f"tx-{uuid.uuid4().hex[:16]}",
                capability_id=capability_id,
                from_level=cap.current_level,
                to_level=cap.current_level,
                evidence_refs=[evidence.digest],
                adjudicator=adjudicator,
                reason=f"rejected: {verdict.reason}",
            )
            cap.history.append(transition)
            self._transitions.append(transition)
            return transition

        # Transition passed — record rollback point + apply.
        rollback = RollbackPoint(
            capability_id=capability_id,
            level_before=cap.current_level,
            evidence_digest=evidence.digest,
        )
        transition = LevelTransition(
            transition_id=f"tx-{uuid.uuid4().hex[:16]}",
            capability_id=capability_id,
            from_level=cap.current_level,
            to_level=verdict.to_level,
            evidence_refs=[evidence.digest],
            adjudicator=adjudicator,
            rollback_point=rollback,
            reason="all transition checks passed",
        )
        cap.current_level = verdict.to_level
        cap.history.append(transition)
        cap.updated_at = now_iso()
        self._transitions.append(transition)
        self._rollback_points.setdefault(capability_id, []).append(rollback)
        return transition

    # ── Downgrade ──────────────────────────────────────────────────────────
    def downgrade(
        self,
        capability_id: str,
        reason: str,
        adjudicator: str = "machine",
        target_level: Optional[str] = None,
    ) -> LevelTransition:
        """Explicitly downgrade a capability (e.g. when evidence is invalidated).

        ``target_level`` defaults to one rung below the current level.  Records
        a transition with a rollback point so the state can be restored.
        """
        cap = self.get(capability_id)
        cur = cap.current_level
        if target_level is None:
            target_level = MATURITY_LEVELS[max(0, level_rank(cur) - 1)]
        if level_rank(target_level) >= level_rank(cur):
            raise ValueError(
                f"downgrade target {target_level!r} must be below current {cur!r}"
            )
        rollback = RollbackPoint(
            capability_id=capability_id,
            level_before=cur,
            evidence_digest="",
        )
        transition = LevelTransition(
            transition_id=f"tx-{uuid.uuid4().hex[:16]}",
            capability_id=capability_id,
            from_level=cur,
            to_level=target_level,
            evidence_refs=[],
            adjudicator=adjudicator,
            rollback_point=rollback,
            reason=reason,
        )
        cap.current_level = target_level
        cap.history.append(transition)
        cap.updated_at = now_iso()
        self._transitions.append(transition)
        self._rollback_points.setdefault(capability_id, []).append(rollback)
        return transition

    # ── Rollback ───────────────────────────────────────────────────────────
    def rollback(
        self,
        capability_id: str,
        rollback_point: RollbackPoint,
        adjudicator: str = "machine",
    ) -> LevelTransition:
        """Restore a capability to a previous level using a recorded rollback point."""
        cap = self.get(capability_id)
        if rollback_point.capability_id != capability_id:
            raise ValueError("rollback point belongs to a different capability")
        if rollback_point.level_before not in LEVEL_RANK:
            raise ValueError(f"unknown rollback level {rollback_point.level_before!r}")
        transition = LevelTransition(
            transition_id=f"tx-{uuid.uuid4().hex[:16]}",
            capability_id=capability_id,
            from_level=cap.current_level,
            to_level=rollback_point.level_before,
            evidence_refs=[rollback_point.evidence_digest] if rollback_point.evidence_digest else [],
            adjudicator=adjudicator,
            rollback_point=rollback_point,
            reason="restored from recorded rollback point",
        )
        cap.current_level = rollback_point.level_before
        cap.history.append(transition)
        cap.updated_at = now_iso()
        self._transitions.append(transition)
        return transition

    # ── Audit / summary ────────────────────────────────────────────────────
    def audit_log(self) -> List[Dict[str, Any]]:
        """Full transition history (append-only, nothing deleted)."""
        return [t.as_dict() for t in self._transitions]

    def capability_history(self, capability_id: str) -> List[Dict[str, Any]]:
        return [t.as_dict() for t in self.get(capability_id).history]

    def rollback_points(self, capability_id: str) -> List[Dict[str, Any]]:
        return [r.as_dict() for r in self._rollback_points.get(capability_id, [])]

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic snapshot of all capability levels (for reports)."""
        return {
            "capabilities": {
                cid: cap.current_level
                for cid, cap in sorted(self._capabilities.items())
            },
            "transitions_total": len(self._transitions),
        }

    def level_counts(self) -> Dict[str, int]:
        """Count of capabilities at each maturity level."""
        out: Dict[str, int] = {lvl: 0 for lvl in MATURITY_LEVELS}
        for cap in self._capabilities.values():
            out[cap.current_level] = out.get(cap.current_level, 0) + 1
        return out
