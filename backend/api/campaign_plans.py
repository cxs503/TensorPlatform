"""Campaign Plans API — ingest master plan phases/dependencies/criteria into queryable tables.

m612 mechanism: master_plan.json → campaign_plans + campaign_plan_milestones + campaign_plan_nodes + campaign_plan_dag_edges

Endpoints:
  POST /tensor/campaign-plans/ingest   — ingest a master plan JSON
  GET  /tensor/campaign-plans          — list all ingested plans
  GET  /tensor/campaign-plans/{plan_id}/phases       — list all phases (milestones) with criteria
  GET  /tensor/campaign-plans/{plan_id}/nodes         — list all nodes with acceptance criteria
  GET  /tensor/campaign-plans/{plan_id}/progress      — total progress summary (numbers)
  GET  /tensor/campaign-plans/{plan_id}/dag           — DAG edges
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/campaign-plans", tags=["战役计划"])


# ───────────────────────── helpers ─────────────────────────

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_plan(db: Session, plan_id: int, org_id: int) -> Any:
    row = db.execute(
        text("SELECT * FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": plan_id, "org": org_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="campaign plan not found")
    return row


# ───────────────────────── ingest ─────────────────────────

class MasterPlanIngest(BaseModel):
    plan_json: dict = Field(..., description="Full master_plan.json object")


@router.post("/ingest", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def ingest_plan(
    body: MasterPlanIngest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Ingest a master_plan.json into campaign_plans tables."""
    org_id = resolved_org(None, user, db)
    plan = body.plan_json
    now = _now_iso()

    source_mission = plan.get("mission_id", "unknown")
    title = plan.get("title", "Untitled Campaign Plan")

    # Upsert campaign_plans row
    existing = db.execute(
        text("SELECT id FROM campaign_plans WHERE organization_id = :org AND source_mission_id = :src"),
        {"org": org_id, "src": source_mission},
    ).scalar()

    if existing:
        plan_id = existing
        db.execute(
            text("""
                UPDATE campaign_plans SET
                    schema_version = :sv, title = :title, owner = :owner,
                    physics_scope = :ps,
                    coupling_framework_requirements = :cf,
                    validation_standards = :vs,
                    statistics = :stats,
                    ingested_at = :now
                WHERE id = :pid
            """),
            {
                "pid": plan_id, "sv": plan.get("schema_version", "2.0.0"),
                "title": title, "owner": plan.get("owner", ""),
                "ps": _dumps(plan.get("physics_scope", [])),
                "cf": _dumps(plan.get("coupling_framework_requirements", [])),
                "vs": _dumps(plan.get("validation_standards", [])),
                "stats": _dumps(plan.get("statistics", {})),
                "now": now,
            },
        )
        # Clear old children for idempotent re-ingest
        db.execute(text("DELETE FROM campaign_plan_dag_edges WHERE campaign_plan_id = :pid"), {"pid": plan_id})
        db.execute(text("DELETE FROM campaign_plan_nodes WHERE campaign_plan_id = :pid"), {"pid": plan_id})
        db.execute(text("DELETE FROM campaign_plan_milestones WHERE campaign_plan_id = :pid"), {"pid": plan_id})
    else:
        plan_id = db.execute(
            text("""
                INSERT INTO campaign_plans
                    (organization_id, source_mission_id, schema_version, title, owner,
                     physics_scope, coupling_framework_requirements, validation_standards, statistics, ingested_at)
                VALUES (:org, :src, :sv, :title, :owner,
                        :ps, :cf, :vs, :stats, :now)
                RETURNING id
            """),
            {
                "org": org_id, "src": source_mission,
                "sv": plan.get("schema_version", "2.0.0"),
                "title": title, "owner": plan.get("owner", ""),
                "ps": _dumps(plan.get("physics_scope", [])),
                "cf": _dumps(plan.get("coupling_framework_requirements", [])),
                "vs": _dumps(plan.get("validation_standards", [])),
                "stats": _dumps(plan.get("statistics", {})),
                "now": now,
            },
        ).scalar()

    # Insert milestones
    for mid, mdata in plan.get("milestones", {}).items():
        db.execute(
            text("""
                INSERT INTO campaign_plan_milestones
                    (campaign_plan_id, milestone_id, name, description, gate_criteria, target_date, status)
                VALUES (:pid, :mid, :name, :desc, :gc, :td, :status)
                ON CONFLICT (campaign_plan_id, milestone_id) DO UPDATE SET
                    name = :name, description = :desc, gate_criteria = :gc,
                    target_date = :td, status = :status
            """),
            {
                "pid": plan_id, "mid": mid,
                "name": mdata.get("name", mid),
                "desc": mdata.get("description", ""),
                "gc": _dumps(mdata.get("gate_criteria", [])),
                "td": mdata.get("target_date", ""),
                "status": mdata.get("status", "planned"),
            },
        )

    # Insert nodes
    for node in plan.get("nodes", []):
        db.execute(
            text("""
                INSERT INTO campaign_plan_nodes
                    (campaign_plan_id, node_id, source, layer, title, goal,
                     acceptance_criteria, depends_on, milestone, budget_hours, risk, physics_domain, status)
                VALUES (:pid, :nid, :src, :layer, :title, :goal,
                        :ac, :dep, :ms, :bh, :risk, :pd, :status)
                ON CONFLICT (campaign_plan_id, node_id) DO UPDATE SET
                    source = :src, layer = :layer, title = :title, goal = :goal,
                    acceptance_criteria = :ac, depends_on = :dep,
                    milestone = :ms, budget_hours = :bh, risk = :risk, physics_domain = :pd, status = :status
            """),
            {
                "pid": plan_id, "nid": node.get("id"),
                "src": node.get("source", ""), "layer": node.get("layer", ""),
                "title": node.get("title", ""), "goal": node.get("goal", ""),
                "ac": _dumps(node.get("acceptance_criteria", [])),
                "dep": _dumps(node.get("depends_on", [])),
                "ms": node.get("milestone", ""),
                "bh": node.get("budget_hours", 0),
                "risk": node.get("risk", "medium"),
                "pd": node.get("physics_domain", ""),
                "status": node.get("status", "planned"),
            },
        )

    # Insert DAG edges
    for edge in plan.get("dag_edges", []):
        db.execute(
            text("""
                INSERT INTO campaign_plan_dag_edges (campaign_plan_id, from_node, to_node)
                VALUES (:pid, :from_n, :to_n)
                ON CONFLICT (campaign_plan_id, from_node, to_node) DO NOTHING
            """),
            {"pid": plan_id, "from_n": edge.get("from"), "to_n": edge.get("to")},
        )

    db.commit()

    # Return summary
    node_count = db.execute(
        text("SELECT count(*) FROM campaign_plan_nodes WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalar()
    milestone_count = db.execute(
        text("SELECT count(*) FROM campaign_plan_milestones WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalar()
    edge_count = db.execute(
        text("SELECT count(*) FROM campaign_plan_dag_edges WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalar()

    return {
        "plan_id": plan_id,
        "source_mission_id": source_mission,
        "title": title,
        "milestones_ingested": milestone_count,
        "nodes_ingested": node_count,
        "edges_ingested": edge_count,
        "idempotent": existing is not None,
    }


# ───────────────────────── list plans ─────────────────────────

@router.get("")
def list_plans(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List all ingested campaign plans."""
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT cp.id, cp.source_mission_id, cp.title, cp.schema_version,
                   cp.owner, cp.ingested_at,
                   (SELECT count(*) FROM campaign_plan_milestones WHERE campaign_plan_id = cp.id) AS milestone_count,
                   (SELECT count(*) FROM campaign_plan_nodes WHERE campaign_plan_id = cp.id) AS node_count,
                   (SELECT count(*) FROM campaign_plan_dag_edges WHERE campaign_plan_id = cp.id) AS edge_count
            FROM campaign_plans cp
            WHERE cp.organization_id = :org
            ORDER BY cp.id
        """),
        {"org": org_id},
    ).mappings().all()
    return {"plans": [dict(r) for r in rows]}


# ───────────────────────── phases (milestones) ─────────────────────────

@router.get("/{plan_id}/phases")
def list_phases(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List all phases (milestones) with gate criteria for a campaign plan."""
    org_id = resolved_org(None, user, db)
    _load_plan(db, plan_id, org_id)
    rows = db.execute(
        text("""
            SELECT id, milestone_id, name, description, gate_criteria, target_date, status
            FROM campaign_plan_milestones
            WHERE campaign_plan_id = :pid
            ORDER BY milestone_id
        """),
        {"pid": plan_id},
    ).mappings().all()
    # Parse gate_criteria from JSON string if needed
    phases = []
    for r in rows:
        item = dict(r)
        if isinstance(item.get("gate_criteria"), str):
            try:
                item["gate_criteria"] = json.loads(item["gate_criteria"])
            except (json.JSONDecodeError, TypeError):
                pass
        phases.append(item)
    return {"plan_id": plan_id, "phases": phases}


# ───────────────────────── nodes with criteria ─────────────────────────

@router.get("/{plan_id}/nodes")
def list_nodes(
    plan_id: int,
    milestone: str | None = None,
    layer: str | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List all nodes with acceptance criteria, optionally filtered by milestone or layer."""
    org_id = resolved_org(None, user, db)
    _load_plan(db, plan_id, org_id)

    where_clauses = ["campaign_plan_id = :pid"]
    params: dict[str, Any] = {"pid": plan_id}
    if milestone:
        where_clauses.append("milestone = :ms")
        params["ms"] = milestone
    if layer:
        where_clauses.append("layer = :layer")
        params["layer"] = layer

    rows = db.execute(
        text(f"""
            SELECT id, node_id, source, layer, title, goal, acceptance_criteria,
                   depends_on, milestone, budget_hours, risk, physics_domain, status
            FROM campaign_plan_nodes
            WHERE {' AND '.join(where_clauses)}
            ORDER BY node_id
        """),
        params,
    ).mappings().all()
    nodes = []
    for r in rows:
        item = dict(r)
        # Parse JSON fields from string if needed (SQLite stores as text)
        for key in ("acceptance_criteria", "depends_on"):
            if isinstance(item.get(key), str):
                try:
                    item[key] = json.loads(item[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        nodes.append(item)
    return {"plan_id": plan_id, "nodes": nodes}


# ───────────────────────── progress ─────────────────────────

@router.get("/{plan_id}/progress")
def get_progress(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Total progress summary: per-milestone and overall node/criteria counts and status distribution."""
    org_id = resolved_org(None, user, db)
    plan = _load_plan(db, plan_id, org_id)

    # Per-milestone progress
    ms_rows = db.execute(
        text("""
            SELECT milestone_id, name, status, gate_criteria
            FROM campaign_plan_milestones
            WHERE campaign_plan_id = :pid
            ORDER BY milestone_id
        """),
        {"pid": plan_id},
    ).mappings().all()

    # Per-milestone node counts
    node_by_ms = db.execute(
        text("""
            SELECT milestone, count(*) AS total_nodes,
                   sum(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_nodes,
                   sum(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_nodes,
                   sum(CASE WHEN status = 'planned' THEN 1 ELSE 0 END) AS planned_nodes,
                   sum(budget_hours) AS total_budget_hours
            FROM campaign_plan_nodes
            WHERE campaign_plan_id = :pid
            GROUP BY milestone
            ORDER BY milestone
        """),
        {"pid": plan_id},
    ).mappings().all()

    # Total criteria count across all nodes
    all_nodes = db.execute(
        text("SELECT acceptance_criteria FROM campaign_plan_nodes WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalars().all()

    total_criteria = 0
    nodes_without_criteria = 0
    for ac_raw in all_nodes:
        try:
            ac = json.loads(ac_raw) if isinstance(ac_raw, str) else ac_raw
        except (json.JSONDecodeError, TypeError):
            ac = []
        if not ac:
            nodes_without_criteria += 1
        total_criteria += len(ac)

    total_nodes = len(all_nodes)

    # Status distribution
    status_rows = db.execute(
        text("""
            SELECT status, count(*) AS count
            FROM campaign_plan_nodes
            WHERE campaign_plan_id = :pid
            GROUP BY status
        """),
        {"pid": plan_id},
    ).mappings().all()
    status_dist = {r["status"]: r["count"] for r in status_rows}
    completed_nodes = status_dist.get("completed", 0)
    in_progress_nodes = status_dist.get("in_progress", 0)
    planned_nodes = status_dist.get("planned", 0)

    # Budget
    budget_row = db.execute(
        text("SELECT sum(budget_hours) AS total_budget_hours FROM campaign_plan_nodes WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalar()
    total_budget = float(budget_row or 0)

    # Risk distribution
    risk_dist = db.execute(
        text("""
            SELECT risk, count(*) AS count
            FROM campaign_plan_nodes
            WHERE campaign_plan_id = :pid
            GROUP BY risk
        """),
        {"pid": plan_id},
    ).mappings().all()

    # Layer distribution
    layer_dist = db.execute(
        text("""
            SELECT layer, count(*) AS count
            FROM campaign_plan_nodes
            WHERE campaign_plan_id = :pid
            GROUP BY layer
        """),
        {"pid": plan_id},
    ).mappings().all()

    # DAG stats
    dag_count = db.execute(
        text("SELECT count(*) FROM campaign_plan_dag_edges WHERE campaign_plan_id = :pid"),
        {"pid": plan_id},
    ).scalar()

    # Build milestone progress
    ms_progress = []
    for ms in ms_rows:
        gc_raw = ms["gate_criteria"]
        try:
            gc = json.loads(gc_raw) if isinstance(gc_raw, str) else gc_raw
        except (json.JSONDecodeError, TypeError):
            gc = []
        ms_node = next((n for n in node_by_ms if n["milestone"] == ms["milestone_id"]), None)
        ms_progress.append({
            "milestone_id": ms["milestone_id"],
            "name": ms["name"],
            "status": ms["status"],
            "gate_criteria_count": len(gc) if gc else 0,
            "total_nodes": ms_node["total_nodes"] if ms_node else 0,
            "completed_nodes": ms_node["completed_nodes"] if ms_node else 0,
            "in_progress_nodes": ms_node["in_progress_nodes"] if ms_node else 0,
            "planned_nodes": ms_node["planned_nodes"] if ms_node else 0,
            "total_budget_hours": float(ms_node["total_budget_hours"]) if ms_node and ms_node["total_budget_hours"] else 0,
        })

    completion_pct = (completed_nodes / total_nodes * 100) if total_nodes > 0 else 0.0

    return {
        "plan_id": plan_id,
        "source_mission_id": plan["source_mission_id"],
        "title": plan["title"],
        "total_nodes": total_nodes,
        "total_criteria": total_criteria,
        "nodes_without_criteria": nodes_without_criteria,
        "criteria_coverage_pct": round((1 - nodes_without_criteria / total_nodes) * 100, 1) if total_nodes > 0 else 0.0,
        "completed_nodes": completed_nodes,
        "in_progress_nodes": in_progress_nodes,
        "planned_nodes": planned_nodes,
        "completion_pct": round(completion_pct, 1),
        "total_budget_hours": total_budget,
        "dag_edges": dag_count,
        "risk_distribution": {r["risk"]: r["count"] for r in risk_dist},
        "layer_distribution": {l["layer"]: l["count"] for l in layer_dist},
        "milestone_progress": ms_progress,
    }


# ───────────────────────── DAG ─────────────────────────

@router.get("/{plan_id}/dag")
def get_dag(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get DAG edges for a campaign plan."""
    org_id = resolved_org(None, user, db)
    _load_plan(db, plan_id, org_id)
    rows = db.execute(
        text("SELECT from_node, to_node FROM campaign_plan_dag_edges WHERE campaign_plan_id = :pid ORDER BY from_node, to_node"),
        {"pid": plan_id},
    ).mappings().all()
    return {"plan_id": plan_id, "edges": [dict(r) for r in rows]}
