"""否定性知识台账存储层。

表结构 ``negative_knowledge_ledger``
-----------------------------------
* ``id``                  自增主键
* ``entry_id``            业务唯一 ID（UUID）
* ``mission_id``          来源 mission（可空：手工登记）
* ``plan_id``             来源战役计划（可空）
* ``node_id``             来源 DAG 节点（可空）
* ``title``               假设/失败标题（用于检索）
* ``hypothesis``          被证伪的假设描述
* ``falsification_evidence``  证伪证据（数字/工件路径/日志摘要）
* ``failure_reason``      失败根因
* ``failure_kind``        失败类型（hypothesis_falsified/method_failed/blocked/infeasible/regression）
* ``applicable_conditions``   适用条件边界（JSON：在什么条件下此结论成立）
* ``keywords``            关键词（JSON 数组，用于检索）
* ``domain_tags``         域标签（JSON 数组，零领域语义——由配置注入）
* ``differentiation_required``  后续提案需声明的差异点（JSON 数组）
* ``source_artifacts``    来源工件路径（JSON 数组）
* ``confidence``          证伪置信度 [0,1]
* ``schema_version``      schema 版本
* ``registered_by``       登记者标识
* ``registered_at``       登记时间
* ``updated_at``          更新时间
* ``status``              状态（active/superseded/retracted）

幂等：唯一键 ``(entry_id)``；同一 mission+node 重复登记不新增行。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    func,
    select,
    or_,
)
from sqlalchemy.engine import Engine

from .config import NegativeKnowledgeConfig

FAILURE_KINDS = ("hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression")
STATUSES = ("active", "superseded", "retracted")


def _metadata(cfg: NegativeKnowledgeConfig) -> tuple[MetaData, Table]:
    md = MetaData()
    table = Table(
        cfg.table_name,
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("entry_id", String(64), nullable=False, unique=True),
        Column("mission_id", BigInteger, nullable=True),
        Column("plan_id", BigInteger, nullable=True),
        Column("node_id", String(128), nullable=True),
        Column("title", String(500), nullable=False),
        Column("hypothesis", Text, nullable=False),
        Column("falsification_evidence", Text, nullable=True),
        Column("failure_reason", Text, nullable=True),
        Column("failure_kind", String(64), nullable=False),
        Column("applicable_conditions", Text, nullable=True, server_default="{}"),
        Column("keywords", Text, nullable=True, server_default="[]"),
        Column("domain_tags", Text, nullable=True, server_default="[]"),
        Column("differentiation_required", Text, nullable=True, server_default="[]"),
        Column("source_artifacts", Text, nullable=True, server_default="[]"),
        Column("confidence", Float, nullable=False, server_default="0.8"),
        Column("schema_version", String(16), nullable=False, server_default="1.0"),
        Column("registered_by", String(128), nullable=True),
        Column("registered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("status", String(24), nullable=False, server_default="active"),
        UniqueConstraint("mission_id", "node_id", name=f"uq_{cfg.table_name}_mission_node"),
    )
    return md, table


@dataclass
class NegativeKnowledgeEntry:
    """否定性知识条目（业务对象）。"""

    entry_id: str
    mission_id: int | None
    plan_id: int | None
    node_id: str | None
    title: str
    hypothesis: str
    falsification_evidence: str | None
    failure_reason: str | None
    failure_kind: str
    applicable_conditions: dict[str, Any]
    keywords: list[str]
    domain_tags: list[str]
    differentiation_required: list[str]
    source_artifacts: list[str]
    confidence: float
    schema_version: str
    registered_by: str | None
    registered_at: datetime | None
    updated_at: datetime | None
    status: str
    id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "falsification_evidence": self.falsification_evidence,
            "failure_reason": self.failure_reason,
            "failure_kind": self.failure_kind,
            "applicable_conditions": self.applicable_conditions,
            "keywords": self.keywords,
            "domain_tags": self.domain_tags,
            "differentiation_required": self.differentiation_required,
            "source_artifacts": self.source_artifacts,
            "confidence": self.confidence,
            "schema_version": self.schema_version,
            "registered_by": self.registered_by,
            "registered_at": self.registered_at.isoformat() if isinstance(self.registered_at, datetime) else self.registered_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "status": self.status,
        }

    def search_text(self) -> str:
        """拼接用于检索的规范文本（标题+假设+关键词+失败原因）。"""
        parts = [self.title, self.hypothesis]
        if self.failure_reason:
            parts.append(self.failure_reason)
        if self.keywords:
            parts.append(" ".join(self.keywords))
        return "\n".join(p for p in parts if p)


def _parse_json_field(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
    return raw


def _row_to_entry(row: Any) -> NegativeKnowledgeEntry:
    r = dict(row) if not isinstance(row, dict) else row
    return NegativeKnowledgeEntry(
        id=r.get("id"),
        entry_id=str(r["entry_id"]),
        mission_id=r.get("mission_id"),
        plan_id=r.get("plan_id"),
        node_id=r.get("node_id"),
        title=str(r["title"]),
        hypothesis=str(r["hypothesis"]),
        falsification_evidence=r.get("falsification_evidence"),
        failure_reason=r.get("failure_reason"),
        failure_kind=str(r["failure_kind"]),
        applicable_conditions=_parse_json_field(r.get("applicable_conditions"), {}),
        keywords=_parse_json_field(r.get("keywords"), []),
        domain_tags=_parse_json_field(r.get("domain_tags"), []),
        differentiation_required=_parse_json_field(r.get("differentiation_required"), []),
        source_artifacts=_parse_json_field(r.get("source_artifacts"), []),
        confidence=float(r.get("confidence", 0.8)),
        schema_version=str(r.get("schema_version", "1.0")),
        registered_by=r.get("registered_by"),
        registered_at=r.get("registered_at"),
        updated_at=r.get("updated_at"),
        status=str(r.get("status", "active")),
    )


class NegativeKnowledgeStore:
    """否定性知识台账 I/O（唯一写入口）。"""

    def __init__(self, engine: Engine, cfg: NegativeKnowledgeConfig) -> None:
        self.cfg = cfg
        self.engine = engine
        self.md, self.table = _metadata(cfg)

    @classmethod
    def for_engine(cls, engine: Engine, cfg: NegativeKnowledgeConfig) -> "NegativeKnowledgeStore":
        store = cls(engine, cfg)
        store.ensure_schema()
        return store

    def ensure_schema(self) -> None:
        """幂等建表。"""
        self.md.create_all(self.engine, checkfirst=True)

    def dispose(self) -> None:
        self.engine.dispose()

    # ── 写 ──────────────────────────────────────────────────────────────

    def register(
        self,
        *,
        title: str,
        hypothesis: str,
        failure_kind: str,
        mission_id: int | None = None,
        plan_id: int | None = None,
        node_id: str | None = None,
        falsification_evidence: str | None = None,
        failure_reason: str | None = None,
        applicable_conditions: dict[str, Any] | None = None,
        keywords: list[str] | None = None,
        domain_tags: list[str] | None = None,
        differentiation_required: list[str] | None = None,
        source_artifacts: list[str] | None = None,
        confidence: float = 0.8,
        registered_by: str | None = None,
    ) -> tuple[NegativeKnowledgeEntry, bool]:
        """登记一条否定性知识。返回 ``(entry, created)``。

        幂等：同一 (mission_id, node_id) 不重复登记。
        """
        if failure_kind not in FAILURE_KINDS:
            raise ValueError(f"未登记的 failure_kind：{failure_kind}")

        # 幂等检查
        if mission_id is not None and node_id is not None:
            existing = self.find_by_mission_node(mission_id, node_id)
            if existing is not None:
                return existing, False

        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        payload = {
            "entry_id": entry_id,
            "mission_id": mission_id,
            "plan_id": plan_id,
            "node_id": node_id,
            "title": title[:500],
            "hypothesis": hypothesis,
            "falsification_evidence": falsification_evidence,
            "failure_reason": failure_reason,
            "failure_kind": failure_kind,
            "applicable_conditions": json.dumps(applicable_conditions or {}, ensure_ascii=False),
            "keywords": json.dumps(keywords or [], ensure_ascii=False),
            "domain_tags": json.dumps(domain_tags or [], ensure_ascii=False),
            "differentiation_required": json.dumps(differentiation_required or [], ensure_ascii=False),
            "source_artifacts": json.dumps(source_artifacts or [], ensure_ascii=False),
            "confidence": min(max(confidence, 0.0), 1.0),
            "schema_version": self.cfg.schema_version,
            "registered_by": registered_by,
            "registered_at": now,
            "updated_at": now,
            "status": "active",
        }

        with self.engine.begin() as conn:
            conn.execute(self.table.insert().values(**payload))

        entry = self.find_by_entry_id(entry_id)
        assert entry is not None
        return entry, True

    def update_status(self, entry_id: str, status: str) -> NegativeKnowledgeEntry:
        """更新条目状态（superseded/retracted）。"""
        if status not in STATUSES:
            raise ValueError(f"非法状态：{status}")
        with self.engine.begin() as conn:
            conn.execute(
                self.table.update()
                .where(self.table.c.entry_id == entry_id)
                .values(status=status, updated_at=datetime.now(timezone.utc))
            )
        entry = self.find_by_entry_id(entry_id)
        if entry is None:
            raise KeyError(f"entry_id 不存在：{entry_id}")
        return entry

    # ── 读 ──────────────────────────────────────────────────────────────

    def find_by_entry_id(self, entry_id: str) -> NegativeKnowledgeEntry | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(self.table).where(self.table.c.entry_id == entry_id)
            ).mappings().first()
        return _row_to_entry(row) if row else None

    def find_by_mission_node(self, mission_id: int, node_id: str) -> NegativeKnowledgeEntry | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(self.table).where(
                    and_(
                        self.table.c.mission_id == mission_id,
                        self.table.c.node_id == node_id,
                    )
                )
            ).mappings().first()
        return _row_to_entry(row) if row else None

    def list_entries(
        self,
        *,
        status: str | None = None,
        failure_kind: str | None = None,
        mission_id: int | None = None,
        limit: int = 200,
    ) -> list[NegativeKnowledgeEntry]:
        stmt = select(self.table).order_by(self.table.c.registered_at.desc())
        if status:
            stmt = stmt.where(self.table.c.status == status)
        if failure_kind:
            stmt = stmt.where(self.table.c.failure_kind == failure_kind)
        if mission_id is not None:
            stmt = stmt.where(self.table.c.mission_id == mission_id)
        stmt = stmt.limit(min(int(limit), 1000))
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_entry(r) for r in rows]

    def count_by_kind(self) -> dict[str, int]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(self.table.c.failure_kind, func.count())
                .where(self.table.c.status == "active")
                .group_by(self.table.c.failure_kind)
            ).all()
        return {str(k): int(v) for k, v in rows}

    def count_by_status(self) -> dict[str, int]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(self.table.c.status, func.count()).group_by(self.table.c.status)
            ).all()
        return {str(k): int(v) for k, v in rows}

    def stats(self) -> dict[str, Any]:
        by_kind = self.count_by_kind()
        by_status = self.count_by_status()
        total = sum(by_status.values())
        return {
            "total": total,
            "by_failure_kind": by_kind,
            "by_status": by_status,
            "active_count": by_status.get("active", 0),
        }
