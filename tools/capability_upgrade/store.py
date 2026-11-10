"""SQLite store for the capability evidence upgrade pipeline (P218-N01).

Append-only by design:

* ``evidence_items``   — immutable evidence records (content-addressed digest).
* ``evidence_events``  — ingest event log; carries the re-judge linkage and the
  measured re-judge latency so the sub-second guarantee is auditable.
* ``rejudge_runs``     — every adjudication (auto *and* manual) with its trigger,
  inputs, per-criterion verdicts and result level.
* ``upgrade_history``  — level transitions (or holds) with evidence IDs, the
  criteria that were applied, the timestamp and the verdict.
* ``slot_evidence``    — the capability↔evidence association (C1 coverage).

Nothing here is domain-specific: capability ids, criteria and metrics are all
injected.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .model import (
    CapabilitySlot,
    Criterion,
    EvidenceItem,
    canonical_json,
    now_iso,
)

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[2] / "artifacts" / "capability_upgrade" / "pipeline.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_slots (
    capability_id  TEXT PRIMARY KEY,
    label          TEXT NOT NULL DEFAULT '',
    initial_level  TEXT NOT NULL,
    current_level  TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id    TEXT PRIMARY KEY,
    capability_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    metrics_json   TEXT NOT NULL DEFAULT '{}',
    observed_level TEXT,
    source_json    TEXT NOT NULL DEFAULT '{}',
    payload_json   TEXT NOT NULL DEFAULT '{}',
    digest         TEXT NOT NULL,
    ingested_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_cap ON evidence_items(capability_id);

CREATE TABLE IF NOT EXISTS slot_evidence (
    capability_id  TEXT NOT NULL,
    evidence_id    TEXT NOT NULL,
    linked_at      TEXT NOT NULL,
    PRIMARY KEY (capability_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS criteria (
    criterion_id   TEXT PRIMARY KEY,
    capability_id  TEXT NOT NULL,
    target_level   TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    spec_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_criteria_cap ON criteria(capability_id);

CREATE TABLE IF NOT EXISTS evidence_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id     TEXT NOT NULL,
    capability_id   TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    emitted_at      TEXT NOT NULL,
    completed_at    TEXT,
    rejudge_run_id  TEXT,
    latency_ms      REAL,
    duplicate       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rejudge_runs (
    run_id          TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL,
    latency_ms      REAL,
    before_level    TEXT NOT NULL,
    result_level    TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    verdicts_json   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_runs_cap ON rejudge_runs(capability_id);

CREATE TABLE IF NOT EXISTS upgrade_history (
    history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    before_level    TEXT NOT NULL,
    after_level     TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    criteria_json   TEXT NOT NULL DEFAULT '[]',
    failures_json   TEXT NOT NULL DEFAULT '[]',
    evaluated_at    TEXT NOT NULL,
    trigger         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_cap ON upgrade_history(capability_id);
"""


