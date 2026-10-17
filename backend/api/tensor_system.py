"""Tensor console: live system health (all probes measured, never cached)."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.s3_stream import artifact_bucket, s3_client

router = APIRouter(prefix="/tensor/system", tags=["系统健康"])


@router.get("/health", dependencies=[Depends(require_auth)])
def system_health(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    health: dict[str, Any] = {"organization_id": org_id}

    # ── database latency ──
    t0 = time.perf_counter()
    db.execute(text("SELECT 1")).scalar()
    health["database"] = {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}

    # ── object store (SeaweedFS S3 gateway via forwarder) ──
    t0 = time.perf_counter()
    try:
        s3_client().head_bucket(Bucket=artifact_bucket())
        health["object_store"] = {
            "ok": True,
            "bucket": artifact_bucket(),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 — health must never raise
        health["object_store"] = {
            "ok": False,
            "bucket": artifact_bucket(),
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }

    # ── executors (heartbeat within 30s = alive) ──
    executors = db.execute(
        text("""
            SELECT executor_id, hostname, version, capabilities, heartbeat_at,
                   (heartbeat_at > now() - interval '30 seconds') AS alive
            FROM tensor_executors ORDER BY executor_id
        """),
    ).mappings().all()
    health["executors"] = [
        {
            "executor_id": e["executor_id"],
            "hostname": e["hostname"],
            "version": e["version"],
            "capabilities": e["capabilities"] or [],
            "heartbeat_at": e["heartbeat_at"].isoformat() if e["heartbeat_at"] else None,
            "alive": bool(e["alive"]),
        }
        for e in executors
    ]

    # ── run registry stats ──
    run_stats = db.execute(
        text("""
            SELECT status, count(*) AS n FROM tensorphysics_runs
            WHERE organization_id = :org GROUP BY status
        """),
        {"org": org_id},
    ).mappings().all()
    exec_stats = db.execute(
        text("""
            SELECT status, count(*) AS n FROM tensor_executions
            WHERE organization_id = :org GROUP BY status
        """),
        {"org": org_id},
    ).mappings().all()
    pending_approvals = db.execute(
        text("""
            SELECT count(*) FROM tensor_approval_requests
            WHERE organization_id = :org AND status = 'pending'
        """),
        {"org": org_id},
    ).scalar()
    health["runs_by_status"] = {r["status"]: r["n"] for r in run_stats}
    health["executions_by_status"] = {r["status"]: r["n"] for r in exec_stats}
    health["pending_approvals"] = int(pending_approvals or 0)

    health["ok"] = bool(
        health["database"]["ok"] and health["object_store"]["ok"]
    )
    return health
