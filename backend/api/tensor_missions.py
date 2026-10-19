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
        "co_repos": list(r["co_repos"] or []) if r.get("co_repos") is not None else None,
        "parent_mission_id": r.get("parent_mission_id"),
        "budget": r["budget"] or {},
        "runner": r["runner"],
        "origin": r["origin"],
        "model": r.get("model"),
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
            SELECT id, organization_id, goal, repo, base_ref, co_repos,
                   parent_mission_id, budget, runner, origin,
                   model, status, claim_token, claimed_by, session_id, result,
                   created_by, created_at, heartbeat_at, finished_at
            FROM tensor_dev_missions WHERE id = :mid
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    return row


# ───────────────────────── create / list / detail ─────────────────────────

class MissionCreate(BaseModel):
    # 4000 上限对常驻记忆 mission 天生太紧（goal 本体 + 注入的身份/工作记忆/
    # 事实账本尾部三件套合计 ~4-5k，2026-10-19 m50 派发实测 422）。DB 列为
    # text，上限是纯校验层约束——放到 12k，注入量仍由 dispatch_helper 行数
    # 上界（40/200/5 行）有界。
    goal: str = Field(min_length=10, max_length=12000)
    repo: str = Field(min_length=2, max_length=120)
    base_ref: str = Field(default="main", min_length=1, max_length=120)
    # cross-project composition: extra repos the mission's worktree set covers
    co_repos: list[str] | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    runner: str = Field(default="openhands", pattern="^(openhands|native)$")
    origin: str = Field(default="owner", pattern="^(owner|agent)$")
    model: str | None = Field(default=None, min_length=2, max_length=64)
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
                (organization_id, goal, repo, base_ref, co_repos, budget, runner,
                 origin, model, created_by)
            VALUES (:org, :goal, :repo, :ref, :corepos, CAST(:budget AS jsonb),
                    :runner, :origin, :model, :uid)
            RETURNING id
        """),
        {"org": org_id, "goal": body.goal.strip(), "repo": body.repo.strip(),
         "ref": body.base_ref, "corepos": body.co_repos, "budget": _dumps(budget),
         "runner": body.runner,
         "origin": body.origin, "model": body.model, "uid": user.get("id")},
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
        "co_repos": body.co_repos or [],
        "budget": budget,
        "runner": body.runner,
        "origin": body.origin,
        "model": body.model,
        "created_by": user.get("username"),
    })
    db.commit()
    return {"id": mission_id, "session_id": session_id, "status": "queued", "budget": budget}


# ───────────────────────── agent dispatch (orchestration) ─────────────────────────
# The tensor_agent platform account is VIEWER and cannot POST /tensor/missions.
# Instead, a *running* orchestrator mission authenticates with its own claim
# token and dispatches sub-missions here; the envelope below (repo whitelist,
# tree depth, budget caps, fan-out and daily limits) is enforced server-side,
# so no privilege escalation is needed anywhere.

REPO_WHITELIST = (
    "TensorLBM", "TensorPhysics", "TensorDEM", "TensorFEM", "TensorFVM",
    "TensorPlatform",
)
DISPATCH_MAX_CHILDREN = 8          # per orchestrator mission
DISPATCH_DAILY_CAP = 24            # agent-originated missions per 24h (global)
DISPATCH_BUDGET_CAPS = {"max_tool_calls": 80, "max_wall_seconds": 7200,
                        "max_tokens": 3_000_000}


class MissionDispatch(BaseModel):
    parent_id: int = Field(ge=1)
    claim_token: str = Field(min_length=8)
    goal: str = Field(min_length=10, max_length=4000)
    repo: str = Field(min_length=2, max_length=120)
    base_ref: str = Field(default="main", min_length=1, max_length=120)
    co_repos: list[str] | None = None
    # role names resolve to models through tools/agent/roles.json on the
    # agent side; a raw relay model id may also be pinned directly
    role: str | None = Field(default=None, min_length=2, max_length=40)
    model: str | None = Field(default=None, min_length=2, max_length=64)
    budget: dict[str, Any] = Field(default_factory=dict)


@router.post("/dispatch", dependencies=[Depends(require_auth)])
def dispatch_mission(
    body: MissionDispatch,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    parent = _authorize_agent(db, body.parent_id, body.claim_token)
    if parent["status"] != "running":
        raise HTTPException(status_code=409,
                            detail=f"parent mission is {parent['status']}, not running")

    prow = db.execute(
        text("SELECT parent_mission_id FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": body.parent_id},
    ).mappings().first()
    if prow["parent_mission_id"] is not None:
        raise HTTPException(status_code=422,
                            detail="sub-missions cannot dispatch (tree depth is capped at 2)")

    repos = [body.repo.strip()] + [r.strip() for r in (body.co_repos or [])]
    for r in repos:
        if r not in REPO_WHITELIST:
            raise HTTPException(status_code=422, detail=f"repo {r!r} is outside the dispatch whitelist")
    if len(set(repos)) != len(repos):
        raise HTTPException(status_code=422, detail="repo list contains duplicates")
    if not body.model:
        # children must be model-pinned: a NULL-model child would wait on the
        # default daemon, which may be busy running this very orchestrator
        raise HTTPException(status_code=422, detail="dispatch requires an explicit model (role resolves to one)")

    budget = dict(DISPATCH_BUDGET_CAPS)
    for key in DISPATCH_BUDGET_CAPS:
        if key in body.budget:
            try:
                budget[key] = max(1, min(DISPATCH_BUDGET_CAPS[key], int(body.budget[key])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"budget.{key} must be an integer")

    children = db.execute(
        text("SELECT count(*) FROM tensor_dev_missions WHERE parent_mission_id = :p"),
        {"p": body.parent_id},
    ).scalar()
    if children >= DISPATCH_MAX_CHILDREN:
        raise HTTPException(status_code=422,
                            detail=f"parent already has {children} children (max {DISPATCH_MAX_CHILDREN})")
    day_count = db.execute(
        text("""
            SELECT count(*) FROM tensor_dev_missions
            WHERE origin = 'agent' AND created_at > now() - interval '24 hours'
        """),
    ).scalar()
    if day_count >= DISPATCH_DAILY_CAP:
        raise HTTPException(status_code=429,
                            detail=f"agent dispatch daily cap reached ({DISPATCH_DAILY_CAP}/24h)")

    # inherit the orchestrator's org so owner sees the whole tree in one list
    org_id = db.execute(
        text("SELECT organization_id FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": body.parent_id},
    ).scalar()
    mission_id = db.execute(
        text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, base_ref, co_repos, budget, runner,
                 origin, model, parent_mission_id, created_by)
            VALUES (:org, :goal, :repo, :ref, :corepos, CAST(:budget AS jsonb),
                    'openhands', 'agent', :model, :parent, :uid)
            RETURNING id
        """),
        {"org": org_id, "goal": body.goal.strip(), "repo": body.repo.strip(),
         "ref": body.base_ref, "corepos": body.co_repos or None,
         "budget": _dumps(budget), "model": body.model, "parent": body.parent_id,
         "uid": user.get("id")},
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
    append_event(db, parent["session_id"], "note", {
        "subtype": "sub_mission_dispatched", "child_id": mission_id,
        "repo": body.repo.strip(), "co_repos": body.co_repos or [],
        "role": body.role, "model": body.model, "goal": body.goal.strip()[:200],
    })
    append_event(db, session_id, "plan", {
        "mission_id": mission_id, "parent_mission_id": body.parent_id,
        "goal": body.goal.strip(), "repo": body.repo.strip(),
        "base_ref": body.base_ref, "co_repos": body.co_repos or [],
        "budget": budget, "runner": "openhands", "origin": "agent",
        "model": body.model, "dispatched_by": parent["claimed_by"],
    })
    db.commit()
    return {"id": mission_id, "session_id": session_id, "parent_id": body.parent_id,
            "status": "queued", "budget": budget, "role": body.role}