class Store:
    """Thin, explicit data-access layer (no ORM, no hidden state)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── slots ─────────────────────────────────────────────────────────────
    def upsert_slot(
        self,
        capability_id: str,
        label: str = "",
        initial_level: str = "WITHHELD",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CapabilitySlot:
        """Declare a slot.  An existing slot keeps its current level."""
        existing = self.get_slot(capability_id)
        ts = now_iso()
        if existing:
            self.conn.execute(
                "UPDATE capability_slots SET label=?, metadata_json=?, updated_at=? "
                "WHERE capability_id=?",
                (label or existing.label, canonical_json(metadata or existing.metadata), ts, capability_id),
            )
            self.conn.commit()
            return self.get_slot(capability_id)  # type: ignore[return-value]
        self.conn.execute(
            "INSERT INTO capability_slots (capability_id,label,initial_level,current_level,"
            "metadata_json,updated_at) VALUES (?,?,?,?,?,?)",
            (
                capability_id,
                label,
                initial_level.upper(),
                initial_level.upper(),
                canonical_json(metadata or {}),
                ts,
            ),
        )
        self.conn.commit()
        return self.get_slot(capability_id)  # type: ignore[return-value]

    def get_slot(self, capability_id: str) -> Optional[CapabilitySlot]:
        row = self.conn.execute(
            "SELECT * FROM capability_slots WHERE capability_id=?", (capability_id,)
        ).fetchone()
        return self._row_to_slot(row) if row else None

    def list_slots(self) -> List[CapabilitySlot]:
        rows = self.conn.execute(
            "SELECT * FROM capability_slots ORDER BY capability_id"
        ).fetchall()
        return [self._row_to_slot(r) for r in rows]

    def set_level(self, capability_id: str, level: str) -> CapabilitySlot:
        self.conn.execute(
            "UPDATE capability_slots SET current_level=?, updated_at=? WHERE capability_id=?",
            (level.upper(), now_iso(), capability_id),
        )
        self.conn.commit()
        slot = self.get_slot(capability_id)
        if slot is None:
            raise KeyError(capability_id)
        return slot

    @staticmethod
    def _row_to_slot(row: sqlite3.Row) -> CapabilitySlot:
        return CapabilitySlot(
            capability_id=row["capability_id"],
            label=row["label"],
            initial_level=row["initial_level"],
            current_level=row["current_level"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            updated_at=row["updated_at"],
        )

    # ── evidence ──────────────────────────────────────────────────────────
    def add_evidence(self, item: EvidenceItem) -> bool:
        """Insert an evidence item.  Returns False if it already existed."""
        exists = self.conn.execute(
            "SELECT 1 FROM evidence_items WHERE evidence_id=?", (item.evidence_id,)
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            "INSERT INTO evidence_items (evidence_id,capability_id,kind,metrics_json,"
            "observed_level,source_json,payload_json,digest,ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                item.evidence_id,
                item.capability_id,
                item.kind,
                canonical_json(item.metrics or {}),
                item.observed_level,
                canonical_json(item.source or {}),
                canonical_json(item.payload or {}),
                item.digest,
                item.ingested_at,
            ),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO slot_evidence (capability_id,evidence_id,linked_at) "
            "VALUES (?,?,?)",
            (item.capability_id, item.evidence_id, item.ingested_at),
        )
        self.conn.commit()
        return True

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceItem]:
        row = self.conn.execute(
            "SELECT * FROM evidence_items WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        return self._row_to_evidence(row) if row else None

    def list_evidence(self, capability_id: str) -> List[EvidenceItem]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_items WHERE capability_id=? ORDER BY ingested_at, evidence_id",
            (capability_id,),
        ).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def evidence_ids(self, capability_id: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT evidence_id FROM slot_evidence WHERE capability_id=? ORDER BY evidence_id",
            (capability_id,),
        ).fetchall()
        return [r["evidence_id"] for r in rows]

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=row["evidence_id"],
            capability_id=row["capability_id"],
            kind=row["kind"],
            metrics=json.loads(row["metrics_json"] or "{}"),
            observed_level=row["observed_level"],
            source=json.loads(row["source_json"] or "{}"),
            payload=json.loads(row["payload_json"] or "{}"),
            ingested_at=row["ingested_at"],
        )

    # ── criteria ──────────────────────────────────────────────────────────
    def upsert_criterion(self, crit: Criterion) -> None:
        self.conn.execute(
            "INSERT INTO criteria (criterion_id,capability_id,target_level,description,spec_json)"
            " VALUES (?,?,?,?,?) ON CONFLICT(criterion_id) DO UPDATE SET"
            " capability_id=excluded.capability_id, target_level=excluded.target_level,"
            " description=excluded.description, spec_json=excluded.spec_json",
            (
                crit.criterion_id,
                crit.capability_id,
                crit.target_level.upper(),
                crit.description,
                canonical_json(crit.spec),
            ),
        )
        self.conn.commit()

    def list_criteria(self, capability_id: str) -> List[Criterion]:
        rows = self.conn.execute(
            "SELECT * FROM criteria WHERE capability_id=? ORDER BY criterion_id",
            (capability_id,),
        ).fetchall()
        return [
            Criterion(
                criterion_id=r["criterion_id"],
                capability_id=r["capability_id"],
                target_level=r["target_level"],
                description=r["description"],
                spec=json.loads(r["spec_json"] or "{}"),
            )
            for r in rows
        ]

    # ── events ────────────────────────────────────────────────────────────
    def append_event(
        self,
        evidence_id: str,
        capability_id: str,
        event_type: str,
        emitted_at: str,
        duplicate: bool = False,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO evidence_events (evidence_id,capability_id,event_type,emitted_at,"
            "duplicate) VALUES (?,?,?,?,?)",
            (evidence_id, capability_id, event_type, emitted_at, int(duplicate)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def complete_event(
        self,
        event_id: int,
        completed_at: str,
        run_id: str,
        latency_ms: float,
    ) -> None:
        self.conn.execute(
            "UPDATE evidence_events SET completed_at=?, rejudge_run_id=?, latency_ms=? "
            "WHERE event_id=?",
            (completed_at, run_id, latency_ms, event_id),
        )
        self.conn.commit()

    def list_events(self, capability_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if capability_id:
            rows = self.conn.execute(
                "SELECT * FROM evidence_events WHERE capability_id=? ORDER BY event_id",
                (capability_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM evidence_events ORDER BY event_id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── rejudge runs ──────────────────────────────────────────────────────
    def record_run(self, run: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rejudge_runs (run_id,capability_id,trigger,evidence_ids,"
            "started_at,finished_at,latency_ms,before_level,result_level,outcome,verdicts_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run["run_id"],
                run["capability_id"],
                run["trigger"],
                canonical_json(run.get("evidence_ids", [])),
                run["started_at"],
                run["finished_at"],
                run.get("latency_ms"),
                run["before_level"],
                run["result_level"],
                run["outcome"],
                canonical_json(run.get("verdicts", [])),
            ),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM rejudge_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, capability_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if capability_id:
            rows = self.conn.execute(
                "SELECT * FROM rejudge_runs WHERE capability_id=? ORDER BY started_at, run_id",
                (capability_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM rejudge_runs ORDER BY started_at, run_id"
            ).fetchall()
        return [self._row_to_run(r) for r in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "capability_id": row["capability_id"],
            "trigger": row["trigger"],
            "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "latency_ms": row["latency_ms"],
            "before_level": row["before_level"],
            "result_level": row["result_level"],
            "outcome": row["outcome"],
            "verdicts": json.loads(row["verdicts_json"] or "[]"),
        }

    # ── upgrade history ───────────────────────────────────────────────────
    def record_history(
        self,
        capability_id: str,
        run_id: str,
        before_level: str,
        after_level: str,
        outcome: str,
        evidence_ids: Iterable[str],
        criteria: List[Dict[str, Any]],
        failures: List[Dict[str, Any]],
        evaluated_at: str,
        trigger: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO upgrade_history (capability_id,run_id,before_level,after_level,outcome,"
            "evidence_ids,criteria_json,failures_json,evaluated_at,trigger)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                capability_id,
                run_id,
                before_level,
                after_level,
                outcome,
                canonical_json(list(evidence_ids)),
                canonical_json(criteria),
                canonical_json(failures),
                evaluated_at,
                trigger,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_history(self, capability_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if capability_id:
            rows = self.conn.execute(
                "SELECT * FROM upgrade_history WHERE capability_id=? ORDER BY history_id",
                (capability_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM upgrade_history ORDER BY history_id"
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "history_id": r["history_id"],
                    "capability_id": r["capability_id"],
                    "run_id": r["run_id"],
                    "before_level": r["before_level"],
                    "after_level": r["after_level"],
                    "outcome": r["outcome"],
                    "evidence_ids": json.loads(r["evidence_ids"] or "[]"),
                    "criteria": json.loads(r["criteria_json"] or "[]"),
                    "failures": json.loads(r["failures_json"] or "[]"),
                    "evaluated_at": r["evaluated_at"],
                    "trigger": r["trigger"],
                }
            )
        return out

    def stats(self) -> Dict[str, Any]:
        def count(sql: str, *args: Any) -> int:
            return int(self.conn.execute(sql, args).fetchone()[0])

        return {
            "slots": count("SELECT COUNT(*) FROM capability_slots"),
            "evidence": count("SELECT COUNT(*) FROM evidence_items"),
            "links": count("SELECT COUNT(*) FROM slot_evidence"),
            "criteria": count("SELECT COUNT(*) FROM criteria"),
            "events": count("SELECT COUNT(*) FROM evidence_events"),
            "runs": count("SELECT COUNT(*) FROM rejudge_runs"),
            "history": count("SELECT COUNT(*) FROM upgrade_history"),
            "levels": {
                r["current_level"]: r["n"]
                for r in self.conn.execute(
                    "SELECT current_level, COUNT(*) AS n FROM capability_slots GROUP BY current_level"
                ).fetchall()
            },
        }