"""Tensor console: dev missions — autonomous agent development tasks.

Protocol (agent = host daemon authenticated as a real platform user):
  POST /tensor/missions                   -> queue a mission (session + plan event)
  POST /tensor/missions/claim             -> atomic claim ({claimed:false} when idle)
  POST /tensor/missions/{id}/start        -> claimed -> running
  POST /tensor/missions/{id}/heartbeat    -> 15s liveness + progress; stop order
  POST /tensor/missions/{id}/complete     -> terminal, or awaiting_acceptance
                                             (+ pending dev_acceptance approval row)
  POST /tensor/missions/{id}/stop         -> owner stop-payment (terminal 'stopped')
  POST /tensor/missions/{id}/reclaim      -> daemon-restart recovery for own claim
Stale claims (heartbeat > 90s) are reaped lazily inside claim. Acceptance is
decided by ADMIN through the shared approval queue (kind=dev_acceptance).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import append_event, resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/missions", tags=["开发任务"])

DEFAULT_BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 3_000_000}
MISSION_STATUSES = (
    "queued", "claimed", "running", "awaiting_acceptance",
    "succeeded", "failed", "stopped",
)
TERMINAL = ("succeeded", "failed", "stopped")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _mission_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "goal": r["goal"],
        "repo": r["repo"],
        "base_ref": r["base_ref"],
        "budget": r["budget"] or {},
        "runner": r["runner"],
        "origin": r["origin"],
        "status": r["status"],
        "claimed_by": r["claimed_by"],
        "session_id": r["session_id"],
        "result": r["result"] or {},
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]) if r.get("created_at") else None,
        "heartbeat_at": _iso(r["heartbeat_at"]) if r.get("heartbeat_at") else None,
        "finished_at": _iso(r["finished_at"]) if r.get("finished_at") else None,
    }


def _load_mission(db: Session, mission_id: int) -> Any:
    row = db.execute(
        text("""
            SELECT id, organization_id, goal, repo, base_ref, budget, runner, origin,
                   status, claim_token, claimed_by, session_id, result, created_by,
                   created_at, heartbeat_at, finished_at
            FROM tensor_dev_missions WHERE id = :mid
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    return row


# ───────────────────────── create / list / detail ─────────────────────────

