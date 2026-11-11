"""Data model + time helpers for the stalled-mission self-healing pipeline.

Nothing here knows about any application domain: a *mission* is a row-shaped
record with a status, timestamps, an optional task key, and a free-form result
JSON blob.  Everything the pipeline decides is derived from those fields.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

UTC = timezone.utc

#: Statuses that mean "an executor currently owns this mission".
ACTIVE_STATUSES = frozenset({"running", "claimed", "in_progress", "in-flight", "dispatched"})
#: Statuses that mean "waiting for a dispatcher slot".
QUEUED_STATUSES = frozenset({"queued", "pending", "ready", "todo", "not_started"})
#: Statuses the pipeline must never touch.
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "done",
        "succeeded",
        "failed",
        "cancelled",
        "canceled",
        "archived",
        "rejected",
        "expired",
        "suspended",
        "paused",
        "withheld",
        "stopped",
        "skipped",
    }
)

#: The six root causes required by the acceptance contract.
CAUSES = (
    "infra_lost",
    "timeout",
    "criteria_infeasible",
    "duplicate_seed",
    "resource_contention",
    "unknown",
)
#: Causes where an automated action can plausibly unblock the mission.
RECOVERABLE_CAUSES = frozenset({"infra_lost", "timeout", "resource_contention"})


def parse_ts(value: Any) -> Optional[float]:
    """Loose ISO-8601 / epoch parser → POSIX seconds (UTC). ``None`` if unusable."""
    if value is None or value == "" or value == "null":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), UTC).isoformat(timespec="seconds")


def canonical(payload: Any) -> str:
    """Canonical JSON — the single serialization used by every digest here."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Thresholds:
    """All stall thresholds in one auditable place.

    ``stall_after_s``       — a running mission with no heartbeat for this long is stalled.
    ``queue_wait_s``        — a queued mission waiting this long never got a slot.
    ``session_stale_s``     — a mission whose owner session has not been seen for this
                              long is treated as infrastructure loss.
    ``max_reschedules``     — infra_lost → re-dispatch at most this many times.
    ``max_timeout_bumps``   — timeout → extend wall clock at most this many times.
    ``max_resource_degrades`` — resource_contention → halve the request at most this often.
    """

    stall_after_s: int = 1800
    queue_wait_s: int = 3600
    session_stale_s: int = 900
    max_reschedules: int = 3
    max_timeout_bumps: int = 2
    max_resource_degrades: int = 2
    timeout_bump_ratio: float = 1.5

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stall_after_s": self.stall_after_s,
            "queue_wait_s": self.queue_wait_s,
            "session_stale_s": self.session_stale_s,
            "max_reschedules": self.max_reschedules,
            "max_timeout_bumps": self.max_timeout_bumps,
            "max_resource_degrades": self.max_resource_degrades,
            "timeout_bump_ratio": self.timeout_bump_ratio,
        }


DEFAULT_THRESHOLDS = Thresholds()


@dataclass
class MissionRow:
    """Normalized view of one ``tensor_dev_missions`` row (or synthetic equivalent)."""

    id: int
    status: str
    created_at: Optional[float] = None
    heartbeat_at: Optional[float] = None
    finished_at: Optional[float] = None
    claimed_by: Optional[str] = None
    agent_session_id: Optional[str] = None
    session_id: Optional[str] = None
    task_key: Optional[str] = None
    repo: Optional[str] = None
    goal: str = ""
    fail_stage: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    budget_used: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "MissionRow":
        def _json(value: Any) -> Dict[str, Any]:
            if isinstance(value, dict):
                return value
            if isinstance(value, (str, bytes)) and value:
                try:
                    parsed = json.loads(value)
                except (ValueError, TypeError):
                    return {}
                return parsed if isinstance(parsed, dict) else {}
            return {}

        return cls(
            id=int(row["id"]),
            status=str(row.get("status") or "").strip().lower(),
            created_at=parse_ts(row.get("created_at")),
            heartbeat_at=parse_ts(row.get("heartbeat_at")),
            finished_at=parse_ts(row.get("finished_at")),
            claimed_by=(str(row["claimed_by"]) if row.get("claimed_by") not in (None, "") else None),
            agent_session_id=(
                str(row["agent_session_id"]) if row.get("agent_session_id") not in (None, "") else None
            ),
            session_id=(str(row["session_id"]) if row.get("session_id") not in (None, "") else None),
            task_key=(str(row["task_key"]) if row.get("task_key") not in (None, "") else None),
            repo=(str(row["repo"]) if row.get("repo") not in (None, "") else None),
            goal=str(row.get("goal") or ""),
            fail_stage=(str(row["fail_stage"]) if row.get("fail_stage") not in (None, "") else None),
            result=_json(row.get("result")),
            budget=_json(row.get("budget")),
            budget_used=_json(row.get("budget_used")),
            raw=dict(row),
        )

    # ── derived quantities ────────────────────────────────────────────────
    def heartbeat_age_s(self, now: float) -> Optional[float]:
        anchor = self.heartbeat_at if self.heartbeat_at is not None else self.created_at
        return None if anchor is None else max(0.0, now - anchor)

    def queue_age_s(self, now: float) -> Optional[float]:
        return None if self.created_at is None else max(0.0, now - self.created_at)

    @property
    def session_key(self) -> Optional[str]:
        """Liveness key, most authoritative first.

        ``session_id`` references ``tensor_agent_sessions.id`` (the row that has
        ``status``/``closed_at``), so it outranks the free-form claim fields.
        """
        return self.session_id or self.claimed_by or self.agent_session_id

    def wall_limit_s(self) -> Optional[float]:
        for key in ("max_wall_seconds", "wall_seconds", "timeout_s"):
            if isinstance(self.budget.get(key), (int, float)):
                return float(self.budget[key])
        return None

    def wall_used_s(self) -> Optional[float]:
        for key in ("wall_seconds", "max_wall_seconds", "elapsed_s"):
            if isinstance(self.budget_used.get(key), (int, float)):
                return float(self.budget_used[key])
        return None

    def acceptance_failures(self) -> int:
        acceptance = self.result.get("acceptance")
        if isinstance(acceptance, dict):
            for key in ("failures", "failed_checks", "rejections"):
                if isinstance(acceptance.get(key), int):
                    return int(acceptance[key])
        for key in ("acceptance_failures", "criteria_failures"):
            if isinstance(self.result.get(key), int):
                return int(self.result[key])
        return 0