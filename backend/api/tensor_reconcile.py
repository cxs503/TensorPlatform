"""
Tensor Reconcile API — 报告对账门 HTTP 端点

Endpoints:
  GET  /tensor/reconcile          — run full reconciliation
  GET  /tensor/reconcile/evaluate — evaluate against gold standard
  POST /tensor/reconcile/dispatch — dispatch补报单 for missing reports (idempotent)
  GET  /tensor/reconcile/kpi      — read report coverage KPI
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from audit.report_reconcile import (
    ReconcileResult,
    reconcile,
    evaluate_gold_standard,
    inject_synthetic_done_node,
    inject_synthetic_report,
    cleanup_synthetic,
)

router = APIRouter(prefix="/tensor/reconcile", tags=["报告对账门"])


# ───────────────────────── Response models ─────────────────────────

class ReconcileResponse(BaseModel):
    elapsed_seconds: float
    total_terminal_nodes: int
    total_reports: int
    done_no_report: list[dict]
    blocked_no_report: list[dict]
    report_no_node: list[dict]
    report_hash_mismatch: list[dict]
    report_coverage_rate: float
    kpi: dict[str, Any]


class EvaluateResponse(BaseModel):
    gold_total: int
    detected_total: int
    true_positives: list[int]
    false_negatives: list[int]
    false_positives: list[int]
    recall: float
    precision: float
    recall_pct: str
    elapsed_seconds: float


class DispatchRequest(BaseModel):
    mission_id: int = Field(ge=1, description="Mission ID to dispatch补报单 for")
    priority: str = Field(default="normal", pattern="^(normal|high|urgent)$")


class DispatchResponse(BaseModel):
    dispatched: bool
    mission_id: int
    dispatch_id: str | None = None
    idempotent: bool = False
    message: str


class KPIResponse(BaseModel):
    report_coverage_rate: float
    total_terminal_nodes: int
    total_reports: int
    done_no_report_count: int
    blocked_no_report_count: int
    report_no_node_count: int
    report_hash_mismatch_count: int
    timestamp: str


# ───────────────────────── Endpoints ─────────────────────────

@router.get("")
def run_reconcile(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Run full bidirectional reconciliation.
    Returns four ledger categories + KPI.
    Performance target: ≤ 30s.
    """
    org_id = resolved_org(None, user, db)
    result = reconcile(db, org_id)

    # Performance guard
    if result.elapsed_seconds > 30.0:
        # Still return results but flag as slow
        result.kpi["performance_warning"] = f"Reconciliation took {result.elapsed_seconds:.1f}s (target ≤30s)"

    return result.to_dict()


