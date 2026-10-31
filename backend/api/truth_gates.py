"""
Truth Gates API — Independence-Convergence Verdict Mechanism

Endpoints:
  POST /truth-gates/votes              — submit a verdict vote
  GET  /truth-gates/vote-groups/{id}   — get aggregated vote result
  POST /truth-gates/metered-usage      — platform-only metering write
  POST /truth-gates/reconcile          — usage reconciliation
  POST /truth-gates/sidecar-check      — run sidecar physical-property check
  GET  /truth-gates/integrity-events   — list integrity events
  GET  /truth-gates/split-events       — list split verdict events
  POST /truth-gates/red-team/permission — permission red-team test
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db
from api.deps import require_auth
from models.truth_gates import (
    ReproVerdictVote, SplitVerdictEvent, MeteredUsage,
    IntegrityEvent, SidecarCheckResult,
)
from services.truth_gates_aggregator import aggregate_votes, reconcile_usage, reconcile_hash
from services.sidecar_checkers import run_checker, CHECKER_REGISTRY

router = APIRouter(prefix="/truth-gates", tags=["Truth Gates — 独立性收敛判真"])


# ─── Pydantic schemas ───────────────────────────────────────────────

class VoteSubmit(BaseModel):
    vote_group_id: str
    mission_id: int | None = None
    voter_model: str
    voter_worktree: str | None = None
    verdict: str  # cheat | pass | partial | fail
    confidence: float = 0.5
    evidence_hash: str | None = None
    reasoning: str | None = None
    is_blind: bool = True


class MeteredUsageSubmit(BaseModel):
    mission_id: int
    agent_session_id: str | None = None
    tool_calls: int = 0
    tokens_used: int = 0
    wall_seconds: float = 0.0
    api_calls_detail: dict | None = None


class ReconcileRequest(BaseModel):
    mission_id: int
    self_reported: dict[str, Any]
    threshold_pct: float = 20.0


class SidecarCheckRequest(BaseModel):
    mission_id: int | None = None
    checker_name: str
    data: dict[str, Any]


# ─── Voting endpoints ───────────────────────────────────────────────

@router.post("/votes")
def submit_vote(
    req: VoteSubmit,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Submit a verdict vote for 2-of-3 majority aggregation."""
    if req.verdict not in ("cheat", "pass", "partial", "fail"):
        raise HTTPException(400, f"Invalid verdict: {req.verdict}")

    vote = ReproVerdictVote(
        vote_group_id=req.vote_group_id,
        mission_id=req.mission_id,
        voter_model=req.voter_model,
        voter_worktree=req.voter_worktree,
        verdict=req.verdict,
        confidence=req.confidence,
        evidence_hash=req.evidence_hash,
        reasoning=req.reasoning,
        is_blind=req.is_blind,
    )
    db.add(vote)
    db.commit()
    db.refresh(vote)

    # Fetch all votes in this group and aggregate
    rows = db.execute(
        text("SELECT voter_model, verdict, confidence FROM repro_verdict_votes WHERE vote_group_id = :vgid"),
        {"vgid": req.vote_group_id},
    ).fetchall()

    votes_list = [{"voter_model": r[0], "verdict": r[1], "confidence": r[2]} for r in rows]
    aggregation = aggregate_votes(votes_list)

    # If split verdict, record event
    if aggregation["is_split"] and len(votes_list) >= 3:
        split_event = SplitVerdictEvent(
            vote_group_id=req.vote_group_id,
            mission_id=req.mission_id,
            vote_tally=aggregation["tally"],
            resolution="unresolved",
            notes=f"Split verdict: {aggregation['tally']}",
        )
        db.add(split_event)
        db.commit()

    # If confirmed cheat (>=2 cheat votes), mark as strike
    strike_confirmed = aggregation["is_confirmed_cheat"]

    return {
        "vote": vote.to_dict(),
        "aggregation": aggregation,
        "strike_confirmed": strike_confirmed,
    }


@router.get("/vote-groups/{vote_group_id}")
def get_vote_group(
    vote_group_id: str,
    db: Session = Depends(get_db),
):
    """Get aggregated vote result for a vote group (pure re-computation)."""
    rows = db.execute(
        text("""
            SELECT voter_model, verdict, confidence, evidence_hash, reasoning, is_blind
            FROM repro_verdict_votes WHERE vote_group_id = :vgid
            ORDER BY created_at
        """),
        {"vgid": vote_group_id},
    ).fetchall()

    if not rows:
        raise HTTPException(404, "Vote group not found")

    votes_list = [
        {"voter_model": r[0], "verdict": r[1], "confidence": r[2],
         "evidence_hash": r[3], "reasoning": r[4], "is_blind": r[5]}
        for r in rows
    ]
    aggregation = aggregate_votes(votes_list)

    # Check for split event
    split_row = db.execute(
        text("SELECT id, vote_tally, resolution FROM split_verdict_events WHERE vote_group_id = :vgid"),
        {"vgid": vote_group_id},
    ).fetchone()

    return {
        "vote_group_id": vote_group_id,
        "votes": votes_list,
        "aggregation": aggregation,
        "split_event": dict(split_row._mapping) if split_row else None,
    }


# ─── Metering endpoints ─────────────────────────────────────────────

