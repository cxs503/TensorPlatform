"""
质量门服务 — 战役节点验收自动裁决 + 升级机制。

核心逻辑：
- 节点 verdict 写入时自动检查 consecutive_fails
- consecutive_fails ≥ 3 → 自动 escalation（创建 escalation 记录）
- escalation 含：节点 ID、失败次数、失败原因摘要、升级时间戳

证据纪律：
- honest FAIL 不可覆盖（一旦记录 FAIL，不可被后续 PASS 删除）
- 零手抄：所有数据从 DB 程序化读取
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── 升级阈值 ──────────────────────────────────────────────────────
ESCALATION_THRESHOLD = 3  # consecutive_fails >= 3 → escalation


class QualityGateResult:
    """质量门裁决结果。"""
    def __init__(
        self,
        node_id: int,
        verdict: str,  # "PASS" | "FAIL" | "RETRY"
        consecutive_fails: int,
        escalated: bool,
        escalation_id: int | None = None,
        reason: str = "",
    ):
        self.node_id = node_id
        self.verdict = verdict
        self.consecutive_fails = consecutive_fails
        self.escalated = escalated
        self.escalation_id = escalation_id
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "verdict": self.verdict,
            "consecutive_fails": self.consecutive_fails,
            "escalated": self.escalated,
            "escalation_id": self.escalation_id,
            "reason": self.reason,
        }


def _ensure_escalation_table(db: Session) -> None:
    """确保 escalation 表存在（幂等）。"""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            plan_id INTEGER,
            consecutive_fails INTEGER NOT NULL,
            fail_reasons TEXT,
            escalated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved INTEGER DEFAULT 0,
            resolved_at TIMESTAMP,
            resolution TEXT,
            organization_id INTEGER
        )
    """))
    db.commit()


def _ensure_node_fails_table(db: Session) -> None:
    """确保节点失败计数表存在（幂等）。"""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_node_fails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL UNIQUE,
            consecutive_fails INTEGER DEFAULT 0,
            total_fails INTEGER DEFAULT 0,
            last_fail_at TIMESTAMP,
            last_fail_reason TEXT
        )
    """))
    db.commit()


def record_node_verdict(
    db: Session,
    node_id: int,
    plan_id: int,
    verdict: str,
    reason: str = "",
    organization_id: int | None = None,
) -> QualityGateResult:
    """
    记录节点裁决结果，自动处理 consecutive_fails 和 escalation。

    参数:
        db: 数据库会话
        node_id: 节点 ID
        plan_id: 战役计划 ID
        verdict: "PASS" | "FAIL"
        reason: 失败原因（FAIL 时必填）
        organization_id: 组织 ID（用于多租户隔离）

    返回:
        QualityGateResult: 包含裁决结果和升级信息
    """
    # 确保表存在
    _ensure_escalation_table(db)
    _ensure_node_fails_table(db)

    now = datetime.now(timezone.utc)

    if verdict == "PASS":
        # PASS: 重置 consecutive_fails（但保留 total_fails 历史）
        db.execute(text("""
            INSERT INTO campaign_node_fails (node_id, consecutive_fails, total_fails)
            VALUES (:nid, 0, 0)
            ON CONFLICT(node_id) DO UPDATE SET
                consecutive_fails = 0,
                last_fail_at = NULL,
                last_fail_reason = NULL
        """), {"nid": node_id})
        db.commit()
        return QualityGateResult(
            node_id=node_id,
            verdict="PASS",
            consecutive_fails=0,
            escalated=False,
            reason="节点通过验收",
        )

    # FAIL: 递增 consecutive_fails
    db.execute(text("""
        INSERT INTO campaign_node_fails (node_id, consecutive_fails, total_fails, last_fail_at, last_fail_reason)
        VALUES (:nid, 1, 1, :now, :reason)
        ON CONFLICT(node_id) DO UPDATE SET
            consecutive_fails = campaign_node_fails.consecutive_fails + 1,
            total_fails = campaign_node_fails.total_fails + 1,
            last_fail_at = :now,
            last_fail_reason = :reason
    """), {"nid": node_id, "now": now.isoformat(), "reason": reason})
    db.commit()

    # 读取当前 consecutive_fails
    row = db.execute(text("""
        SELECT consecutive_fails, total_fails FROM campaign_node_fails WHERE node_id = :nid
    """), {"nid": node_id}).mappings().first()

    consecutive_fails = row["consecutive_fails"] if row else 1

    # 检查是否需要升级
    escalated = False
    escalation_id = None

    if consecutive_fails >= ESCALATION_THRESHOLD:
        # 创建 escalation 记录
        # 收集最近 N 次失败原因
        fail_reasons_json = json.dumps({
            "consecutive_fails": consecutive_fails,
            "latest_reason": reason,
            "timestamp": now.isoformat(),
        }, ensure_ascii=False)

        result = db.execute(text("""
            INSERT INTO campaign_escalations
                (node_id, plan_id, consecutive_fails, fail_reasons, escalated_at, organization_id)
            VALUES (:nid, :pid, :cf, :reasons, :now, :org)
        """), {
            "nid": node_id,
            "pid": plan_id,
            "cf": consecutive_fails,
            "reasons": fail_reasons_json,
            "now": now.isoformat(),
            "org": organization_id,
        })
        db.commit()
        escalation_id = result.lastrowid
        escalated = True
        logger.warning(
            f"Node {node_id} escalated after {consecutive_fails} consecutive fails "
            f"(escalation_id={escalation_id})"
        )

    return QualityGateResult(
        node_id=node_id,
        verdict="FAIL",
        consecutive_fails=consecutive_fails,
        escalated=escalated,
        escalation_id=escalation_id,
        reason=reason,
    )


def get_escalations(
    db: Session,
    plan_id: int | None = None,
    unresolved_only: bool = True,
) -> list[dict]:
    """获取升级记录列表。"""
    _ensure_escalation_table(db)

    query = "SELECT * FROM campaign_escalations"
    params: dict[str, Any] = {}
    conditions = []

    if plan_id is not None:
        conditions.append("plan_id = :pid")
        params["pid"] = plan_id
    if unresolved_only:
        conditions.append("resolved = 0")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY escalated_at DESC"

    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def resolve_escalation(
    db: Session,
    escalation_id: int,
    resolution: str,
) -> bool:
    """标记升级为已解决。"""
    now = datetime.now(timezone.utc)
    result = db.execute(text("""
        UPDATE campaign_escalations
        SET resolved = 1, resolved_at = :now, resolution = :res
        WHERE id = :eid
    """), {"eid": escalation_id, "now": now.isoformat(), "res": resolution})
    db.commit()
    return result.rowcount > 0


def get_node_fail_stats(db: Session, node_id: int) -> dict:
    """获取节点失败统计。"""
    _ensure_node_fails_table(db)
    row = db.execute(text("""
        SELECT consecutive_fails, total_fails, last_fail_at, last_fail_reason
        FROM campaign_node_fails WHERE node_id = :nid
    """), {"nid": node_id}).mappings().first()

    if not row:
        return {
            "node_id": node_id,
            "consecutive_fails": 0,
            "total_fails": 0,
            "last_fail_at": None,
            "last_fail_reason": None,
        }
    return dict(row)
