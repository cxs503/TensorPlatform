"""引导回路落库：迁移后库内可查的持久化层（可换执行器，SQL 保持可移植）。

设计要点：
- **一份 SQL 两种执行器**：:class:`SqliteExecutor` 用于单测（把迁移文件原样执行，
  保证「迁移 ⇔ 代码」不漂移），:class:`PsqlExecutor` 用于部署验证（生产库内可查）；
- **SQL 限定在可移植子集**：只用 ``CREATE TABLE IF NOT EXISTS`` / ``INSERT`` /
  ``SELECT`` / ``CREATE INDEX``，类型只用 TEXT / INTEGER / DOUBLE PRECISION /
  BOOLEAN / TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP —— Postgres 与 SQLite 都接受；
- **只增不改**：审查账本带 sha256 链（prev_hash/entry_hash），结论表以唯一键做幂等，
  honest FAIL 的结论在持久层同样不可覆写。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .exploration import (
    OutcomeConflictError,
    OutcomeRecord,
    S_N_TRACK,
    ExplorationAccount,
    HonestFailProtectedError,
    resolve_outcome_conflict,
)
from .gradient import GradientPlan, TIERS

HERE = Path(__file__).resolve()
DEFAULT_MIGRATION = HERE.parents[2] / "migrations" / "104_guidance_loop.sql"

#: 引导回路落库的实体清单（迁移后必须全部可查）。
GUIDANCE_TABLES: Tuple[str, ...] = (
    "review_budget_ledger",
    "innovation_proposals",
    "task_gradient",
    "exploration_budget",
    "guidance_outcome",
    "guidance_cycle",
)

GENESIS_HASH = "0" * 64

CONCLUSIONS = ("approved", "approved_with_findings", "changes_requested", "rejected", "inconclusive", "exempted")
REVIEW_TYPES = ("execution", "technical", "outcome", "innovation")
LEDGER_KINDS = ("review", "exemption", "production")
PROPOSAL_STATUSES = ("submitted", "under_review", "approved", "rejected", "parked")


class StoreError(RuntimeError):
    """持久化失败（SQL 错误 / 链校验失败 / 实体缺失）。"""


class ProposalGateError(RuntimeError):
    """提案未经创新审查或审查未通过，不得升级。"""


# ─────────────────────────── 执行器 ───────────────────────────
class SqliteExecutor:
    """SQLite 执行器（单测用；把迁移文件当建库脚本执行）。"""

    def __init__(self, path: str = ":memory:") -> None:
        import sqlite3

        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def raw(self):
        return self._conn

    def execute(self, sql: str) -> List[Tuple[Any, ...]]:
        cursor = self._conn.execute(sql)
        if cursor.description is None:
            self._conn.commit()
            return []
        rows = [[None if value is None else str(value) for value in row] for row in cursor.fetchall()]
        self._conn.commit()
        return [tuple(row) for row in rows]


class PsqlExecutor:
    """psql 执行器（部署验证用）。口令**只从环境读取**，绝不落仓。"""

    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        database: Optional[str] = None,
        port: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.host = host or os.environ.get("TP_PG_HOST", "127.0.0.1")
        self.user = user or os.environ.get("TP_PG_USER", "foundation")
        self.database = database or os.environ.get("TP_PG_DB", "tensorplatform")
        self.port = port or os.environ.get("TP_PG_PORT", "5432")
        self.timeout = timeout

    def _env(self) -> Dict[str, str]:
        env = os.environ.copy()
        password = os.environ.get("PGPASSWORD") or os.environ.get("TP_PG_PASSWORD")
        if password:
            env["PGPASSWORD"] = password
        env.setdefault("PATH", "/usr/bin:/bin")
        return env

    def execute(self, sql: str) -> List[Tuple[Any, ...]]:
        cmd = [
            "psql",
            "-h", self.host,
            "-p", self.port,
            "-U", self.user,
            "-d", self.database,
            "-v", "ON_ERROR_STOP=1",
            "-tA",
            "-F", "\x1f",
            "-c", sql,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, env=self._env(), timeout=self.timeout)
        if result.returncode != 0:
            raise StoreError(f"psql 失败: {result.stderr.strip()[:600]}")
        rows = []
        for line in result.stdout.splitlines():
            if line == "":
                continue
            rows.append(tuple(line.split("\x1f")))
        return rows


# ─────────────────────────── SQL 片段 ───────────────────────────
def sql_literal(value: Any) -> str:
    """把 Python 值渲染成可移植 SQL 字面量（含转义）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def strip_comments(script: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in script.splitlines())


