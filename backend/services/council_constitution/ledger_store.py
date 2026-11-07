"""裁决账本的**关系库镜像**：换存储不改协议（IN28-N07）。

设计纪律（与 ``core.py`` 的账本协议逐字节一致，本模块**不修改**协议）：
- **协议不变**：记录形状与哈希口径完全沿用 ``core.Ledger``——同一 ``schema``、
  同一 ``prev_hash``/``entry_hash`` 计算（``core.sha256_obj`` 的规范化 JSON），
  因此文件账本的 ``entry_hash`` 与库中行的 ``entry_hash`` 必须逐字节相等；
- **append-only**：写入前重算哈希并校验前驱链接；不平即拒写（``LedgerStoreError``）；
- **字段级对齐可证伪**：``parity()`` 逐行逐字段与 JSONL 比对，输出差分明细，
  不以"行数相等"冒充一致；
- **后端可替换**：只依赖 SQLAlchemy Core（PG 生产、SQLite 单测），
  换库不改协议、不改调用方。

对外 schema：``tp.council.ledger_store/v1``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import (
    BigInteger, Column, MetaData, String, Table, create_engine, func, select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import JSON

from .core import SCHEMA_LEDGER, sha256_obj, utc_now

SCHEMA_LEDGER_STORE = "tp.council.ledger_store/v1"
DEFAULT_TABLE = "council_ledger_entries"
GENESIS_HASH = "0" * 64

# 账本记录的字段顺序（与 core.Ledger.append 写出的键完全一致）
FIELD_ORDER: tuple[str, ...] = ("schema", "seq", "recorded_at", "prev_hash", "record", "entry_hash")


class LedgerStoreError(RuntimeError):
    """账本写库被拒（链不连续、哈希不匹配、字段缺失、协议被改）。"""


def _make_engine(dsn_or_engine: str | Engine) -> Engine:
    if isinstance(dsn_or_engine, Engine):
        return dsn_or_engine
    dsn = dsn_or_engine
    kwargs: dict[str, Any] = {"future": True}
    if dsn.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in dsn:
            kwargs["poolclass"] = StaticPool
    return create_engine(dsn, **kwargs)


def dsn_from_env(default: str | None = None) -> str:
    """解析生产 DSN：``COUNCIL_LEDGER_DSN`` → ``TP_PG_DSN`` → ``DATABASE_URL`` → default。"""
    for key in ("COUNCIL_LEDGER_DSN", "TP_PG_DSN", "DATABASE_URL"):
        val = os.getenv(key)
        if val:
            return val
    if default:
        return default
    raise LedgerStoreError("未配置账本库 DSN（COUNCIL_LEDGER_DSN / TP_PG_DSN / DATABASE_URL）")


def _normalize_pg_dsn(dsn: str) -> str:
    """``postgresql://`` 缺驱动时补 SQLAlchemy 驱动名（psycopg3）。"""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    return dsn


