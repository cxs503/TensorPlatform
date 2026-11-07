#!/usr/bin/env python3
"""coordinator_shadow v1 —— 影子账本（IN27-H1）：append-only，一拍一行，幂等。

v0 的三个病，这里逐条治
-----------------------
1. ``id = (SELECT max(id)+1)`` → 并发/重跑会撞车。v1 用**自增序列 + beat_id 唯一索引**：
   ``beat_id = 时间槽（epoch // 1800）``，同一 30 分钟槽重复跑 = ``ON CONFLICT DO NOTHING``。
2. 写库失败无人知道：v1 每拍都写（成功写结果、失败写 ``error`` 字段），**拍在不在账上**
   本身就是健康信号；``acceptance.py`` 用「应到拍数 vs 实到拍数」算连续覆盖率。
3. 表结构不给后来者留扩位：v1 在**同表**上增量迁移（``ALTER TABLE ADD COLUMN IF NOT
   EXISTS`` 语义），老列 ``id/ts/model/urgent/verdict/pulse_digest`` 保持原义，
   IN27-H7 的分歧率评估（以本表为源）不受影响；新列全部可空，老读方忽略即可。

写入边界
--------
``LedgerGuard`` 只放行本表 DDL 与 ``INSERT INTO coordinator_shadow``；别的写语句（尤其
对任务表/评价表的 DML）一律拒——影子的写面就这么大。
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .diagnose import Diagnosis
from .pulse import iso, parse_ts, utc_now

SCHEMA = "coordinator-shadow-beat/1"
TABLE = "coordinator_shadow"

PG_MIGRATION: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} (id bigint, ts timestamptz default now(), model text, "
    "urgent boolean, verdict jsonb, pulse_digest jsonb)",
    f"CREATE SEQUENCE IF NOT EXISTS {TABLE}_id_seq",
    f"ALTER TABLE {TABLE} ALTER COLUMN id SET DEFAULT nextval('{TABLE}_id_seq')",
    f"SELECT setval('{TABLE}_id_seq', GREATEST(COALESCE((SELECT max(id) FROM {TABLE}), 0), 1))",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS schema text",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS beat_id text",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS provenance jsonb",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS filings jsonb DEFAULT '[]'::jsonb",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS suppressed jsonb DEFAULT '[]'::jsonb",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS duration_ms integer",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS error text",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS mode text DEFAULT 'live'",
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coordinator_shadow_beat ON {TABLE} (beat_id)",
    f"CREATE INDEX IF NOT EXISTS ix_coordinator_shadow_ts ON {TABLE} (ts DESC)",
)

SQLITE_MIGRATION: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT "
    "CURRENT_TIMESTAMP, model TEXT, urgent INTEGER, verdict TEXT, pulse_digest TEXT, schema TEXT, "
    "beat_id TEXT, provenance TEXT, filings TEXT DEFAULT '[]', suppressed TEXT DEFAULT '[]', "
    "duration_ms INTEGER, error TEXT, mode TEXT DEFAULT 'live')",
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coordinator_shadow_beat ON {TABLE} (beat_id)",
)

_ALLOWED_PREFIXES = (
    f"CREATE TABLE IF NOT EXISTS {TABLE.upper()}",
    f"CREATE SEQUENCE IF NOT EXISTS {TABLE.upper()}",
    f"ALTER TABLE {TABLE.upper()}",
    "SELECT SETVAL(",
    f"CREATE UNIQUE INDEX IF NOT EXISTS UX_COORDINATOR_SHADOW_BEAT ON {TABLE.upper()}",
    f"CREATE INDEX IF NOT EXISTS IX_COORDINATOR_SHADOW_TS ON {TABLE.upper()}",
    f"INSERT INTO {TABLE.upper()}",
)


class LedgerBoundaryViolation(RuntimeError):
    pass


class LedgerGuard:
    """账本通道只放行本表 DDL + append-only INSERT。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.audit: list[str] = []

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        body = " ".join(sql.split())
        if not body.upper().startswith(_ALLOWED_PREFIXES):
            raise LedgerBoundaryViolation(f"账本通道拒写：{body[:60]!r}")
        self.audit.append(body[:120])
        return self._inner.execute(body, params)


