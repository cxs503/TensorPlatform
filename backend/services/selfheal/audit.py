"""Tamper-evident audit chain for self-healing operations.

Every healing action is appended as ``AuditEvent(seq, ts, node_id, action,
outcome, detail, prev_hash, hash)``.  ``hash`` is the SHA-256 of the canonical
JSON payload plus ``prev_hash``; rewriting, deleting or reordering an event
breaks ``verify()``.  Persistence (``store``) maps one event to one
``campaign_events`` row, so the chain is both queryable in SQL and
cryptographically checkable end to end.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

GENESIS_HASH = "0" * 64

#: Actions that count as *self-healing* operations.  Normal progress events
#: (``node_succeeded`` / ``node_failed`` / ``schedule_dispatched``) are not
#: listed: a healthy, unsaturated campaign must produce none of these
#: (acceptance C4, zero false positives).
HEAL_ACTIONS = frozenset(
    {
        "retry_scheduled",
        "retry_dispatched",
        "recovered",
        "degraded",
        "degrade_rejected",
        "bypassed",
        "escalated",
        "dependency_blocked",
        "saturation_detected",
        "schedule_deferred",
        "cycle_rejected",
    }
)

#: Actions whose subject is the whole plan rather than one node.
PLAN_SCOPED_ACTIONS = frozenset({"saturation_detected", "cycle_rejected"})


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def compute_hash(
    seq: int,
    ts: float,
    node_id: Any,
    action: str,
    outcome: str,
    detail: dict[str, Any],
    prev_hash: str,
) -> str:
    body = {
        "seq": seq,
        "ts": round(float(ts), 6),
        "node_id": node_id,
        "action": action,
        "outcome": outcome,
        "detail": detail,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    ts: float
    node_id: Any
    action: str
    outcome: str
    detail: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    hash: str = ""

    @property
    def is_heal_action(self) -> bool:
        return self.action in HEAL_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "node_id": self.node_id,
            "action": self.action,
            "outcome": self.outcome,
            "detail": self.detail,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        return cls(
            seq=int(data["seq"]),
            ts=float(data["ts"]),
            node_id=data.get("node_id"),
            action=str(data["action"]),
            outcome=str(data.get("outcome", "")),
            detail=dict(data.get("detail") or {}),
            prev_hash=str(data.get("prev_hash") or GENESIS_HASH),
            hash=str(data.get("hash") or ""),
        )


class AuditChain:
    """Append-only, hash-linked list of ``AuditEvent``."""

    def __init__(self, events: Iterable[AuditEvent | dict[str, Any]] | None = None):
        self.events: list[AuditEvent] = []
        for ev in events or ():
            self.events.append(ev if isinstance(ev, AuditEvent) else AuditEvent.from_dict(ev))

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)

    @property
    def last_hash(self) -> str:
        return self.events[-1].hash if self.events else GENESIS_HASH

    def append(
        self,
        ts: float,
        node_id: Any,
        action: str,
        outcome: str,
        **detail: Any,
    ) -> AuditEvent:
        seq = len(self.events) + 1
        prev = self.last_hash
        detail = dict(detail)
        h = compute_hash(seq, ts, node_id, action, outcome, detail, prev)
        ev = AuditEvent(
            seq=seq,
            ts=float(ts),
            node_id=node_id,
            action=action,
            outcome=outcome,
            detail=detail,
            prev_hash=prev,
            hash=h,
        )
        self.events.append(ev)
        return ev

    def verify(self) -> bool:
        prev = GENESIS_HASH
        for i, ev in enumerate(self.events, start=1):
            if ev.seq != i or ev.prev_hash != prev:
                return False
            expected = compute_hash(
                ev.seq, ev.ts, ev.node_id, ev.action, ev.outcome, ev.detail, prev
            )
            if expected != ev.hash:
                return False
            prev = ev.hash
        return True

    def heal_events(self) -> list[AuditEvent]:
        return [ev for ev in self.events if ev.is_heal_action]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [ev.to_dict() for ev in self.events]


def verify_event_dicts(events: Iterable[dict[str, Any]]) -> bool:
    """Verify a serialized chain without constructing an ``AuditChain``."""
    return AuditChain(events).verify()