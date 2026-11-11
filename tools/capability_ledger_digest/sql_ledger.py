"""SQL-backed capability ledger with a canonical, bit-reproducible dump.

The ledger lives in SQLite so that the audited state is queryable, but the
*evidence* of the audit is a canonical text dump of that state plus its SHA-256.
That pairing is what makes criterion (c) machine checkable: after the SQL write,
``canonical_dump()`` and the digest stored in the audit report must be identical
strings, and re-deriving them from the database file must reproduce them bit for
bit.

Determinism rules:
  * every row is written with :func:`fmt_number` text, so no float formatting
    ambiguity can leak into the dump;
  * rows are dumped in sorted key order, never in insertion order;
  * no timestamp, rowid, run id or host detail is stored or dumped.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .classifier import Classification
from .spec import SCHEMA_VERSION, sha256_text

LEDGER_SCHEMA_VERSION = "1.0"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_capability (
    capability_id   TEXT PRIMARY KEY,
    level           TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    reason          TEXT NOT NULL,
    chain_complete  INTEGER NOT NULL,
    chain_sha256    TEXT NOT NULL,
    current_level   TEXT NOT NULL,
    transition      TEXT NOT NULL,
    expected_level  TEXT
);
CREATE TABLE IF NOT EXISTS ledger_gate (
    capability_id TEXT NOT NULL,
    gate          TEXT NOT NULL,
    measured      TEXT NOT NULL,
    threshold     TEXT NOT NULL,
    comparator    TEXT NOT NULL,
    passed        INTEGER NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (capability_id, gate)
);
"""

CAPABILITY_COLUMNS: Tuple[str, ...] = (
    "capability_id",
    "level",
    "rule_id",
    "reason",
    "chain_complete",
    "chain_sha256",
    "current_level",
    "transition",
    "expected_level",
)
GATE_COLUMNS: Tuple[str, ...] = (
    "capability_id",
    "gate",
    "measured",
    "threshold",
    "comparator",
    "passed",
    "note",
)
META_COLUMNS: Tuple[str, ...] = ("key", "value")


def _cell(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass
class SqlLedger:
    """A capability ledger stored in SQL with a canonical text projection."""

    path: Path
    conn: Optional[sqlite3.Connection] = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "SqlLedger":
        if self.conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "SqlLedger":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- write -------------------------------------------------------------
    def write(
        self,
        results: Sequence[Classification],
        meta: Optional[Dict[str, Any]] = None,
    ) -> "SqlLedger":
        """Replace the ledger content with ``results`` (idempotent upsert)."""
        self.open()
        assert self.conn is not None
        meta = dict(meta or {})
        meta.setdefault("ledger_schema_version", LEDGER_SCHEMA_VERSION)
        meta.setdefault("schema_version", SCHEMA_VERSION)
        meta["capability_count"] = str(len(results))

        ordered = sorted(results, key=lambda r: r.capability_id)
        with self.conn:  # single transaction: never a half-written ledger
            self.conn.execute("DELETE FROM ledger_gate")
            self.conn.execute("DELETE FROM ledger_capability")
            self.conn.execute("DELETE FROM ledger_meta")
            for res in ordered:
                self.conn.execute(
                    "INSERT INTO ledger_capability ("
                    + ", ".join(CAPABILITY_COLUMNS)
                    + ") VALUES ("
                    + ", ".join("?" for _ in CAPABILITY_COLUMNS)
                    + ")",
                    (
                        res.capability_id,
                        res.level,
                        res.rule_id,
                        res.reason,
                        1 if res.chain.chain_complete else 0,
                        res.chain.sha256(),
                        res.current_level,
                        res.transition,
                        res.expected_level,
                    ),
                )
                for item in sorted(res.chain.items, key=lambda i: i.item):
                    self.conn.execute(
                        "INSERT INTO ledger_gate ("
                        + ", ".join(GATE_COLUMNS)
                        + ") VALUES ("
                        + ", ".join("?" for _ in GATE_COLUMNS)
                        + ")",
                        (
                            res.capability_id,
                            item.item,
                            item.measured,
                            item.threshold,
                            item.comparator,
                            1 if item.passed else 0,
                            item.note,
                        ),
                    )
            for key in sorted(meta):
                self.conn.execute(
                    "INSERT INTO ledger_meta (key, value) VALUES (?, ?)",
                    (str(key), _cell(meta[key])),
                )
        return self

    # -- read --------------------------------------------------------------
    def rows(self, table: str, order_by: Sequence[str]) -> List[Dict[str, Any]]:
        self.open()
        assert self.conn is not None
        columns = {
            "ledger_capability": CAPABILITY_COLUMNS,
            "ledger_gate": GATE_COLUMNS,
            "ledger_meta": META_COLUMNS,
        }[table]
        sql = (
            f"SELECT {', '.join(columns)} FROM {table} "
            f"ORDER BY {', '.join(order_by)}"
        )
        return [dict(row) for row in self.conn.execute(sql)]

    def canonical_dump(self) -> str:
        """Deterministic text projection of the whole ledger."""
        lines: List[str] = ["# capability_ledger_digest canonical dump v1", ""]
        for table, order, columns in (
            ("ledger_meta", ("key",), META_COLUMNS),
            ("ledger_capability", ("capability_id",), CAPABILITY_COLUMNS),
            ("ledger_gate", ("capability_id", "gate"), GATE_COLUMNS),
        ):
            lines.append(f"## {table}")
            lines.append("|".join(columns))
            for row in self.rows(table, order):
                lines.append("|".join(_cell(row[c]) for c in columns))
            lines.append("")
        return "\n".join(lines)

    def digest(self) -> str:
        return sha256_text(self.canonical_dump())

    def level_map(self) -> Dict[str, str]:
        return {r["capability_id"]: r["level"] for r in self.rows("ledger_capability", ("capability_id",))}

    def to_markdown(self) -> str:
        rows = self.rows("ledger_capability", ("capability_id",))
        head = "| " + " | ".join(CAPABILITY_COLUMNS) + " |"
        sep = "|" + "|".join("---" for _ in CAPABILITY_COLUMNS) + "|"
        body = [
            "| " + " | ".join(_cell(r[c]) for c in CAPABILITY_COLUMNS) + " |"
            for r in rows
        ]
        return "\n".join([head, sep, *body])