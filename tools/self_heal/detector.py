"""Stall detection — decide *which* missions are stalled (no root cause yet).

Two independent signals, both machine-checkable from persisted columns:

``heartbeat_stale``
    status ∈ ACTIVE and ``heartbeat_at`` older than ``stall_after_s``.  A
    running mission without a heartbeat is either dead or wedged; either way it
    will never finish on its own.  A missing ``heartbeat_at`` falls back to
    ``created_at`` so a mission that never sent a single beat is caught too.

``queue_wait_timeout``
    status ∈ QUEUED and ``created_at`` older than ``queue_wait_s``.  A healthy
    queue drains; a mission that waited a full hour never got a slot.

Deliberately *not* detected: otherwise-healthy missions (heartbeat inside the
window), suspended/paused rows, terminal rows, and rows whose timestamps are
missing entirely (``unknown`` is reported by the caller instead of a silent
pass — "cannot look" must not be mistaken for "nothing to see").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import (
    ACTIVE_STATUSES,
    DEFAULT_THRESHOLDS,
    QUEUED_STATUSES,
    TERMINAL_STATUSES,
    MissionRow,
    Thresholds,
)

REASON_HEARTBEAT_STALE = "heartbeat_stale"
REASON_HEARTBEAT_MISSING = "heartbeat_missing"
REASON_QUEUE_WAIT = "queue_wait_timeout"
DETECTION_REASONS = (REASON_HEARTBEAT_STALE, REASON_HEARTBEAT_MISSING, REASON_QUEUE_WAIT)

#: severities are ordinal: lower fires first when the same mission matches twice.
SEVERITY = {REASON_HEARTBEAT_MISSING: 0, REASON_HEARTBEAT_STALE: 1, REASON_QUEUE_WAIT: 2}


@dataclass(frozen=True)
class Detection:
    mission_id: int
    status: str
    reason: str
    age_s: float
    threshold_s: float
    detected_at: float  # epoch at which the threshold was crossed (onset estimate)
    severity: int
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "reason": self.reason,
            "age_s": round(self.age_s, 3),
            "threshold_s": self.threshold_s,
            "detected_at": self.detected_at,
            "severity": self.severity,
            "message": self.message,
        }


def _active(row: MissionRow) -> bool:
    return row.status in ACTIVE_STATUSES


def evaluate(row: MissionRow, now: float, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Optional[Detection]:
    """Return a :class:`Detection` for one row, or ``None`` when it is healthy."""
    if row.status in TERMINAL_STATUSES:
        return None

    if _active(row):
        age = row.heartbeat_age_s(now)
        if age is None:
            return None  # no timestamps at all: caller reports "unobservable"
        if row.heartbeat_at is None:
            if age < thresholds.stall_after_s:
                return None
            return Detection(
                mission_id=row.id,
                status=row.status,
                reason=REASON_HEARTBEAT_MISSING,
                age_s=age,
                threshold_s=float(thresholds.stall_after_s),
                detected_at=(row.created_at or now - age) + thresholds.stall_after_s,
                severity=SEVERITY[REASON_HEARTBEAT_MISSING],
                message=(
                    f"active mission never recorded a heartbeat; created {age / 60:.1f} min ago "
                    f"(threshold {thresholds.stall_after_s / 60:.0f} min)"
                ),
            )
        if age < thresholds.stall_after_s:
            return None
        return Detection(
            mission_id=row.id,
            status=row.status,
            reason=REASON_HEARTBEAT_STALE,
            age_s=age,
            threshold_s=float(thresholds.stall_after_s),
            detected_at=(row.heartbeat_at or now - age) + thresholds.stall_after_s,
            severity=SEVERITY[REASON_HEARTBEAT_STALE],
            message=(
                f"active mission heartbeat is {age / 60:.1f} min old "
                f"(threshold {thresholds.stall_after_s / 60:.0f} min)"
            ),
        )

    if row.status in QUEUED_STATUSES:
        age = row.queue_age_s(now)
        if age is None or age < thresholds.queue_wait_s:
            return None
        return Detection(
            mission_id=row.id,
            status=row.status,
            reason=REASON_QUEUE_WAIT,
            age_s=age,
            threshold_s=float(thresholds.queue_wait_s),
            detected_at=(row.created_at or now - age) + thresholds.queue_wait_s,
            severity=SEVERITY[REASON_QUEUE_WAIT],
            message=(
                f"queued mission waited {age / 60:.1f} min without a slot "
                f"(threshold {thresholds.queue_wait_s / 60:.0f} min)"
            ),
        )

    return None


def _unobservable(row: MissionRow, now: float) -> bool:
    if row.status in TERMINAL_STATUSES:
        return False
    if row.status in ACTIVE_STATUSES:
        return row.created_at is None and row.heartbeat_at is None
    if row.status in QUEUED_STATUSES:
        return row.created_at is None
    return False


def detect_stalled(
    rows: Iterable[MissionRow | Dict[str, Any]],
    now: float,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Tuple[List[Detection], List[Dict[str, Any]]]:
    """Detect stalled missions.

    Returns ``(detections, unobservable)``; the second list is *explicit*
    blindness (rows with no usable timestamp) so that "checked and clean" is
    distinguishable from "could not check".
    """
    detections: List[Detection] = []
    unobservable: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for raw in rows:
        row = raw if isinstance(raw, MissionRow) else MissionRow.from_mapping(raw)
        if row.id in seen:
            continue
        seen.add(row.id)
        if _unobservable(row, now):
            unobservable.append({"mission_id": row.id, "status": row.status, "reason": "no_timestamp"})
            continue
        det = evaluate(row, now, thresholds)
        if det is not None:
            detections.append(det)
    detections.sort(key=lambda d: (d.severity, d.mission_id))
    unobservable.sort(key=lambda item: item["mission_id"])
    return detections, unobservable


def summarize(detections: Sequence[Detection]) -> Dict[str, int]:
    counts: Dict[str, int] = {reason: 0 for reason in DETECTION_REASONS}
    for det in detections:
        counts[det.reason] = counts.get(det.reason, 0) + 1
    return counts