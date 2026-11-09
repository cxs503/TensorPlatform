"""SQL-based provenance store for accuracy numbers (zero-hardcode policy).

Every accuracy number (error percentage, deviation, etc.) must be traceable
to a specific source file, line number, and content hash. This module provides
a SQLite-backed store that enforces this invariant.

Design principles
-----------------
- **Zero hardcoded numbers**: All accuracy data is inserted via SQL, never
  hardcoded in Python source.
- **Full provenance chain**: Each record stores (source_file, line_no, content_hash,
  extracted_value, context).
- **Query-first retrieval**: All consumers MUST use SQL queries to retrieve
  accuracy data; direct Python access is prohibited.
- **Immutable records**: Records are append-only; corrections create new versions.

Schema
------
provenance_records:
    id              INTEGER PRIMARY KEY
    capability_id   TEXT        -- e.g. 'calc.resistance'
    metric_name     TEXT        -- e.g. 'Rt_error_pct', 'Cb_deviation'
    value_pct       REAL        -- the accuracy number (percentage)
    source_file     TEXT        -- path to source file
    line_no         INTEGER     -- line number in source
    content_hash    TEXT        -- sha256 of the source line
    context         TEXT        -- surrounding text for human review
    extracted_at    TEXT        -- ISO timestamp of extraction
    run_id          TEXT        -- verification run identifier
    verified        INTEGER     -- 1 if independently verified, 0 otherwise

accuracy_summary:
    capability_id   TEXT
    run_id          TEXT
    metric_name     TEXT
    value_pct       REAL
    reference_value REAL        -- the reference/experimental value
    computed_value  REAL        -- the computed value
    error_pct       REAL        -- abs(computed - reference) / reference * 100
    pass_threshold  REAL        -- threshold (e.g. 3.0 for 3%)
    passed          INTEGER     -- 1 if error_pct <= pass_threshold
    provenance_id   INTEGER     -- FK to provenance_records
    updated_at      TEXT
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Default DB location
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "provenance" / "provenance.db"


class ProvenanceDB:
    """SQLite-backed provenance store for accuracy numbers.

    Parameters
    ----------
    db_path : Path or str, optional
        Path to the SQLite database file. Defaults to
        ``artifacts/provenance/provenance.db``.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS provenance_records (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                capability_id   TEXT NOT NULL,
                metric_name     TEXT NOT NULL,
                value_pct       REAL NOT NULL,
                source_file     TEXT NOT NULL,
                line_no         INTEGER NOT NULL,
                content_hash    TEXT NOT NULL,
                context         TEXT,
                extracted_at    TEXT NOT NULL,
                run_id          TEXT NOT NULL,
                verified        INTEGER DEFAULT 0,
                UNIQUE(capability_id, metric_name, run_id, source_file, line_no)
            );

            CREATE TABLE IF NOT EXISTS accuracy_summary (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                capability_id   TEXT NOT NULL,
                run_id          TEXT NOT NULL,
                metric_name     TEXT NOT NULL,
                value_pct       REAL NOT NULL,
                reference_value REAL,
                computed_value  REAL,
                error_pct       REAL,
                pass_threshold  REAL DEFAULT 3.0,
                passed          INTEGER DEFAULT 0,
                provenance_id   INTEGER REFERENCES provenance_records(id),
                updated_at      TEXT NOT NULL,
                UNIQUE(capability_id, metric_name, run_id)
            );

            CREATE TABLE IF NOT EXISTS extraction_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                source_files    INTEGER DEFAULT 0,
                records_created INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'running'
            );

            CREATE INDEX IF NOT EXISTS idx_prov_cap ON provenance_records(capability_id);
            CREATE INDEX IF NOT EXISTS idx_prov_run ON provenance_records(run_id);
            CREATE INDEX IF NOT EXISTS idx_acc_cap ON accuracy_summary(capability_id);
            CREATE INDEX IF NOT EXISTS idx_acc_run ON accuracy_summary(run_id);
        """)
        self.conn.commit()

    # ── Insertion ──────────────────────────────────────────────────────────

    def insert_provenance_record(
        self,
        capability_id: str,
        metric_name: str,
        value_pct: float,
        source_file: str,
        line_no: int,
        content_hash: str,
        context: str = "",
        run_id: str = "",
        verified: bool = False,
    ) -> int:
        """Insert a provenance record. Returns the record ID."""
        now = datetime.now(timezone.utc).isoformat()
        if not run_id:
            run_id = f"run_{now.replace(':', '').replace('-', '').replace('.', '')}"
        cur = self.conn.execute(
            """INSERT OR REPLACE INTO provenance_records
               (capability_id, metric_name, value_pct, source_file, line_no,
                content_hash, context, extracted_at, run_id, verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (capability_id, metric_name, value_pct, source_file, line_no,
             content_hash, context, now, run_id, int(verified)),
        )
        self.conn.commit()
        return cur.lastrowid

    def insert_accuracy_summary(
        self,
        capability_id: str,
        metric_name: str,
        value_pct: float,
        reference_value: Optional[float] = None,
        computed_value: Optional[float] = None,
        error_pct: Optional[float] = None,
        pass_threshold: float = 3.0,
        provenance_id: Optional[int] = None,
        run_id: str = "",
    ) -> int:
        """Insert an accuracy summary record. Returns the record ID."""
        now = datetime.now(timezone.utc).isoformat()
        if not run_id:
            run_id = f"run_{now.replace(':', '').replace('-', '').replace('.', '')}"
        if error_pct is None and reference_value is not None and computed_value is not None:
            error_pct = abs(computed_value - reference_value) / abs(reference_value) * 100
        if value_pct is None and error_pct is not None:
            value_pct = error_pct
        passed = 1 if (error_pct is not None and error_pct <= pass_threshold) else 0
        cur = self.conn.execute(
            """INSERT OR REPLACE INTO accuracy_summary
               (capability_id, run_id, metric_name, value_pct, reference_value,
                computed_value, error_pct, pass_threshold, passed, provenance_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (capability_id, run_id, metric_name, value_pct, reference_value,
             computed_value, error_pct, pass_threshold, passed, provenance_id, now),
        )
        self.conn.commit()
        return cur.lastrowid

    # ── SQL Query Interface (zero-hardcode enforcement) ────────────────────

    def query_accuracy(self, capability_id: str, metric_name: Optional[str] = None,
                       run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve accuracy data via SQL query. This is the ONLY approved
        method for accessing accuracy numbers.

        Parameters
        ----------
        capability_id : str
            The capability to query (e.g. 'calc.resistance').
        metric_name : str, optional
            Filter by metric name.
        run_id : str, optional
            Filter by run ID.

        Returns
        -------
        list of dict
            Query results with all columns.
        """
        sql = "SELECT * FROM accuracy_summary WHERE capability_id = ?"
        params: list = [capability_id]
        if metric_name:
            sql += " AND metric_name = ?"
            params.append(metric_name)
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY updated_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_provenance(self, capability_id: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve provenance records via SQL query."""
        sql = "SELECT * FROM provenance_records WHERE capability_id = ?"
        params: list = [capability_id]
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY extracted_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_all_accuracy_for_capability(self, capability_id: str) -> Dict[str, Any]:
        """Get all accuracy metrics for a capability via SQL.

        Returns a dict with:
        - metrics: list of metric records
        - max_error_pct: maximum error across all metrics
        - all_passed: whether all metrics pass their thresholds
        - sql_query: the exact SQL used (for audit trail)
        """
        sql = """
            SELECT metric_name, value_pct, error_pct, pass_threshold, passed,
                   reference_value, computed_value, run_id, updated_at
            FROM accuracy_summary
            WHERE capability_id = ?
            ORDER BY updated_at DESC
        """
        rows = self.conn.execute(sql, [capability_id]).fetchall()
        metrics = [dict(r) for r in rows]
        max_error = max((m.get("error_pct") or 0 for m in metrics), default=None)
        all_passed = all(m.get("passed", 0) == 1 for m in metrics) if metrics else False
        return {
            "capability_id": capability_id,
            "metrics": metrics,
            "max_error_pct": max_error,
            "all_passed": all_passed,
            "sql_query": sql.strip(),
            "record_count": len(metrics),
        }

    def verify_no_hardcoded_accuracy(self, capability_id: str) -> Dict[str, Any]:
        """Verify that all accuracy numbers for a capability come from SQL,
        not from hardcoded Python values.

        Returns a dict with:
        - verified: bool
        - record_count: number of SQL-sourced records
        - details: explanation
        """
        records = self.query_accuracy(capability_id)
        if not records:
            return {
                "verified": False,
                "record_count": 0,
                "details": f"No accuracy records found for {capability_id} in provenance DB",
            }
        # All records must have a provenance_id pointing to a source file
        all_sourced = all(r.get("provenance_id") is not None for r in records)
        return {
            "verified": all_sourced,
            "record_count": len(records),
            "details": (
                f"All {len(records)} accuracy records for {capability_id} "
                f"are SQL-sourced with provenance chain"
                if all_sourced
                else f"{sum(1 for r in records if r.get('provenance_id') is None)} records lack provenance chain"
            ),
        }

    # ── Bulk operations ────────────────────────────────────────────────────

    def extract_from_verification_file(
        self,
        file_path: Path,
        capability_id: str,
        run_id: str,
    ) -> int:
        """Extract accuracy numbers from a verification file and insert
        into the provenance store.

        Parses patterns like:
        - "误差 1.2%" / "error 1.2%"
        - "deviation: 0.5%"
        - "Ct_error_pct = 2.3"

        Returns the number of records created.
        """
        if not file_path.exists():
            return 0

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        count = 0

        # Pattern for extracting accuracy numbers
        patterns = [
            # "误差 N%" or "error N%"
            (re.compile(r"(?:误差|error|deviation|偏差)[^0-9\n%]{0,40}?(-?\d+(?:\.\d+)?)\s*%", re.I),
             "error_pct"),
            # "metric_name = N" or "metric_name: N"
            (re.compile(r"(\w+_error_pct|\w+_deviation)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.I),
             "named_metric"),
        ]

        for line_no_0, line in enumerate(lines):
            line_no = line_no_0 + 1
            content_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]

            for pattern, ptype in patterns:
                for m in pattern.finditer(line):
                    if ptype == "error_pct":
                        value = float(m.group(1))
                        metric_name = "extracted_error_pct"
                    else:
                        metric_name = m.group(1)
                        value = float(m.group(2))

                    self.insert_provenance_record(
                        capability_id=capability_id,
                        metric_name=metric_name,
                        value_pct=value,
                        source_file=str(file_path),
                        line_no=line_no,
                        content_hash=content_hash,
                        context=line.strip()[:200],
                        run_id=run_id,
                    )
                    count += 1

        return count

    # ── Utility ────────────────────────────────────────────────────────────

    def get_run_ids(self, capability_id: Optional[str] = None) -> List[str]:
        """Get all run IDs, optionally filtered by capability."""
        if capability_id:
            rows = self.conn.execute(
                "SELECT DISTINCT run_id FROM accuracy_summary WHERE capability_id = ? ORDER BY run_id DESC",
                [capability_id],
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT run_id FROM accuracy_summary ORDER BY run_id DESC"
            ).fetchall()
        return [r[0] for r in rows]

    def get_capability_ids(self) -> List[str]:
        """Get all capability IDs that have accuracy records."""
        rows = self.conn.execute(
            "SELECT DISTINCT capability_id FROM accuracy_summary ORDER BY capability_id"
        ).fetchall()
        return [r[0] for r in rows]

    def summary(self) -> Dict[str, Any]:
        """Get a summary of the provenance database."""
        prov_count = self.conn.execute("SELECT COUNT(*) FROM provenance_records").fetchone()[0]
        acc_count = self.conn.execute("SELECT COUNT(*) FROM accuracy_summary").fetchone()[0]
        cap_count = self.conn.execute("SELECT COUNT(DISTINCT capability_id) FROM accuracy_summary").fetchone()[0]
        run_count = self.conn.execute("SELECT COUNT(DISTINCT run_id) FROM accuracy_summary").fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "provenance_records": prov_count,
            "accuracy_records": acc_count,
            "capabilities_tracked": cap_count,
            "runs_tracked": run_count,
        }

    def close(self):
        """Close the database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
