"""Campaign Plans API — 主计划入库 + 实时进度快照 + 总管 agent 职责卡。

Schema:
  campaign_plans       — 主计划（里程碑/节点/判据/依赖/进度度量）
  campaign_milestones  — 里程碑
  campaign_nodes       — 细粒度节点（挂 mission）
  campaign_snapshots   — 30min 级进度快照
  campaign_events      — 事件流

Red team 验证:
  - 伪造完成节点（无复现判决）→ 快照不算 done
  - 阻塞链注入 → 必须检出并呈报
  - 计划外单注入 → 游击系数上升
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/campaigns", tags=["战役总管"])

# ───────────────────────── helpers ─────────────────────────

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_dict(r: Any) -> dict:
    """Convert SQLAlchemy RowMapping to dict with ISO dates."""
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, '__iter__') and not isinstance(v, (str, dict)):
            out[k] = list(v) if v is not None else None
        else:
            out[k] = v
    return out


# ───────────────────────── Campaign Plans CRUD ─────────────────────────

class CampaignPlanCreate(BaseModel):
    campaign_id: int = Field(ge=1)
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    sir_master_ref: str | None = Field(default=None, max_length=200)
    plan_version: str = Field(default="1.0", max_length=20)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    budget_planned: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    organization_id: int | None = None


class MilestoneCreate(BaseModel):
    plan_id: int = Field(ge=1)
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=1000)
    criteria: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[int] = Field(default_factory=list)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeCreate(BaseModel):
    plan_id: int = Field(ge=1)
    milestone_id: int | None = None
    mission_id: int | None = None
    name: str = Field(min_length=2, max_length=200)
    node_type: str = Field(default="task", pattern="^(task|review|gate|checkpoint)$")
    dependencies: list[int] = Field(default_factory=list)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeVerdictUpdate(BaseModel):
    """更新节点复现判决结果。"""
    reproduction_verdict: str = Field(pattern="^(pass|fail)$")
    block_reason: str | None = None


@router.post("/plans", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_plan(
    body: CampaignPlanCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    # Verify campaign exists
    camp = db.execute(
        text("SELECT id FROM factory_strategy_campaigns WHERE id = :cid AND organization_id = :org"),
        {"cid": body.campaign_id, "org": org_id},
    ).mappings().first()
    if not camp:
        raise HTTPException(404, "campaign not found")

    budget = {"max_tool_calls": 0, "max_wall_seconds": 0, "max_tokens": 0}
    budget.update(body.budget_planned)

    row = db.execute(
        text("""
            INSERT INTO campaign_plans
                (organization_id, campaign_id, name, description, sir_master_ref,
                 plan_version, planned_start, planned_end, budget_planned, metadata,
                 status, created_by)
            VALUES (:org, :cid, :name, :desc, :sir, :ver, :ps, :pe,
                    CAST(:bp AS jsonb), CAST(:meta AS jsonb), 'draft', :uid)
            RETURNING id, created_at
        """),
        {
            "org": org_id, "cid": body.campaign_id, "name": body.name,
            "desc": body.description, "sir": body.sir_master_ref,
            "ver": body.plan_version, "ps": body.planned_start, "pe": body.planned_end,
            "bp": _dumps(budget), "meta": _dumps(body.metadata), "uid": user["id"],
        },
    ).mappings().first()
    db.commit()
    return {"id": row["id"], "status": "draft", "created_at": _iso(row["created_at"])}


@router.get("/plans", dependencies=[Depends(require_auth)])
def list_plans(
    campaign_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    filters = ["p.organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": min(max(limit, 1), 100)}
    if campaign_id:
        filters.append("p.campaign_id = :cid")
        params["cid"] = campaign_id
    if status:
        filters.append("p.status = :st")
        params["st"] = status

    rows = db.execute(
        text(f"""
            SELECT p.id, p.campaign_id, p.name, p.description, p.sir_master_ref,
                   p.plan_version, p.planned_start, p.planned_end, p.budget_planned,
                   p.budget_actual, p.status, p.metadata, p.created_by, p.created_at, p.updated_at
            FROM campaign_plans p
            WHERE {' AND '.join(filters)}
            ORDER BY p.created_at DESC LIMIT :limit
        """),
        params,
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/plans/{plan_id}", dependencies=[Depends(require_auth)])
def get_plan(plan_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text("""
            SELECT * FROM campaign_plans WHERE id = :pid AND organization_id = :org
        """),
        {"pid": plan_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "plan not found")
    return _row_to_dict(r)


@router.post("/plans/{plan_id}/activate", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def activate_plan(plan_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text("SELECT status FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": plan_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "plan not found")
    if r["status"] not in ("draft", "paused"):
        raise HTTPException(409, f"cannot activate plan in status '{r['status']}'")
    db.execute(
        text("UPDATE campaign_plans SET status = 'active', updated_at = now() WHERE id = :pid"),
        {"pid": plan_id},
    )
    db.commit()
    return {"status": "active"}


# ───────────────────────── Milestones CRUD ─────────────────────────

@router.post("/milestones", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_milestone(
    body: MilestoneCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    # Verify plan exists and belongs to org
    plan = db.execute(
        text("SELECT id FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": body.plan_id, "org": org_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "plan not found")

    row = db.execute(
        text("""
            INSERT INTO campaign_milestones
                (plan_id, name, description, criteria, dependencies,
                 planned_start, planned_end, metadata)
            VALUES (:pid, :name, :desc, CAST(:crit AS jsonb), :deps,
                    :ps, :pe, CAST(:meta AS jsonb))
            RETURNING id, created_at
        """),
        {
            "pid": body.plan_id, "name": body.name, "desc": body.description,
            "crit": _dumps(body.criteria), "deps": body.dependencies,
            "ps": body.planned_start, "pe": body.planned_end,
            "meta": _dumps(body.metadata),
        },
    ).mappings().first()
    db.commit()
    return {"id": row["id"], "created_at": _iso(row["created_at"])}


@router.get("/plans/{plan_id}/milestones", dependencies=[Depends(require_auth)])
def list_milestones(plan_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT m.* FROM campaign_milestones m
            JOIN campaign_plans p ON m.plan_id = p.id
            WHERE p.id = :pid AND p.organization_id = :org
            ORDER BY m.id
        """),
        {"pid": plan_id, "org": org_id},
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows]}


