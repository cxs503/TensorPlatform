"""Tensor console: execution lifecycle + executor claim protocol.

Protocol (executor = host daemon, authenticated as a real platform user):
  POST /tensor/executions/claim            -> atomic claim ({claimed:false} when idle)
  POST /tensor/executions/{id}/start       -> claimed -> running
  POST /tensor/executions/{id}/heartbeat   -> 15s liveness + log tail; kill order
  POST /tensor/executions/{id}/complete    -> terminal state + registered runs
Stale claims (heartbeat > 90s) are reaped lazily inside claim.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import (
    append_event,
    require_stage,
    resolved_org,
    truncate_log_tail,
)
from database.config import get_db

router = APIRouter(prefix="/tensor/executions", tags=["科研执行"])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "execution_key": r["execution_key"],
        "scenario_key": r["scenario_key"],
        "stage": r["stage"],
        "status": r["status"],
        "claimed_by": r["claimed_by"],
        "triggered_by": r["triggered_by"],
        "budget": r["budget"] or {},
        "heartbeat_at": r["heartbeat_at"].isoformat() if r.get("heartbeat_at") else None,
        "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
        "finished_at": r["finished_at"].isoformat() if r.get("finished_at") else None,
        "log_tail": r["log_tail"],
        "registered_run_ids": r["registered_run_ids"] or [],
        "result_summary": r["result_summary"] or {},
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def _load_execution(db: Session, execution_id: int, user: dict | None = None) -> Any:
    row = db.execute(
        text("""
            SELECT id, organization_id, execution_key, scenario_key, stage, status,
                   claimed_by, triggered_by, budget, heartbeat_at, started_at,
                   finished_at, log_tail, registered_run_ids, result_summary, created_at
            FROM tensor_executions WHERE id = :eid
        """),
        {"eid": execution_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="execution not found")
    if user is not None:
        from api.tenant_access import is_admin

        org_id = resolved_org(None, user, db)
        if int(row["organization_id"]) != int(org_id) and not is_admin(user):
            raise HTTPException(status_code=404, detail="execution not found")
    return row


class ExecutionTrigger(BaseModel):
    scenario_key: str = Field(min_length=2, max_length=120)
    stage: str = Field(default="S0", pattern=r"^S[0-4]$")
    max_wall_seconds: int = Field(default=900, ge=60, le=7200)
    organization_id: int | None = Field(default=None, ge=1)


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def trigger_execution(
    body: ExecutionTrigger,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Queue a scenario-stage execution; an agent session opens with a plan event."""
    require_stage(body.stage)
    org_id = resolved_org(body.organization_id, user, db)
    scenario = db.execute(
        text("SELECT id, current_stage FROM tensor_scenarios WHERE organization_id=:org AND scenario_key=:key"),
        {"org": org_id, "key": body.scenario_key},
    ).mappings().first()
    if not scenario:
        raise HTTPException(status_code=404, detail="scenario not found")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    execution_key = f"{body.scenario_key}-{body.stage}-{stamp}"
    budget = {"max_wall_seconds": body.max_wall_seconds}

    execution_id = db.execute(
        text("""
            INSERT INTO tensor_executions
                (organization_id, execution_key, scenario_key, stage, status,
                 triggered_by, budget)
            VALUES (:org, :ekey, :skey, :stage, 'queued', :uid, CAST(:budget AS jsonb))
            RETURNING id
        """),
        {"org": org_id, "ekey": execution_key, "skey": body.scenario_key,
         "stage": body.stage, "uid": user.get("id"), "budget": _dumps(budget)},
    ).scalar()

    session_id = db.execute(
        text("""
            INSERT INTO tensor_agent_sessions
                (organization_id, execution_id, agent_name, status, budget, started_by)
            VALUES (:org, :eid, 'tensor-executor', 'active', CAST(:budget AS jsonb), :uid)
            RETURNING id
        """),
        {"org": org_id, "eid": execution_id, "budget": _dumps(budget), "uid": user.get("id")},
    ).scalar()
    append_event(db, session_id, "plan", {
        "execution_key": execution_key,
        "scenario_key": body.scenario_key,
        "stage": body.stage,
        "budget": budget,
        "triggered_by": user.get("username"),
    })
    db.commit()
    return {"id": execution_id, "execution_key": execution_key, "session_id": session_id,
            "status": "queued", "budget": budget}