@router.get("/{mission_id}/children", dependencies=[Depends(require_auth)])
def list_children(
    mission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _load_mission(db, mission_id)
    rows = db.execute(
        text("""
            SELECT id, goal, repo, co_repos, parent_mission_id, model, origin,
                   status, claimed_by, created_at, finished_at
            FROM tensor_dev_missions WHERE parent_mission_id = :mid ORDER BY id
        """),
        {"mid": mission_id},
    ).mappings().all()
    return {"parent_id": mission_id, "items": [{
        "id": r["id"], "goal": r["goal"], "repo": r["repo"],
        "co_repos": list(r["co_repos"] or []), "model": r["model"],
        "origin": r["origin"], "status": r["status"], "claimed_by": r["claimed_by"],
        "created_at": _iso(r["created_at"]), "finished_at": _iso(r["finished_at"]),
    } for r in rows]}


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
            SELECT id, goal, repo, base_ref, co_repos, parent_mission_id, budget,
                   runner, origin, model, status,
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
    # model-pinned daemons claim only their own missions; the designated
    # default daemon additionally picks up legacy NULL-model missions
    model: str | None = Field(default=None, min_length=2, max_length=64)
    claim_null_model: bool = False


@router.post("/claim", dependencies=[Depends(require_auth)])
def claim_mission(
    body: MissionClaim,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    # lazy reap: claims whose heartbeat went silent > 240s. 90s 实测误杀：
    # 后端自身停摆（2026-10-19 m49 事件，一个 claim 挂 170s）期间心跳落不了
    # 地，恢复后第一个 claim 把健康的在飞 mission 收割成 agent_lost。真死
    # daemon 4 分钟内也能检出，换 240s 只延后误报、不放过真死。
    reaped = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'failed', finished_at = now(),
                result = result || CAST(:patch AS jsonb)
            WHERE status IN ('claimed','running')
              AND heartbeat_at < now() - interval '240 seconds'
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
    # runner-scoped claim: coexisting daemons each pull only their own class;
    # model pinning routes each queued mission to its per-model daemon
    runner_filter = "AND runner = :runner" if body.runner else ""
    if body.model and body.claim_null_model:
        model_filter = "AND (model = :claim_model OR model IS NULL)"
    elif body.model:
        model_filter = "AND model = :claim_model"
    else:
        model_filter = ""
    row = db.execute(
        text(f"""
            UPDATE tensor_dev_missions
            SET status = 'claimed', claim_token = :tok, claimed_by = :agent,
                heartbeat_at = now()
            WHERE id = (
                SELECT id FROM tensor_dev_missions
                WHERE status = 'queued' {runner_filter} {model_filter}
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, goal, repo, base_ref, co_repos, budget, runner, origin,
                      model, session_id, organization_id
        """),
        {"tok": token, "agent": body.agent_id, "runner": body.runner,
         "claim_model": body.model},
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
            "co_repos": list(row["co_repos"] or []) if row["co_repos"] is not None else [],
            "budget": row["budget"] or {},
            "runner": row["runner"],
            "origin": row["origin"],
            "model": row["model"],
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


class RefillRequest(BaseModel):
    """换班不换人：给非 agent 过失终态（agent_lost/budget_exceeded）的 mission
    补预算重新排队。灵魂——native_messages 检查点、worktree、常驻记忆——在
    主机侧原样保留，daemon 重 claim 后原位续跑；goal 作为新班次开场重述。"""
    add_tool_calls: int = Field(default=0, ge=0, le=10000)
    add_wall_seconds: int = Field(default=0, ge=0, le=864000)
    add_tokens: int = Field(default=0, ge=0, le=10_000_000_000)
    model: str | None = Field(default=None, min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=500)


@router.post("/{mission_id}/refill",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def refill_mission(
    mission_id: int,
    body: RefillRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    # 行锁（PR #22 初审第 2 点）：并发 refill 的第二个请求阻塞在 FOR UPDATE，
    # 等第一个提交后看到 queued → 409；UPDATE 再带状态条件双保险，杜绝
    # 重复加预算/覆盖审计
    row = db.execute(
        text("""
            SELECT id, organization_id, goal, repo, base_ref, co_repos,
                   parent_mission_id, budget, runner, origin,
                   model, status, claim_token, claimed_by, session_id, result,
                   created_by, created_at, heartbeat_at, finished_at
            FROM tensor_dev_missions WHERE id = :mid
            FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="mission 不存在")
    if row["status"] not in TERMINAL:
        raise HTTPException(
            status_code=409,
            detail=f"mission 状态 {row['status']}——refill 只作用于终态")
    result = row["result"] or {}
    if isinstance(result, str):
        result = json.loads(result)
    stop = (result or {}).get("stop_condition")
    if stop not in ("agent_lost", "budget_exceeded"):
        raise HTTPException(
            status_code=409,
            detail=f"stop_condition={stop!r} 非 agent_lost/budget_exceeded——"
                   "agent 自身终局（如 honest FAIL）请重新派发，不要 refill")
    if (body.add_tool_calls, body.add_wall_seconds, body.add_tokens,
            body.model) == (0, 0, 0, None):
        raise HTTPException(status_code=422, detail="refill 至少补一项预算或换 model")
    audit = _dumps({
        "refill": {
            "by": user.get("username") if isinstance(user, dict) else None,
            "note": body.note,
            "added": {"tool_calls": body.add_tool_calls,
                      "wall_seconds": body.add_wall_seconds,
                      "tokens": body.add_tokens},
            "model": body.model,
        },
    })
    updated = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'queued', claim_token = NULL, claimed_by = NULL,
                finished_at = NULL, heartbeat_at = now(),
                model = COALESCE(:model, model),
                budget = budget || jsonb_build_object(
                    'max_tool_calls',
                        COALESCE((budget->>'max_tool_calls')::int, 0) + :tc,
                    'max_wall_seconds',
                        COALESCE((budget->>'max_wall_seconds')::int, 0) + :ws,
                    'max_tokens',
                        COALESCE((budget->>'max_tokens')::bigint, 0) + :tk),
                result = (result - 'stop_condition') || CAST(:audit AS jsonb)
            WHERE id = :mid AND status IN ('succeeded', 'failed', 'stopped')
            RETURNING status, model, budget
        """),
        {"mid": mission_id, "model": body.model, "tc": body.add_tool_calls,
         "ws": body.add_wall_seconds, "tk": body.add_tokens, "audit": audit},
    ).mappings().first()
    if updated is None:
        # 行锁下仍变不了状态=并发对手先落地：明确冲突，不重复记账
        db.rollback()
        raise HTTPException(
            status_code=409, detail="并发 refill：已被另一请求先行处理")
    if row["session_id"]:
        # 会话是灵魂的载体：随 refill 重开，事件流留续跑审计
        db.execute(
            text("UPDATE tensor_agent_sessions "
                 "SET status='active', closed_at=NULL WHERE id=:sid"),
            {"sid": row["session_id"]},
        )
        append_event(db, row["session_id"], "decision", {
            "status": "queued", "stop_condition": "refilled",
            "note": body.note, "by": user.get("username")
            if isinstance(user, dict) else None,
        })
    db.commit()
    return {"id": mission_id, "status": updated["status"],
            "model": updated["model"], "budget": updated["budget"]}