@dataclass
class Beat:
    beat_id: str
    ts: str
    model: str | None
    urgent: bool
    verdict: Mapping[str, Any]
    pulse_digest: Mapping[str, Any]
    schema: str = SCHEMA
    provenance: Mapping[str, Any] = field(default_factory=dict)
    filings: list[Mapping[str, Any]] = field(default_factory=list)
    suppressed: list[Mapping[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    mode: str = "live"

    def to_dict(self) -> dict[str, Any]:
        return {
            "beat_id": self.beat_id,
            "ts": self.ts,
            "model": self.model,
            "urgent": self.urgent,
            "verdict": dict(self.verdict),
            "pulse_digest": dict(self.pulse_digest),
            "schema": self.schema,
            "provenance": dict(self.provenance),
            "filings": [dict(f) for f in self.filings],
            "suppressed": [dict(s) for s in self.suppressed],
            "duration_ms": self.duration_ms,
            "error": self.error,
            "mode": self.mode,
            # findings 只数「规则层越界」，advisories 另计：LLM 的观察不能混进越界计数
            "findings": len([f for f in ((self.verdict or {}).get("findings") or [])
                             if not (isinstance(f, dict) and f.get("advisory"))]),
            "advisories": len([f for f in ((self.verdict or {}).get("findings") or [])
                               if isinstance(f, dict) and f.get("advisory")]),
            "filings_count": len(self.filings),
        }


def beat_from_diagnosis(diag: Diagnosis, *, filings: Sequence[Mapping[str, Any]],
                        suppressed: Sequence[Mapping[str, Any]], model: str | None,
                        duration_ms: int, error: str | None = None, mode: str = "live") -> Beat:
    return Beat(
        beat_id=diag.beat_id,
        ts=diag.as_of,
        model=model,
        urgent=bool(diag.urgent),
        verdict=diag.verdict(),
        pulse_digest=diag.pulse_digest,
        provenance={**dict(diag.provenance), "skipped": diag.skipped},
        filings=list(filings),
        suppressed=list(suppressed),
        duration_ms=duration_ms,
        error=error,
        mode=mode,
    )


INSERT_SQL_PG = (
    f"INSERT INTO {TABLE} (ts, model, urgent, verdict, pulse_digest, schema, beat_id, provenance, "
    "filings, suppressed, duration_ms, error, mode) VALUES (CAST(:ts AS timestamptz), :model, :urgent, "
    "CAST(:verdict AS jsonb), CAST(:pulse_digest AS jsonb), :schema, :beat_id, CAST(:provenance AS jsonb), "
    "CAST(:filings AS jsonb), CAST(:suppressed AS jsonb), :duration_ms, :error, :mode) "
    "ON CONFLICT (beat_id) DO NOTHING"
)

INSERT_SQL_SQLITE = (
    f"INSERT INTO {TABLE} (ts, model, urgent, verdict, pulse_digest, schema, beat_id, provenance, "
    "filings, suppressed, duration_ms, error, mode) VALUES (:ts, :model, :urgent, :verdict, "
    ":pulse_digest, :schema, :beat_id, :provenance, :filings, :suppressed, :duration_ms, :error, :mode) "
    "ON CONFLICT (beat_id) DO NOTHING"
)

SELECT_SQL_PG = (
    "SELECT ts::text, beat_id, model, urgent, verdict::text, pulse_digest::text, schema, "
    "provenance::text, filings::text, suppressed::text, duration_ms, error, mode "
    f"FROM {TABLE} WHERE ts >= CAST(:start AS timestamptz) ORDER BY ts ASC"
)
SELECT_SQL_SQLITE = (
    "SELECT ts, beat_id, model, urgent, verdict, pulse_digest, schema, provenance, filings, "
    f"suppressed, duration_ms, error, mode FROM {TABLE} WHERE ts >= :start ORDER BY ts ASC"
)


class SqliteLedger:
    dialect = "sqlite"

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.guard = LedgerGuard(self)

    def execute(self, sql: str, params: Mapping[str, Any] | None = None):
        cur = self.conn.execute(sql, dict(params or {}))
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None):
        return self.conn.execute(sql, dict(params or {})).fetchall()

    # --- 对外 API -----------------------------------------------------
    def migrate(self) -> list[str]:
        """建表 → 补列 → 建索引（顺序不能换：v0 老表没有 beat_id 时先建索引会炸）。"""
        ran = []
        create_table = SQLITE_MIGRATION[0]
        self.guard.execute(create_table)
        ran.append(create_table)
        ran.extend(self._add_missing_columns())
        for stmt in SQLITE_MIGRATION[1:]:
            self.guard.execute(stmt)
            ran.append(stmt)
        return ran

    _WANTED_COLUMNS = {
        "schema": "TEXT", "beat_id": "TEXT", "provenance": "TEXT",
        "filings": "TEXT DEFAULT '[]'", "suppressed": "TEXT DEFAULT '[]'",
        "duration_ms": "INTEGER", "error": "TEXT", "mode": "TEXT DEFAULT 'live'",
    }

    def _add_missing_columns(self) -> list[str]:
        """v0 老表（只有 id/ts/model/urgent/verdict/pulse_digest）原地补列。"""
        have = {row[1] for row in self.conn.execute(f"PRAGMA table_info({TABLE})").fetchall()}
        ran = []
        for col, decl in self._WANTED_COLUMNS.items():
            if col in have:
                continue
            stmt = f"ALTER TABLE {TABLE} ADD COLUMN {col} {decl}"
            self.guard.execute(stmt)
            ran.append(stmt)
        if "beat_id" not in have:
            self.guard.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coordinator_shadow_beat ON {TABLE} (beat_id)")
            ran.append("unique-index")
        return ran

    def append(self, beat: Beat) -> bool:
        """幂等追加一拍；返回是否真的新增（False=重复 beat_id）。"""
        params = {
            "ts": beat.ts, "model": beat.model, "urgent": int(bool(beat.urgent)),
            "verdict": json.dumps(dict(beat.verdict), ensure_ascii=False),
            "pulse_digest": json.dumps(dict(beat.pulse_digest), ensure_ascii=False),
            "schema": beat.schema, "beat_id": beat.beat_id,
            "provenance": json.dumps(dict(beat.provenance), ensure_ascii=False),
            "filings": json.dumps([dict(f) for f in beat.filings], ensure_ascii=False),
            "suppressed": json.dumps([dict(s) for s in beat.suppressed], ensure_ascii=False),
            "duration_ms": int(beat.duration_ms), "error": beat.error, "mode": beat.mode,
        }
        before = self.fetchall(f"SELECT count(*) FROM {TABLE}", {})[0][0]
        self.guard.execute(INSERT_SQL_SQLITE, params)
        after = self.fetchall(f"SELECT count(*) FROM {TABLE}", {})[0][0]
        return after > before

    def read(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        rows = self.fetchall(SELECT_SQL_SQLITE, {"start": start or "1970-01-01"})
        out = [self._row_to_dict(r) for r in rows]
        if end:
            out = [b for b in out if (b.get("ts") or "") <= end]
        return out

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        def load(v):
            if v is None:
                return None
            if isinstance(v, (dict, list)):
                return v
            try:
                return json.loads(v)
            except (TypeError, json.JSONDecodeError):
                return v

        return {
            "ts": str(row[0]) if row[0] is not None else None,
            "beat_id": row[1], "model": row[2], "urgent": bool(row[3]),
            "verdict": load(row[4]) or {}, "pulse_digest": load(row[5]) or {},
            "schema": row[6], "provenance": load(row[7]) or {},
            "filings": load(row[8]) or [], "suppressed": load(row[9]) or [],
            "duration_ms": row[10], "error": row[11], "mode": row[12],
        }


class PgLedger:
    """生产账本（psycopg；连接串 ``TP_DSN``）。"""

    dialect = "postgresql"

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "TP_DSN", "dbname=tensorplatform user=foundation host=/var/run/postgresql"
        )
        self._conn = None
        self.guard = LedgerGuard(self)

    @property
    def conn(self):  # pragma: no cover
        if self._conn is None:
            try:
                import psycopg  # type: ignore

                self._conn = psycopg.connect(self.dsn, autocommit=True)
            except ImportError:
                import psycopg2  # type: ignore

                self._conn = psycopg2.connect(self.dsn)
                self._conn.autocommit = True
        return self._conn

    def execute(self, sql: str, params: Mapping[str, Any] | None = None):  # pragma: no cover
        from .pulse import to_psycopg_placeholders

        cur = self.conn.cursor()
        cur.execute(to_psycopg_placeholders(sql) if params else sql, dict(params or {}) or None)
        count = cur.rowcount
        rows = cur.fetchall() if cur.description else []
        cur.close()
        return count if not rows else rows

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None):  # pragma: no cover
        from .pulse import to_psycopg_placeholders

        cur = self.conn.cursor()
        cur.execute(to_psycopg_placeholders(sql) if params else sql, dict(params or {}) or None)
        rows = cur.fetchall()
        cur.close()
        return rows

    def migrate(self) -> list[str]:  # pragma: no cover
        for stmt in PG_MIGRATION:
            self.guard.execute(stmt)
        return list(PG_MIGRATION)

    def _resync_id_sequence(self) -> None:  # pragma: no cover
        """把 id 序列推到 max(id)+1：与 v0 原型（``max(id)+1`` 手算自增）共存时必需。"""
        self.guard.execute(
            f"SELECT setval('{TABLE}_id_seq', GREATEST(COALESCE((SELECT max(id) FROM {TABLE}), 0), 1))")

    def append(self, beat: Beat) -> bool:  # pragma: no cover
        params = {
            "ts": beat.ts, "model": beat.model, "urgent": bool(beat.urgent),
            "verdict": json.dumps(dict(beat.verdict), ensure_ascii=False),
            "pulse_digest": json.dumps(dict(beat.pulse_digest), ensure_ascii=False),
            "schema": beat.schema, "beat_id": beat.beat_id,
            "provenance": json.dumps(dict(beat.provenance), ensure_ascii=False),
            "filings": json.dumps([dict(f) for f in beat.filings], ensure_ascii=False),
            "suppressed": json.dumps([dict(s) for s in beat.suppressed], ensure_ascii=False),
            "duration_ms": int(beat.duration_ms), "error": beat.error, "mode": beat.mode,
        }
        try:
            return bool(self.guard.execute(INSERT_SQL_PG, params))
        except Exception as exc:
            msg = str(exc).lower()
            # v0 原型仍在跑时它用 max(id)+1 手算 id，会和序列撞主键；撞了就重同步序列再试一次。
            if "duplicate key" in msg and "pkey" in msg:
                self._resync_id_sequence()
                return bool(self.guard.execute(INSERT_SQL_PG, params))
            if "duplicate key" in msg and "beat_id" in msg:
                return False
            raise

    def read(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:  # pragma: no cover
        rows = self.fetchall(SELECT_SQL_PG, {"start": start or "1970-01-01"})
        out = [SqliteLedger._row_to_dict(r) for r in rows]
        if end:
            out = [b for b in out if (b.get("ts") or "") <= end]
        return out


# ─────────────────────────── 运行状态（跨拍） ───────────────────────────


@dataclass
class RuntimeState:
    """跨拍状态：签名连击、上次立单时间、上一拍心跳。落 JSON，原子写。"""

    streaks: dict[str, int] = field(default_factory=dict)
    last_filed: dict[str, str] = field(default_factory=dict)
    last_filed_organ: dict[str, str] = field(default_factory=dict)
    last_beat_ts: str | None = None
    last_beat_id: str | None = None
    beats_total: int = 0
    errors_total: int = 0
    filings_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "coordinator-shadow-state/1",
            "streaks": dict(self.streaks),
            "last_filed": dict(self.last_filed),
            "last_filed_organ": dict(self.last_filed_organ),
            "last_beat_ts": self.last_beat_ts,
            "last_beat_id": self.last_beat_id,
            "beats_total": self.beats_total,
            "errors_total": self.errors_total,
            "filings_total": self.filings_total,
        }

    @classmethod
    def load(cls, path: str | Path | None) -> "RuntimeState":
        if not path:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return cls()
        st = cls()
        st.streaks = {str(k): int(v) for k, v in (data.get("streaks") or {}).items()}
        st.last_filed = {str(k): str(v) for k, v in (data.get("last_filed") or {}).items()}
        st.last_filed_organ = {str(k): str(v) for k, v in (data.get("last_filed_organ") or {}).items()}
        st.last_beat_ts = data.get("last_beat_ts")
        st.last_beat_id = data.get("last_beat_id")
        st.beats_total = int(data.get("beats_total") or 0)
        st.errors_total = int(data.get("errors_total") or 0)
        st.filings_total = int(data.get("filings_total") or 0)
        return st

    def save(self, path: str | Path | None) -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))
        tmp.replace(p)


def write_heartbeat(path: str | Path | None, *, status: str, beat: Beat | None = None,
                    extra: Mapping[str, Any] | None = None) -> None:
    """外部看门狗只读这个文件：有无心跳 = 影子还活着没有。"""
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": iso(utc_now()), "status": status}
    if beat is not None:
        payload.update({"beat_id": beat.beat_id, "beat_ts": beat.ts, "error": beat.error,
                        "filings": len(beat.filings), "urgent": beat.urgent})
    payload.update(dict(extra or {}))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(p)