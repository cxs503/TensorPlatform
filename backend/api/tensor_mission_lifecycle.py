"""Mission lifecycle: suspend/resume, write-off, necessity gate, orphan detection.

Implements the owner 2026-10-31 directive:
  1) Suspend mechanism with budget freeze (not just UI)
  2) Write-off hook for all terminal states
  3) Worker probe endpoint for status re-check
  4) Necessity gate (pre-dispatch 3-check)
  5) Dashboard snapshot for guide_tracker

Endpoints:
  POST /tensor/missions/{id}/suspend     — freeze budget, release claim
  POST /tensor/missions/{id}/resume      — restore to queued
  GET  /tensor/missions/suspended        — list suspended missions
  POST /tensor/missions/{id}/write-off   — terminal settlement
  GET  /tensor/missions/{id}/probe       — lightweight status for workers
  GET  /tensor/missions/orphan-queue     — orphan detection
  POST /tensor/missions/necessity-gate   — pre-dispatch 3-check
  GET  /tensor/missions/dashboard-snapshot — 4-row guide_tracker
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import append_event, resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/missions", tags=["任务生命周期"])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SUSPEND / RESUME
# ─────────────────────────────────────────────────────────────────────────────

class SuspendRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{mission_id}/suspend",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def suspend_mission(
    mission_id: int,
    body: SuspendRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Suspend a queued/claimed/running mission.
    
    - Status → 'suspended'
    - Release claim_token (so no one else is blocked)
    - Freeze budget: snapshot current usage into budget_frozen
    - Record suspend reason + operator
    - Budget clock freezes (resume continues from snapshot, no zero, no re-count)
    """
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""
            SELECT id, organization_id, status, claim_token, claimed_by,
                   session_id, budget, budget_used, created_at
            FROM tensor_dev_missions WHERE id = :mid
            FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "mission not found")
    if int(row["organization_id"]) != org_id:
        raise HTTPException(404, "mission not found")
    if row["status"] not in ("queued", "claimed", "running"):
        raise HTTPException(409, f"cannot suspend status={row['status']} (must be queued/claimed/running)")

    # Snapshot current budget usage
    budget_used = row["budget_used"] or {}
    if isinstance(budget_used, str):
        budget_used = json.loads(budget_used)
    budget_limit = row["budget"] or {}
    if isinstance(budget_limit, str):
        budget_limit = json.loads(budget_limit)

    frozen = {
        "wall_seconds_used": budget_used.get("wall_seconds_used", 0),
        "tool_calls_used": budget_used.get("tool_calls_used", 0),
        "tokens_used": budget_used.get("tokens_used", 0),
        "suspended_at": datetime.now(timezone.utc).isoformat(),
        "budget_limit_snapshot": budget_limit,
    }

    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'suspended',
                claim_token = NULL,
                claimed_by = NULL,
                suspended_at = now(),
                suspend_reason = :reason,
                suspended_by = :uid,
                budget_frozen = CAST(:frozen AS jsonb)
            WHERE id = :mid
        """),
        {"mid": mission_id, "reason": body.reason, "uid": user.get("id"),
         "frozen": _dumps(frozen)},
    )
    # Audit log
    db.execute(
        text("""
            INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
            VALUES (:mid, 'suspend', :uid, CAST(:detail AS jsonb))
        """),
        {"mid": mission_id, "uid": user.get("id"),
         "detail": _dumps({"reason": body.reason, "budget_frozen": frozen})},
    )
    # Credit ledger entry for suspension
    db.execute(
        text("""
            INSERT INTO credit_ledger
                (organization_id, mission_id, session_id, entry_type,
                 budget_used, budget_limit, wall_seconds_used, tool_calls_used, tokens_used, note)
            VALUES (:org, :mid, :sid, 'suspended',
                    CAST(:bu AS jsonb), CAST(:bl AS jsonb),
                    :ws, :tc, :tk, :note)
        """),
        {"org": org_id, "mid": mission_id, "sid": row["session_id"],
         "bu": _dumps(budget_used), "bl": _dumps(budget_limit),
         "ws": frozen["wall_seconds_used"], "tc": frozen["tool_calls_used"],
         "tk": frozen["tokens_used"], "note": f"suspended: {body.reason}"},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "note",
                     {"subtype": "mission_suspended", "reason": body.reason})
    db.commit()
    return {
        "id": mission_id,
        "status": "suspended",
        "budget_frozen": frozen,
        "message": "Mission suspended. Budget clock frozen. Claim released.",
    }


@router.post("/{mission_id}/resume",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def resume_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Resume a suspended mission back to 'queued'.
    
    - Preserves creation timestamp (original queue position)
    - Budget continues from frozen snapshot (no zero, no re-count)
    - Clears suspend metadata
    """
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""
            SELECT id, organization_id, status, budget_frozen, budget_used,
                   session_id, suspended_at, suspend_reason
            FROM tensor_dev_missions WHERE id = :mid
            FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "mission not found")
    if int(row["organization_id"]) != org_id:
        raise HTTPException(404, "mission not found")
    if row["status"] != "suspended":
        raise HTTPException(409, f"cannot resume status={row['status']} (must be suspended)")

    frozen = row["budget_frozen"] or {}
    if isinstance(frozen, str):
        frozen = json.loads(frozen)

    # Restore budget_used from frozen snapshot (continue, not reset)
    budget_used = row["budget_used"] or {}
    if isinstance(budget_used, str):
        budget_used = json.loads(budget_used)
    # Merge frozen values back (they are the baseline)
    budget_used["wall_seconds_used"] = frozen.get("wall_seconds_used", budget_used.get("wall_seconds_used", 0))
    budget_used["tool_calls_used"] = frozen.get("tool_calls_used", budget_used.get("tool_calls_used", 0))
    budget_used["tokens_used"] = frozen.get("tokens_used", budget_used.get("tokens_used", 0))

    suspend_duration_s = None
    if row["suspended_at"]:
        suspended_at = row["suspended_at"]
        if hasattr(suspended_at, 'timestamp'):
            suspend_duration_s = (datetime.now(timezone.utc) - suspended_at.replace(tzinfo=timezone.utc)).total_seconds()
        else:
            suspend_duration_s = None

    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'queued',
                suspended_at = NULL,
                suspend_reason = NULL,
                suspended_by = NULL,
                budget_frozen = NULL,
                budget_used = CAST(:bu AS jsonb),
                heartbeat_at = now()
            WHERE id = :mid
        """),
        {"mid": mission_id, "bu": _dumps(budget_used)},
    )
    db.execute(
        text("""
            INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
            VALUES (:mid, 'resume', :uid, CAST(:detail AS jsonb))
        """),
        {"mid": mission_id, "uid": user.get("id"),
         "detail": _dumps({"budget_resumed": budget_used, "suspend_duration_s": suspend_duration_s})},
    )
    db.execute(
        text("""
            INSERT INTO credit_ledger
                (organization_id, mission_id, session_id, entry_type,
                 budget_used, budget_limit, wall_seconds_used, tool_calls_used, tokens_used, note)
            VALUES (:org, :mid, :sid, 'resumed',
                    CAST(:bu AS jsonb), '{}'::jsonb,
                    :ws, :tc, :tk, 'resumed from suspend')
        """),
        {"org": org_id, "mid": mission_id, "sid": row["session_id"],
         "bu": _dumps(budget_used),
         "ws": budget_used.get("wall_seconds_used", 0),
         "tc": budget_used.get("tool_calls_used", 0),
         "tk": budget_used.get("tokens_used", 0)},
    )
    if row["session_id"]:
        append_event(db, row["session_id"], "note",
                     {"subtype": "mission_resumed", "budget_continued": budget_used})
    db.commit()
    return {
        "id": mission_id,
        "status": "queued",
        "budget_continued": budget_used,
        "suspend_duration_s": suspend_duration_s,
        "message": "Mission resumed. Budget continues from frozen snapshot.",
    }


@router.get("/suspended",
            dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def list_suspended_missions(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List all suspended missions with reason and duration."""
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT id, goal, repo, status, suspend_reason, suspended_at,
                   suspended_by, budget_frozen, budget_used, created_at,
                   EXTRACT(EPOCH FROM (now() - suspended_at))::int AS suspend_duration_s
            FROM tensor_dev_missions
            WHERE organization_id = :org AND status = 'suspended'
            ORDER BY suspended_at DESC
        """),
        {"org": org_id},
    ).mappings().all()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "goal": r["goal"][:120],
            "repo": r["repo"],
            "suspend_reason": r["suspend_reason"],
            "suspended_at": _iso(r["suspended_at"]),
            "suspend_duration_s": r["suspend_duration_s"],
            "budget_frozen": r["budget_frozen"] or {},
            "budget_used": r["budget_used"] or {},
        })
    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# 2. WRITE-OFF (terminal settlement hook)
# ─────────────────────────────────────────────────────────────────────────────

class WriteOffRequest(BaseModel):
    fail_stage: str | None = Field(default=None, max_length=60,
                                    description="Mandatory for failed missions")
    report_owed: bool = Field(default=False,
                               description="True if no report was produced")
    note: str | None = Field(default=None, max_length=500)


@router.post("/{mission_id}/write-off",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def write_off_mission(
    mission_id: int,
    body: WriteOffRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Terminal settlement hook for all terminal states.
    
    - Validates fail_stage is set for failed missions
    - Marks report_owed if no report produced
    - Writes credit_ledger entry with actual usage
    - Invalidates claim_token
    - Marks worktree as recyclable
    """
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""
            SELECT id, organization_id, status, claim_token, session_id,
                   budget, budget_used, fail_stage, report_owed, result
            FROM tensor_dev_missions WHERE id = :mid
            FOR UPDATE
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "mission not found")
    if int(row["organization_id"]) != org_id:
        raise HTTPException(404, "mission not found")

    terminal_states = ("succeeded", "failed", "stopped", "revoked")
    if row["status"] not in terminal_states:
        raise HTTPException(409, f"cannot write-off status={row['status']} (must be terminal)")

    # Enforce fail_stage for failed missions
    if row["status"] == "failed":
        fail_stage = body.fail_stage if body else None
        existing_fail_stage = row["fail_stage"]
        if not fail_stage and not existing_fail_stage:
            raise HTTPException(422, "failed missions must have fail_stage set (tool_loop/build/test/acceptance/agent_lost)")
        if fail_stage:
            db.execute(
                text("UPDATE tensor_dev_missions SET fail_stage = :fs WHERE id = :mid"),
                {"mid": mission_id, "fs": fail_stage},
            )
        fail_stage = fail_stage or existing_fail_stage
    else:
        fail_stage = body.fail_stage if body else None

    report_owed = body.report_owed if body else False
    # Check if result contains a report reference
    result = row["result"] or {}
    if isinstance(result, str):
        result = json.loads(result)
    has_report = bool(result.get("report_id") or result.get("branch") or result.get("evidence"))
    if row["status"] == "failed" and not has_report:
        report_owed = True

    budget_used = row["budget_used"] or {}
    if isinstance(budget_used, str):
        budget_used = json.loads(budget_used)
    budget_limit = row["budget"] or {}
    if isinstance(budget_limit, str):
        budget_limit = json.loads(budget_limit)

    # Update mission: invalidate claim, mark worktree recyclable, set write_off timestamp
    db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET claim_token = NULL,
                worktree_recyclable = TRUE,
                write_off_at = now(),
                report_owed = :ro,
                fail_stage = COALESCE(:fs, fail_stage)
            WHERE id = :mid
        """),
        {"mid": mission_id, "ro": report_owed, "fs": fail_stage},
    )
    # Credit ledger entry
    db.execute(
        text("""
            INSERT INTO credit_ledger
                (organization_id, mission_id, session_id, entry_type,
                 budget_used, budget_limit, wall_seconds_used, tool_calls_used,
                 tokens_used, fail_stage, report_owed, note)
            VALUES (:org, :mid, :sid, :etype,
                    CAST(:bu AS jsonb), CAST(:bl AS jsonb),
                    :ws, :tc, :tk, :fs, :ro, :note)
        """),
        {"org": org_id, "mid": mission_id, "sid": row["session_id"],
         "etype": row["status"],
         "bu": _dumps(budget_used), "bl": _dumps(budget_limit),
         "ws": budget_used.get("wall_seconds_used", 0),
         "tc": budget_used.get("tool_calls_used", 0),
         "tk": budget_used.get("tokens_used", 0),
         "fs": fail_stage, "ro": report_owed,
         "note": body.note if body else None},
    )
    # Lifecycle log
    db.execute(
        text("""
            INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
            VALUES (:mid, 'write_off', :uid, CAST(:detail AS jsonb))
        """),
        {"mid": mission_id, "uid": user.get("id"),
         "detail": _dumps({
             "status": row["status"],
             "fail_stage": fail_stage,
             "report_owed": report_owed,
             "budget_used": budget_used,
         })},
    )
    db.commit()
    return {
        "id": mission_id,
        "status": row["status"],
        "write_off_at": datetime.now(timezone.utc).isoformat(),
        "fail_stage": fail_stage,
        "report_owed": report_owed,
        "worktree_recyclable": True,
        "budget_used": budget_used,
        "message": "Write-off complete. Claim invalidated. Worktree recyclable.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. WORKER PROBE (lightweight status for worker re-check)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{mission_id}/probe", dependencies=[Depends(require_auth)])
def probe_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Lightweight status probe for workers.
    
    Workers call this every N tool calls to check if they should continue.
    Returns minimal payload: status + stop_signal.
    If status is not 'running' or 'claimed', worker must abort its loop.
    """
    row = db.execute(
        text("""
            SELECT id, status, claim_token
            FROM tensor_dev_missions WHERE id = :mid
        """),
        {"mid": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "mission not found")

    status = row["status"]
    # Determine if worker should continue
    should_continue = status in ("running", "claimed")
    stop_signal = None if should_continue else f"status_is_{status}"

    return {
        "id": mission_id,
        "status": status,
        "should_continue": should_continue,
        "stop_signal": stop_signal,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. ORPHAN DETECTION
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/orphan-queue",
            dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def get_orphan_queue(
    hours_threshold: int = Query(default=48, ge=1, le=720),
    heartbeat_minutes: int = Query(default=30, ge=5, le=120),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Detect orphan missions.
    
    Two categories:
    1. Queued > 48h with no matching claimer (runner alive check: any claim in last 1h)
    2. Running/claimed with heartbeat stale > 30min → reclaim candidate
    
    Returns both lists with recommended actions.
    """
    org_id = resolved_org(None, user, db)

    # Category 1: Queued too long (orphan candidates)
    # Check if there are active runners (any claim in last 1h for this org)
    active_runners = db.execute(
        text("""
            SELECT COUNT(DISTINCT claimed_by) AS active_runner_count
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND status IN ('claimed', 'running')
              AND heartbeat_at > now() - interval '1 hour'
        """),
        {"org": org_id},
    ).scalar()

    queued_orphans = db.execute(
        text("""
            SELECT id, goal, repo, runner, model, created_at,
                   EXTRACT(EPOCH FROM (now() - created_at))::int AS age_seconds
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND status = 'queued'
              AND created_at < now() - make_interval(hours => :hours)
            ORDER BY created_at ASC
        """),
        {"org": org_id, "hours": hours_threshold},
    ).mappings().all()

    orphan_items = []
    for r in queued_orphans:
        # If no active runners at all, it's definitely orphan
        is_orphan = active_runners == 0 or r["age_seconds"] > hours_threshold * 3600
        orphan_items.append({
            "id": r["id"],
            "goal": r["goal"][:120],
            "repo": r["repo"],
            "runner": r["runner"],
            "model": r["model"],
            "age_seconds": r["age_seconds"],
            "is_orphan": is_orphan,
            "active_runners": active_runners,
            "recommended_action": "mark_orphan" if is_orphan else "wait",
        })

    # Category 2: Stale heartbeat (reclaim candidates)
    stale_running = db.execute(
        text("""
            SELECT id, goal, repo, status, claimed_by, heartbeat_at,
                   EXTRACT(EPOCH FROM (now() - heartbeat_at))::int AS stale_seconds
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND status IN ('claimed', 'running')
              AND heartbeat_at < now() - make_interval(mins => :mins)
            ORDER BY heartbeat_at ASC
        """),
        {"org": org_id, "mins": heartbeat_minutes},
    ).mappings().all()

    stale_items = []
    for r in stale_running:
        stale_items.append({
            "id": r["id"],
            "goal": r["goal"][:120],
            "repo": r["repo"],
            "status": r["status"],
            "claimed_by": r["claimed_by"],
            "stale_seconds": r["stale_seconds"],
            "recommended_action": "reclaim" if r["stale_seconds"] > heartbeat_minutes * 60 else "monitor",
        })

    return {
        "queued_orphans": orphan_items,
        "stale_running": stale_items,
        "active_runner_count": active_runners,
        "thresholds": {
            "orphan_hours": hours_threshold,
            "stale_minutes": heartbeat_minutes,
        },
    }


@router.post("/orphan-queue/process",
             dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def process_orphan_queue(
    hours_threshold: int = 48,
    heartbeat_minutes: int = 30,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Auto-process orphan queue: mark old queued as orphan, reclaim stale running."""
    org_id = resolved_org(None, user, db)

    # Mark queued > threshold as orphan
    orphaned = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'orphan', finished_at = now(),
                result = result || CAST(:patch AS jsonb)
            WHERE organization_id = :org
              AND status = 'queued'
              AND created_at < now() - make_interval(hours => :hours)
            RETURNING id
        """),
        {"org": org_id, "hours": hours_threshold,
         "patch": _dumps({"stop_condition": "orphan_no_claimer",
                          "detected_at": datetime.now(timezone.utc).isoformat()})},
    ).mappings().all()

    for o in orphaned:
        db.execute(
            text("""
                INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
                VALUES (:mid, 'orphan_detect', :uid, CAST(:detail AS jsonb))
            """),
            {"mid": o["id"], "uid": user.get("id"),
             "detail": _dumps({"reason": f"queued > {hours_threshold}h with no claimer"})},
        )

    # Reclaim stale running (mark as failed with agent_lost + fail_stage)
    reclaimed = db.execute(
        text("""
            UPDATE tensor_dev_missions
            SET status = 'failed', finished_at = now(),
                fail_stage = COALESCE(fail_stage, 'agent_lost'),
                result = result || CAST(:patch AS jsonb)
            WHERE organization_id = :org
              AND status IN ('claimed', 'running')
              AND heartbeat_at < now() - make_interval(mins => :mins)
            RETURNING id
        """),
        {"org": org_id, "mins": heartbeat_minutes,
         "patch": _dumps({"stop_condition": "agent_lost",
                          "reclaimed_at": datetime.now(timezone.utc).isoformat()})},
    ).mappings().all()

    for r in reclaimed:
        db.execute(
            text("""
                INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
                VALUES (:mid, 'reclaim', :uid, CAST(:detail AS jsonb))
            """),
            {"mid": r["id"], "uid": user.get("id"),
             "detail": _dumps({"reason": f"heartbeat stale > {heartbeat_minutes}min",
                               "fail_stage": "agent_lost"})},
        )

    db.commit()
    return {
        "orphaned_count": len(orphaned),
        "orphaned_ids": [o["id"] for o in orphaned],
        "reclaimed_count": len(reclaimed),
        "reclaimed_ids": [r["id"] for r in reclaimed],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. NECESSITY GATE (pre-dispatch 3-check)
# ─────────────────────────────────────────────────────────────────────────────

class NecessityGateRequest(BaseModel):
    """Pre-dispatch necessity check.
    
    Three checks:
    (a) Already covered? Same mission has newer report / same repo merged same capability
    (b) Mechanically fixable? Duplicate/typo/link → mechanical fix channel
    (c) Same goal in-flight? De-duplicate
    """
    goal: str = Field(min_length=10, max_length=12000)
    repo: str = Field(min_length=2, max_length=120)
    task_key: str | None = Field(default=None, max_length=120)
    origin: str = Field(default="owner", pattern="^(owner|agent|system)$")
    worker_role: str | None = None


@router.post("/necessity-gate",
             dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def necessity_gate(
    body: NecessityGateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Pre-dispatch 3-check. Returns decision: pass / reject / downgrade_mechanical.
    
    If decision is not 'pass', the dispatch should be blocked or downgraded.
    All decisions are logged for audit.
    """
    org_id = resolved_org(None, user, db)
    goal_hash = hashlib.sha256(body.goal.strip().encode()).hexdigest()[:16]

    # Check (a): Already covered? Look for succeeded missions with same repo + similar goal
    covered = db.execute(
        text("""
            SELECT id, goal, status, finished_at, result
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND repo = :repo
              AND status = 'succeeded'
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 5
        """),
        {"org": org_id, "repo": body.repo},
    ).mappings().all()

    # Simple overlap detection: check if any recent succeeded mission has significant
    # goal overlap (same first 50 chars or same task_key prefix)
    for c in covered:
        if body.task_key and c.get("id"):
            # Check if same task_key prefix exists
            existing_task = db.execute(
                text("""
                    SELECT id FROM tensor_dev_missions
                    WHERE organization_id = :org AND task_key = :key AND status = 'succeeded'
                """),
                {"org": org_id, "key": body.task_key},
            ).scalar()
            if existing_task:
                decision = "reject_covered"
                _log_gate(db, org_id, body.task_key, goal_hash, body.repo, decision,
                          f"task_key {body.task_key} already succeeded in mission {existing_task}",
                          existing_task, None)
                return {
                    "decision": decision,
                    "reason": f"Already covered: task_key={body.task_key} succeeded in mission #{existing_task}",
                    "matched_mission_id": existing_task,
                    "audited": True,
                }

    # Check (b): Mechanically fixable? Detect duplicate goals (exact or near-exact)
    # Look for in-flight missions with identical or near-identical goals
    duplicates = db.execute(
        text("""
            SELECT id, goal, status, task_key
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND repo = :repo
              AND status IN ('queued', 'claimed', 'running')
              AND id != COALESCE((SELECT id FROM tensor_dev_missions WHERE task_key = :key LIMIT 1), 0)
            ORDER BY created_at DESC
            LIMIT 10
        """),
        {"org": org_id, "repo": body.repo, "key": body.task_key or ""},
    ).mappings().all()

    # Check for exact goal match (mechanical dedup)
    goal_normalized = body.goal.strip().lower()
    for d in duplicates:
        d_goal = (d["goal"] or "").strip().lower()
        if d_goal == goal_normalized:
            # Exact duplicate → mechanical fix (dedup)
            decision = "downgrade_mechanical"
            fix_id = _create_mechanical_fix(db, org_id, "dedup_report",
                                             [d["id"]], {"duplicate_goal": body.goal[:200]})
            _log_gate(db, org_id, body.task_key, goal_hash, body.repo, decision,
                      f"Exact duplicate of in-flight mission #{d['id']}", d["id"], fix_id)
            return {
                "decision": decision,
                "reason": f"Mechanical dedup: exact duplicate of mission #{d['id']} (status={d['status']})",
                "matched_mission_id": d["id"],
                "mechanical_fix_id": fix_id,
                "audited": True,
            }

    # Check (c): Same goal in-flight? (semantic overlap — same repo + similar keywords)
    in_flight_same_repo = db.execute(
        text("""
            SELECT id, goal, status
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND repo = :repo
              AND status IN ('queued', 'claimed', 'running')
            ORDER BY created_at DESC
            LIMIT 5
        """),
        {"org": org_id, "repo": body.repo},
    ).mappings().all()

    # Simple keyword overlap: if >60% of significant words match, flag as in-flight duplicate
    goal_words = set(w for w in goal_normalized.split() if len(w) > 3)
    if goal_words:
        for inf in in_flight_same_repo:
            inf_words = set(w for w in (inf["goal"] or "").lower().split() if len(w) > 3)
            if inf_words:
                overlap = len(goal_words & inf_words) / max(len(goal_words), len(inf_words))
                if overlap > 0.7 and len(goal_words) > 3:
                    decision = "reject_inflight"
                    _log_gate(db, org_id, body.task_key, goal_hash, body.repo, decision,
                              f"Semantic overlap ({overlap:.0%}) with in-flight mission #{inf['id']}",
                              inf["id"], None)
                    return {
                        "decision": decision,
                        "reason": f"In-flight duplicate: {overlap:.0%} keyword overlap with mission #{inf['id']} (status={inf['status']})",
                        "matched_mission_id": inf["id"],
                        "audited": True,
                    }

    # All checks passed
    _log_gate(db, org_id, body.task_key, goal_hash, body.repo, "pass",
              "All 3 checks passed", None, None)
    return {
        "decision": "pass",
        "reason": "No coverage, no duplicate, no in-flight overlap detected.",
        "matched_mission_id": None,
        "mechanical_fix_id": None,
        "audited": True,
    }


def _log_gate(db, org_id, task_key, goal_hash, repo, decision, reason, matched_id, fix_id):
    db.execute(
        text("""
            INSERT INTO necessity_gate_log
                (organization_id, task_key, goal_hash, repo, decision, reason,
                 matched_mission_id, mechanical_fix_id)
            VALUES (:org, :key, :hash, :repo, :dec, :reason, :mid, :fid)
        """),
        {"org": org_id, "key": task_key, "hash": goal_hash, "repo": repo,
         "dec": decision, "reason": reason, "mid": matched_id, "fid": fix_id},
    )
    db.commit()


def _create_mechanical_fix(db, org_id, fix_type, source_ids, detail):
    result = db.execute(
        text("""
            INSERT INTO mechanical_fix_log
                (organization_id, fix_type, source_mission_ids, detail)
            VALUES (:org, :ftype, CAST(:sids AS jsonb), CAST(:detail AS jsonb))
            RETURNING id
        """),
        {"org": org_id, "ftype": fix_type, "sids": _dumps(source_ids),
         "detail": _dumps(detail)},
    ).scalar()
    db.commit()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 6. DASHBOARD SNAPSHOT (guide_tracker integration)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard-snapshot",
            dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def dashboard_snapshot(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """4-row guide_tracker snapshot:
    1. Suspended missions (count + reasons)
    2. Report-owed debt (missions with report_owed=true)
    3. Orphan queue (queued > 48h)
    4. Mechanical fix records (recent)
    """
    org_id = resolved_org(None, user, db)

    # Row 1: Suspended
    suspended = db.execute(
        text("""
            SELECT COUNT(*) AS cnt,
                   ARRAY_AGG(suspend_reason ORDER BY suspended_at DESC) AS reasons
            FROM tensor_dev_missions
            WHERE organization_id = :org AND status = 'suspended'
        """),
        {"org": org_id},
    ).mappings().first()

    # Row 2: Report-owed debt
    report_owed = db.execute(
        text("""
            SELECT COUNT(*) AS cnt,
                   ARRAY_AGG(id ORDER BY finished_at DESC) AS mission_ids
            FROM tensor_dev_missions
            WHERE organization_id = :org AND report_owed = TRUE
        """),
        {"org": org_id},
    ).mappings().first()

    # Row 3: Orphan queue
    orphans = db.execute(
        text("""
            SELECT COUNT(*) AS cnt
            FROM tensor_dev_missions
            WHERE organization_id = :org
              AND status IN ('orphan', 'queued')
              AND (status = 'orphan' OR created_at < now() - interval '48 hours')
        """),
        {"org": org_id},
    ).scalar()

    # Row 4: Mechanical fix records (last 24h)
    mech_fixes = db.execute(
        text("""
            SELECT COUNT(*) AS cnt,
                   ARRAY_AGG(fix_type) AS types
            FROM mechanical_fix_log
            WHERE organization_id = :org
              AND created_at > now() - interval '24 hours'
        """),
        {"org": org_id},
    ).mappings().first()

    # Budget summary from credit_ledger
    budget_summary = db.execute(
        text("""
            SELECT entry_type, COUNT(*) AS cnt,
                   SUM(COALESCE(tool_calls_used, 0)) AS total_tool_calls,
                   SUM(COALESCE(wall_seconds_used, 0)) AS total_wall_s
            FROM credit_ledger
            WHERE organization_id = :org
              AND created_at > now() - interval '7 days'
            GROUP BY entry_type
        """),
        {"org": org_id},
    ).mappings().all()

    return {
        "suspended": {
            "count": suspended["cnt"] or 0,
            "reasons": suspended["reasons"] or [],
        },
        "report_owed": {
            "count": report_owed["cnt"] or 0,
            "mission_ids": report_owed["mission_ids"] or [],
        },
        "orphan_queue": {
            "count": orphans or 0,
        },
        "mechanical_fixes_24h": {
            "count": mech_fixes["cnt"] or 0,
            "types": mech_fixes["types"] or [],
        },
        "credit_ledger_7d": [
            {"entry_type": r["entry_type"], "count": r["cnt"],
             "total_tool_calls": r["total_tool_calls"],
             "total_wall_s": r["total_wall_s"]}
            for r in budget_summary
        ],
    }