@router.get("", dependencies=[Depends(require_auth)])
def list_executions(
    scenario_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    filters = "organization_id = :org"
    params: dict[str, Any] = {"org": org_id}
    if scenario_key:
        filters += " AND scenario_key = :skey"
        params["skey"] = scenario_key
    if status:
        filters += " AND status = :status"
        params["status"] = status
    total = db.execute(
        text(f"SELECT COUNT(*) FROM tensor_executions WHERE {filters}"), params
    ).scalar()
    rows = db.execute(
        text(f"""
            SELECT id, execution_key, scenario_key, stage, status, claimed_by,
                   triggered_by, budget, heartbeat_at, started_at, finished_at,
                   log_tail, registered_run_ids, result_summary, created_at
            FROM tensor_executions WHERE {filters}
            ORDER BY id DESC LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return {"items": [_execution_row(r) for r in rows], "total": total}


@router.get("/{execution_id}", dependencies=[Depends(require_auth)])
def get_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _load_execution(db, execution_id, user)
    session_row = db.execute(
        text("""
            SELECT s.id, s.agent_name, s.status, s.budget, s.created_at, s.closed_at,
                   (SELECT count(*) FROM tensor_agent_session_events e WHERE e.session_id = s.id) AS event_count
            FROM tensor_agent_sessions s
            WHERE s.execution_id = :eid ORDER BY s.id DESC LIMIT 1
        """),
        {"eid": execution_id},
    ).mappings().first()
    out = _execution_row(row)
    out["session"] = (
        {
            "id": session_row["id"],
            "agent_name": session_row["agent_name"],
            "status": session_row["status"],
            "budget": session_row["budget"] or {},
            "created_at": session_row["created_at"].isoformat(),
            "closed_at": session_row["closed_at"].isoformat() if session_row["closed_at"] else None,
            "event_count": session_row["event_count"],
        }
        if session_row else None
    )
    return out


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{execution_id}/cancel",
             dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def cancel_execution(
    execution_id: int,
    body: CancelRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _load_execution(db, execution_id)
    if row["status"] in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"execution already {row['status']}")
    new_status = "cancelled" if row["status"] == "queued" else "cancel_requested"
    db.execute(
        text("""
            UPDATE tensor_executions
            SET status = :st, updated_at = now(),
                result_summary = result_summary || CAST(:patch AS jsonb)
            WHERE id = :eid
        """),
        {"st": new_status, "eid": execution_id,
         "patch": _dumps({"cancel_reason": (body.reason if body else None) or "user requested"})},
    )
    db.commit()
    return {"id": execution_id, "status": new_status}


# ───────────────────────── executor protocol ─────────────────────────

class ClaimRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=40)
    capabilities: list[str] = Field(default_factory=list)


@router.post("/claim", dependencies=[Depends(require_auth)])
def claim_execution(
    body: ClaimRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    # executor liveness bookkeeping
    db.execute(
        text("""
            INSERT INTO tensor_executors (executor_id, hostname, version, capabilities, heartbeat_at)
            VALUES (:eid, :host, :ver, CAST(:caps AS jsonb), now())
            ON CONFLICT (executor_id) DO UPDATE
              SET hostname = EXCLUDED.hostname, version = EXCLUDED.version,
                  capabilities = EXCLUDED.capabilities, heartbeat_at = now()
        """),
        {"eid": body.executor_id, "host": body.hostname, "ver": body.version,
         "caps": _dumps(body.capabilities)},
    )

    # lazy reap: claims whose heartbeat went silent > 90s
    reaped = db.execute(
        text("""
            UPDATE tensor_executions
            SET status = 'failed', finished_at = now(), updated_at = now(),
                result_summary = result_summary ||
                  CAST(:patch AS jsonb)
            WHERE status IN ('claimed','running')
              AND heartbeat_at < now() - interval '90 seconds'
            RETURNING id
        """),
        {"patch": _dumps({"stop_condition": "executor_lost"})},
    ).scalars().all()

    token = uuid.uuid4().hex
    row = db.execute(
        text("""
            UPDATE tensor_executions
            SET status = 'claimed', claim_token = :tok, claimed_by = :executor,
                heartbeat_at = now(), updated_at = now()
            WHERE id = (
                SELECT id FROM tensor_executions
                WHERE status = 'queued'
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, execution_key, scenario_key, stage, budget, organization_id
        """),
        {"tok": token, "executor": body.executor_id},
    ).mappings().first()
    db.commit()

    if not row:
        return {"claimed": False, "reaped": [int(i) for i in reaped]}

    session_id = db.execute(
        text("""
            INSERT INTO tensor_agent_sessions
                (organization_id, execution_id, agent_name, status, budget, started_by)
            VALUES (:org, :eid, :executor, 'active', CAST(:budget AS jsonb), NULL)
            RETURNING id
        """),
        {"org": row["organization_id"], "eid": row["id"], "executor": body.executor_id,
         "budget": _dumps(row["budget"] or {})},
    ).scalar()
    db.commit()
    return {
        "claimed": True,
        "execution": _claim_view(row, token),
        "session_id": session_id,
        "reaped": [int(i) for i in reaped],
    }


def _claim_view(row: Any, token: str) -> dict[str, Any]:
    budget = row["budget"] or {}
    return {
        "id": row["id"],
        "execution_key": row["execution_key"],
        "scenario_key": row["scenario_key"],
        "stage": row["stage"],
        "budget": budget,
        "claim_token": token,
    }


def _authorize_executor(db: Session, execution_id: int, claim_token: str) -> Any:
    row = db.execute(
        text("SELECT id, status, claim_token, claimed_by FROM tensor_executions WHERE id = :eid"),
        {"eid": execution_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="execution not found")
    if not row["claim_token"] or row["claim_token"] != claim_token:
        raise HTTPException(status_code=403, detail="invalid claim token")
    if row["status"] in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"execution already {row['status']}")
    return row


class StartRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    log_tail: str | None = None


@router.post("/{execution_id}/start", dependencies=[Depends(require_auth)])
def start_execution(
    execution_id: int,
    body: StartRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_executor(db, execution_id, body.claim_token)
    if row["status"] not in ("claimed",):
        raise HTTPException(status_code=409, detail=f"cannot start from status {row['status']}")
    db.execute(
        text("""
            UPDATE tensor_executions
            SET status = 'running', started_at = now(), heartbeat_at = now(),
                log_tail = COALESCE(CAST(:log AS text), log_tail), updated_at = now()
            WHERE id = :eid
        """),
        {"eid": execution_id, "log": truncate_log_tail(body.log_tail)},
    )
    session_id = _session_for_execution(db, execution_id)
    if session_id:
        append_event(db, session_id, "execution_started", {
            "executor": row["claimed_by"], "at": _now_iso(),
        })
    db.commit()
    return {"id": execution_id, "status": "running"}


class HeartbeatRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    log_tail: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)


@router.post("/{execution_id}/heartbeat", dependencies=[Depends(require_auth)])
def heartbeat_execution(
    execution_id: int,
    body: HeartbeatRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_executor(db, execution_id, body.claim_token)
    action = "kill" if row["status"] == "cancel_requested" else "continue"
    db.execute(
        text("""
            UPDATE tensor_executions
            SET heartbeat_at = now(),
                log_tail = COALESCE(CAST(:log AS text), log_tail), updated_at = now()
            WHERE id = :eid
        """),
        {"eid": execution_id, "log": truncate_log_tail(body.log_tail)},
    )
    # the executing daemon is alive iff its supervised run heartbeats
    if row["claimed_by"]:
        db.execute(
            text("""
                INSERT INTO tensor_executors (executor_id, heartbeat_at)
                VALUES (:eid, now())
                ON CONFLICT (executor_id) DO UPDATE SET heartbeat_at = now()
            """),
            {"eid": row["claimed_by"]},
        )
    db.commit()
    return {"id": execution_id, "status": row["status"], "action": action}


class CompleteRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    status: str = Field(pattern="^(succeeded|failed|cancelled)$")
    stop_condition: str | None = Field(default=None, max_length=120)
    registered_run_ids: list[str] = Field(default_factory=list)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    log_tail: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/{execution_id}/complete", dependencies=[Depends(require_auth)])
def complete_execution(
    execution_id: int,
    body: CompleteRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_executor(db, execution_id, body.claim_token)
    summary = dict(body.result_summary)
    if body.stop_condition:
        summary["stop_condition"] = body.stop_condition
    db.execute(
        text("""
            UPDATE tensor_executions
            SET status = :st, finished_at = now(), updated_at = now(),
                registered_run_ids = CAST(:runs AS jsonb),
                result_summary = result_summary || CAST(:summary AS jsonb),
                log_tail = COALESCE(CAST(:log AS text), log_tail),
                claim_token = NULL
            WHERE id = :eid
        """),
        {"st": body.status, "eid": execution_id, "runs": _dumps(body.registered_run_ids),
         "summary": _dumps(summary), "log": truncate_log_tail(body.log_tail)},
    )
    session_id = _session_for_execution(db, execution_id)
    if session_id:
        for run_id in body.registered_run_ids:
            append_event(db, session_id, "run_registered", {"run_id": run_id})
        for ev in body.events[:50]:
            kind = str(ev.get("kind", "note"))
            if kind in ("run_registered",):
                continue
            append_event(db, session_id, kind, dict(ev.get("payload") or {}))
        append_event(db, session_id, "decision", {
            "status": body.status, "stop_condition": body.stop_condition,
            "registered_run_ids": body.registered_run_ids,
        })
        session_status = {"succeeded": "completed", "failed": "failed",
                          "cancelled": "stopped"}.get(body.status, "stopped")
        db.execute(
            text("UPDATE tensor_agent_sessions SET status = :st, closed_at = now() WHERE id = :sid"),
            {"st": session_status, "sid": session_id},
        )
    db.commit()
    return {"id": execution_id, "status": body.status}


def _session_for_execution(db: Session, execution_id: int) -> int | None:
    return db.execute(
        text("SELECT id FROM tensor_agent_sessions WHERE execution_id = :eid ORDER BY id DESC LIMIT 1"),
        {"eid": execution_id},
    ).scalar()


class ReclaimRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=120)


@router.post("/{execution_id}/reclaim", dependencies=[Depends(require_auth)])
def reclaim_execution(
    execution_id: int,
    body: ReclaimRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Restart recovery: the same executor re-arms its own stale claim."""
    row = db.execute(
        text("""
            SELECT id, status, claimed_by FROM tensor_executions WHERE id = :eid
        """),
        {"eid": execution_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="execution not found")
    if row["status"] not in ("claimed", "running"):
        raise HTTPException(status_code=409, detail=f"cannot reclaim status {row['status']}")
    if row["claimed_by"] != body.executor_id:
        raise HTTPException(status_code=403, detail="claim belongs to another executor")
    token = uuid.uuid4().hex
    db.execute(
        text("""
            UPDATE tensor_executions
            SET claim_token = :tok, heartbeat_at = now(), updated_at = now()
            WHERE id = :eid
        """),
        {"tok": token, "eid": execution_id},
    )
    db.commit()
    return {"id": execution_id, "claim_token": token, "status": row["status"]}