@router.post("/metered-usage")
def submit_metered_usage(
    req: MeteredUsageSubmit,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Platform-only metering write.
    Agent accounts MUST be rejected (403) — this is the permission red-team test.
    """
    # Permission check: only platform/admin can write metered usage
    user_role = user.get("role", "")
    username = user.get("username", "")

    # Block agent accounts (non-admin roles trying to write metered data)
    if user_role not in ("admin", "platform"):
        raise HTTPException(
            403,
            f"FORBIDDEN: Only platform middleware may write metered_usage. "
            f"Agent account '{username}' (role={user_role}) rejected. "
            f"This is a permission red-team enforcement."
        )

    meter = MeteredUsage(
        mission_id=req.mission_id,
        agent_session_id=req.agent_session_id,
        tool_calls=req.tool_calls,
        tokens_used=req.tokens_used,
        wall_seconds=req.wall_seconds,
        api_calls_detail=req.api_calls_detail,
        recorded_by="platform_middleware",
    )
    db.add(meter)
    db.commit()
    db.refresh(meter)
    return {"ok": True, "metered_usage": meter.to_dict()}


@router.post("/reconcile")
def reconcile(
    req: ReconcileRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Reconcile self-reported usage vs platform-metered usage.
    If deviation > threshold, create integrity event.
    """
    # Get latest metered usage for this mission
    meter_row = db.execute(
        text("""
            SELECT tool_calls, tokens_used, wall_seconds
            FROM metered_usage WHERE mission_id = :mid
            ORDER BY created_at DESC LIMIT 1
        """),
        {"mid": req.mission_id},
    ).fetchone()

    if not meter_row:
        raise HTTPException(404, f"No metered usage found for mission {req.mission_id}")

    metered = {
        "tool_calls": meter_row[0],
        "tokens_used": meter_row[1],
        "wall_seconds": meter_row[2],
    }

    result = reconcile_usage(req.self_reported, metered, req.threshold_pct)

    # If anomaly, create integrity event
    if result["is_anomaly"]:
        event = IntegrityEvent(
            mission_id=req.mission_id,
            event_type="usage_deviation",
            self_reported=req.self_reported,
            metered=metered,
            deviation_pct=result["max_deviation_pct"],
            severity="critical" if result["max_deviation_pct"] > 100 else "warning",
            notes=f"Anomaly fields: {result['anomaly_fields']}",
        )
        db.add(event)
        db.commit()

    return {
        "mission_id": req.mission_id,
        "reconciliation": result,
        "integrity_event_created": result["is_anomaly"],
    }


# ─── Sidecar checker endpoints ──────────────────────────────────────

@router.post("/sidecar-check")
def sidecar_check(
    req: SidecarCheckRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Run a sidecar physical-property constraint check."""
    result = run_checker(req.checker_name, req.data)

    # Persist result
    check_result = SidecarCheckResult(
        mission_id=req.mission_id,
        checker_name=result.get("checker_name", req.checker_name),
        output_type=result.get("output_type", "unknown"),
        passed=result["passed"],
        details=result.get("details"),
        red_flag=result.get("red_flag", False),
    )
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    return {
        "check": check_result.to_dict(),
        "result": result,
    }


@router.get("/sidecar-checkers")
def list_checkers():
    """List available sidecar checkers."""
    return {
        "checkers": [
            {"name": c.name, "output_type": c.output_type}
            for c in CHECKER_REGISTRY.values()
        ]
    }


# ─── Integrity & split event queries ────────────────────────────────

@router.get("/integrity-events")
def list_integrity_events(
    mission_id: int | None = None,
    db: Session = Depends(get_db),
):
    """List integrity events (usage deviation, hash mismatch, etc.)."""
    if mission_id:
        rows = db.execute(
            text("SELECT * FROM integrity_events WHERE mission_id = :mid ORDER BY created_at DESC"),
            {"mid": mission_id},
        ).fetchall()
    else:
        rows = db.execute(
            text("SELECT * FROM integrity_events ORDER BY created_at DESC LIMIT 100")
        ).fetchall()

    return {"events": [dict(r._mapping) for r in rows]}


@router.get("/split-events")
def list_split_events(db: Session = Depends(get_db)):
    """List split verdict events (no 2-of-3 consensus)."""
    rows = db.execute(
        text("SELECT * FROM split_verdict_events ORDER BY created_at DESC LIMIT 100")
    ).fetchall()
    return {"events": [dict(r._mapping) for r in rows]}


# ─── Permission red-team test endpoint ──────────────────────────────

@router.post("/red-team/permission-test")
def permission_red_team_test(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Permission red-team: attempt to write metered_usage as agent account.
    This endpoint demonstrates the enforcement — agent accounts get 403.
    """
    user_role = user.get("role", "")
    username = user.get("username", "")

    # This is the test: if the user is NOT admin/platform, they should be blocked
    if user_role not in ("admin", "platform"):
        return {
            "red_team_result": "BLOCKED",
            "message": f"Agent account '{username}' (role={user_role}) correctly blocked from writing metered_usage",
            "http_status": 403,
        }
    else:
        return {
            "red_team_result": "ALLOWED",
            "message": f"Admin/platform account '{username}' permitted to write metered_usage",
            "http_status": 200,
        }
