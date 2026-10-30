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
from services.factory_profile import get_factory_profile, validate_projects, validate_base_ref, require_factory_agent
from api.domain.tensor_common import append_event, resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/missions", tags=["开发任务"])

DEFAULT_BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 3_000_000}
MISSION_STATUSES = (
    # lifecycle statuses (095): suspended, orphan, revoked
    "queued", "claimed", "running", "awaiting_acceptance",
    "succeeded", "failed", "stopped", "revoked",
    "suspended", "orphan",
)
TERMINAL = ("succeeded", "failed", "stopped", "revoked", "orphan")


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
        "agent_session_id": str(r["agent_session_id"]) if r.get("agent_session_id") else None,
        "worker_role": r.get("worker_role"),
        "campaign_id": r.get("campaign_id"),
        "campaign_phase": r.get("campaign_phase"),
        "status": r["status"],
        "claimed_by": r["claimed_by"],
        "session_id": r["session_id"],
        "result": r["result"] or {},
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]) if r.get("created_at") else None,
        "heartbeat_at": _iso(r["heartbeat_at"]) if r.get("heartbeat_at") else None,
        "finished_at": _iso(r["finished_at"]) if r.get("finished_at") else None,
        "fail_stage": r.get("fail_stage"),
        "report_owed": r.get("report_owed", False),
        "worktree_recyclable": r.get("worktree_recyclable", False),
        "write_off_at": _iso(r.get("write_off_at")) if r.get("write_off_at") else None,
        "suspended_at": _iso(r.get("suspended_at")) if r.get("suspended_at") else None,
        "suspend_reason": r.get("suspend_reason"),
        "budget_used": r.get("budget_used") or {},
    }