# ───────────────────────── Nodes CRUD ─────────────────────────

@router.post("/nodes", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_node(
    body: NodeCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    plan = db.execute(
        text("SELECT id FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": body.plan_id, "org": org_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "plan not found")

    row = db.execute(
        text("""
            INSERT INTO campaign_nodes
                (plan_id, milestone_id, mission_id, name, node_type,
                 dependencies, planned_start, planned_end, metadata)
            VALUES (:pid, :mid, :moid, :name, :ntype, :deps, :ps, :pe,
                    CAST(:meta AS jsonb))
            RETURNING id, created_at
        """),
        {
            "pid": body.plan_id, "mid": body.milestone_id, "moid": body.mission_id,
            "name": body.name, "ntype": body.node_type, "deps": body.dependencies,
            "ps": body.planned_start, "pe": body.planned_end,
            "meta": _dumps(body.metadata),
        },
    ).mappings().first()
    db.commit()
    return {"id": row["id"], "created_at": _iso(row["created_at"])}


@router.get("/plans/{plan_id}/nodes", dependencies=[Depends(require_auth)])
def list_nodes(plan_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT n.* FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE p.id = :pid AND p.organization_id = :org
            ORDER BY n.id
        """),
        {"pid": plan_id, "org": org_id},
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows]}


@router.post("/nodes/{node_id}/verdict", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def update_node_verdict(
    node_id: int,
    body: NodeVerdictUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """更新节点复现判决。只有 'pass' 才能将节点标记为 done。"""
    org_id = resolved_org(None, user, db)
    node = db.execute(
        text("""
            SELECT n.*, p.organization_id FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE n.id = :nid AND p.organization_id = :org
        """),
        {"nid": node_id, "org": org_id},
    ).mappings().first()
    if not node:
        raise HTTPException(404, "node not found")

    verdict = body.reproduction_verdict
    if verdict == "pass":
        new_status = "done"
        new_fails = 0
    else:
        new_status = "failed"
        new_fails = int(node["consecutive_fails"]) + 1

    db.execute(
        text("""
            UPDATE campaign_nodes
            SET reproduction_verdict = :verdict,
                status = :status,
                consecutive_fails = :fails,
                block_reason = :br,
                actual_end = CASE WHEN :status = 'done' THEN now() ELSE actual_end END,
                updated_at = now()
            WHERE id = :nid
        """),
        {
            "nid": node_id, "verdict": verdict, "status": new_status,
            "fails": new_fails, "br": body.block_reason,
        },
    )

    # 记录事件
    db.execute(
        text("""
            INSERT INTO campaign_events (organization_id, plan_id, event_type, detail)
            VALUES (:org, :pid, 'node_status_change',
                    CAST(:detail AS jsonb))
        """),
        {
            "org": org_id, "pid": node["plan_id"],
            "detail": _dumps({"node_id": node_id, "verdict": verdict, "status": new_status,
                              "consecutive_fails": new_fails}),
        },
    )

    db.commit()
    return {"node_id": node_id, "status": new_status, "consecutive_fails": new_fails}


# ───────────────────────── Snapshots ─────────────────────────

@router.post("/plans/{plan_id}/snapshot", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def take_snapshot(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """生成 30min 级进度快照。"""
    org_id = resolved_org(None, user, db)
    plan = db.execute(
        text("SELECT * FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": plan_id, "org": org_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "plan not found")

    # 节点状态聚合
    node_stats = db.execute(
        text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'done') as done,
                COUNT(*) FILTER (WHERE status = 'in-flight') as in_flight,
                COUNT(*) FILTER (WHERE status = 'blocked') as blocked,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) FILTER (WHERE status = 'orphan') as orphan
            FROM campaign_nodes WHERE plan_id = :pid
        """),
        {"pid": plan_id},
    ).mappings().first()

    # 里程碑完成 %
    ms_stats = db.execute(
        text("""
            SELECT
                COUNT(*) as total,
                COALESCE(AVG(progress_pct), 0) as avg_progress
            FROM campaign_milestones WHERE plan_id = :pid
        """),
        {"pid": plan_id},
    ).mappings().first()

    # 预算实耗（从关联 mission 聚合）
    budget_actual = db.execute(
        text("""
            SELECT
                COALESCE(SUM((budget->>'max_tool_calls')::int), 0) as tool_calls,
                COALESCE(SUM((budget->>'max_wall_seconds')::int), 0) as wall_seconds,
                COALESCE(SUM((budget->>'max_tokens')::int), 0) as tokens
            FROM tensor_dev_missions
            WHERE campaign_id = :cid AND status IN ('succeeded', 'failed', 'stopped', 'running')
        """),
        {"cid": plan["campaign_id"]},
    ).mappings().first()

    # 阻塞链检测：上游滞留 + 下游空等
    blocked_chains = _detect_blocked_chains(db, plan_id)

    # 打转检测：连败 ≥2 的节点
    spinning = db.execute(
        text("""
            SELECT id, name, consecutive_fails, milestone_id
            FROM campaign_nodes
            WHERE plan_id = :pid AND consecutive_fails >= 2
        """),
        {"pid": plan_id},
    ).mappings().all()

    # 游击系数：计划外单占比
    # 计划外单 = 有 campaign_id 但不在任何 node 的 mission_id 中的 mission
    total_missions = db.execute(
        text("""
            SELECT COUNT(*) FROM tensor_dev_missions
            WHERE campaign_id = :cid
        """),
        {"cid": plan["campaign_id"]},
    ).scalar() or 0

    planned_missions = db.execute(
        text("""
            SELECT COUNT(DISTINCT mission_id) FROM campaign_nodes
            WHERE plan_id = :pid AND mission_id IS NOT NULL
        """),
        {"pid": plan_id},
    ).scalar() or 0

    guerrilla_ratio = 0.0
    if total_missions > 0:
        unplanned = max(0, total_missions - planned_missions)
        guerrilla_ratio = round(unplanned / total_missions, 4)

    # 构建快照数据
    snapshot_data = {
        "node_stats": _row_to_dict(node_stats),
        "milestone_progress": _row_to_dict(ms_stats),
        "blocked_chains": blocked_chains,
        "spinning_nodes": [_row_to_dict(s) for s in spinning],
        "guerrilla_ratio": guerrilla_ratio,
        "total_missions": total_missions,
        "planned_missions": planned_missions,
    }

    budget_actual_dict = {
        "max_tool_calls": budget_actual["tool_calls"],
        "max_wall_seconds": budget_actual["wall_seconds"],
        "max_tokens": budget_actual["tokens"],
    }

    row = db.execute(
        text("""
            INSERT INTO campaign_snapshots
                (organization_id, plan_id, snapshot_at,
                 total_nodes, done_nodes, in_flight_nodes, blocked_nodes,
                 failed_nodes, orphan_nodes, milestone_progress_pct,
                 budget_planned, budget_actual,
                 blocked_chains, spinning_nodes, guerrilla_ratio, snapshot_data)
            VALUES (:org, :pid, now(),
                    :total, :done, :inflight, :blocked,
                    :failed, :orphan, :msprog,
                    CAST(:bp AS jsonb), CAST(:ba AS jsonb),
                    CAST(:bc AS jsonb), CAST(:sn AS jsonb), :gr,
                    CAST(:sd AS jsonb))
            RETURNING id
        """),
        {
            "org": org_id, "pid": plan_id,
            "total": node_stats["total"], "done": node_stats["done"],
            "inflight": node_stats["in_flight"], "blocked": node_stats["blocked"],
            "failed": node_stats["failed"], "orphan": node_stats["orphan"],
            "msprog": int(ms_stats["avg_progress"]),
            "bp": _dumps(dict(plan["budget_planned"]) if plan["budget_planned"] else {}),
            "ba": _dumps(budget_actual_dict),
            "bc": _dumps(blocked_chains),
            "sn": _dumps([_row_to_dict(s) for s in spinning]),
            "gr": guerrilla_ratio,
            "sd": _dumps(snapshot_data),
        },
    ).mappings().first()

    # 记录事件
    db.execute(
        text("""
            INSERT INTO campaign_events (organization_id, plan_id, event_type, detail)
            VALUES (:org, :pid, 'snapshot_taken', CAST(:detail AS jsonb))
        """),
        {
            "org": org_id, "pid": plan_id,
            "detail": _dumps({"snapshot_id": row["id"], **snapshot_data}),
        },
    )

    # 如果有阻塞链或打转节点，记录告警事件
    if blocked_chains:
        db.execute(
            text("""
                INSERT INTO campaign_events (organization_id, plan_id, event_type, detail)
                VALUES (:org, :pid, 'block_detected', CAST(:detail AS jsonb))
            """),
            {
                "org": org_id, "pid": plan_id,
                "detail": _dumps({"blocked_chains": blocked_chains}),
            },
        )
    if spinning:
        db.execute(
            text("""
                INSERT INTO campaign_events (organization_id, plan_id, event_type, detail)
                VALUES (:org, :pid, 'spinning_detected', CAST(:detail AS jsonb))
            """),
            {
                "org": org_id, "pid": plan_id,
                "detail": _dumps({"spinning_nodes": [_row_to_dict(s) for s in spinning]}),
            },
        )

    db.commit()
    return {"snapshot_id": row["id"], **snapshot_data}


def _detect_blocked_chains(db: Session, plan_id: int) -> list[dict]:
    """检测阻塞链：上游节点 failed/blocked 导致下游 pending 空等。"""
    # 获取所有 blocked/failed 节点
    blocked_nodes = db.execute(
        text("""
            SELECT id, name, status FROM campaign_nodes
            WHERE plan_id = :pid AND status IN ('blocked', 'failed')
        """),
        {"pid": plan_id},
    ).mappings().all()

    chains = []
    for bn in blocked_nodes:
        # 查找依赖此节点的下游节点
        downstream = db.execute(
            text("""
                SELECT id, name, status FROM campaign_nodes
                WHERE plan_id = :pid AND :upstream_id = ANY(dependencies)
            """),
            {"pid": plan_id, "upstream_id": bn["id"]},
        ).mappings().all()

        pending_downstream = [d for d in downstream if d["status"] == "pending"]
        if pending_downstream:
            chains.append({
                "upstream_id": bn["id"],
                "upstream_name": bn["name"],
                "upstream_status": bn["status"],
                "blocked_downstream": [{"id": d["id"], "name": d["name"]} for d in pending_downstream],
            })

    return chains


@router.get("/plans/{plan_id}/snapshots", dependencies=[Depends(require_auth)])
def list_snapshots(
    plan_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT s.* FROM campaign_snapshots s
            JOIN campaign_plans p ON s.plan_id = p.id
            WHERE p.id = :pid AND p.organization_id = :org
            ORDER BY s.snapshot_at DESC LIMIT :limit
        """),
        {"pid": plan_id, "org": org_id, "limit": min(max(limit, 1), 100)},
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows]}


# ───────────────────────── Events ─────────────────────────

@router.get("/plans/{plan_id}/events", dependencies=[Depends(require_auth)])
def list_events(
    plan_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT e.* FROM campaign_events e
            JOIN campaign_plans p ON e.plan_id = p.id
            WHERE p.id = :pid AND p.organization_id = :org
            ORDER BY e.created_at DESC LIMIT :limit
        """),
        {"pid": plan_id, "org": org_id, "limit": min(max(limit, 1), 200)},
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows]}


