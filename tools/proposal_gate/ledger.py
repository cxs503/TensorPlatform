"""提案门账本 ``proposal_gate_ledger``：四态可 SQL 溯源 + 幂等唯一键。

表结构（规范列）
----------------
``(mission_id, plan_id, sha256, schema_version, verdict, dup_of, score, at)``
外加审计超集列（不替代规范列，只补证据）：
``state``（四态之一）、``reasons``（JSON 字符串）、``source_path``、``updated_at``。

四态（配置 ``states`` 映射）
---------------------------
* ``proposal``      —— 通过契约与去重门的提案（进池，待仲裁/采纳）
* ``adopted``       —— 已被战役播种采纳
* ``dedup_rejected``—— 重复 / 孪生提案被拒（``dup_of`` 指向首次）
* ``quarantined``   —— 契约门失败（fail-closed），附隔离区路径与逐条理由

幂等
----
唯一键 ``(mission_id, sha256)``：同一 mission 内同内容重复提交**不新增行**，
返回既有行并把 verdict 记为 ``duplicate``——反作弊要求 (a) 的机器实现。
实现上用 SQLAlchemy Core，SQLite/PostgreSQL 双向可跑（无方言私有语法）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

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
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .config import GateConfig

_MAX_BLOB_CHARS = 20_000

STATES: tuple[str, ...] = ("proposal", "adopted", "dedup_rejected", "quarantined")
VERDICTS: tuple[str, ...] = ("accepted", "adopted", "duplicate", "near_duplicate", "twin", "quarantined")


def _metadata(cfg: GateConfig) -> tuple[MetaData, Table]:
    md = MetaData()
    table = Table(
        cfg.table_name,
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("mission_id", BigInteger, nullable=False),
        Column("plan_id", BigInteger, nullable=True),
        Column("sha256", String(64), nullable=False),
        Column("schema_version", String(16), nullable=False),
        Column("verdict", String(24), nullable=False),
        Column("state", String(24), nullable=False),
        Column("dup_of", String(64), nullable=True),
        Column("score", Float, nullable=True),
        Column("reasons", Text, nullable=False, server_default="[]"),
        Column("blob", Text, nullable=True),
        Column("source_path", Text, nullable=True),
        Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("mission_id", "sha256", name=f"uq_{cfg.table_name}_mission_sha"),
    )
    return md, table


@dataclass(frozen=True)
class LedgerRow:
    id: int | None
    mission_id: int
    plan_id: int | None
    sha256: str
    schema_version: str
    verdict: str
    state: str
    dup_of: str | None
    score: float | None
    reasons: tuple[str, ...]
    source_path: str | None
    at: datetime | None
    blob: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "verdict": self.verdict,
            "state": self.state,
            "dup_of": self.dup_of,
            "score": self.score,
            "reasons": list(self.reasons),
            "source_path": self.source_path,
            "at": self.at.isoformat() if isinstance(self.at, datetime) else self.at,
            "blob_len": len(self.blob) if self.blob else 0,
        }


@dataclass(frozen=True)
class GateStats:
    """四态计数 + 重复率 + KPI 暴露（metric_key 来自配置）。"""

    counts: dict[str, int]
    total: int
    dup_rate: float
    kpi: tuple[dict[str, Any], ...]
    verdict_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "verdict_counts": self.verdict_counts,
            "total": self.total,
            "dup_rate": round(self.dup_rate, 6),
            "near_dup_rate": round(
                (self.verdict_counts.get("near_duplicate", 0) / self.total) if self.total else 0.0,
                6,
            ),
            "kpi": list(self.kpi),
        }


def _row_to_dataclass(row: Mapping[str, Any]) -> LedgerRow:
    reasons_raw = row.get("reasons") or "[]"
    try:
        reasons = tuple(json.loads(reasons_raw)) if isinstance(reasons_raw, str) else tuple(reasons_raw)
    except json.JSONDecodeError:
        reasons = (str(reasons_raw),)
    return LedgerRow(
        id=row.get("id"),
        mission_id=int(row["mission_id"]),
        plan_id=row.get("plan_id"),
        sha256=str(row["sha256"]),
        schema_version=str(row["schema_version"]),
        verdict=str(row["verdict"]),
        state=str(row["state"]),
        dup_of=row.get("dup_of"),
        score=row.get("score"),
        reasons=tuple(str(r) for r in reasons),
        source_path=row.get("source_path"),
        at=row.get("at"),
        blob=row.get("blob"),
    )


class LedgerStore:
    """账本 I/O（唯一写入口；所有判定逻辑在 gate 层，本类不含业务判据）。"""

    def __init__(self, engine: Engine, cfg: GateConfig) -> None:
        self.cfg = cfg
        self.engine = engine
        self.md, self.table = _metadata(cfg)

    # ── 生命周期 ────────────────────────────────────────────────────────
    @classmethod
    def for_sqlite(cls, path: str, cfg: GateConfig) -> "LedgerStore":
        engine = create_engine(f"sqlite:///{path}", future=True)
        store = cls(engine, cfg)
        store.ensure_schema()
        return store

    @classmethod
    def for_engine(cls, engine: Engine, cfg: GateConfig) -> "LedgerStore":
        store = cls(engine, cfg)
        store.ensure_schema()
        return store

    def ensure_schema(self) -> None:
        """幂等建表（checkfirst）。生产面另有 PG 迁移 109_proposal_gate_ledger.sql。"""
        self.md.create_all(self.engine, checkfirst=True)

    def dispose(self) -> None:
        self.engine.dispose()

    # ── 写 ──────────────────────────────────────────────────────────────
    def get(self, mission_id: int, sha256: str) -> LedgerRow | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(self.table).where(
                    and_(self.table.c.mission_id == mission_id, self.table.c.sha256 == sha256)
                )
            ).mappings().first()
        return _row_to_dataclass(row) if row else None

    def find_by_sha(self, sha256: str) -> LedgerRow | None:
        """跨 mission 找同一内容摘要的首行（跨战役重复提交的证据来源）。"""
        with self.engine.begin() as conn:
            row = conn.execute(
                select(self.table).where(self.table.c.sha256 == sha256).order_by(self.table.c.id)
            ).mappings().first()
        return _row_to_dataclass(row) if row else None

    def record(
        self,
        *,
        mission_id: int,
        sha256: str,
        verdict: str,
        schema_version: str,
        plan_id: int | None = None,
        dup_of: str | None = None,
        score: float | None = None,
        reasons: Sequence[str] = (),
        source_path: str | None = None,
        state: str | None = None,
        blob: str | None = None,
    ) -> tuple[LedgerRow, bool]:
        """写一行（幂等）。返回 ``(row, created)``；已存在则原样返回既有行。"""
        if verdict not in VERDICTS:
            raise ValueError(f"未登记的 verdict：{verdict}")
        target_state = state or self.cfg.state_for(verdict)
        if target_state not in STATES:
            raise ValueError(f"未登记的四态：{target_state}")

        existing = self.get(mission_id, sha256)
        if existing is not None:
            return existing, False

        now = datetime.now(timezone.utc)
        payload = {
            "mission_id": mission_id,
            "plan_id": plan_id,
            "sha256": sha256,
            "schema_version": schema_version,
            "verdict": verdict,
            "state": target_state,
            "dup_of": dup_of,
            "score": score,
            "reasons": json.dumps(list(reasons), ensure_ascii=False),
            "source_path": source_path,
            "blob": (str(blob)[:_MAX_BLOB_CHARS] if blob else None),
            "at": now,
            "updated_at": now,
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(self.table.insert().values(**payload))
        except IntegrityError:
            # 并发下唯一键兜底：谁先写谁赢，后到者读既有行（仍然幂等）
            row = self.get(mission_id, sha256)
            if row is None:  # pragma: no cover - 唯一键冲突却读不到，说明表被外力改动
                raise
            return row, False
        row = self.get(mission_id, sha256)
        assert row is not None  # 刚写入
        return row, True

    def set_state(
        self,
        *,
        mission_id: int,
        sha256: str,
        state: str,
        verdict: str | None = None,
        plan_id: int | None = None,
        reason: str | None = None,
    ) -> LedgerRow:
        """状态迁移（上行：proposal → adopted）。留痕：reasons 追加一条迁移记录。"""
        if state not in STATES:
            raise ValueError(f"未登记的四态：{state}")
        existing = self.get(mission_id, sha256)
        if existing is None:
            raise KeyError(f"账本无此行：mission_id={mission_id} sha256={sha256}")
        reasons = list(existing.reasons)
        if reason:
            reasons.append(reason)
        values: dict[str, Any] = {
            "state": state,
            "updated_at": datetime.now(timezone.utc),
            "reasons": json.dumps(reasons, ensure_ascii=False),
        }
        if verdict is not None:
            values["verdict"] = verdict
        if plan_id is not None:
            values["plan_id"] = plan_id
        with self.engine.begin() as conn:
            conn.execute(
                self.table.update()
                .where(
                    and_(
                        self.table.c.mission_id == mission_id,
                        self.table.c.sha256 == sha256,
                    )
                )
                .values(**values)
            )
        row = self.get(mission_id, sha256)
        assert row is not None
        return row

    # ── 读 ──────────────────────────────────────────────────────────────
    def rows(
        self,
        *,
        state: str | None = None,
        mission_id: int | None = None,
        limit: int = 500,
    ) -> list[LedgerRow]:
        stmt = select(self.table).order_by(self.table.c.id)
        if state is not None:
            stmt = stmt.where(self.table.c.state == state)
        if mission_id is not None:
            stmt = stmt.where(self.table.c.mission_id == mission_id)
        stmt = stmt.limit(int(limit))
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_dataclass(r) for r in rows]

    def verdict_counts(self) -> dict[str, int]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(self.table.c.verdict, func.count()).group_by(self.table.c.verdict)
            ).all()
        out = {v: 0 for v in VERDICTS}
        for verdict, n in rows:
            out[str(verdict)] = int(n)
        return out

    def counts(self) -> dict[str, int]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(self.table.c.state, func.count()).group_by(self.table.c.state)
            ).all()
        out = {s: 0 for s in STATES}
        for state, n in rows:
            out[str(state)] = int(n)
        return out

    def stats(self, *, adopted_covered: int | None = None) -> GateStats:
        """四态计数 + 重复率 + KPI（``guide.proposal_dup_rejected`` 等，键名来自配置）。"""
        counts = self.counts()
        total = sum(counts.values())
        dup_rate = (counts.get("dedup_rejected", 0) / total) if total else 0.0
        kpi: list[dict[str, Any]] = []
        dup_key = self.cfg.get("kpi.dup_rejected_key")
        if dup_key:
            kpi.append(
                {
                    "metric_key": dup_key,
                    "value": counts.get("dedup_rejected", 0),
                    "rate": round(dup_rate, 6),
                    "unit": "count",
                    "denominator": total,
                }
            )
        cov_key = self.cfg.get("kpi.seed_coverage_key")
        if cov_key:
            adopted = counts.get("adopted", 0)
            coverage = (adopted_covered / adopted) if (adopted and adopted_covered is not None) else None
            kpi.append(
                {
                    "metric_key": cov_key,
                    "value": adopted_covered if adopted_covered is not None else None,
                    "rate": round(coverage, 6) if coverage is not None else None,
                    "unit": "count",
                    "denominator": adopted,
                }
            )
        return GateStats(
            counts=counts,
            total=total,
            dup_rate=dup_rate,
            kpi=tuple(kpi),
            verdict_counts=self.verdict_counts(),
        )

    def by_state(self) -> dict[str, list[dict[str, Any]]]:
        return {s: [r.as_dict() for r in self.rows(state=s, limit=10_000)] for s in STATES}


def sql_trace(store: LedgerStore, state: str | None = None) -> str:
    """返回可直接贴进报告/审计链的 SQL（数字可被任何人重跑复核）。"""
    table = store.cfg.table_name
    cond = f" WHERE state = '{state}'" if state else ""
    return (
        f"SELECT state, count(*) FROM {table}{cond} GROUP BY state ORDER BY state;\n"
        f"SELECT count(*) FROM {table}{cond};\n"
        f"SELECT mission_id, sha256, verdict, dup_of, score, at FROM {table}"
        f"{cond} ORDER BY id;"
    )


def iter_rows(rows: Iterable[LedgerRow]) -> Iterable[dict[str, Any]]:
    for r in rows:
        yield r.as_dict()