class MissionCreate(BaseModel):
    goal: str = Field(min_length=10, max_length=4000)
    repo: str = Field(min_length=2, max_length=120)
    base_ref: str = Field(default="main", min_length=1, max_length=120)
    budget: dict[str, Any] = Field(default_factory=dict)
    runner: str = Field(default="openhands", pattern="^(openhands|native)$")
    origin: str = Field(default="owner", pattern="^(owner|agent)$")
    organization_id: int | None = Field(default=None, ge=1)


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_mission(
    body: MissionCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    budget = dict(DEFAULT_BUDGET)
    for key, lo, hi in (("max_tool_calls", 1, 2000),
                        ("max_wall_seconds", 60, 86400),
                        ("max_tokens", 1000, 50_000_000)):
        if key in body.budget:
            try:
                budget[key] = max(lo, min(hi, int(body.budget[key])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"budget.{key} must be an integer")

    mission_id = db.execute(
        text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, base_ref, budget, runner, origin, created_by)
            VALUES (:org, :goal, :repo, :ref, CAST(:budget AS jsonb), :runner, :origin, :uid)
            RETURNING id
        """),
        {"org": org_id, "goal": body.goal.strip(), "repo": body.repo.strip(),
         "ref": body.base_ref, "budget": _dumps(budget), "runner": body.runner,
         "origin": body.origin, "uid": user.get("id")},
    ).scalar()

    session_id = db.execute(
        text("""
            INSERT INTO tensor_agent_sessions
                (organization_id, agent_name, status, budget, started_by)
            VALUES (:org, 'tensor-agent', 'active', CAST(:budget AS jsonb), :uid)
            RETURNING id
        """),
        {"org": org_id, "budget": _dumps(budget), "uid": user.get("id")},
    ).scalar()
    db.execute(
        text("UPDATE tensor_dev_missions SET session_id = :sid WHERE id = :mid"),
        {"sid": session_id, "mid": mission_id},
    )
    append_event(db, session_id, "plan", {
        "mission_id": mission_id,
        "goal": body.goal.strip(),
        "repo": body.repo.strip(),
        "base_ref": body.base_ref,
        "budget": budget,
        "runner": body.runner,
        "origin": body.origin,
        "created_by": user.get("username"),
    })
    db.commit()
    return {"id": mission_id, "session_id": session_id, "status": "queued", "budget": budget}


@router.get("", dependencies=[Depends(require_auth)])
def list_missions(
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
    if status:
        if status not in MISSION_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {MISSION_STATUSES}")
        filters += " AND status = :st"
        params["st"] = status
    total = db.execute(
        text(f"SELECT COUNT(*) FROM tensor_dev_missions WHERE {filters}"), params
    ).scalar()
    rows = db.execute(
        text(f"""
            SELECT id, goal, repo, base_ref, budget, runner, origin, status,
                   claimed_by, session_id, result, created_by, created_at,
                   heartbeat_at, finished_at
            FROM tensor_dev_missions WHERE {filters}
            ORDER BY id DESC LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return {"items": [_mission_row(r) for r in rows], "total": total}


@router.get("/{mission_id}", dependencies=[Depends(require_auth)])
def get_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _load_mission(db, mission_id)
    org_id = resolved_org(None, user, db)
    from api.tenant_access import is_admin

    if int(row["organization_id"]) != int(org_id) and not is_admin(user):
        raise HTTPException(status_code=404, detail="mission not found")
    out = _mission_row(row)
    if row["session_id"]:
        session = db.execute(
            text("""
                SELECT id, agent_name, status, created_at, closed_at,
                       (SELECT count(*) FROM tensor_agent_session_events e
                        WHERE e.session_id = s.id) AS event_count
                FROM tensor_agent_sessions s WHERE s.id = :sid
            """),
            {"sid": row["session_id"]},
        ).mappings().first()
        out["session"] = {
            "id": session["id"], "agent_name": session["agent_name"],
            "status": session["status"], "event_count": session["event_count"],
            "created_at": session["created_at"].isoformat(),
            "closed_at": _iso(session["closed_at"]),
        } if session else None
    approval = db.execute(
        text("""
            SELECT id, kind, status, reason, evidence, decided_by, decided_at,
                   decision_note, created_at
            FROM tensor_approval_requests WHERE mission_id = :mid
            ORDER BY id DESC LIMIT 1
        """),
        {"mid": mission_id},
    ).mappings().first()
    out["approval"] = {
        "id": approval["id"], "kind": approval["kind"], "status": approval["status"],
        "reason": approval["reason"], "evidence": approval["evidence"] or {},
        "decided_at": _iso(approval["decided_at"]), "decision_note": approval["decision_note"],
        "created_at": approval["created_at"].isoformat(),
    } if approval else None
    return out


# ───────────────────────── agent protocol ─────────────────────────

class MissionClaim(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=40)
    runner: str | None = Field(default=None, pattern="^(openhands|native)$")


@router.post("/claim", dependencies=[Depends(require_auth)])
def claim_mission(
    body: MissionClaim,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    # lazy reap: claims whose heartbeat went silent > 90s
    reaped = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'failed', finished_at = now(),
                result = result || CAST(:patch AS jsonb)
            WHERE status IN ('claimed','running')
              AND heartbeat_at < now() - interval '90 seconds'
            RETURNING id, session_id
        """),
        {"patch": _dumps({"stop_condition": "agent_lost"})},
    ).mappings().all()
    for dead in reaped:
        if dead["session_id"]:
            db.execute(
                text("UPDATE tensor_agent_sessions SET status='failed', closed_at=now() WHERE id=:sid"),
                {"sid": dead["session_id"]},
            )
            append_event(db, dead["session_id"], "decision",
                         {"status": "failed", "stop_condition": "agent_lost"})

    # dashed canonical form: the UUID column normalizes on store, so the
    # returned token must round-trip byte-identically (plain .hex would not)
    token = str(uuid.uuid4())
    # runner-scoped claim: coexisting daemons each pull only their own class
    runner_filter = "AND runner = :runner" if body.runner else ""
    row = db.execute(
        text(f"""
            UPDATE tensor_dev_missions
            SET status = 'claimed', claim_token = :tok, claimed_by = :agent,
                heartbeat_at = now()
            WHERE id = (
                SELECT id FROM tensor_dev_missions
                WHERE status = 'queued' {runner_filter}
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, goal, repo, base_ref, budget, runner, origin,
                      session_id, organization_id
        """),
        {"tok": token, "agent": body.agent_id, "runner": body.runner},
    ).mappings().first()
    db.commit()

    if not row:
        return {"claimed": False, "reaped": [int(r["id"]) for r in reaped]}

    if row["session_id"]:
        append_event(db, row["session_id"], "note",
                     {"subtype": "mission_claimed", "agent": body.agent_id,
                      "hostname": body.hostname, "version": body.version})
    db.commit()
    return {
        "claimed": True,
        "mission": {
            "id": row["id"],
            "goal": row["goal"],
            "repo": row["repo"],
            "base_ref": row["base_ref"],
            "budget": row["budget"] or {},
            "runner": row["runner"],
            "origin": row["origin"],
            "claim_token": token,
        },
        "session_id": row["session_id"],
        "reaped": [int(r["id"]) for r in reaped],
    }


def _authorize_agent(db: Session, mission_id: int, claim_token: str) -> Any:
    row = db.execute(
        text("""
            SELECT id, status, claim_token, claimed_by, session_id
            FROM tensor_dev_missions WHERE id = :mid
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    # UUID columns come back as uuid.UUID objects — compare as canonical strings
    if not row["claim_token"] or str(row["claim_token"]) != claim_token:
        raise HTTPException(status_code=403, detail="invalid claim token")
    return row


class StartRequest(BaseModel):
    claim_token: str = Field(min_length=8)


@router.post("/{mission_id}/start", dependencies=[Depends(require_auth)])
def start_mission(
    mission_id: int,
    body: StartRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_agent(db, mission_id, body.claim_token)
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail=f"cannot start from status {row['status']}")
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'running', heartbeat_at = now()
            WHERE id = :mid
        """),
        {"mid": mission_id},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "note", {"subtype": "mission_started"})
    db.commit()
    return {"id": mission_id, "status": "running"}


class HeartbeatRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    progress: dict[str, Any] = Field(default_factory=dict)


@router.post("/{mission_id}/heartbeat", dependencies=[Depends(require_auth)])
def heartbeat_mission(
    mission_id: int,
    body: HeartbeatRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_agent(db, mission_id, body.claim_token)
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET heartbeat_at = now(),
                result = CASE
                    WHEN CAST(:prog AS jsonb) = 'null'::jsonb THEN result
                    ELSE jsonb_set(result, '{progress}', CAST(:prog AS jsonb))
                END
            WHERE id = :mid
        """),
        {"mid": mission_id, "prog": _dumps(body.progress or None)},
    )
    db.commit()
    return {"id": mission_id, "status": row["status"],
            "action": "stop" if row["status"] == "stopped" else "continue"}


class CompleteRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    status: str = Field(pattern="^(succeeded|failed)$")
    acceptance: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    branch: str | None = Field(default=None, max_length=200)
    head_commit: str | None = Field(default=None, max_length=64)
    checks: dict[str, Any] = Field(default_factory=dict)


@router.post("/{mission_id}/complete", dependencies=[Depends(require_auth)])
def complete_mission(
    mission_id: int,
    body: CompleteRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_agent(db, mission_id, body.claim_token)
    if row["status"] in TERMINAL or row["status"] == "awaiting_acceptance":
        raise HTTPException(status_code=409, detail=f"mission already {row['status']}")
    mission = _load_mission(db, mission_id)
    evidence = {
        "branch": body.branch,
        "head_commit": body.head_commit,
        "summary": body.summary,
        "checks": body.checks,
        "finished_status": body.status,
    }

    approval_id = None
    if body.acceptance:
        existing = db.execute(
            text("""
                SELECT id FROM tensor_approval_requests
                WHERE mission_id = :mid AND kind = 'dev_acceptance' AND status = 'pending'
            """),
            {"mid": mission_id},
        ).scalar()
        if not existing:
            approval_id = db.execute(
                text("""
                    INSERT INTO tensor_approval_requests
                        (organization_id, mission_id, kind, reason, evidence,
                         status, requested_by)
                    VALUES (:org, :mid, 'dev_acceptance', :reason,
                            CAST(:evidence AS jsonb), 'pending', :reqby)
                    RETURNING id
                """),
                {"org": mission["organization_id"], "mid": mission_id,
                 "reason": (body.summary.get("headline") or mission["goal"])[:2000],
                 "evidence": _dumps(evidence), "reqby": row["claimed_by"] or user.get("username")},
            ).scalar()
        else:
            approval_id = existing
            db.execute(  # reclaim path: refresh the pending row with new evidence
                text("""
                    UPDATE tensor_approval_requests
                    SET evidence = CAST(:evidence AS jsonb), reason = :reason
                    WHERE id = :aid
                """),
                {"aid": existing,
                 "reason": (body.summary.get("headline") or mission["goal"])[:2000],
                 "evidence": _dumps(evidence)},
            )
        db.execute(
            text("""
                UPDATE tensor_dev_missions
                SET status = 'awaiting_acceptance', claim_token = NULL,
                    result = result || CAST(:summary AS jsonb)
                WHERE id = :mid
            """),
            {"mid": mission_id,
             "summary": _dumps({**body.summary, "evidence": evidence})},
        )
        if row["session_id"]:
            append_event(db, row["session_id"], "approval_requested", {
                "approval_id": approval_id, "kind": "dev_acceptance", "evidence": evidence,
            })
        db.commit()
        return {"id": mission_id, "status": "awaiting_acceptance", "approval_id": approval_id}

    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = :st, finished_at = now(), claim_token = NULL,
                result = result || CAST(:summary AS jsonb)
            WHERE id = :mid
        """),
        {"st": body.status, "mid": mission_id,
         "summary": _dumps({**body.summary, "evidence": evidence})},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "decision", {
            "status": body.status, "summary": body.summary,
            "branch": body.branch, "head_commit": body.head_commit,
        })
        session_status = "completed" if body.status == "succeeded" else "failed"
        db.execute(
            text("UPDATE tensor_agent_sessions SET status=:st, closed_at=now() WHERE id=:sid"),
            {"st": session_status, "sid": row["session_id"]},
        )
    db.commit()
    return {"id": mission_id, "status": body.status, "approval_id": None}


class StopRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{mission_id}/stop",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def stop_mission(
    mission_id: int,
    body: StopRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _load_mission(db, mission_id)
    if row["status"] in TERMINAL:
        raise HTTPException(status_code=409, detail=f"mission already {row['status']}")
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'stopped', finished_at = now(), claim_token = NULL
            WHERE id = :mid
        """),
        {"mid": mission_id},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "decision", {
            "status": "stopped", "stop_condition": "owner_stopped",
            "reason": (body.reason if body else None) or "owner requested",
        })
        db.execute(
            text("UPDATE tensor_agent_sessions SET status='stopped', closed_at=now() WHERE id=:sid"),
            {"sid": row["session_id"]},
        )
    db.commit()
    return {"id": mission_id, "status": "stopped"}


class ReclaimRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)


@router.post("/{mission_id}/reclaim", dependencies=[Depends(require_auth)])
def reclaim_mission(
    mission_id: int,
    body: ReclaimRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Daemon-restart recovery: the same agent re-arms its own claim."""
    row = db.execute(
        text("SELECT id, status, claimed_by, session_id FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    if row["status"] not in ("claimed", "running", "awaiting_acceptance"):
        raise HTTPException(status_code=409, detail=f"cannot reclaim status {row['status']}")
    if row["claimed_by"] != body.agent_id:
        raise HTTPException(status_code=403, detail="claim belongs to another agent")
    token = str(uuid.uuid4())
    new_status = "running" if row["status"] == "awaiting_acceptance" else row["status"]
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET claim_token = :tok, heartbeat_at = now(), status = :st
            WHERE id = :mid
        """),
        {"tok": token, "st": new_status, "mid": mission_id},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "note",
                     {"subtype": "mission_reclaimed", "agent": body.agent_id})
    db.commit()
    return {"id": mission_id, "claim_token": token, "status": new_status}
