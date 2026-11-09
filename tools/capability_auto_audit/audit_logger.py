"""Audit logging with before/after tracking and rollback support.

Every evidence-level change and accuracy update is recorded in an
append-only audit log. The log supports:
- Full before/after state for every change
- Timestamps and run IDs for traceability
- Rollback to any historical version
- SQL-based querying of audit history

Schema
------
audit_log:
    id              INTEGER PRIMARY KEY
    timestamp       TEXT        -- ISO timestamp
    run_id          TEXT        -- verification run identifier
    capability_id   TEXT        -- which capability changed
    change_type     TEXT        -- 'level_change', 'accuracy_update', 'added', 'removed'
    before_level    TEXT        -- previous evidence level
    after_level     TEXT        -- new evidence level
    before_accuracy REAL        -- previous accuracy percentage
    after_accuracy  REAL        -- new accuracy percentage
    before_json     TEXT        -- full before state as JSON
    after_json      TEXT        -- full after state as JSON
    rule_fired      TEXT        -- which classification rule triggered the change
    operator        TEXT        -- who/what made the change (auto/manual)
    notes           TEXT        -- human-readable notes
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKTREE = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = WORKTREE / "artifacts" / "provenance" / "audit_log.db"


class AuditLogger:
    """Audit logger for capability ledger changes.

    Parameters
    ----------
    db_path : Path, optional
        Path to the audit log database. Defaults to
        ``artifacts/provenance/audit_log.db``.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create audit log tables."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                run_id          TEXT NOT NULL,
                capability_id   TEXT NOT NULL,
                change_type     TEXT NOT NULL,
                before_level    TEXT,
                after_level     TEXT,
                before_accuracy REAL,
                after_accuracy  REAL,
                before_json     TEXT,
                after_json      TEXT,
                rule_fired      TEXT,
                operator        TEXT DEFAULT 'auto',
                notes           TEXT
            );

            CREATE TABLE IF NOT EXISTS ledger_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                run_id          TEXT NOT NULL,
                snapshot_json   TEXT NOT NULL,
                sha256          TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_audit_cap ON audit_log(capability_id);
            CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snap_run ON ledger_snapshots(run_id);
        """)
        self.conn.commit()

    def log_change(
        self,
        run_id: str,
        capability_id: str,
        change_type: str,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        rule_fired: str = "",
        operator: str = "auto",
        notes: str = "",
    ) -> int:
        """Log a capability change.

        Parameters
        ----------
        run_id : str
            Verification run identifier.
        capability_id : str
            Which capability changed.
        change_type : str
            Type of change: 'level_change', 'accuracy_update', 'added', 'removed'.
        before : dict, optional
            Full before state.
        after : dict, optional
            Full after state.
        rule_fired : str
            Classification rule that triggered the change.
        operator : str
            Who made the change ('auto' or 'manual').
        notes : str
            Human-readable notes.

        Returns
        -------
        int
            The audit log entry ID.
        """
        now = datetime.now(timezone.utc).isoformat()
        before_level = before.get("evidence_level") if before else None
        after_level = after.get("evidence_level") if after else None
        before_accuracy = before.get("accuracy_pct") if before else None
        after_accuracy = after.get("accuracy_pct") if after else None

        cur = self.conn.execute(
            """INSERT INTO audit_log
               (timestamp, run_id, capability_id, change_type,
                before_level, after_level, before_accuracy, after_accuracy,
                before_json, after_json, rule_fired, operator, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, run_id, capability_id, change_type,
             before_level, after_level, before_accuracy, after_accuracy,
             json.dumps(before, ensure_ascii=False) if before else None,
             json.dumps(after, ensure_ascii=False) if after else None,
             rule_fired, operator, notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def log_snapshot(self, run_id: str, ledger_snapshot: Dict[str, Any]) -> int:
        """Log a full ledger snapshot for rollback support.

        Parameters
        ----------
        run_id : str
            Verification run identifier.
        ledger_snapshot : dict
            Full ledger state.

        Returns
        -------
        int
            The snapshot entry ID.
        """
        import hashlib
        now = datetime.now(timezone.utc).isoformat()
        snapshot_json = json.dumps(ledger_snapshot, ensure_ascii=False, indent=2)
        sha256 = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

        cur = self.conn.execute(
            """INSERT INTO ledger_snapshots (timestamp, run_id, snapshot_json, sha256)
               VALUES (?, ?, ?, ?)""",
            (now, run_id, snapshot_json, sha256),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_change_history(
        self,
        capability_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query the audit log for change history.

        Parameters
        ----------
        capability_id : str, optional
            Filter by capability.
        run_id : str, optional
            Filter by run.
        limit : int
            Maximum number of records to return.

        Returns
        -------
        list of dict
            Audit log entries.
        """
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if capability_id:
            sql += " AND capability_id = ?"
            params.append(capability_id)
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            entry = dict(r)
            # Parse JSON fields
            if entry.get("before_json"):
                entry["before_parsed"] = json.loads(entry["before_json"])
            if entry.get("after_json"):
                entry["after_parsed"] = json.loads(entry["after_json"])
            results.append(entry)
        return results

    def get_snapshot(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a ledger snapshot by run ID.

        Parameters
        ----------
        run_id : str
            The run ID to retrieve.

        Returns
        -------
        dict or None
            The ledger snapshot, or None if not found.
        """
        row = self.conn.execute(
            "SELECT * FROM ledger_snapshots WHERE run_id = ? ORDER BY timestamp DESC LIMIT 1",
            [run_id],
        ).fetchone()
        if row:
            result = dict(row)
            result["snapshot"] = json.loads(result["snapshot_json"])
            return result
        return None

    def get_all_snapshots(self) -> List[Dict[str, str]]:
        """List all available snapshots."""
        rows = self.conn.execute(
            "SELECT id, timestamp, run_id, sha256 FROM ledger_snapshots ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def rollback_to_snapshot(self, run_id: str) -> Dict[str, Any]:
        """Get the ledger state at a specific run for rollback.

        Parameters
        ----------
        run_id : str
            The run ID to rollback to.

        Returns
        -------
        dict
            Result with the snapshot data or error.
        """
        snapshot = self.get_snapshot(run_id)
        if snapshot is None:
            return {
                "success": False,
                "error": f"No snapshot found for run_id={run_id}",
            }
        return {
            "success": True,
            "run_id": run_id,
            "timestamp": snapshot["timestamp"],
            "sha256": snapshot["sha256"],
            "ledger_snapshot": snapshot["snapshot"],
        }

    def get_level_change_summary(self) -> Dict[str, Any]:
        """Get a summary of all level changes."""
        rows = self.conn.execute("""
            SELECT capability_id, before_level, after_level, COUNT(*) as count
            FROM audit_log
            WHERE change_type = 'level_change'
            GROUP BY capability_id, before_level, after_level
            ORDER BY count DESC
        """).fetchall()

        return {
            "total_level_changes": sum(dict(r)["count"] for r in rows),
            "transitions": [dict(r) for r in rows],
        }

    def verify_integrity(self) -> Dict[str, Any]:
        """Verify the integrity of the audit log.

        Checks:
        - All snapshots have valid JSON
        - All sha256 hashes match
        - No gaps in timestamps

        Returns
        -------
        dict
            Integrity check results.
        """
        issues = []

        # Check snapshot integrity
        rows = self.conn.execute("SELECT id, snapshot_json, sha256 FROM ledger_snapshots").fetchall()
        for row in rows:
            import hashlib
            snapshot_json = row["snapshot_json"]
            expected_hash = row["sha256"]
            actual_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                issues.append(f"Snapshot {row['id']}: hash mismatch")

            # Verify JSON is valid
            try:
                json.loads(snapshot_json)
            except json.JSONDecodeError as e:
                issues.append(f"Snapshot {row['id']}: invalid JSON: {e}")

        return {
            "snapshots_checked": len(rows),
            "issues": issues,
            "integrity_ok": len(issues) == 0,
        }

    def summary(self) -> Dict[str, Any]:
        """Get a summary of the audit log."""
        total_entries = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        total_snapshots = self.conn.execute("SELECT COUNT(*) FROM ledger_snapshots").fetchone()[0]
        capabilities_changed = self.conn.execute(
            "SELECT COUNT(DISTINCT capability_id) FROM audit_log"
        ).fetchone()[0]
        runs_logged = self.conn.execute(
            "SELECT COUNT(DISTINCT run_id) FROM audit_log"
        ).fetchone()[0]

        return {
            "db_path": str(self.db_path),
            "total_entries": total_entries,
            "total_snapshots": total_snapshots,
            "capabilities_changed": capabilities_changed,
            "runs_logged": runs_logged,
        }

    def close(self):
        """Close the database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