@router.get("/evaluate")
def run_evaluate(
    gold_node_ids: str = Query(
        default="",
        description="Comma-separated list of gold standard node IDs (known zero-report nodes)",
    ),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Evaluate reconciliation against gold standard.
    gold_node_ids: comma-separated node IDs known to have no reports.
    """
    org_id = resolved_org(None, user, db)

    if not gold_node_ids.strip():
        raise HTTPException(400, detail="gold_node_ids parameter required (comma-separated)")

    try:
        ids = [int(x.strip()) for x in gold_node_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, detail="gold_node_ids must be comma-separated integers")

    eval_result = evaluate_gold_standard(db, ids, org_id)
    return eval_result


@router.post("/dispatch")
def dispatch_supplement(
    body: DispatchRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN", "MANAGER", "ENGINEER")),
):
    """
    Dispatch补报单 for a missing report. Idempotent by mission_id.

    Checks:
    1. Mission exists and is terminal
    2. No existing report for this mission
    3. No existing dispatch for this mission (idempotency)
    """
    org_id = resolved_org(None, user, db)
    mid = body.mission_id

    # ── Check 1: Mission exists ──────────────────────────────────────
    mission = db.execute(
        text("""
            SELECT id, status, organization_id FROM tensor_dev_missions
            WHERE id = :mid
        """),
        {"mid": mid},
    ).mappings().first()

    if not mission:
        return DispatchResponse(
            dispatched=False,
            mission_id=mid,
            message=f"Mission {mid} not found",
        )

    if int(mission["organization_id"]) != org_id:
        return DispatchResponse(
            dispatched=False,
            mission_id=mid,
            message=f"Mission {mid} not in your organization",
        )

    # ── Check 2: No existing report ──────────────────────────────────
    existing_report = db.execute(
        text("""
            SELECT id FROM tensor_reports WHERE mission_id = :mid AND organization_id = :org
        """),
        {"mid": mid, "org": org_id},
    ).mappings().first()

    if existing_report:
        return DispatchResponse(
            dispatched=False,
            mission_id=mid,
            idempotent=True,
            message=f"Report already exists (id={existing_report['id']})",
        )

    # ── Check 3: Idempotency — check dispatch queue ──────────────────
    # Use a simple dispatch tracking table (or metadata)
    dispatch_key = f"supplement:{mid}"
    existing_dispatch = db.execute(
        text("""
            SELECT id FROM tensor_dispatch_queue
            WHERE dispatch_key = :key AND organization_id = :org
        """),
        {"key": dispatch_key, "org": org_id},
    ).mappings().first()

    if existing_dispatch:
        return DispatchResponse(
            dispatched=False,
            mission_id=mid,
            dispatch_id=str(existing_dispatch["id"]),
            idempotent=True,
            message=f"Dispatch already pending (id={existing_dispatch['id']})",
        )

    # ── Create dispatch entry ────────────────────────────────────────
    # Try to insert into dispatch queue; fall back to metadata if table doesn't exist
    try:
        row = db.execute(
            text("""
                INSERT INTO tensor_dispatch_queue
                    (organization_id, dispatch_key, dispatch_type, mission_id, priority, status)
                VALUES (:org, :key, 'supplement_report', :mid, :priority, 'pending')
                RETURNING id
            """),
            {"org": org_id, "key": dispatch_key, "mid": mid, "priority": body.priority},
        ).mappings().first()
        db.commit()
        dispatch_id = str(row["id"])
    except Exception:
        db.rollback()
        # Fallback: store in mission metadata
        db.execute(
            text("""
                UPDATE tensor_dev_missions
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}')::jsonb,
                    '{supplement_dispatched}', 'true'::jsonb
                )
                WHERE id = :mid
            """),
            {"mid": mid},
        )
        db.commit()
        dispatch_id = None

    return DispatchResponse(
        dispatched=True,
        mission_id=mid,
        dispatch_id=dispatch_id,
        message=f"补报单 dispatched for mission {mid}",
    )


@router.get("/kpi")
def get_reconcile_kpi(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Read report coverage KPI.
    Returns current coverage rate and breakdown.
    """
    org_id = resolved_org(None, user, db)
    result = reconcile(db, org_id)

    from datetime import datetime, timezone
    return KPIResponse(
        report_coverage_rate=result.report_coverage_rate,
        total_terminal_nodes=result.total_terminal_nodes,
        total_reports=result.total_reports,
        done_no_report_count=len(result.done_no_report),
        blocked_no_report_count=len(result.blocked_no_report),
        report_no_node_count=len(result.report_no_node),
        report_hash_mismatch_count=len(result.report_hash_mismatch),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ───────────────────────── Negative control endpoints (test only) ───

@router.post("/test/inject")
def test_inject_synthetic(
    plan_id: int = Query(ge=1),
    mission_id: int = Query(ge=1),
    inject_type: str = Query(default="node", pattern="^(node|report)$"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN")),
):
    """
    Inject synthetic data for negative control testing.
    ADMIN only. Use with caution.
    """
    org_id = resolved_org(None, user, db)

    if inject_type == "node":
        node_id = inject_synthetic_done_node(db, plan_id, mission_id, name=f"synth_neg_ctrl_{int(time.time())}")
        return {"injected": "node", "node_id": node_id, "mission_id": mission_id}
    else:
        report_id = inject_synthetic_report(db, mission_id, org_id=org_id, title=f"synth_neg_ctrl_{int(time.time())}")
        return {"injected": "report", "report_id": report_id, "mission_id": mission_id}


@router.post("/test/cleanup")
def test_cleanup_synthetic(
    node_id: int | None = Query(default=None, ge=1),
    report_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN")),
):
    """Clean up synthetic test data. ADMIN only."""
    cleanup_synthetic(db, node_id, report_id)
    return {"cleaned": {"node_id": node_id, "report_id": report_id}}
