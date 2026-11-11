"""One deterministic healing window: detect → classify → plan → execute → ledger.

Design rules that the acceptance contract depends on:

* **Deterministic** — the same rows, same ``now`` and same ``window_id`` derive
  the same detections, causes, plans and attempt numbers.  Wall-clock time only
  enters through the caller-supplied ``now``.
* **Idempotent** — an action whose ``idempotency_key`` is already in the ledger
  is not executed again; the run reports it as ``skipped``.  Ten re-runs of one
  window leave the ledger byte-identical.
* **Auditable** — every executed action produces one hash-chained entry with
  its cause, evidence-derived confidence, action params and executor outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .classifier import Classification, HealContext, classify
from .detector import Detection, detect_stalled
from .executor import ActionOutcome, DryRunExecutor, Executor
from .ledger import LedgerEntry, SelfHealLedger
from .model import DEFAULT_THRESHOLDS, MissionRow, Thresholds, digest
from .recovery import ACTIONS, RecoveryPlan, plan_recovery


@dataclass
class PipelineResult:
    window_id: str
    now: float
    detected: List[Detection] = field(default_factory=list)
    unobservable: List[Dict[str, Any]] = field(default_factory=list)
    classifications: List[Classification] = field(default_factory=list)
    plans: List[RecoveryPlan] = field(default_factory=list)
    outcomes: Dict[int, ActionOutcome] = field(default_factory=dict)
    new_entries: List[LedgerEntry] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    ledger_digest: str = ""
    ledger_verify: Dict[str, Any] = field(default_factory=dict)

    @property
    def executed(self) -> int:
        return len(self.new_entries)

    def cause_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.classifications:
            counts[item.cause] = counts.get(item.cause, 0) + 1
        return counts

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "now": self.now,
            "detected": [d.as_dict() for d in self.detected],
            "unobservable": list(self.unobservable),
            "classifications": [c.as_dict() for c in self.classifications],
            "plans": [p.as_dict() for p in self.plans],
            "outcomes": {str(k): v.as_dict() for k, v in sorted(self.outcomes.items())},
            "executed": self.executed,
            "skipped": list(self.skipped),
            "ledger_digest": self.ledger_digest,
            "ledger_verify": dict(self.ledger_verify),
        }


def build_context(
    rows: Sequence[MissionRow],
    now: float,
    window_id: str,
    *,
    session_last_seen: Optional[Mapping[str, float]] = None,
    session_index_complete: bool = False,
    closed_sessions: Iterable[str] = (),
    known_sessions: Iterable[str] = (),
    load: Optional[Mapping[str, float]] = None,
) -> HealContext:
    """Derive the duplicate index from the rows themselves; pass in the rest."""
    active_by_key: Dict[str, List[int]] = {}
    for row in rows:
        if row.status not in ("running", "claimed", "in_progress", "in-flight", "dispatched"):
            continue
        if not row.task_key:
            continue
        active_by_key.setdefault(row.task_key, []).append(row.id)
    task_key_owner = {key: min(ids) for key, ids in active_by_key.items()}
    return HealContext(
        now=now,
        window_id=window_id,
        session_last_seen={str(k): float(v) for k, v in (session_last_seen or {}).items()},
        session_index_complete=session_index_complete,
        closed_sessions=tuple(str(s) for s in closed_sessions),
        known_sessions=tuple(str(s) for s in known_sessions),
        load={str(k): float(v) for k, v in (load or {}).items() if isinstance(v, (int, float))},
        active_task_keys={key: len(ids) for key, ids in active_by_key.items()},
        task_key_owner=task_key_owner,
    )


def run_pipeline(
    rows: Iterable[MissionRow | Mapping[str, Any]],
    *,
    now: float,
    window_id: str,
    ledger: Optional[SelfHealLedger] = None,
    context: Optional[HealContext] = None,
    executor: Optional[Executor] = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    on_decision: Optional[Callable[[MissionRow, Detection, Classification, RecoveryPlan, ActionOutcome], None]] = None,
) -> PipelineResult:
    """Run one healing window and append executed actions to ``ledger``."""
    ledger = ledger if ledger is not None else SelfHealLedger()
    norm: List[MissionRow] = [
        raw if isinstance(raw, MissionRow) else MissionRow.from_mapping(raw) for raw in rows
    ]
    by_id = {row.id: row for row in norm}
    ctx = context or build_context(norm, now, window_id)
    ctx.now = now
    ctx.window_id = window_id
    if executor is None:
        executor = DryRunExecutor()

    detections, unobservable = detect_stalled(norm, now, thresholds)
    result = PipelineResult(
        window_id=str(window_id),
        now=float(now),
        detected=detections,
        unobservable=unobservable,
    )

    for det in detections:
        row = by_id[det.mission_id]
        classification = classify(row, det, ctx, thresholds)
        result.classifications.append(classification)
        plan = plan_recovery(
            row,
            classification.cause,
            window_id=window_id,
            prior_attempts=lambda mid, act: ledger.prior_attempts(mid, act, window_id),
            thresholds=thresholds,
            duplicate_of=ctx.task_key_owner.get(row.task_key or ""),
        )
        result.plans.append(plan)

        existing = ledger.find(plan.idempotency_key)
        if existing is not None:
            result.skipped.append(
                {
                    "mission_id": row.id,
                    "action": plan.action,
                    "attempt": plan.attempt,
                    "reason": "already_in_ledger",
                    "entry_seq": existing.seq,
                }
            )
            continue

        outcome = executor.execute(plan, row, {"window_id": window_id, "now": now})
        result.outcomes[row.id] = outcome
        entry, created = ledger.append(
            window_id=window_id,
            mission_id=row.id,
            detection_reason=det.reason,
            cause=classification.cause,
            confidence=classification.confidence,
            action=plan.action,
            params=plan.params,
            attempt=plan.attempt,
            outcome=outcome.status,
            detail={
                "executor": type(executor).__name__,
                "outcome_detail": dict(outcome.detail),
                "evidence": classification.evidence,
                "rationale": plan.rationale,
                "detection": det.as_dict(),
                "confidence": classification.confidence,
            },
            detected_at=det.detected_at,
            created_at=float(now),
            idempotency_key=plan.idempotency_key,
        )
        if created:
            result.new_entries.append(entry)
        if on_decision is not None:
            on_decision(row, det, classification, plan, outcome)

    result.ledger_digest = ledger.digest()
    result.ledger_verify = ledger.verify()
    return result


def recovery_success_rate(entries: Sequence[LedgerEntry]) -> Dict[str, Any]:
    """Fraction of recoverable-cause actions that the executor accepted.

    Accepted = ``applied`` or ``archived`` (see :data:`executor.ACCEPTED_OUTCOMES`).
    Escalations are not counted as successes: they hand the problem to a human,
    which is a different outcome than a machine recovery.
    """
    from .executor import ACCEPTED_OUTCOMES

    recoverable = [e for e in entries if e.cause in ("infra_lost", "timeout", "resource_contention")]
    accepted = [e for e in recoverable if e.outcome in ACCEPTED_OUTCOMES]
    by_cause: Dict[str, Dict[str, int]] = {}
    for entry in recoverable:
        bucket = by_cause.setdefault(entry.cause, {"attempted": 0, "accepted": 0})
        bucket["attempted"] += 1
        if entry.outcome in ACCEPTED_OUTCOMES:
            bucket["accepted"] += 1
    return {
        "attempted": len(recoverable),
        "accepted": len(accepted),
        "success_rate": round(len(accepted) / len(recoverable), 4) if recoverable else None,
        "by_cause": by_cause,
    }


def mean_time_to_recover(entries: Sequence[LedgerEntry]) -> Optional[float]:
    """Mean seconds from threshold-crossing (onset) to the healing action."""
    from .executor import ACCEPTED_OUTCOMES

    deltas = [
        max(0.0, e.created_at - e.detected_at)
        for e in entries
        if e.outcome in ACCEPTED_OUTCOMES
    ]
    return round(sum(deltas) / len(deltas), 2) if deltas else None