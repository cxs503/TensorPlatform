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
    dispatch_missing_reports,
    close_resolved_dispatches,
    snapshot_coverage,
    coverage_trend,
    gate_status,
    dispatch_key_for,
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
    plan_ids: str = Query(default="", description="Comma-separated campaign plan ids (empty = whole org)"),
    include_non_terminal: bool = Query(default=False, description="Also scan in-flight nodes"),
    verify_ownership: bool = Query(
        default=False,
        description="Re-verify each report's submitter against the mission claimant "
                    "(reuses the p130 submission-time ownership tooling; off by "
                    "default because pre-gate legacy rows cannot be judged retroactively)"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Run bidirectional reconciliation: terminal nodes ↔ reports.
    Returns the four gate categories + informational orphans + KPI + gate verdict.
    Performance target: ≤ 30s (single pass, indexed).
    """
    org_id = resolved_org(None, user, db)
    result = reconcile(db, org_id, plan_ids=plan_ids or None,
                       include_non_terminal=include_non_terminal,
                       verify_ownership=verify_ownership)

    payload = result.to_dict()
    payload["gate"] = gate_status(result)

    # Performance guard
    if result.elapsed_seconds > 30.0:
        payload["kpi"]["performance_warning"] = (
            f"Reconciliation took {result.elapsed_seconds:.1f}s (target ≤30s)")
    return payload


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
            SELECT dispatch_id FROM tensor_report_supplement_queue
            WHERE dispatch_key = :key AND organization_id = :org
        """),
        {"key": dispatch_key, "org": org_id},
    ).mappings().first()

    if existing_dispatch:
        return DispatchResponse(
            dispatched=False,
            mission_id=mid,
            dispatch_id=str(existing_dispatch["dispatch_id"]),
            idempotent=True,
            message=f"Dispatch already pending (id={existing_dispatch['dispatch_id']})",
        )

    # ── Create dispatch entry (shared outbox, portable DDL, no jsonb) ─
    from audit.report_reconcile import ReconcileEntry, _insert_dispatch, ensure_supplement_queue
    ensure_supplement_queue(db)
    created = _insert_dispatch(
        db,
        org_id,
        ReconcileEntry(category="done_no_report", node_id=None, mission_id=mid, report_id=None,
                       plan_id=None, node_name=None, node_status="done"),
        body.priority,
    )
    dispatch_id = dispatch_key_for(mid)

    return DispatchResponse(
        dispatched=created,
        mission_id=mid,
        dispatch_id=dispatch_id,
        idempotent=not created,
        message=(f"补报单 dispatched for mission {mid}" if created
                 else f"Dispatch already pending for mission {mid}"),
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


# ─────────────── Closure endpoints: batch dispatch + KPI ────────────

class BatchDispatchRequest(BaseModel):
    plan_ids: str = Field(default="", description="Comma-separated campaign plan ids (empty = whole org)")
    priority: str = Field(default="normal", pattern="^(normal|high|urgent)$")
    dry_run: bool = Field(default=False, description="Only list candidates, write nothing")


class BatchDispatchResponse(BaseModel):
    dry_run: bool
    organization_id: int
    plan_ids: list[int]
    candidates: int
    dispatched: int
    already_queued: int
    resolved: int
    tickets: list[dict]


@router.post("/dispatch/batch", response_model=BatchDispatchResponse)
def dispatch_batch(
    body: BatchDispatchRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN", "MANAGER", "ENGINEER")),
):
    """
    Auto-generate补报单 for every terminal node without a report.

    Idempotency key = mission_id (UNIQUE organization_id+dispatch_key):
    running twice creates zero new rows. Tickets whose mission later produced a
    report are auto-closed to 'resolved' (账本只增不改).
    """
    org_id = resolved_org(None, user, db)
    return dispatch_missing_reports(
        db, org_id, plan_ids=body.plan_ids or None,
        priority=body.priority, dry_run=body.dry_run,
    )


@router.post("/dispatch/close")
def dispatch_close(
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN", "MANAGER")),
):
    """Mark pending tickets resolved once their report exists (自动消账)."""
    org_id = resolved_org(None, user, db)
    return close_resolved_dispatches(db, org_id)


@router.post("/kpi/snapshot")
def publish_kpi_snapshot(
    plan_ids: str = Query(default="", description="Comma-separated plan ids (empty = whole org)"),
    notes: str = Query(default="", description="Free-form round/batch label"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("ADMIN", "MANAGER")),
):
    """Run reconciliation and persist report_coverage_rate into tensor_guide_tracker."""
    org_id = resolved_org(None, user, db)
    return snapshot_coverage(db, org_id, plan_ids=plan_ids or None, notes=notes)


@router.get("/kpi/trend")
def get_kpi_trend(
    limit: int = Query(default=60, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Trend line of report_coverage_rate snapshots."""
    org_id = resolved_org(None, user, db)
    return coverage_trend(db, org_id, limit=limit)
