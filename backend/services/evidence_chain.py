"""
证据链服务 — 节点 result → tensor_reports → provenance 全链路可追溯。

证据纪律：
- honest FAIL 不可覆盖
- 零手抄：每个数字 SQL 溯源
- 报告 = 论文级六要素
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _ensure_provenance_table(db: Session) -> None:
    """确保 provenance 表存在。"""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS evidence_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER,
            plan_id INTEGER,
            mission_id INTEGER,
            report_id INTEGER,
            evidence_type TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_table TEXT,
            source_id INTEGER
        )
    """))
    db.commit()


def record_provenance(
    db: Session,
    evidence_type: str,
    payload: dict,
    node_id: int | None = None,
    plan_id: int | None = None,
    mission_id: int | None = None,
    report_id: int | None = None,
    source_table: str | None = None,
    source_id: int | None = None,
) -> int:
    """
    记录证据链条目。

    参数:
        evidence_type: 证据类型（node_result / report / benchmark / kpi）
        payload: 证据内容
        node_id: 关联节点
        plan_id: 关联战役
        mission_id: 关联任务
        report_id: 关联报告
        source_table: 源表名
        source_id: 源记录 ID

    返回:
        provenance_id: 新记录的 ID
    """
    _ensure_provenance_table(db)

    content_json = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

    result = db.execute(text("""
        INSERT INTO evidence_provenance
            (node_id, plan_id, mission_id, report_id, evidence_type, content_hash, payload, source_table, source_id)
        VALUES (:nid, :pid, :mid, :rid, :etype, :hash, :payload, :stable, :sid)
    """), {
        "nid": node_id,
        "pid": plan_id,
        "mid": mission_id,
        "rid": report_id,
        "etype": evidence_type,
        "hash": content_hash,
        "payload": content_json,
        "stable": source_table,
        "sid": source_id,
    })
    db.commit()
    return result.lastrowid


def trace_evidence_chain(
    db: Session,
    node_id: int | None = None,
    plan_id: int | None = None,
    mission_id: int | None = None,
    report_id: int | None = None,
) -> list[dict]:
    """
    追溯证据链。

    从任意节点出发，找到所有关联的证据记录。
    """
    _ensure_provenance_table(db)

    conditions = []
    params: dict[str, Any] = {}

    if node_id is not None:
        conditions.append("node_id = :nid")
        params["nid"] = node_id
    if plan_id is not None:
        conditions.append("plan_id = :pid")
        params["pid"] = plan_id
    if mission_id is not None:
        conditions.append("mission_id = :mid")
        params["mid"] = mission_id
    if report_id is not None:
        conditions.append("report_id = :rid")
        params["rid"] = report_id

    if not conditions:
        return []

    query = "SELECT * FROM evidence_provenance WHERE " + " OR ".join(conditions)
    query += " ORDER BY created_at ASC"

    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def verify_provenance_integrity(db: Session, provenance_id: int) -> dict:
    """
    验证证据完整性（hash 校验）。

    返回:
        {valid: bool, expected_hash: str, actual_hash: str}
    """
    _ensure_provenance_table(db)

    row = db.execute(text("""
        SELECT id, content_hash, payload FROM evidence_provenance WHERE id = :pid
    """), {"pid": provenance_id}).mappings().first()

    if not row:
        return {"valid": False, "error": "Provenance record not found"}

    payload = row["payload"]
    expected_hash = row["content_hash"]
    actual_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return {
        "provenance_id": provenance_id,
        "valid": expected_hash == actual_hash,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
    }


def get_evidence_for_node(db: Session, node_id: int) -> list[dict]:
    """获取节点的所有关联证据。"""
    return trace_evidence_chain(db, node_id=node_id)


def get_evidence_for_report(db: Session, report_id: int) -> list[dict]:
    """获取报告的所有关联证据。"""
    return trace_evidence_chain(db, report_id=report_id)
