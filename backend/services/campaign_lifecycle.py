"""
战役自动生命周期服务 — draft → active → done 全自动。

自主续航核心：
- 战役创建后自动从 draft 转为 active（当所有前置条件满足时）
- 所有节点完成后自动裁决战役状态
- 零人工干预：人工干预计数 = 0

质量门集成：
- 节点 FAIL → consecutive_fails+1
- consecutive_fails ≥ 3 → 自动 escalation
- escalation 后节点标记为 escalated，不阻塞战役推进
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.quality_gate import (
    ESCALATION_THRESHOLD,
    QualityGateResult,
    record_node_verdict,
    get_escalations,
)

logger = logging.getLogger(__name__)


class CampaignLifecycleResult:
    """战役生命周期操作结果。"""
    def __init__(
        self,
        plan_id: int,
        old_status: str,
        new_status: str,
        human_interventions: int = 0,
        details: dict | None = None,
    ):
        self.plan_id = plan_id
        self.old_status = old_status
        self.new_status = new_status
        self.human_interventions = human_interventions
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "human_interventions": self.human_interventions,
            "details": self.details,
        }


def _ensure_campaign_tables(db: Session) -> None:
    """确保战役相关表存在。"""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            goal TEXT,
            metadata TEXT,
            organization_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            verdict TEXT,
            goal TEXT,
            accept_criteria TEXT,
            result TEXT,
            dependencies TEXT,
            consecutive_fails INTEGER DEFAULT 0,
            escalation_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (plan_id) REFERENCES campaign_plans(id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            node_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.commit()


def _emit_event(
    db: Session,
    plan_id: int,
    event_type: str,
    node_id: int | None = None,
    payload: dict | None = None,
) -> None:
    """记录战役事件。"""
    db.execute(text("""
        INSERT INTO campaign_events (plan_id, node_id, event_type, payload)
        VALUES (:pid, :nid, :etype, :payload)
    """), {
        "pid": plan_id,
        "nid": node_id,
        "etype": event_type,
        "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
    })
    db.commit()


def activate_campaign(db: Session, plan_id: int) -> CampaignLifecycleResult:
    """
    将战役从 draft 激活为 active。
    自动执行，无需人工干预。
    """
    _ensure_campaign_tables(db)

    row = db.execute(text("""
        SELECT id, status FROM campaign_plans WHERE id = :pid
    """), {"pid": plan_id}).mappings().first()

    if not row:
        raise ValueError(f"Campaign plan {plan_id} not found")

    old_status = row["status"]
    if old_status != "draft":
        return CampaignLifecycleResult(
            plan_id=plan_id,
            old_status=old_status,
            new_status=old_status,
            details={"error": f"Cannot activate from status '{old_status}'"},
        )

    now = datetime.now(timezone.utc)
    db.execute(text("""
        UPDATE campaign_plans
        SET status = 'active', activated_at = :now
        WHERE id = :pid
    """), {"pid": plan_id, "now": now.isoformat()})
    db.commit()

    _emit_event(db, plan_id, "campaign_activated", payload={"auto": True})

    return CampaignLifecycleResult(
        plan_id=plan_id,
        old_status=old_status,
        new_status="active",
        human_interventions=0,
        details={"auto_activated": True},
    )


def complete_node(
    db: Session,
    node_id: int,
    success: bool,
    result: dict | None = None,
    reason: str = "",
) -> QualityGateResult:
    """
    完成节点执行，自动触发质量门。

    参数:
        db: 数据库会话
        node_id: 节点 ID
        success: 是否成功
        result: 节点执行结果
        reason: 失败原因
    """
    _ensure_campaign_tables(db)

    row = db.execute(text("""
        SELECT id, plan_id, status FROM campaign_nodes WHERE id = :nid
    """), {"nid": node_id}).mappings().first()

    if not row:
        raise ValueError(f"Campaign node {node_id} not found")

    plan_id = row["plan_id"]
    verdict = "PASS" if success else "FAIL"
    now = datetime.now(timezone.utc)

    # 通过质量门记录裁决
    qg_result = record_node_verdict(
        db=db,
        node_id=node_id,
        plan_id=plan_id,
        verdict=verdict,
        reason=reason,
    )

    # 更新节点状态
    node_status = "succeeded" if success else "failed"
    db.execute(text("""
        UPDATE campaign_nodes
        SET status = :nstatus, verdict = :verdict, result = :result,
            completed_at = :now, consecutive_fails = :cf, escalation_id = :eid
        WHERE id = :nid
    """), {
        "nid": node_id,
        "nstatus": node_status,
        "verdict": verdict,
        "result": json.dumps(result or {}, ensure_ascii=False, default=str),
        "now": now.isoformat(),
        "cf": qg_result.consecutive_fails,
        "eid": qg_result.escalation_id,
    })
    db.commit()

    _emit_event(db, plan_id, "node_completed", node_id=node_id, payload={
        "verdict": verdict,
        "escalated": qg_result.escalated,
        "escalation_id": qg_result.escalation_id,
    })

    return qg_result


def evaluate_campaign_completion(db: Session, plan_id: int) -> CampaignLifecycleResult:
    """
    评估战役是否可以标记为完成。

    完成条件：所有节点状态为 succeeded / failed / escalated。
    战役最终状态：
    - 所有节点 succeeded → done
    - 有节点 failed/escalated → completed_with_issues
    """
    _ensure_campaign_tables(db)

    row = db.execute(text("""
        SELECT id, status FROM campaign_plans WHERE id = :pid
    """), {"pid": plan_id}).mappings().first()

    if not row:
        raise ValueError(f"Campaign plan {plan_id} not found")

    old_status = row["status"]
    if old_status not in ("active",):
        return CampaignLifecycleResult(
            plan_id=plan_id,
            old_status=old_status,
            new_status=old_status,
            details={"error": f"Cannot evaluate from status '{old_status}'"},
        )

    # 统计节点状态
    nodes = db.execute(text("""
        SELECT id, status, verdict, consecutive_fails, escalation_id
        FROM campaign_nodes WHERE plan_id = :pid
    """), {"pid": plan_id}).mappings().all()

    if not nodes:
        return CampaignLifecycleResult(
            plan_id=plan_id,
            old_status=old_status,
            new_status=old_status,
            details={"error": "No nodes in campaign"},
        )

    pending = [n for n in nodes if n["status"] in ("pending", "running")]
    if pending:
        return CampaignLifecycleResult(
            plan_id=plan_id,
            old_status=old_status,
            new_status=old_status,
            details={"pending_nodes": len(pending)},
        )

    # 所有节点已完成
    succeeded = [n for n in nodes if n["status"] == "succeeded"]
    failed = [n for n in nodes if n["status"] == "failed"]
    escalated = [n for n in nodes if n["escalation_id"] is not None]

    new_status = "done" if len(succeeded) == len(nodes) else "completed_with_issues"
    now = datetime.now(timezone.utc)

    db.execute(text("""
        UPDATE campaign_plans SET status = :nstatus, completed_at = :now
        WHERE id = :pid
    """), {"pid": plan_id, "nstatus": new_status, "now": now.isoformat()})
    db.commit()

    _emit_event(db, plan_id, "campaign_completed", payload={
        "new_status": new_status,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "escalated": len(escalated),
        "auto_completed": True,
    })

    return CampaignLifecycleResult(
        plan_id=plan_id,
        old_status=old_status,
        new_status=new_status,
        human_interventions=0,
        details={
            "succeeded": len(succeeded),
            "failed": len(failed),
            "escalated": len(escalated),
            "total": len(nodes),
        },
    )