# ───────────────────────── Controller Agent Output ─────────────────────────

class ControllerReport(BaseModel):
    """总管 agent 输出：结构化周报级「计划 vs 实际」偏差分析。"""
    plan_id: int
    snapshot_id: int | None = None
    period_start: str
    period_end: str
    # 偏差分析
    deviation_analysis: dict[str, Any]
    # 下波建议任务清单
    suggested_tasks: list[dict[str, Any]]
    # 需 owner 裁决项
    escalation_items: list[dict[str, Any]]
    # 游击系数
    guerrilla_ratio: float
    # 阻塞链
    blocked_chains: list[dict[str, Any]]
    # 打转节点
    spinning_nodes: list[dict[str, Any]]


@router.post("/plans/{plan_id}/controller-report", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def generate_controller_report(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """生成总管 agent 结构化报告（影子模式，不实际派单）。"""
    org_id = resolved_org(None, user, db)
    plan = db.execute(
        text("SELECT * FROM campaign_plans WHERE id = :pid AND organization_id = :org"),
        {"pid": plan_id, "org": org_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "plan not found")

    # 获取最新快照
    latest_snapshot = db.execute(
        text("""
            SELECT * FROM campaign_snapshots
            WHERE plan_id = :pid ORDER BY snapshot_at DESC LIMIT 1
        """),
        {"pid": plan_id},
    ).mappings().first()

    # 获取近期事件流
    recent_events = db.execute(
        text("""
            SELECT * FROM campaign_events
            WHERE plan_id = :pid AND created_at > now() - INTERVAL '7 days'
            ORDER BY created_at DESC LIMIT 100
        """),
        {"pid": plan_id},
    ).mappings().all()

    # 获取节点详情
    nodes = db.execute(
        text("""
            SELECT * FROM campaign_nodes WHERE plan_id = :pid ORDER BY id
        """),
        {"pid": plan_id},
    ).mappings().all()

    # 获取里程碑详情
    milestones = db.execute(
        text("""
            SELECT * FROM campaign_milestones WHERE plan_id = :pid ORDER BY id
        """),
        {"pid": plan_id},
    ).mappings().all()

    # 构建偏差分析
    deviation = _build_deviation_analysis(plan, latest_snapshot, nodes, milestones)

    # 构建建议任务清单
    suggested = _build_suggested_tasks(nodes, milestones, latest_snapshot)

    # 构建裁决项
    escalations = _build_escalations(nodes, latest_snapshot, recent_events)

    snapshot_data = {}
    guerrilla = 0.0
    blocked = []
    spinning = []
    if latest_snapshot:
        sd = latest_snapshot.get("snapshot_data", {})
        if isinstance(sd, str):
            sd = json.loads(sd)
        snapshot_data = sd
        guerrilla = latest_snapshot.get("guerrilla_ratio", 0.0)
        bc = latest_snapshot.get("blocked_chains", [])
        if isinstance(bc, str):
            bc = json.loads(bc)
        blocked = bc
        sn = latest_snapshot.get("spinning_nodes", [])
        if isinstance(sn, str):
            sn = json.loads(sn)
        spinning = sn

    report = ControllerReport(
        plan_id=plan_id,
        snapshot_id=latest_snapshot["id"] if latest_snapshot else None,
        period_start=_iso(plan["planned_start"]) or _iso(plan["created_at"]),
        period_end=_iso(plan["planned_end"]) or datetime.now(timezone.utc).isoformat(),
        deviation_analysis=deviation,
        suggested_tasks=suggested,
        escalation_items=escalations,
        guerrilla_ratio=guerrilla,
        blocked_chains=blocked,
        spinning_nodes=spinning,
    )

    return report.model_dump()


def _build_deviation_analysis(plan, snapshot, nodes, milestones) -> dict:
    """构建计划 vs 实际偏差分析。"""
    total = len(nodes)
    done = sum(1 for n in nodes if n["status"] == "done")
    in_flight = sum(1 for n in nodes if n["status"] == "in-flight")
    blocked = sum(1 for n in nodes if n["status"] == "blocked")
    failed = sum(1 for n in nodes if n["status"] == "failed")

    planned_progress = 0
    if plan["planned_start"] and plan["planned_end"]:
        total_duration = (plan["planned_end"] - plan["planned_start"]).total_seconds()
        if total_duration > 0:
            elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - plan["planned_start"].replace(tzinfo=None)).total_seconds()
            planned_progress = min(100, max(0, int(elapsed / total_duration * 100)))

    actual_progress = int(done / total * 100) if total > 0 else 0

    return {
        "total_nodes": total,
        "done_nodes": done,
        "in_flight_nodes": in_flight,
        "blocked_nodes": blocked,
        "failed_nodes": failed,
        "planned_progress_pct": planned_progress,
        "actual_progress_pct": actual_progress,
        "progress_deviation": actual_progress - planned_progress,
        "milestone_count": len(milestones),
        "milestone_done": sum(1 for m in milestones if m["status"] == "done"),
    }


def _build_suggested_tasks(nodes, milestones, snapshot) -> list[dict]:
    """构建下波建议任务清单。"""
    tasks = []
    # 找出 pending 且依赖已满足的节点
    done_node_ids = {n["id"] for n in nodes if n["status"] == "done"}
    for n in nodes:
        if n["status"] != "pending":
            continue
        deps = n.get("dependencies") or []
        if all(d in done_node_ids for d in deps):
            tasks.append({
                "node_id": n["id"],
                "name": n["name"],
                "node_type": n["node_type"],
                "milestone_id": n["milestone_id"],
                "priority": "high" if not deps else "normal",
                "reason": "dependencies satisfied",
            })
    return tasks


def _build_escalations(nodes, snapshot, events) -> list[dict]:
    """构建需 owner 裁决项。"""
    items = []
    # 连败 ≥2 的节点
    for n in nodes:
        if n.get("consecutive_fails", 0) >= 2:
            items.append({
                "type": "spinning_node",
                "node_id": n["id"],
                "node_name": n["name"],
                "consecutive_fails": n["consecutive_fails"],
                "recommendation": "escalate to root cause analysis",
            })

    # 阻塞链
    if snapshot:
        bc = snapshot.get("blocked_chains", [])
        if isinstance(bc, str):
            bc = json.loads(bc)
        for chain in bc:
            items.append({
                "type": "blocked_chain",
                "upstream_id": chain.get("upstream_id"),
                "upstream_name": chain.get("upstream_name"),
                "blocked_downstream": chain.get("blocked_downstream", []),
                "recommendation": "resolve upstream blocker or re-plan downstream",
            })

    return items
