"""Tensor console: third-party reproduction agent + integrity system.

Owner 2026-10-31 directive:
  - Reproduction runner='repro': auto-generate repro sub-missions for succeeded missions.
  - Reproduction agent is read-only on repo — fresh worktree from main or agent branch,
    NEVER reads author's work tree. Blind order: compute criteria first, then compare.
  - Reproduction model is platform-assigned, author cannot choose.
  - Verdicts: pass | partial | fail | cheat (append-only table).
  - cheat → strike on author model in integrity table + negative credit.
  - Rolling 30-day strikes >= 2 → auto blacklist.
  - Blacklist: claim filter hard-excludes, dispatch skips, dashboard lists.
  - Blacklist release: owner-only, audited, never auto-forgive.
  - cheat/fail → mother mission status='revoked' (not 'failed').
  - revoked ≠ failed in statistics.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org, append_event
from database.config import get_db

router = APIRouter(prefix="/tensor/repro", tags=["科研运行台·复现"])

VALID_VERDICTS = ("pass", "partial", "fail", "cheat")
STRIKE_THRESHOLD = 2
STRIKE_WINDOW_DAYS = 30


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ───────────────────────── Verdicts ─────────────────────────

class VerdictCreate(BaseModel):
    mission_id: int = Field(ge=1)
    repro_model: str = Field(min_length=2, max_length=64)
    verdict: str = Field(pattern="^(pass|partial|fail|cheat)$")
    claims_vs_recomputed: dict[str, Any] = Field(default_factory=dict)
    evidence_paths: list[str] = Field(default_factory=list)
    wall_time_sec: float | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=5000)
    organization_id: int | None = Field(default=None, ge=1)


@router.get("/verdicts", dependencies=[Depends(require_auth)])
def list_verdicts(
    mission_id: int | None = None,
    verdict: str | None = None,
    repro_model: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List reproduction verdicts. Filter by mission, verdict type, or repro model."""
    org_id = resolved_org(None, user, db)
    filters = ["organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit}
    if mission_id is not None:
        filters.append("mission_id = :mid")
        params["mid"] = mission_id
    if verdict is not None:
        if verdict not in VALID_VERDICTS:
            raise HTTPException(422, f"verdict must be one of {VALID_VERDICTS}")
        filters.append("verdict = :v")
        params["v"] = verdict
    if repro_model is not None:
        filters.append("repro_model = :rm")
        params["rm"] = repro_model

    rows = db.execute(
        text(f"""SELECT id, mission_id, repro_model, verdict, claims_vs_recomputed,
                        evidence_paths, wall_time_sec, notes, created_at
                 FROM tensor_repro_verdicts
                 WHERE {' AND '.join(filters)}
                 ORDER BY created_at DESC LIMIT :limit"""),
        params,
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "mission_id": r["mission_id"],
                "repro_model": r["repro_model"],
                "verdict": r["verdict"],
                "claims_vs_recomputed": r["claims_vs_recomputed"] or {},
                "evidence_paths": r["evidence_paths"] or [],
                "wall_time_sec": r["wall_time_sec"],
                "notes": r["notes"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/verdicts", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def create_verdict(
    body: VerdictCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Record a reproduction verdict. Append-only (DB trigger blocks UPDATE/DELETE).

    Side effects on verdict=cheat:
      - Strike the author model in tensor_agent_integrity.
      - Record negative credit in tensor_credit_ledger.
      - If rolling 30-day strikes >= 2, auto-blacklist the model.
      - Set mother mission status to 'revoked'.

    Side effects on verdict=fail:
      - Set mother mission status to 'revoked' (disciplinary, not honest-fail).
    """
    org_id = resolved_org(body.organization_id, user, db)

    # Verify mission exists and belongs to this org
    mission = db.execute(
        text("SELECT id, status, model, organization_id FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": body.mission_id},
    ).mappings().first()
    if not mission:
        raise HTTPException(404, "mission not found")
    if mission["organization_id"] != org_id:
        raise HTTPException(404, "mission not found")

    # Insert verdict (append-only)
    row = db.execute(
        text("""
            INSERT INTO tensor_repro_verdicts
                (organization_id, mission_id, repro_model, verdict,
                 claims_vs_recomputed, evidence_paths, wall_time_sec, notes)
            VALUES (:org, :mid, :rm, :v,
                    CAST(:cvr AS jsonb), CAST(:ep AS jsonb), :wt, :notes)
            RETURNING id, created_at
        """),
        {
            "org": org_id,
            "mid": body.mission_id,
            "rm": body.repro_model,
            "v": body.verdict,
            "cvr": _dumps(body.claims_vs_recomputed),
            "ep": _dumps(body.evidence_paths),
            "wt": body.wall_time_sec,
            "notes": body.notes,
        },
    ).mappings().first()

    verdict_id = row["id"]

    # ── Side effects ──
    author_model = mission["model"]

    if body.verdict == "cheat" and author_model:
        # Strike the author model
        _apply_strike(db, org_id, author_model, body.mission_id,
                      f"cheat verdict on mission {body.mission_id} by repro model {body.repro_model}")
        # Negative credit
        db.execute(
            text("""
                INSERT INTO tensor_credit_ledger (organization_id, model, delta, reason, mission_id)
                VALUES (:org, :model, -10, :reason, :mid)
            """),
            {"org": org_id, "model": author_model, "reason": f"cheat:mission_{body.mission_id}", "mid": body.mission_id},
        )
        # Revoke mother mission
        db.execute(
            text("UPDATE tensor_dev_missions SET status = 'revoked', finished_at = now() WHERE id = :mid AND status != 'revoked'"),
            {"mid": body.mission_id},
        )

    elif body.verdict == "fail":
        # Revoke mother mission (disciplinary fail, distinct from honest fail)
        db.execute(
            text("UPDATE tensor_dev_missions SET status = 'revoked', finished_at = now() WHERE id = :mid AND status != 'revoked'"),
            {"mid": body.mission_id},
        )

    db.commit()
    return {
        "id": verdict_id,
        "mission_id": body.mission_id,
        "repro_model": body.repro_model,
        "verdict": body.verdict,
        "created_at": _iso(row["created_at"]),
    }


# _apply_strike: cheat 专用惩罚（仅 cheat 分支调用；pass/partial/fail 不触strike）
def _apply_strike(db: Session, org_id: int, model: str, mission_id: int, notes: str) -> None:
    """Add a strike to the model's integrity record. Auto-blacklist if threshold reached."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=STRIKE_WINDOW_DAYS)

    # Upsert integrity row
    existing = db.execute(
        text("""
            SELECT id, strikes FROM tensor_agent_integrity
            WHERE organization_id = :org AND model = :model
        """),
        {"org": org_id, "model": model},
    ).mappings().first()

    if existing:
        new_strikes = existing["strikes"] + 1
        db.execute(
            text("""
                UPDATE tensor_agent_integrity
                SET strikes = :s, last_strike_at = now(), updated_at = now(), notes = :notes
                WHERE id = :id
            """),
            {"s": new_strikes, "id": existing["id"], "notes": notes},
        )
    else:
        db.execute(
            text("""
                INSERT INTO tensor_agent_integrity (organization_id, model, strikes, last_strike_at, notes)
                VALUES (:org, :model, 1, now(), :notes)
            """),
            {"org": org_id, "model": model, "notes": notes},
        )

    # Count recent strikes (within window)
    recent = db.execute(
        text("""
            SELECT COUNT(*) FROM tensor_repro_verdicts
            WHERE organization_id = :org
              AND verdict = 'cheat'
              AND mission_id IN (
                  SELECT id FROM tensor_dev_missions WHERE model = :model AND organization_id = :org
              )
              AND created_at >= :since
        """),
        {"org": org_id, "model": model, "since": window_start},
    ).scalar()

    if recent >= STRIKE_THRESHOLD:
        # Auto-blacklist
        bl_existing = db.execute(
            text("""
                SELECT id FROM tensor_model_blacklist
                WHERE organization_id = :org AND model = :model AND released_at IS NULL
            """),
            {"org": org_id, "model": model},
        ).mappings().first()
        if not bl_existing:
            db.execute(
                text("""
                    INSERT INTO tensor_model_blacklist (organization_id, model, reason)
                    VALUES (:org, :model, :reason)
                    ON CONFLICT (organization_id, model) DO UPDATE
                    SET released_at = NULL, blacklisted_at = now(),
                        reason = EXCLUDED.reason
                """),
                {"org": org_id, "model": model,
                 "reason": f"auto-blacklisted: {recent} cheat verdicts in {STRIKE_WINDOW_DAYS} days"},
            )


# ───────────────────────── Reproduction Queue ─────────────────────────

class ReproQueueCreate(BaseModel):
    parent_mission_id: int = Field(ge=1)
    repro_model: str | None = Field(default=None, min_length=2, max_length=64)
    organization_id: int | None = Field(default=None, ge=1)


@router.post("/queue", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def enqueue_repro(
    body: ReproQueueCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create a reproduction sub-mission for a succeeded parent mission.

    The repro mission:
      - Has runner='repro'
      - Has parent_mission_id set
      - Model is platform-assigned (different from author's model)
      - Budget is capped (read-only repo inspection)
    """
    org_id = resolved_org(body.organization_id, user, db)

    parent = db.execute(
        text("""SELECT id, goal, repo, base_ref, model, status, organization_id
                FROM tensor_dev_missions WHERE id = :mid"""),
        {"mid": body.parent_mission_id},
    ).mappings().first()
    if not parent:
        raise HTTPException(404, "parent mission not found")
    if parent["organization_id"] != org_id:
        raise HTTPException(404, "parent mission not found")
    if parent["status"] not in ("succeeded", "revoked"):
        raise HTTPException(409, f"parent mission is {parent['status']}, not succeeded/revoked")

    # Platform assigns repro model (must differ from author's model)
    repro_model = body.repro_model
    if repro_model and repro_model == parent["model"]:
        raise HTTPException(422, "repro model must differ from author model (blind order)")
    if not repro_model:
        # Auto-assign: pick a different model from available pool
        repro_model = "repro-agent-default"
        if parent["model"] == repro_model:
            repro_model = "repro-agent-alt"

    # Check blacklist
    bl = db.execute(
        text("""SELECT id FROM tensor_model_blacklist
                WHERE organization_id = :org AND model = :model AND released_at IS NULL"""),
        {"org": org_id, "model": parent["model"]},
    ).mappings().first()
    is_blacklisted = bl is not None

    # Create repro sub-mission
    repro_budget = {"max_tool_calls": 40, "max_wall_seconds": 3600, "max_tokens": 1_500_000}
    goal = f"[REPRO] Verify claims of parent mission #{parent['id']}: {parent['goal'][:200]}"

    mission_id = db.execute(
        text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, base_ref, budget, runner, origin,
                 model, parent_mission_id, created_by, status)
            VALUES (:org, :goal, :repo, :ref, CAST(:budget AS jsonb),
                    'repro', 'owner', :model, :pid, :uid, 'queued')
            RETURNING id
        """),
        {
            "org": org_id, "goal": goal, "repo": parent["repo"],
            "ref": parent["base_ref"], "budget": _dumps(repro_budget),
            "model": repro_model, "pid": parent["id"], "uid": user.get("id"),
        },
    ).scalar()

    db.commit()
    return {
        "id": mission_id,
        "parent_mission_id": parent["id"],
        "runner": "repro",
        "repro_model": repro_model,
        "author_model": parent["model"],
        "author_blacklisted": is_blacklisted,
        "status": "queued",
        "budget": repro_budget,
    }


@router.get("/queue", dependencies=[Depends(require_auth)])
def list_repro_queue(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List reproduction missions (runner='repro')."""
    org_id = resolved_org(None, user, db)
    filters = ["organization_id = :org", "runner = 'repro'"]
    params: dict[str, Any] = {"org": org_id, "limit": limit}
    if status:
        filters.append("status = :s")
        params["s"] = status

    rows = db.execute(
        text(f"""SELECT id, parent_mission_id, goal, model, status, created_at, finished_at
                 FROM tensor_dev_missions
                 WHERE {' AND '.join(filters)}
                 ORDER BY created_at DESC LIMIT :limit"""),
        params,
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "parent_mission_id": r["parent_mission_id"],
                "goal": r["goal"],
                "model": r["model"],
                "status": r["status"],
                "created_at": _iso(r["created_at"]),
                "finished_at": _iso(r["finished_at"]),
            }
            for r in rows
        ],
        "total": len(rows),
    }


# ───────────────────────── Integrity / Blacklist ─────────────────────────

@router.get("/integrity", dependencies=[Depends(require_auth)])
def list_integrity(
    model: str | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List agent integrity records (strikes per model)."""
    org_id = resolved_org(None, user, db)
    if model:
        rows = db.execute(
            text("""SELECT model, strikes, last_strike_at, notes, updated_at
                    FROM tensor_agent_integrity
                    WHERE organization_id = :org AND model = :model"""),
            {"org": org_id, "model": model},
        ).mappings().all()
    else:
        rows = db.execute(
            text("""SELECT model, strikes, last_strike_at, notes, updated_at
                    FROM tensor_agent_integrity
                    WHERE organization_id = :org
                    ORDER BY strikes DESC, updated_at DESC"""),
            {"org": org_id},
        ).mappings().all()
    return {
        "items": [
            {
                "model": r["model"],
                "strikes": r["strikes"],
                "last_strike_at": _iso(r["last_strike_at"]),
                "notes": r["notes"],
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/blacklist", dependencies=[Depends(require_auth)])
def list_blacklist(
    active_only: bool = True,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List blacklisted models."""
    org_id = resolved_org(None, user, db)
    if active_only:
        rows = db.execute(
            text("""SELECT model, reason, blacklisted_at, released_at, released_by
                    FROM tensor_model_blacklist
                    WHERE organization_id = :org AND released_at IS NULL
                    ORDER BY blacklisted_at DESC"""),
            {"org": org_id},
        ).mappings().all()
    else:
        rows = db.execute(
            text("""SELECT model, reason, blacklisted_at, released_at, released_by
                    FROM tensor_model_blacklist
                    WHERE organization_id = :org
                    ORDER BY blacklisted_at DESC"""),
            {"org": org_id},
        ).mappings().all()
    return {
        "items": [
            {
                "model": r["model"],
                "reason": r["reason"],
                "blacklisted_at": _iso(r["blacklisted_at"]),
                "released_at": _iso(r["released_at"]),
                "released_by": r["released_by"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/blacklist/{model}/release", dependencies=[Depends(require_role("ADMIN"))])
def release_blacklist(
    model: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Owner-only: release a model from blacklist. Never auto-forgiven."""
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""SELECT id FROM tensor_model_blacklist
                WHERE organization_id = :org AND model = :model AND released_at IS NULL"""),
        {"org": org_id, "model": model},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "active blacklist entry not found")

    db.execute(
        text("""UPDATE tensor_model_blacklist
                SET released_at = now(), released_by = :uid
                WHERE id = :id"""),
        {"uid": user["id"], "id": row["id"]},
    )
    db.commit()
    return {"model": model, "released": True, "released_by": user.get("username")}


# ───────────────────────── Credit Ledger ─────────────────────────

@router.get("/credits", dependencies=[Depends(require_auth)])
def list_credits(
    model: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List credit ledger entries."""
    org_id = resolved_org(None, user, db)
    filters = ["organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit}
    if model:
        filters.append("model = :model")
        params["model"] = model
    rows = db.execute(
        text(f"""SELECT id, model, delta, reason, mission_id, created_at
                 FROM tensor_credit_ledger
                 WHERE {' AND '.join(filters)}
                 ORDER BY created_at DESC LIMIT :limit"""),
        params,
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "model": r["model"],
                "delta": r["delta"],
                "reason": r["reason"],
                "mission_id": r["mission_id"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ],
        "total": len(rows),
    }


# ───────────────────────── Guide Tracker (KPI) ─────────────────────────

@router.get("/kpi", dependencies=[Depends(require_auth)])
def get_kpi(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get reproduction KPI metrics for guide_tracker.

    Metrics:
      - repro_rate: (pass + partial) / total_verdicts (target: 0.95)
      - cheat_detected: count of cheat verdicts
      - blacklisted_models: count of active blacklist entries
      - total_strikes: sum of all strikes
    """
    org_id = resolved_org(None, user, db)

    total_verdicts = db.execute(
        text("SELECT COUNT(*) FROM tensor_repro_verdicts WHERE organization_id = :org"),
        {"org": org_id},
    ).scalar() or 0

    pass_partial = db.execute(
        text("""SELECT COUNT(*) FROM tensor_repro_verdicts
                WHERE organization_id = :org AND verdict IN ('pass','partial')"""),
        {"org": org_id},
    ).scalar() or 0

    cheat_count = db.execute(
        text("""SELECT COUNT(*) FROM tensor_repro_verdicts
                WHERE organization_id = :org AND verdict = 'cheat'"""),
        {"org": org_id},
    ).scalar() or 0

    fail_count = db.execute(
        text("""SELECT COUNT(*) FROM tensor_repro_verdicts
                WHERE organization_id = :org AND verdict = 'fail'"""),
        {"org": org_id},
    ).scalar() or 0

    blacklisted = db.execute(
        text("""SELECT COUNT(*) FROM tensor_model_blacklist
                WHERE organization_id = :org AND released_at IS NULL"""),
        {"org": org_id},
    ).scalar() or 0

    total_strikes = db.execute(
        text("""SELECT COALESCE(SUM(strikes), 0) FROM tensor_agent_integrity
                WHERE organization_id = :org"""),
        {"org": org_id},
    ).scalar() or 0

    repro_rate = (pass_partial / total_verdicts) if total_verdicts > 0 else 0.0

    # Snapshot to guide_tracker
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for key, value, target in [
        ("repro_rate", repro_rate, 0.95),
        ("cheat_detected", float(cheat_count), None),
        ("blacklisted_models", float(blacklisted), None),
        ("total_strikes", float(total_strikes), None),
    ]:
        db.execute(
            text("""
                INSERT INTO tensor_guide_tracker (organization_id, metric_key, metric_value, target_value, snapshot_at)
                VALUES (:org, :key, :val, :target, :snap)
                ON CONFLICT (organization_id, metric_key, snapshot_at) DO UPDATE
                SET metric_value = EXCLUDED.metric_value
            """),
            {"org": org_id, "key": key, "val": value, "target": target, "snap": now},
        )
    db.commit()

    return {
        "repro_rate": round(repro_rate, 4),
        "repro_rate_target": 0.95,
        "total_verdicts": total_verdicts,
        "pass_partial": pass_partial,
        "cheat_detected": cheat_count,
        "fail_detected": fail_count,
        "blacklisted_models": blacklisted,
        "total_strikes": total_strikes,
        "snapshot_at": now.isoformat(),
    }


# ───────────────────────── Statistics (revoked ≠ failed) ─────────────────────────

@router.get("/stats", dependencies=[Depends(require_auth)])
def get_stats(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Mission statistics with revoked separated from failed.

    revoked: disciplinary (cheat/fail verdict from repro agent)
    failed: honest failure (not overturned by repro)
    """
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""SELECT status, COUNT(*) as cnt
                FROM tensor_dev_missions
                WHERE organization_id = :org
                GROUP BY status"""),
        {"org": org_id},
    ).mappings().all()
    stats = {r["status"]: r["cnt"] for r in rows}
    return {
        "succeeded": stats.get("succeeded", 0),
        "failed": stats.get("failed", 0),
        "revoked": stats.get("revoked", 0),
        "stopped": stats.get("stopped", 0),
        "running": stats.get("running", 0),
        "queued": stats.get("queued", 0),
        "claimed": stats.get("claimed", 0),
        "note": "revoked is disciplinary (repro cheat/fail), NOT honest failure",
    }