def _load_mission(db: Session, mission_id: int, user: dict | None = None, *, lock: bool = False) -> Any:
    row = db.execute(
        text("""
            SELECT id, organization_id, goal, repo, base_ref, co_repos,
                   parent_mission_id, budget, runner, origin,
                   model, agent_session_id, worker_role, campaign_id, campaign_phase, status, claim_token, claimed_by, session_id, result,
                   created_by, created_at, heartbeat_at, finished_at,
                   fail_stage, report_owed, worktree_recyclable, write_off_at,
                   suspended_at, suspend_reason, budget_used
            FROM tensor_dev_missions WHERE id = :mid
        """ + (" FOR UPDATE" if lock else "")),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    if user is not None and int(row["organization_id"]) != resolved_org(None, user, db):
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
    worker_role: str | None = Field(default=None, pattern="^(platform|data|models|applications|infrastructure|verifier|strategy)$")
    agent_session_id: uuid.UUID | None = None
    task_key: str | None = Field(default=None, min_length=2, max_length=120, pattern="^[a-zA-Z0-9_.:-]+$")


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_mission(
    body: MissionCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    validate_projects(body.repo, body.co_repos)
    validate_base_ref(body.base_ref)
    if len(body.goal.strip()) < 10:
        raise HTTPException(422, "goal must contain at least 10 non-padding characters")
    if body.agent_session_id:
        resident = db.execute(text("SELECT role,model FROM factory_resident_workers WHERE agent_session_id=:sid AND organization_id=:org AND enabled FOR UPDATE"),
                              {"sid": str(body.agent_session_id), "org": org_id}).mappings().first()
        if not resident:
            raise HTTPException(404, "agent session not found or disabled")
        if body.worker_role and body.worker_role != resident["role"]:
            raise HTTPException(422, "role differs from agent session")
        body.worker_role = resident["role"]
        if body.model and body.model != resident["model"]:
            raise HTTPException(422, "model differs from agent session")
        body.model = resident["model"]
    if body.task_key:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, :org))"),
                   {"key": body.task_key, "org": org_id})
        existing = db.execute(text("SELECT id,session_id,status,budget,goal,repo,worker_role,agent_session_id,model,base_ref FROM tensor_dev_missions WHERE organization_id=:org AND task_key=:key"),
                              {"org": org_id, "key": body.task_key}).mappings().first()
        if existing:
            if (existing["goal"], existing["repo"], existing["worker_role"], str(existing["agent_session_id"]), existing["model"], existing["base_ref"]) != (body.goal.strip(), body.repo.strip(), body.worker_role, str(body.agent_session_id), body.model, body.base_ref):
                raise HTTPException(409, "task_key already identifies a different task")
            return {"id": existing["id"], "session_id": existing["session_id"],
                    "status": existing["status"], "budget": existing["budget"], "idempotent": True}
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
                 origin, model, created_by, worker_role, task_key, agent_session_id)
            VALUES (:org, :goal, :repo, :ref, :corepos, CAST(:budget AS jsonb),
                    :runner, :origin, :model, :uid, :worker_role, :task_key, :agent_session_id)
            RETURNING id
        """),
        {"org": org_id, "goal": body.goal.strip(), "repo": body.repo.strip(),
         "ref": body.base_ref, "corepos": body.co_repos, "budget": _dumps(budget),
         "runner": body.runner,
         "origin": body.origin, "model": body.model, "uid": user.get("id"),
         "worker_role": body.worker_role, "task_key": body.task_key, "agent_session_id": str(body.agent_session_id) if body.agent_session_id else None},
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
    parent = _authorize_agent(db, body.parent_id, body.claim_token, user)
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

    validate_projects(body.repo, body.co_repos)
    validate_base_ref(body.base_ref)
    repos = [body.repo.strip()] + [r.strip() for r in (body.co_repos or [])]
    for r in repos:
        if r not in (get_factory_profile().project_names if get_factory_profile() else REPO_WHITELIST):
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
            WHERE organization_id = :org AND origin = 'agent' AND created_at > now() - interval '24 hours'
        """),
        {"org": resolved_org(None, user, db)},
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
                    :runner, 'agent', :model, :parent, :uid)
            RETURNING id
        """),
        {"org": org_id, "runner": parent["runner"], "goal": body.goal.strip(), "repo": body.repo.strip(),
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
        "budget": budget, "runner": parent["runner"], "origin": "agent",
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
    _load_mission(db, mission_id, user)
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
                   runner, origin, model, agent_session_id, worker_role, campaign_id, campaign_phase, status,
                   claimed_by, session_id, result, created_by, created_at,
                   heartbeat_at, finished_at,
                   fail_stage, report_owed, worktree_recyclable, write_off_at,
                   suspended_at, suspend_reason, budget_used
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
    row = _load_mission(db, mission_id, user)
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
    mission_id: int | None = Field(default=None, ge=1)
    agent_session_id: uuid.UUID | None = None
    worker_role: str | None = Field(default=None, pattern="^(platform|data|models|applications|infrastructure|verifier|strategy)$")


@router.post("/claim", dependencies=[Depends(require_auth)])
def claim_mission(
    body: MissionClaim,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    require_factory_agent(user)
    org_id = resolved_org(None, user, db)
    if body.agent_session_id:
        registered = db.execute(text("SELECT role,model FROM factory_resident_workers WHERE agent_session_id=:sid AND user_id=:uid AND organization_id=:org AND enabled FOR UPDATE"),
                                {"sid": str(body.agent_session_id), "uid": user["id"], "org": org_id}).mappings().first()
        if not registered:
            raise HTTPException(403, "agent session is not bound to this principal")
        if body.model != registered["model"] or body.worker_role != registered["role"]:
            raise HTTPException(409, "session configuration changed; refresh before claiming")
        active = db.execute(text("SELECT id FROM tensor_dev_missions WHERE agent_session_id=:sid AND status IN ('claimed','running') AND heartbeat_at > now()-interval '240 seconds'"), {"sid": str(body.agent_session_id)}).scalar()
        if active:
            return {"claimed": False, "busy": True}
    elif body.worker_role:
        raise HTTPException(403, "resident role claims require an agent session")
    # lazy reap: claims whose heartbeat went silent > 240s. 90s 实测误杀：
    # 后端自身停摆（2026-10-19 m49 事件，一个 claim 挂 170s）期间心跳落不了
    # 地，恢复后第一个 claim 把健康的在飞 mission 收割成 agent_lost。真死
    # daemon 4 分钟内也能检出，换 240s 只延后误报、不放过真死。
    reaped = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'failed', finished_at = now(),
                result = result || CAST(:patch AS jsonb)
            WHERE organization_id = :org AND status IN ('claimed','running')
              AND heartbeat_at < now() - interval '240 seconds'
            RETURNING id, session_id
        """),
        {"org": org_id, "patch": _dumps({"stop_condition": "agent_lost"})},
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
    mission_filter = "AND id = :claim_mission_id" if body.mission_id else ""
    session_filter = "AND agent_session_id = :agent_session_id" if body.agent_session_id else "AND agent_session_id IS NULL"
    role_filter = "AND worker_role = :worker_role" if body.worker_role else "AND worker_role IS NULL"
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
                claimed_user_id = :uid,
                heartbeat_at = now()
            WHERE id = (
                SELECT id FROM tensor_dev_missions
                WHERE organization_id = :org AND status = 'queued' {runner_filter} {model_filter} {mission_filter} {role_filter} {session_filter}
                  AND NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(COALESCE(tensor_dev_missions.result->'dependency_ids','[]'::jsonb)) dep_id
                    LEFT JOIN tensor_dev_missions prerequisite ON prerequisite.id=CAST(dep_id.value AS bigint)
                    WHERE prerequisite.id IS NULL OR prerequisite.status IN ('queued','claimed','running')
                  )
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, goal, repo, base_ref, co_repos, budget, runner, origin,
                      model, session_id, organization_id, worker_role, task_key, agent_session_id, campaign_id, campaign_phase
        """),
        {"tok": token, "agent": body.agent_id, "uid": user["id"], "org": org_id, "runner": body.runner,
         "claim_model": body.model, "claim_mission_id": body.mission_id, "worker_role": body.worker_role, "agent_session_id": str(body.agent_session_id) if body.agent_session_id else None},
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
            "agent_session_id": str(row["agent_session_id"]) if row["agent_session_id"] else None,
            "worker_role": row["worker_role"],
            "task_key": row["task_key"],
            "campaign_id": row["campaign_id"],
            "campaign_phase": row["campaign_phase"],
            "claim_token": token,
        },
        "session_id": row["session_id"],
        "reaped": [int(r["id"]) for r in reaped],
    }


def _authorize_agent(db: Session, mission_id: int, claim_token: str, user: dict) -> Any:
    require_factory_agent(user)
    row = db.execute(
        text("""
            SELECT id, status, claim_token, claimed_by, claimed_user_id, organization_id, session_id, runner
            FROM tensor_dev_missions WHERE id = :mid FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    if int(row["organization_id"]) != resolved_org(None, user, db):
        raise HTTPException(status_code=404, detail="mission not found")
    if row["claimed_user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="claim belongs to another principal")
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
    row = _authorize_agent(db, mission_id, body.claim_token, user)
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
    # Budget usage tracking (reported by worker each heartbeat)
    tool_calls_used: int | None = Field(default=None, ge=0)
    tokens_used: int | None = Field(default=None, ge=0)


@router.post("/{mission_id}/heartbeat", dependencies=[Depends(require_auth)])
def heartbeat_mission(
    mission_id: int,
    body: HeartbeatRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_agent(db, mission_id, body.claim_token, user)
    # Build budget_used patch from heartbeat report
    budget_patch = {}
    if body.tool_calls_used is not None:
        budget_patch["tool_calls_used"] = body.tool_calls_used
    if body.tokens_used is not None:
        budget_patch["tokens_used"] = body.tokens_used
    # Wall seconds: computed from heartbeat interval (approximate)
    budget_patch["wall_seconds_used"] = body.progress.get("wall_seconds", 0) if body.progress else 0

    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET heartbeat_at = now(),
                result = CASE
                    WHEN CAST(:prog AS jsonb) = 'null'::jsonb THEN result
                    ELSE jsonb_set(result, '{progress}', CAST(:prog AS jsonb))
                END,
                budget_used = budget_used || CAST(:bpatch AS jsonb)
            WHERE id = :mid
        """),
        {"mid": mission_id, "prog": _dumps(body.progress or None),
         "bpatch": _dumps(budget_patch)},
    )
    db.commit()
    return {"id": mission_id, "status": row["status"],
            "action": "continue" if row["status"] in ("claimed", "running") else "stop"}


class CompleteRequest(BaseModel):
    claim_token: str = Field(min_length=8)
    status: str = Field(pattern="^(succeeded|failed)$")
    acceptance: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    branch: str | None = Field(default=None, max_length=200)
    head_commit: str | None = Field(default=None, max_length=64)
    checks: dict[str, Any] = Field(default_factory=dict)
    # Lifecycle: fail_stage is mandatory for failed missions (095)
    fail_stage: str | None = Field(default=None, max_length=60,
        description="Stage where failure occurred: tool_loop/build/test/acceptance/agent_lost")


@router.post("/{mission_id}/complete", dependencies=[Depends(require_auth)])
def complete_mission(
    mission_id: int,
    body: CompleteRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = _authorize_agent(db, mission_id, body.claim_token, user)
    if row["status"] in TERMINAL or row["status"] == "awaiting_acceptance":
        raise HTTPException(status_code=409, detail=f"mission already {row['status']}")
    if row["status"] != "running":
        raise HTTPException(status_code=409, detail="mission must be running before completion")
    if body.status == "succeeded" and not body.acceptance:
        raise HTTPException(status_code=422, detail="successful work must request acceptance")
    if body.acceptance and body.status != "succeeded":
        raise HTTPException(status_code=422, detail="failed work cannot request successful acceptance")
    gate=body.summary.get('quality_gate')
    if gate is not None:
        if not isinstance(gate,dict) or type(gate.get('passed')) is not bool:
            raise HTTPException(status_code=422, detail='invalid quality gate result')
        if body.status=='succeeded' and not gate['passed']:
            raise HTTPException(status_code=422, detail='failed quality gate cannot request successful acceptance')
    mission = _load_mission(db, mission_id, user)
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

    # Determine report_owed: if failed and no branch/evidence, report is owed
    report_owed = (body.status == 'failed' and not body.branch and not evidence.get('branch'))
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = :st, finished_at = now(), claim_token = NULL,
                result = result || CAST(:summary AS jsonb),
                fail_stage = COALESCE(:fs, fail_stage),
                report_owed = CASE WHEN :ro THEN TRUE ELSE report_owed END,
                worktree_recyclable = TRUE
            WHERE id = :mid
        """),
        {"st": body.status, "mid": mission_id,
         "summary": _dumps({**body.summary, "evidence": evidence}),
         "fs": body.fail_stage, "ro": report_owed},
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
    row = _load_mission(db, mission_id, user, lock=True)
    if row["status"] in TERMINAL:
        raise HTTPException(status_code=409, detail=f"mission already {row['status']}")
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'stopped', finished_at = now()
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
    require_factory_agent(user)
    _load_mission(db, mission_id, user, lock=True)
    row = db.execute(
        text("SELECT id, status, claimed_by, claimed_user_id, session_id FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="mission not found")
    if row["status"] not in ("claimed", "running"):
        raise HTTPException(status_code=409, detail=f"cannot reclaim status {row['status']}")
    if row["claimed_by"] != body.agent_id or row["claimed_user_id"] != user["id"]:
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
                   model, agent_session_id, worker_role, campaign_id, campaign_phase, status, claim_token, claimed_by, session_id, result,
                   created_by, created_at, heartbeat_at, finished_at,
                   fail_stage, report_owed, worktree_recyclable, write_off_at,
                   suspended_at, suspend_reason, budget_used
            FROM tensor_dev_missions WHERE id = :mid
            FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="mission 不存在")
    if int(row["organization_id"]) != resolved_org(None, user, db):
        raise HTTPException(status_code=404, detail="mission not found")
    if row["status"] not in TERMINAL:
        raise HTTPException(
            status_code=409,
            detail=f"mission 状态 {row['status']}——refill 只作用于终态")
    result = row["result"] or {}
    if isinstance(result, str):
        result = json.loads(result)
    stop = (result or {}).get("stop_condition")
    # infra 类终局可续班（m50 实证：LLM 端点抖动→agent_crash，检查点完好
    # 却无资格 refill）；honest FAIL 类（finished_without_acceptance/
    # owner_stopped）仍拒绝——那是 agent 自身结论，重派而非续班
    if stop not in ("agent_lost", "budget_exceeded", "agent_crash",
                    "gateway_unavailable"):
        raise HTTPException(
            status_code=409,
            detail=f"stop_condition={stop!r} 非 infra 类终局"
                   "（agent_lost/budget_exceeded/agent_crash/"
                   "gateway_unavailable）——agent 自身终局（如 honest FAIL）"
                   "请重新派发，不要 refill")
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
