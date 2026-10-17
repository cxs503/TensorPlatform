"""Tensor console: live agent-activity aggregation for the overview board.

One polled endpoint: executors (heartbeat), in-flight executions with
log tails, recent terminal executions, the cross-session append-only
event stream, and registry stats. Everything measured, nothing cached.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/activity", tags=["科研运行台·活动"])


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


@router.get("", dependencies=[Depends(require_auth)])
def activity_overview(
    events_limit: int = 60,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    events_limit = max(1, min(events_limit, 200))
    out: dict[str, Any] = {}

    # ── executors: heartbeat age drives the pulse on the board ──
    out["executors"] = [
        {
            "executor_id": e["executor_id"],
            "hostname": e["hostname"],
            "version": e["version"],
            "capabilities": e["capabilities"] or [],
            "heartbeat_at": _iso(e["heartbeat_at"]),
            "heartbeat_age_s": round(float(e["age_s"]), 1) if e["age_s"] is not None else None,
            "alive": bool(e["alive"]),
        }
        for e in db.execute(text("""
            SELECT executor_id, hostname, version, capabilities, heartbeat_at,
                   EXTRACT(EPOCH FROM (now() - heartbeat_at)) AS age_s,
                   (heartbeat_at > now() - interval '30 seconds') AS alive
            FROM tensor_executors ORDER BY executor_id
        """)).mappings().all()
    ]

    # ── in-flight executions (claim queue + running) ──
    out["active_executions"] = [
        {
            "id": r["id"],
            "execution_key": r["execution_key"],
            "scenario_key": r["scenario_key"],
            "stage": r["stage"],
            "status": r["status"],
            "claimed_by": r["claimed_by"],
            "budget": r["budget"] or {},
            "log_tail": r["log_tail"],
            "started_at": _iso(r["started_at"]),
            "created_at": _iso(r["created_at"]),
        }
        for r in db.execute(text("""
            SELECT id, execution_key, scenario_key, stage, status, claimed_by,
                   budget, log_tail, started_at, created_at
            FROM tensor_executions
            WHERE organization_id = :org
              AND status IN ('queued','claimed','running','cancel_requested')
            ORDER BY id
        """), {"org": org_id}).mappings().all()
    ]

    # ── recent terminal executions (idle-state context) ──
    out["recent_executions"] = [
        {
            "id": r["id"],
            "execution_key": r["execution_key"],
            "scenario_key": r["scenario_key"],
            "stage": r["stage"],
            "status": r["status"],
            "registered_run_ids": r["registered_run_ids"] or [],
            "result_summary": r["result_summary"] or {},
            "started_at": _iso(r["started_at"]),
            "finished_at": _iso(r["finished_at"]),
            "created_at": _iso(r["created_at"]),
        }
        for r in db.execute(text("""
            SELECT id, execution_key, scenario_key, stage, status,
                   registered_run_ids, result_summary, started_at, finished_at, created_at
            FROM tensor_executions
            WHERE organization_id = :org
              AND status IN ('succeeded','failed','cancelled')
            ORDER BY id DESC LIMIT 8
        """), {"org": org_id}).mappings().all()
    ]

    # ── cross-session append-only event stream (the agent's heartbeat trail) ──
    out["recent_events"] = [
        {
            "id": e["id"],
            "session_id": e["session_id"],
            "seq": e["seq"],
            "kind": e["kind"],
            "agent_name": e["agent_name"],
            "execution_id": e["execution_id"],
            "execution_key": e["execution_key"],
            "payload": e["payload"] or {},
            "created_at": _iso(e["created_at"]),
        }
        for e in db.execute(text("""
            SELECT e.id, e.session_id, e.seq, e.kind, e.payload, e.created_at,
                   s.agent_name, s.execution_id, x.execution_key
            FROM tensor_agent_session_events e
            JOIN tensor_agent_sessions s ON s.id = e.session_id
            LEFT JOIN tensor_executions x ON x.id = s.execution_id
            WHERE s.organization_id = :org
            ORDER BY e.id DESC LIMIT :lim
        """), {"org": org_id, "lim": events_limit}).mappings().all()
    ]

    # ── registry stats ──
    runs = db.execute(text("""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE validation_status = 'passed') AS passed,
               COALESCE(SUM(jsonb_array_length(manifest -> 'outputs')), 0) AS artifacts
        FROM tensorphysics_runs WHERE organization_id = :org
    """), {"org": org_id}).mappings().first()
    exec_stats = db.execute(text("""
        SELECT status, count(*) AS n FROM tensor_executions
        WHERE organization_id = :org GROUP BY status
    """), {"org": org_id}).mappings().all()
    pending = db.execute(text("""
        SELECT count(*) FROM tensor_approval_requests
        WHERE organization_id = :org AND status = 'pending'
    """), {"org": org_id}).scalar()
    events_total = db.execute(text("""
        SELECT count(*) FROM tensor_agent_session_events e
        JOIN tensor_agent_sessions s ON s.id = e.session_id
        WHERE s.organization_id = :org
    """), {"org": org_id}).scalar()
    out["stats"] = {
        "runs_total": int(runs["n"]),
        "runs_passed": int(runs["passed"]),
        "artifacts": int(runs["artifacts"]),
        "executions_by_status": {r["status"]: r["n"] for r in exec_stats},
        "pending_approvals": int(pending or 0),
        "events_total": int(events_total or 0),
    }
    return out