def split_statements(script: str) -> List[str]:
    statements = []
    for chunk in strip_comments(script).split(";"):
        cleaned = chunk.strip()
        if cleaned:
            statements.append(cleaned)
    return statements


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


# ─────────────────────────── 仓储 ───────────────────────────
class GuidanceStore:
    """引导回路仓储。所有写操作幂等或明确拒绝覆盖。"""

    def __init__(self, executor, migration_path: Optional[Path] = None, organization_id: int = 0) -> None:
        self.executor = executor
        self.migration_path = Path(migration_path or DEFAULT_MIGRATION)
        self.organization_id = organization_id

    # ---- 建表 / 巡检 ----
    def ensure_schema(self) -> List[str]:
        script = self.migration_path.read_text(encoding="utf-8")
        executed = []
        for statement in split_statements(script):
            self.executor.execute(statement)
            executed.append(statement.split("(")[0].strip())
        return executed

    def existing_tables(self) -> Tuple[str, ...]:
        rows = self.executor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ) if self._is_postgres() else self.executor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        present = {str(row[0]) for row in rows}
        return tuple(table for table in GUIDANCE_TABLES if table in present)

    def _is_postgres(self) -> bool:
        return isinstance(self.executor, PsqlExecutor)

    def count_rows(self, table: str) -> int:
        if table not in GUIDANCE_TABLES:
            raise StoreError(f"未知实体: {table!r}")
        rows = self.executor.execute(f"SELECT count(*) FROM {table}")
        return _as_int(rows[0][0]) if rows else 0

    # ---- 审查预算账本（sha256 链，只增不改） ----
    def _last_review_entry(self) -> Tuple[int, str]:
        rows = self.executor.execute(
            "SELECT seq, entry_hash FROM review_budget_ledger ORDER BY seq DESC LIMIT 1"
        )
        if not rows:
            return 0, GENESIS_HASH
        return _as_int(rows[0][0]), str(rows[0][1])

    @staticmethod
    def _review_digest(payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append_review(
        self,
        *,
        kind: str,
        review_type: str,
        subject: str,
        conclusion: str,
        reviewer: str,
        duration_s: Optional[float] = None,
        duration_source: str = "not_observable",
        source: str = "caller",
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if kind not in LEDGER_KINDS:
            raise StoreError(f"未知账目类别: {kind!r}")
        if review_type not in REVIEW_TYPES:
            raise StoreError(f"未知审查类型: {review_type!r}")
        if conclusion not in CONCLUSIONS:
            raise StoreError(f"未知结论: {conclusion!r}")
        seq, prev_hash = self._last_review_entry()
        seq += 1
        body = {
            "seq": seq,
            "kind": kind,
            "review_type": review_type,
            "subject": subject,
            "conclusion": conclusion,
            "reviewer": reviewer,
            "duration_s": None if duration_s is None else round(float(duration_s), 3),
            "duration_source": duration_source,
            "source": source,
            "prev_hash": prev_hash,
        }
        entry_hash = self._review_digest(body)
        entry_id = f"rv-{seq:06d}-{entry_hash[:12]}"
        columns = (
            "entry_id", "seq", "kind", "review_type", "subject", "conclusion", "reviewer",
            "duration_s", "duration_source", "source", "note", "prev_hash", "entry_hash",
            "organization_id",
        )
        values = (
            entry_id, seq, kind, review_type, subject, conclusion, reviewer,
            duration_s, duration_source, source, note, prev_hash, entry_hash,
            self.organization_id,
        )
        sql = f"INSERT INTO review_budget_ledger ({', '.join(columns)}) VALUES ({', '.join(sql_literal(v) for v in values)})"
        self.executor.execute(sql)
        return {"entry_id": entry_id, "seq": seq, "entry_hash": entry_hash, "prev_hash": prev_hash, "kind": kind}

    def verify_review_chain(self) -> Dict[str, Any]:
        rows = self.executor.execute(
            "SELECT seq, kind, review_type, subject, conclusion, reviewer, duration_s, "
            "duration_source, source, prev_hash, entry_hash FROM review_budget_ledger ORDER BY seq"
        )
        expected_prev = GENESIS_HASH
        for row in rows:
            seq, kind, review_type, subject, conclusion, reviewer, duration_s, duration_source, source, prev_hash, entry_hash = row
            if str(prev_hash) != expected_prev:
                return {"ok": False, "broken_at": _as_int(seq), "entries": len(rows), "reason": "prev_hash 链断裂"}
            body = {
                "seq": _as_int(seq),
                "kind": str(kind),
                "review_type": str(review_type),
                "subject": str(subject),
                "conclusion": str(conclusion),
                "reviewer": str(reviewer),
                "duration_s": None if duration_s in (None, "") else round(float(duration_s), 3),
                "duration_source": str(duration_source),
                "source": str(source),
                "prev_hash": str(prev_hash),
            }
            if self._review_digest(body) != str(entry_hash):
                return {"ok": False, "broken_at": _as_int(seq), "entries": len(rows), "reason": "entry_hash 不匹配"}
            expected_prev = str(entry_hash)
        return {"ok": True, "entries": len(rows), "head": expected_prev}

    # ---- 结论账本 ----
    def _existing_outcome(self, mission_key: str) -> Optional[str]:
        rows = self.executor.execute(
            f"SELECT outcome FROM guidance_outcome WHERE mission_key={sql_literal(mission_key)}"
        )
        return None if not rows else str(rows[0][0])

    def insert_outcome(self, record: OutcomeRecord, cycle_id: Optional[str] = None) -> str:
        existing = self._existing_outcome(record.mission_key)
        if existing is not None:
            # 持久层同样执行「honest FAIL 不可覆盖 / 只增不改」，与内存账本同一判据。
            return resolve_outcome_conflict(existing, record.outcome)
        columns = ("mission_key", "worker", "tier", "outcome", "exempt", "cycle_id", "organization_id")
        values = (
            record.mission_key, record.worker, record.tier, record.outcome,
            record.exempt, cycle_id, self.organization_id,
        )
        sql = f"INSERT INTO guidance_outcome ({', '.join(columns)}) VALUES ({', '.join(sql_literal(v) for v in values)})"
        self.executor.execute(sql)
        return "inserted"

    def outcomes(self, cycle_id: Optional[str] = None) -> List[Dict[str, Any]]:
        where = "" if cycle_id is None else f" WHERE cycle_id={sql_literal(cycle_id)}"
        rows = self.executor.execute(
            "SELECT mission_key, worker, tier, outcome, exempt FROM guidance_outcome" + where + " ORDER BY mission_key"
        )
        return [
            {
                "mission_key": str(row[0]),
                "worker": str(row[1]),
                "tier": str(row[2]),
                "outcome": str(row[3]),
                "exempt": str(row[4]) in ("1", "t", "true", "True", "TRUE"),
            }
            for row in rows
        ]

    # ---- 梯度快照 ----
    def record_cycle(self, cycle_id: str, stats_digest: str, total_pool: int, payload: Mapping[str, Any]) -> bool:
        rows = self.executor.execute(
            f"SELECT cycle_id FROM guidance_cycle WHERE cycle_id={sql_literal(cycle_id)}"
        )
        if rows:
            return False
        columns = ("cycle_id", "stats_digest", "total_pool", "yield_bar", "exploration_share", "payload", "organization_id")
        values = (
            cycle_id,
            stats_digest,
            total_pool,
            payload.get("yield_bar"),
            payload.get("exploration_share"),
            json.dumps(payload, sort_keys=True),
            self.organization_id,
        )
        sql = f"INSERT INTO guidance_cycle ({', '.join(columns)}) VALUES ({', '.join(sql_literal(v) for v in values)})"
        self.executor.execute(sql)
        return True

    def record_gradient(self, plan: GradientPlan, reason: str = "") -> int:
        inserted = 0
        for row in plan.as_rows():
            existing = self.executor.execute(
                "SELECT tier FROM task_gradient WHERE "
                f"cycle_id={sql_literal(row['cycle_id'])} AND tier={sql_literal(row['tier'])}"
            )
            if existing:
                continue
            columns = (
                "cycle_id", "tier", "base_share", "calibrated_share", "dispatched_count",
                "tier_yield", "yield_bar", "multiplier", "stats_digest", "total_pool",
                "reason", "organization_id",
            )
            values = (
                row["cycle_id"], row["tier"], row["base_share"], row["calibrated_share"],
                row["dispatched_count"], row["tier_yield"], row["yield_bar"], row["multiplier"],
                row["stats_digest"], row["total_pool"], reason, self.organization_id,
            )
            sql = f"INSERT INTO task_gradient ({', '.join(columns)}) VALUES ({', '.join(sql_literal(v) for v in values)})"
            self.executor.execute(sql)
            inserted += 1
        return inserted

    def gradient_rows(self, cycle_id: str) -> List[Dict[str, Any]]:
        rows = self.executor.execute(
            "SELECT tier, base_share, calibrated_share, dispatched_count, tier_yield, multiplier "
            f"FROM task_gradient WHERE cycle_id={sql_literal(cycle_id)} ORDER BY tier"
        )
        return [
            {
                "tier": str(row[0]),
                "base_share": float(row[1]),
                "calibrated_share": float(row[2]),
                "dispatched_count": _as_int(row[3]),
                "tier_yield": float(row[4]),
                "multiplier": float(row[5]),
            }
            for row in rows
        ]

    # ---- 探索预算 ----
    def record_exploration_account(self, account: ExplorationAccount) -> bool:
        entry_id = f"exp-{account.cycle_id}-{account.worker}-account"
        existing = self.executor.execute(
            f"SELECT entry_id FROM exploration_budget WHERE entry_id={sql_literal(entry_id)}"
        )
        if existing:
            return False
        self._insert_exploration(
            entry_id=entry_id,
            cycle_id=account.cycle_id,
            worker=account.worker,
            tier="",
            track="S4+S5",
            entry_type="account",
            allocated=account.allocated,
            consumed=account.consumed,
            refunded=account.refunded,
            remaining=account.remaining,
            multiplier=account.multiplier,
            mission_key=None,
            outcome=None,
            exempt=False,
        )
        return True

    def record_settlement(self, settlement: Mapping[str, Any]) -> bool:
        entry_id = f"exp-{settlement['cycle_id']}-{settlement['worker']}-{settlement['mission_key']}-{settlement['outcome']}"
        existing = self.executor.execute(
            f"SELECT entry_id FROM exploration_budget WHERE entry_id={sql_literal(entry_id)}"
        )
        if existing:
            return False
        exempt = bool(settlement.get("exempt_from_failure_rate"))
        self._insert_exploration(
            entry_id=entry_id,
            cycle_id=str(settlement["cycle_id"]),
            worker=str(settlement["worker"]),
            tier=str(settlement["tier"]),
            track=str(settlement.get("track", settlement["tier"])),
            entry_type="s_n" if exempt else "settlement",
            allocated=0.0,
            consumed=float(settlement.get("cost", 0.0)),
            refunded=float(settlement.get("refunded", 0.0)),
            remaining=0.0,
            multiplier=1.0,
            mission_key=str(settlement["mission_key"]),
            outcome=str(settlement["outcome"]),
            exempt=exempt,
        )
        return True

    def _insert_exploration(self, **fields: Any) -> None:
        columns = (
            "entry_id", "cycle_id", "worker", "tier", "track", "entry_type", "allocated",
            "consumed", "refunded", "remaining", "multiplier", "mission_key", "outcome",
            "exempt", "organization_id",
        )
        values = tuple(fields.get(column) for column in columns[:-1]) + (self.organization_id,)
        sql = (
            f"INSERT INTO exploration_budget ({', '.join(columns)}) "
            f"VALUES ({', '.join(sql_literal(v) for v in values)})"
        )
        self.executor.execute(sql)

    def exploration_rows(self, cycle_id: str) -> List[Dict[str, Any]]:
        rows = self.executor.execute(
            "SELECT entry_id, worker, tier, track, entry_type, allocated, consumed, refunded, "
            "remaining, exempt, outcome FROM exploration_budget "
            f"WHERE cycle_id={sql_literal(cycle_id)} ORDER BY entry_id"
        )
        return [
            {
                "entry_id": str(row[0]),
                "worker": str(row[1]),
                "tier": str(row[2]),
                "track": str(row[3]),
                "entry_type": str(row[4]),
                "allocated": float(row[5]),
                "consumed": float(row[6]),
                "refunded": float(row[7]),
                "remaining": float(row[8]),
                "exempt": str(row[9]) in ("1", "t", "true", "True", "TRUE"),
                "outcome": None if row[10] in (None, "") else str(row[10]),
            }
            for row in rows
        ]

    # ---- 创新提案 ----
    def insert_proposal(
        self,
        proposal_id: str,
        title: str,
        hypothesis: str = "",
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        existing = self.executor.execute(
            f"SELECT proposal_id FROM innovation_proposals WHERE proposal_id={sql_literal(proposal_id)}"
        )
        if existing:
            return False
        columns = ("proposal_id", "title", "hypothesis", "status", "review_entry_id", "evidence", "organization_id")
        values = (
            proposal_id, title, hypothesis, "submitted", None,
            json.dumps(dict(evidence or {}), sort_keys=True), self.organization_id,
        )
        sql = f"INSERT INTO innovation_proposals ({', '.join(columns)}) VALUES ({', '.join(sql_literal(v) for v in values)})"
        self.executor.execute(sql)
        return True

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        rows = self.executor.execute(
            "SELECT proposal_id, title, hypothesis, status, review_entry_id, created_at "
            f"FROM innovation_proposals WHERE proposal_id={sql_literal(proposal_id)}"
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "proposal_id": str(row[0]),
            "title": str(row[1]),
            "hypothesis": str(row[2] or ""),
            "status": str(row[3]),
            "review_entry_id": None if row[4] in (None, "") else str(row[4]),
            "created_at": None if row[5] in (None, "") else str(row[5]),
        }

    def link_review(self, proposal_id: str, review_entry_id: str, conclusion: str) -> str:
        """把创新审查结论挂到提案上；只有通过类结论才能推进状态。"""
        if conclusion not in CONCLUSIONS:
            raise StoreError(f"未知结论: {conclusion!r}")
        status = "approved" if conclusion in ("approved", "approved_with_findings") else "rejected"
        self.executor.execute(
            f"UPDATE innovation_proposals SET status={sql_literal(status)}, "
            f"review_entry_id={sql_literal(review_entry_id)} "
            f"WHERE proposal_id={sql_literal(proposal_id)}"
        )
        return status

    def promote_proposal(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise ProposalGateError(f"提案不存在: {proposal_id!r}")
        if proposal["status"] != "approved":
            raise ProposalGateError(
                f"提案 {proposal_id!r} 未经创新审查通过（当前 {proposal['status']!r}），不得升级"
            )
        return proposal

    # ---- 巡检 ----
    def summary(self) -> Dict[str, Any]:
        return {
            "tables_present": list(self.existing_tables()),
            "counts": {table: self.count_rows(table) for table in GUIDANCE_TABLES},
            "chain": self.verify_review_chain(),
        }