class SqlLedgerStore:
    """裁决账本的关系库镜像（append-only + hash 链 + 文件↔库字段级对账）。"""

    schema_version = SCHEMA_LEDGER_STORE

    def __init__(self, dsn_or_engine: str | Engine, table: str = DEFAULT_TABLE) -> None:
        if isinstance(dsn_or_engine, str):
            dsn_or_engine = _normalize_pg_dsn(dsn_or_engine)
        self.engine = _make_engine(dsn_or_engine)
        self.table_name = table
        self._metadata = MetaData()
        self.table = Table(
            table,
            self._metadata,
            Column("seq", BigInteger, primary_key=True),
            Column("schema", String(64), nullable=False),
            Column("recorded_at", String(40), nullable=False),
            Column("prev_hash", String(64), nullable=False),
            Column("entry_hash", String(64), nullable=False, unique=True),
            Column("record", JSON, nullable=False),
        )

    # ------------------------------------------------------------------ 介质
    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def ensure_schema(self) -> None:
        self._metadata.create_all(self.engine, checkfirst=True)

    def close(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------------ 读
    def entries(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.table).order_by(self.table.c.seq)).mappings().all()
        return [self._row_to_entry(r) for r in rows]

    def count(self) -> int:
        self.ensure_schema()
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(self.table)).scalar_one())

    def head_hash(self) -> str:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(self.table.c.entry_hash).order_by(self.table.c.seq.desc()).limit(1)
            ).first()
        return row[0] if row else GENESIS_HASH

    # ------------------------------------------------------------------ 写
    @staticmethod
    def _row_to_entry(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema": row["schema"],
            "seq": int(row["seq"]),
            "recorded_at": row["recorded_at"],
            "prev_hash": row["prev_hash"],
            "record": row["record"],
            "entry_hash": row["entry_hash"],
        }

    @staticmethod
    def expected_hash(entry: Mapping[str, Any]) -> str:
        """与 ``core.Ledger`` 完全相同的哈希口径（同序列化、同字段集）。"""
        return sha256_obj({k: v for k, v in entry.items() if k != "entry_hash"})

    def append_entry(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """校验后落库一条账本记录（幂等：同 entry_hash 已存在则跳过）。"""
        missing = [k for k in FIELD_ORDER if k not in entry]
        if missing:
            raise LedgerStoreError(f"账本记录缺字段 {missing}（协议不允许裁剪）")
        entry = dict(entry)
        if entry["schema"] != SCHEMA_LEDGER:
            raise LedgerStoreError(f"账本 schema 不符: {entry['schema']!r}")
        recomputed = self.expected_hash(entry)
        if recomputed != entry["entry_hash"]:
            raise LedgerStoreError(
                f"seq={entry['seq']} entry_hash 与内容不符（协议级校验失败）"
            )
        self.ensure_schema()
        with self.engine.begin() as conn:
            head = conn.execute(
                select(self.table.c.entry_hash).order_by(self.table.c.seq.desc()).limit(1)
            ).first()
            head_hash = head[0] if head else GENESIS_HASH
            existing = conn.execute(
                select(self.table.c.seq).where(self.table.c.entry_hash == entry["entry_hash"])
            ).first()
            if existing:
                return {"inserted": False, "seq": int(existing[0]), "reason": "already_present"}
            if entry["prev_hash"] != head_hash:
                raise LedgerStoreError(
                    f"seq={entry['seq']} prev_hash 与库内链头不符（append-only 链断裂）"
                )
            conn.execute(self.table.insert().values(**{k: entry[k] for k in FIELD_ORDER}))
        return {"inserted": True, "seq": int(entry["seq"])}

    def append_record(self, record: Mapping[str, Any], recorded_at: str | None = None) -> dict[str, Any]:
        """按协议生成一条记录并落库（与 ``core.Ledger.append`` 同构）。"""
        self.ensure_schema()
        entries = self.entries()
        prev_hash = entries[-1]["entry_hash"] if entries else GENESIS_HASH
        entry: dict[str, Any] = {
            "schema": SCHEMA_LEDGER,
            "seq": len(entries) + 1,
            "recorded_at": recorded_at or utc_now(),
            "prev_hash": prev_hash,
            "record": dict(record),
        }
        entry["entry_hash"] = self.expected_hash(entry)
        self.append_entry(entry)
        return entry

    def import_file(self, path: str | Path, strict: bool = True) -> dict[str, Any]:
        """把 JSONL 文件账本整链导入库（幂等）。"""
        file_entries = _read_jsonl(path)
        inserted = skipped = 0
        conflicts: list[dict[str, Any]] = []
        for entry in file_entries:
            try:
                res = self.append_entry(entry)
            except LedgerStoreError as exc:
                if strict:
                    raise
                conflicts.append({"seq": entry.get("seq"), "error": str(exc)})
                continue
            inserted += int(res["inserted"])
            skipped += int(not res["inserted"])
        return {"file_entries": len(file_entries), "inserted": inserted, "skipped": skipped,
                "conflicts": conflicts}

    # ------------------------------------------------------------------ 校验
    def verify(self) -> dict[str, Any]:
        """与 ``core.Ledger.verify`` 同口径的链校验（seq 连续 / prev_hash / entry_hash）。"""
        entries = self.entries()
        prior = GENESIS_HASH
        problems: list[str] = []
        for i, e in enumerate(entries, 1):
            if e.get("seq") != i:
                problems.append(f"seq 断裂 @{i}")
            if e.get("prev_hash") != prior:
                problems.append(f"prev_hash 不连续 @{i}")
            if self.expected_hash(e) != e.get("entry_hash"):
                problems.append(f"entry_hash 不匹配 @{i}（记录被改）")
            prior = e.get("entry_hash")
        return {"ok": not problems, "entries": len(entries), "problems": problems, "head_hash": prior}

    def parity(self, path: str | Path) -> dict[str, Any]:
        """文件 hash 链 ↔ 库逐行逐字段对账（criterion (a)）。"""
        file_entries = _read_jsonl(path)
        db_entries = self.entries()
        field_diffs: list[dict[str, Any]] = []
        n = max(len(file_entries), len(db_entries))
        for i in range(n):
            f = file_entries[i] if i < len(file_entries) else None
            d = db_entries[i] if i < len(db_entries) else None
            if f is None or d is None:
                field_diffs.append({"seq": i + 1, "field": "<row>", "file": f is not None,
                                    "db": d is not None})
                continue
            for field in FIELD_ORDER:
                if _canon(f.get(field)) != _canon(d.get(field)):
                    field_diffs.append({"seq": i + 1, "field": field,
                                        "file": f.get(field), "db": d.get(field)})
        db_chain = self.verify()
        file_ok = _verify_entries(file_entries)["ok"]
        return {
            "schema": self.schema_version,
            "dialect": self.dialect,
            "table": self.table_name,
            "rows_db": len(db_entries),
            "rows_file": len(file_entries),
            "row_count_equal": len(db_entries) == len(file_entries),
            "field_diffs": field_diffs,
            "field_diff_count": len(field_diffs),
            "db_chain_ok": db_chain["ok"],
            "file_chain_ok": file_ok,
            "file_head_hash": _verify_entries(file_entries)["head_hash"],
            "db_head_hash": db_chain["head_hash"],
            "ok": bool(
                len(db_entries) == len(file_entries)
                and not field_diffs
                and db_chain["ok"]
                and file_ok
            ),
        }


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _verify_entries(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    prior = GENESIS_HASH
    problems: list[str] = []
    for i, e in enumerate(entries, 1):
        if e.get("seq") != i:
            problems.append(f"seq 断裂 @{i}")
        if e.get("prev_hash") != prior:
            problems.append(f"prev_hash 不连续 @{i}")
        recomputed = sha256_obj({k: v for k, v in e.items() if k != "entry_hash"})
        if recomputed != e.get("entry_hash"):
            problems.append(f"entry_hash 不匹配 @{i}")
        prior = e.get("entry_hash", prior)
    return {"ok": not problems, "entries": len(entries), "problems": problems, "head_hash": prior}


__all__ = [
    "DEFAULT_TABLE",
    "FIELD_ORDER",
    "GENESIS_HASH",
    "LedgerStoreError",
    "SCHEMA_LEDGER_STORE",
    "SqlLedgerStore",
    "dsn_from_env",
]