"""Mission-level self-healing API (stalled detection → cause → bounded recovery → ledger).

Endpoints under ``/api/v1/tensor/self-heal``:

============================  ======  ========================================================
path                          verb    purpose
============================  ======  ========================================================
``/run``                      POST    run one healing window (DB rows or an injected corpus)
``/status``                   GET     ledger head, chain verification, counters
``/ledger``                   GET     audit entries (newest first)
``/dashboard``                GET     success rate / time-to-recover / cause distribution
``/config``                   GET     thresholds actually in force
============================  ======  ========================================================

Design notes
------------
* **Zero application semantics.**  Missions are opaque rows; the six root
  causes and their actions live in ``tools.self_heal`` and are machine-checked
  by ``tools.benchmark_runner.domain_guard``.
* **Fail closed on a broken chain.**  If the persisted ledger does not verify,
  ``/run`` refuses (HTTP 409) instead of appending onto tampered history.
* **Dry by default.**  ``dry_run=true`` performs no mission writes; set
  ``dry_run=false`` to use :class:`tools.self_heal.executor.SqlMissionExecutor`.
  Redundant copies are retired as ``status='stopped'`` + ``result.archived=true``
  because ``contracts/mission_status_vocab.v1.json`` has no ``archived`` state.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from database.config import get_db
from tools.self_heal import (
    MissionRow,
    SelfHealLedger,
    SqlMissionExecutor,
    Thresholds,
    run_pipeline,
)
from tools.self_heal.executor import DryRunExecutor
from tools.self_heal.ledger import LedgerEntry
from tools.self_heal.pipeline import build_context, mean_time_to_recover, recovery_success_rate

UTC = timezone.utc
router = APIRouter(prefix="/tensor/self-heal", tags=["任务自愈"])

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS self_heal_ledger (
    seq BIGINT PRIMARY KEY,
    window_id VARCHAR(48) NOT NULL,
    mission_id BIGINT NOT NULL,
    detection_reason VARCHAR(32) NOT NULL,
    cause VARCHAR(32) NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    action VARCHAR(32) NOT NULL,
    params TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    detail TEXT NOT NULL,
    detected_at DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    prev_hash VARCHAR(64) NOT NULL,
    hash VARCHAR(64) NOT NULL
)
"""

ACTIVE_STATUSES = ("running", "claimed", "in_progress", "dispatched")
QUEUED_STATUSES = ("queued", "pending", "ready")
MISSION_COLUMNS = (
    "id,status,created_at,heartbeat_at,finished_at,claimed_by,agent_session_id,"
    "task_key,repo,goal,fail_stage,result,budget,budget_used,session_id"
)


# ── request models ────────────────────────────────────────────────────────


class ThresholdIn(BaseModel):
    stall_after_s: int = Field(default=1800, ge=60, le=86400)
    queue_wait_s: int = Field(default=3600, ge=60, le=604800)
    session_stale_s: int = Field(default=900, ge=60, le=86400)
    max_reschedules: int = Field(default=3, ge=0, le=10)
    max_timeout_bumps: int = Field(default=2, ge=0, le=10)
    max_resource_degrades: int = Field(default=2, ge=0, le=10)
    timeout_bump_ratio: float = Field(default=1.5, ge=1.0, le=3.0)


class RunIn(BaseModel):
    now: Optional[float] = None
    window_id: Optional[str] = None
    dry_run: bool = True
    #: Optional injected corpus (dict rows with at least ``id``/``status``);
    #: when omitted the window is built from ``tensor_dev_missions``.
    missions: Optional[List[Dict[str, Any]]] = None
    #: Optional side context for injected corpora.
    session_last_seen: Optional[Dict[str, float]] = None
    known_sessions: Optional[List[str]] = None
    closed_sessions: Optional[List[str]] = None
    load: Optional[Dict[str, float]] = None
    thresholds: Optional[ThresholdIn] = None
    limit: int = Field(default=5000, ge=1, le=50000)


# ── helpers ───────────────────────────────────────────────────────────────


def _ensure_schema(db: Session) -> None:
    db.execute(text(SCHEMA_DDL))
    db.commit()


def _dialect(db: Session) -> str:
    try:
        return db.bind.dialect.name  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - defensive
        return "sqlite"


