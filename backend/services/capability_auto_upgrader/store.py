"""Evidence run store + evaluation log (append-only, content-addressed).

The store answers "what has actually been run", the evaluation log answers "what
did the upgrader decide on each of those runs, and why".  Both are append-only so
the figures and the report can be regenerated from artefacts alone.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .model import EvidenceError, EvidenceRun, canonical_json, sha256_obj, utc_now

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS evidence_run (
    run_digest     TEXT PRIMARY KEY,
    capability_id  TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""


class RunStore:
    """Append-only store of evidence runs (JSONL + SQLite mirror)."""

    def __init__(self, root: Path | str, db_name: str = "evidence_run.db"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_path = self.root / "evidence_runs.jsonl"
        self.evaluations_path = self.root / "evaluations.jsonl"
        self._conn = sqlite3.connect(str(self.root / db_name))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(RUNS_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- runs ------------------------------------------------------------
    def add(self, run: EvidenceRun) -> bool:
        """Store a run.  Returns False when an identical digest already exists."""
        existing = self._conn.execute(
            "SELECT run_digest FROM evidence_run WHERE run_digest = ?", (run.digest,)
        ).fetchone()
        if existing is not None:
            return False
        payload = run.as_dict()
        with open(self.runs_path, "a", encoding="utf-8") as fh:
            fh.write(canonical_json(payload) + "\n")
        self._conn.execute(
            "INSERT INTO evidence_run (run_digest, capability_id, seq, payload, created_at) "
            "VALUES (?,?,?,?,?)",
            (run.digest, run.capability_id, run.seq, canonical_json(payload), utc_now()),
        )
        self._conn.commit()
        return True

    def has(self, digest: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM evidence_run WHERE run_digest = ?", (digest,)
        ).fetchone()
        return row is not None

    def runs(self, capability_id: Optional[str] = None) -> List[EvidenceRun]:
        sql = "SELECT payload FROM evidence_run"
        params: tuple = ()
        if capability_id is not None:
            sql += " WHERE capability_id = ?"
            params = (capability_id,)
        sql += " ORDER BY capability_id, seq"
        return [EvidenceRun.from_dict(json.loads(row["payload"]))
                for row in self._conn.execute(sql, params).fetchall()]

    def latest_seq(self, capability_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(seq) AS s FROM evidence_run WHERE capability_id = ?", (capability_id,)
        ).fetchone()
        return int(row["s"] or 0)

    def capabilities(self) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT capability_id FROM evidence_run ORDER BY capability_id"
        ).fetchall()
        return [row["capability_id"] for row in rows]

    # -- evaluation log --------------------------------------------------
    def log_evaluation(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        entry = dict(record)
        entry.setdefault("ts", utc_now())
        entry["entry_sha256"] = sha256_obj({k: v for k, v in entry.items() if k != "entry_sha256"})
        with open(self.evaluations_path, "a", encoding="utf-8") as fh:
            fh.write(canonical_json(entry) + "\n")
        return entry

    def evaluations(self) -> List[Dict[str, Any]]:
        if not self.evaluations_path.is_file():
            return []
        out = []
        for line in self.evaluations_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def verify_evaluations(self) -> Dict[str, Any]:
        problems: List[str] = []
        entries = self.evaluations()
        for idx, entry in enumerate(entries, 1):
            declared = entry.get("entry_sha256")
            recomputed = sha256_obj({k: v for k, v in entry.items() if k != "entry_sha256"})
            if declared != recomputed:
                problems.append(f"evaluation #{idx}: sha256 mismatch")
        return {"ok": not problems, "n_entries": len(entries), "problems": problems}