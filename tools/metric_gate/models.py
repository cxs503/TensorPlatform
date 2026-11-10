"""Metric snapshot persistence layer.

SQLite-backed store for metric snapshots with evidence counts.
Used by the gate (write-time) and sentinel (read-time analysis).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    select,
)
from sqlalchemy.engine import Engine


@dataclass
class MetricSnapshotRow:
    """A persisted metric snapshot."""
    id: int | None
    metric_name: str
    value: float
    evidence_count: int
    recompute_expr: str | None
    recomputed_value: float | None
    gate_diff: float | None
    gate_accepted: bool
    metadata_json: str
    created_at: datetime

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "metric_name": self.metric_name,
            "value": self.value,
            "evidence_count": self.evidence_count,
            "recompute_expr": self.recompute_expr,
            "recomputed_value": self.recomputed_value,
            "gate_diff": self.gate_diff,
            "gate_accepted": self.gate_accepted,
            "metadata": json.loads(self.metadata_json) if self.metadata_json else {},
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }


def _make_table(table_name: str = "metric_snapshots") -> tuple[MetaData, Table]:
    md = MetaData()
    tbl = Table(
        table_name,
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("metric_name", String(256), nullable=False, index=True),
        Column("value", Float, nullable=False),
        Column("evidence_count", Integer, nullable=False, default=0),
        Column("recompute_expr", Text, nullable=True),
        Column("recomputed_value", Float, nullable=True),
        Column("gate_diff", Float, nullable=True),
        Column("gate_accepted", Integer, nullable=False, default=1),  # 0/1 for SQLite
        Column("metadata_json", Text, nullable=True, default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    return md, tbl


class MetricSnapshotStore:
    """SQLite-backed metric snapshot store."""

    def __init__(self, engine: Engine, table_name: str = "metric_snapshots"):
        self.engine = engine
        self.md, self.table = _make_table(table_name)
        self.md.create_all(engine, checkfirst=True)

    @classmethod
    def from_url(cls, url: str = "sqlite:///metric_snapshots.db", **kwargs) -> "MetricSnapshotStore":
        engine = create_engine(url, echo=False)
        return cls(engine, **kwargs)

    def insert(
        self,
        metric_name: str,
        value: float,
        evidence_count: int = 0,
        recompute_expr: str | None = None,
        recomputed_value: float | None = None,
        gate_diff: float | None = None,
        gate_accepted: bool = True,
        metadata: Dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> MetricSnapshotRow:
        """Insert a metric snapshot."""
        now = timestamp or datetime.now(timezone.utc)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self.engine.begin() as conn:
            result = conn.execute(
                self.table.insert().values(
                    metric_name=metric_name,
                    value=value,
                    evidence_count=evidence_count,
                    recompute_expr=recompute_expr,
                    recomputed_value=recomputed_value,
                    gate_diff=gate_diff,
                    gate_accepted=1 if gate_accepted else 0,
                    metadata_json=meta_json,
                    created_at=now,
                )
            )
            row_id = result.inserted_primary_key[0]

        return self.get_by_id(row_id)

    def get_by_id(self, row_id: int) -> MetricSnapshotRow:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(self.table).where(self.table.c.id == row_id)
            ).mappings().first()
        return self._row_to_snapshot(row) if row else None

    def get_recent(
        self,
        metric_name: str,
        limit: int = 100,
    ) -> List[MetricSnapshotRow]:
        """Get recent snapshots for a metric, newest first."""
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(self.table)
                .where(self.table.c.metric_name == metric_name)
                .order_by(desc(self.table.c.created_at))
                .limit(limit)
            ).mappings().all()
        return [self._row_to_snapshot(r) for r in rows]

    def get_all(self, metric_name: str | None = None) -> List[MetricSnapshotRow]:
        """Get all snapshots, optionally filtered by metric name."""
        with self.engine.begin() as conn:
            stmt = select(self.table)
            if metric_name:
                stmt = stmt.where(self.table.c.metric_name == metric_name)
            stmt = stmt.order_by(self.table.c.created_at)
            rows = conn.execute(stmt).mappings().all()
        return [self._row_to_snapshot(r) for r in rows]

    def count(self, metric_name: str | None = None) -> int:
        with self.engine.begin() as conn:
            stmt = select(self.table)
            if metric_name:
                stmt = stmt.where(self.table.c.metric_name == metric_name)
            from sqlalchemy import func
            result = conn.execute(
                select(func.count()).select_from(stmt.subquery())
            ).scalar()
        return result or 0

    def _row_to_snapshot(self, row) -> MetricSnapshotRow:
        r = dict(row) if not isinstance(row, dict) else row
        return MetricSnapshotRow(
            id=r.get("id"),
            metric_name=r["metric_name"],
            value=r["value"],
            evidence_count=r["evidence_count"],
            recompute_expr=r.get("recompute_expr"),
            recomputed_value=r.get("recomputed_value"),
            gate_diff=r.get("gate_diff"),
            gate_accepted=bool(r.get("gate_accepted", 1)),
            metadata_json=r.get("metadata_json", "{}"),
            created_at=r["created_at"],
        )