def _cutoff_iso(now: float, days: int = 7) -> str:
    return datetime.fromtimestamp(now - days * 86400.0, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _load_ledger(db: Session) -> SelfHealLedger:
    rows = db.execute(
        text(
            "SELECT seq,window_id,mission_id,detection_reason,cause,confidence,action,params,"
            "attempt,outcome,detail,detected_at,created_at,idempotency_key,prev_hash,hash "
            "FROM self_heal_ledger ORDER BY seq"
        )
    ).mappings().all()
    entries: List[LedgerEntry] = []
    for row in rows:
        payload = dict(row)
        for key in ("params", "detail"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    payload[key] = json.loads(value)
                except ValueError:
                    payload[key] = {}
        entries.append(LedgerEntry.from_dict(payload))
    return SelfHealLedger(entries)


def _load_missions(db: Session, org: int, limit: int, now: float) -> List[Dict[str, Any]]:
    placeholders = ",".join(f":s{i}" for i in range(len(ACTIVE_STATUSES) + len(QUEUED_STATUSES)))
    statuses = ACTIVE_STATUSES + QUEUED_STATUSES
    params: Dict[str, Any] = {"org": org, "cutoff": _cutoff_iso(now), "limit": limit}
    params.update({f"s{i}": status for i, status in enumerate(statuses)})
    rows = db.execute(
        text(
            f"SELECT {MISSION_COLUMNS} FROM tensor_dev_missions "
            f"WHERE organization_id = :org AND status IN ({placeholders}) "
            "AND created_at >= :cutoff ORDER BY id DESC LIMIT :limit"
        ),
        params,
    ).mappings().all()
    return [dict(row) for row in rows]


def _session_context(db: Session, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve owner-session liveness for the window's missions.

    Only ``mission.session_id`` is a foreign key into ``tensor_agent_sessions.id``;
    free-form claim fields are session *labels*, so they are never treated as
    proof that a session is missing (that would flag healthy missions as
    infrastructure loss).
    """
    keys = sorted({str(r.get("session_id")) for r in rows} - {"None", ""})
    closed: List[str] = []
    known: List[str] = []
    complete = True
    if keys:
        placeholders = ",".join(f":k{i}" for i in range(len(keys)))
        params = {f"k{i}": int(key) if key.isdigit() else key for i, key in enumerate(keys)}
        try:
            found = db.execute(
                text(f"SELECT id, status, closed_at FROM tensor_agent_sessions WHERE id IN ({placeholders})"),
                params,
            ).mappings().all()
        except Exception:  # pragma: no cover - table absent on minimal installs
            found = []
            complete = False
        for row in found:
            sid = str(row["id"])
            known.append(sid)
            if (row.get("status") or "").lower() != "active" or row.get("closed_at") is not None:
                closed.append(sid)
    return {"closed_sessions": closed, "known_sessions": known, "complete": complete}


def _queue_load(db: Session, org: int) -> Dict[str, float]:
    placeholders = ",".join(f":s{i}" for i in range(len(QUEUED_STATUSES)))
    params: Dict[str, Any] = {"org": org}
    params.update({f"s{i}": status for i, status in enumerate(QUEUED_STATUSES)})
    queue_len = db.execute(
        text(
            f"SELECT COUNT(*) FROM tensor_dev_missions WHERE organization_id = :org AND status IN ({placeholders})"
        ),
        params,
    ).scalar()
    capacity = int(os.getenv("SELF_HEAL_QUEUE_CAPACITY", "64"))
    return {"queue_len": float(queue_len or 0), "queue_capacity": float(max(1, capacity))}


def _persist(db: Session, entries: Sequence[LedgerEntry]) -> int:
    inserted = 0
    for entry in entries:
        res = db.execute(
            text(
                "INSERT INTO self_heal_ledger (seq,window_id,mission_id,detection_reason,cause,confidence,"
                "action,params,attempt,outcome,detail,detected_at,created_at,idempotency_key,prev_hash,hash) "
                "VALUES (:seq,:window_id,:mission_id,:detection_reason,:cause,:confidence,:action,:params,"
                ":attempt,:outcome,:detail,:detected_at,:created_at,:idempotency_key,:prev_hash,:hash) "
                "ON CONFLICT (idempotency_key) DO NOTHING"
            ),
            {
                **entry.to_dict(),
                "params": json.dumps(entry.params, sort_keys=True),
                "detail": json.dumps(entry.detail, sort_keys=True),
            },
        )
        inserted += int(getattr(res, "rowcount", 0) or 0)
    db.commit()
    return inserted


def _window_id(now: float) -> str:
    return "W-" + datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%MZ")


def _thresholds(spec: Optional[ThresholdIn]) -> Thresholds:
    if spec is None:
        return Thresholds()
    return Thresholds(**spec.model_dump())


def _dashboard_payload(ledger: SelfHealLedger) -> Dict[str, Any]:
    entries = ledger.entries
    by_window: Dict[str, Dict[str, int]] = {}
    by_cause: Dict[str, int] = {}
    by_outcome: Dict[str, int] = {}
    for entry in entries:
        bucket = by_window.setdefault(entry.window_id, {"entries": 0, "accepted": 0})
        bucket["entries"] += 1
        if entry.outcome in ("applied", "archived"):
            bucket["accepted"] += 1
        by_cause[entry.cause] = by_cause.get(entry.cause, 0) + 1
        by_outcome[entry.outcome] = by_outcome.get(entry.outcome, 0) + 1
    open_escalations = [
        {
            "mission_id": entry.mission_id,
            "cause": entry.cause,
            "window_id": entry.window_id,
            "params": entry.params,
        }
        for entry in entries
        if entry.outcome == "escalated"
        and not any(
            later.mission_id == entry.mission_id
            and later.seq > entry.seq
            and later.outcome in ("applied", "archived")
            for later in entries
        )
    ]
    verify = ledger.verify()
    return {
        "entries": len(entries),
        "windows": len(by_window),
        "by_window": dict(sorted(by_window.items())),
        "cause_distribution": dict(sorted(by_cause.items())),
        "outcome_distribution": dict(sorted(by_outcome.items())),
        "recovery": recovery_success_rate(entries),
        "mean_time_to_recover_s": mean_time_to_recover(entries),
        "open_escalations": open_escalations[:50],
        "open_escalation_count": len(open_escalations),
        "chain": verify,
        "ledger_digest": ledger.digest(),
    }


# ── endpoints ─────────────────────────────────────────────────────────────


@router.get("/config")
def self_heal_config(user: dict = Depends(require_auth)):
    """Thresholds in force plus the recovery action matrix (read-only)."""
    defaults = Thresholds()
    from tools.self_heal.recovery import _CAUSE_ACTION  # noqa: PLC0415 - documented matrix

    return {
        "thresholds": defaults.as_dict(),
        "actions": dict(_CAUSE_ACTION),
        "recoverable_causes": ["infra_lost", "timeout", "resource_contention"],
        "queue_capacity_env": "SELF_HEAL_QUEUE_CAPACITY",
    }


@router.post("/run")
def self_heal_run(
    body: RunIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Run one healing window over stalled missions and persist the ledger."""
    _ensure_schema(db)
    ledger = _load_ledger(db)
    verify = ledger.verify()
    if not verify.get("ok"):
        raise HTTPException(status_code=409, detail={"error": "ledger_chain_broken", "detail": verify})

    now = float(body.now if body.now is not None else datetime.now(UTC).timestamp())
    window_id = body.window_id or _window_id(now)
    thresholds = _thresholds(body.thresholds)

    if body.missions is not None:
        rows = [dict(row) for row in body.missions]
        sessions = {
            "closed_sessions": body.closed_sessions or [],
            "known_sessions": body.known_sessions or [],
        }
        load = dict(body.load or {})
    else:
        org = resolved_org(None, user, db)
        rows = _load_missions(db, org, body.limit, now)
        sessions = _session_context(db, rows)
        load = _queue_load(db, org)

    missions = [MissionRow.from_mapping(row) for row in rows]
    context = build_context(
        missions,
        now,
        window_id,
        session_last_seen=body.session_last_seen,
        # conservative: a session key that is absent from the session table is
        # NOT by itself infra loss (free-form claims are not foreign keys)
        session_index_complete=False,
        closed_sessions=sessions["closed_sessions"],
        known_sessions=sessions["known_sessions"],
        load=load,
    )
    executor = DryRunExecutor() if body.dry_run else SqlMissionExecutor(db, now=now)
    result = run_pipeline(
        rows,
        now=now,
        window_id=window_id,
        ledger=ledger,
        context=context,
        executor=executor,
        thresholds=thresholds,
    )
    persisted = _persist(db, result.new_entries)

    payload = result.as_dict()
    payload.update(
        {
            "dry_run": body.dry_run,
            "persisted_entries": persisted,
            "scanned_missions": len(rows),
            "thresholds": thresholds.as_dict(),
        }
    )
    return payload


@router.get("/status")
def self_heal_status(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _ensure_schema(db)
    ledger = _load_ledger(db)
    return {
        "entries": len(ledger),
        "chain": ledger.verify(),
        "ledger_digest": ledger.digest(),
        "recent": [entry.to_dict() for entry in ledger.entries[-limit:]],
    }


@router.get("/ledger")
def self_heal_ledger(
    window_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _ensure_schema(db)
    ledger = _load_ledger(db)
    entries = [e for e in ledger.entries if window_id is None or e.window_id == window_id]
    return {
        "count": len(entries),
        "entries": [entry.to_dict() for entry in entries[-limit:][::-1]],
        "chain": ledger.verify(),
    }


@router.get("/dashboard")
def self_heal_dashboard(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _ensure_schema(db)
    ledger = _load_ledger(db)
    payload = _dashboard_payload(ledger)
    payload["schema"] = "self_heal_dashboard/v1"
    return payload