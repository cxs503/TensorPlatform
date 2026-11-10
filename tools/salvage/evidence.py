"""Salvage evidence backfill — evidence rows only, never a status transition.

Contract enforced here
----------------------
* Every salvage finding becomes an **independent evidence row** with
  ``evidence_kind = 'salvage'``.  It is an addition to the ledger, never an
  update of ``campaign_nodes``: salvaged traces are *inputs for the next wave*,
  not acceptance.  Nothing in this module emits ``UPDATE campaign_nodes`` /
  ``SET status`` (asserted mechanically by the acceptance harness).
* The PostgreSQL path is probed honestly.  If the runtime role lacks write
  privileges, the row set is still persisted to a reproducible local ledger
  (JSONL + SQLite with a ``CHECK`` constraint on ``evidence_kind``) and the
  refusal text is recorded verbatim in the backfill report — no silent failure,
  no "assumed written".
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

EVIDENCE_KIND = "salvage"
SCHEMA_VERSION = "1.0.0"

#: statuses a salvage row must never imply
FORBIDDEN_STATUS_WORDS = ("done", "verified", "accepted", "passed")

PG_DDL = """
CREATE TABLE IF NOT EXISTS salvage_evidence (
    mission_id      BIGINT PRIMARY KEY,
    evidence_kind   TEXT NOT NULL CHECK (evidence_kind = 'salvage'),
    plan_id         BIGINT,
    node_id         BIGINT,
    repo            TEXT,
    ext_id          TEXT,
    artifact_path   TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    files_kept      INTEGER NOT NULL,
    classes         JSONB NOT NULL,
    node_status_observed TEXT,
    node_status_after    TEXT,
    note            TEXT
);
""".strip()

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS salvage_evidence (
    mission_id      INTEGER PRIMARY KEY,
    evidence_kind   TEXT NOT NULL CHECK (evidence_kind = 'salvage'),
    plan_id         INTEGER,
    node_id         INTEGER,
    repo            TEXT,
    ext_id          TEXT,
    artifact_path   TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    files_kept      INTEGER NOT NULL,
    classes         TEXT NOT NULL,
    node_status_observed TEXT,
    node_status_after    TEXT,
    note            TEXT
);
""".strip()

INSERT_SQLITE = (
    "INSERT OR REPLACE INTO salvage_evidence (mission_id, evidence_kind, plan_id, node_id, repo,"
    " ext_id, artifact_path, manifest_sha256, files_kept, classes, node_status_observed,"
    " node_status_after, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def build_rows(index: dict, out_root: Path, status_after: dict | None = None) -> list[dict]:
    """One deterministic evidence row per packaged mission (sorted by mission)."""
    out_root = Path(out_root)
    rows: list[dict] = []
    for entry in sorted(index.get("entries", []), key=lambda e: str(e.get("mission_key"))):
        mission_id = entry.get("mission_id")
        if mission_id is None:
            continue
        counts = entry.get("counts", {}) or {}
        if int(counts.get("kept", 0) or 0) == 0:
            # no salvaged material -> nothing to hand to the next wave -> no row
            # (this is what keeps the negative control free of partial evidence)
            continue
        by_class = counts.get("by_class_kept", {}) or {}
        after = (status_after or {}).get(str(mission_id), entry.get("node_status"))
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "evidence_kind": EVIDENCE_KIND,
                "mission_id": int(mission_id),
                "plan_id": entry.get("plan_id"),
                "node_id": entry.get("node_id"),
                "repo": entry.get("repo"),
                "ext_id": entry.get("ext_id"),
                "artifact_path": str(entry.get("manifest")),
                "artifact_root": str(out_root),
                "manifest_sha256": entry.get("manifest_sha256"),
                "files_kept": int(counts.get("kept", 0) or 0),
                "files_pruned": int(counts.get("pruned", 0) or 0),
                "classes": dict(sorted(by_class.items())),
                "node_status_observed": entry.get("node_status"),
                "node_status_after": after,
                "note": (
                    "salvage: uncommitted output of a dead mission, packaged for inheritance; "
                    "this row is evidence of material, not of acceptance"
                ),
            }
        )
    return rows


def assert_no_status_mutation(rows: list[dict]) -> None:
    """Two layers of defence: no forbidden word in status fields, and statuses must agree."""
    for row in rows:
        observed = str(row.get("node_status_observed"))
        after = str(row.get("node_status_after"))
        if observed != after:
            raise ValueError(f"status drift for mission {row['mission_id']}: {observed} -> {after}")
        for word in FORBIDDEN_STATUS_WORDS:
            if word in observed.lower():
                raise ValueError(
                    f"salvage row for mission {row['mission_id']} would look like acceptance "
                    f"(status={observed!r}); salvage evidence must stay partial"
                )


def write_jsonl(rows: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def write_sqlite(rows: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SQLITE_DDL)
        conn.executemany(
            INSERT_SQLITE,
            [
                (
                    row["mission_id"],
                    row["evidence_kind"],
                    row.get("plan_id"),
                    row.get("node_id"),
                    row.get("repo"),
                    row.get("ext_id"),
                    row["artifact_path"],
                    row["manifest_sha256"],
                    row["files_kept"],
                    json.dumps(row.get("classes", {}), sort_keys=True),
                    row.get("node_status_observed"),
                    row.get("node_status_after"),
                    row.get("note"),
                )
                for row in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def attempt_postgres_backfill(rows: list[dict], db: str | None = None, timeout: int = 30) -> dict:
    """Probe the PostgreSQL channel; returns a status dict, never raises on refusal."""
    statements = ["BEGIN;", PG_DDL + ";"]
    for row in rows:
        statements.append(
            "INSERT INTO salvage_evidence (mission_id, evidence_kind, plan_id, node_id, repo,"
            " ext_id, artifact_path, manifest_sha256, files_kept, classes, node_status_observed,"
            " node_status_after, note) VALUES ({mid}, 'salvage', {pid}, {nid}, {repo}, {ext},"
            " {path}, {sha}, {kept}, {classes}::jsonb, {obs}, {aft}, {note})"
            " ON CONFLICT (mission_id) DO NOTHING;".format(
                mid=row["mission_id"],
                pid=row.get("plan_id") if row.get("plan_id") is not None else "NULL",
                nid=row.get("node_id") if row.get("node_id") is not None else "NULL",
                repo=_sql_literal(row.get("repo")),
                ext=_sql_literal(row.get("ext_id")),
                path=_sql_literal(row.get("artifact_path")),
                sha=_sql_literal(row.get("manifest_sha256")),
                kept=row.get("files_kept", 0),
                classes=_sql_literal(json.dumps(row.get("classes", {}), sort_keys=True)),
                obs=_sql_literal(row.get("node_status_observed")),
                aft=_sql_literal(row.get("node_status_after")),
                note=_sql_literal(row.get("note")),
            )
        )
    statements.append("COMMIT;")
    cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
    if db:
        cmd[1:1] = ["-d", db]
    try:
        proc = subprocess.run(
            cmd, input="\n".join(statements), capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:400], "persisted": False}
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {
            "status": "blocked",
            "error": (error[-1] if error else "unknown")[:400],
            "persisted": False,
            "ddl": PG_DDL,
        }
    return {"status": "written", "error": None, "persisted": True, "rows": len(rows)}


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def backfill(
    index: dict,
    out_root: Path,
    status_after: dict | None = None,
    db: str | None = None,
) -> dict:
    """Persist evidence rows through the local ledger and probe PostgreSQL."""
    rows = build_rows(index, out_root, status_after=status_after)
    assert_no_status_mutation(rows)
    out_root = Path(out_root)
    jsonl = write_jsonl(rows, out_root / "evidence" / "salvage_evidence.jsonl")
    sqlite_path = write_sqlite(rows, out_root / "evidence" / "salvage_evidence.db")
    pg = attempt_postgres_backfill(rows, db=db)
    report = {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": EVIDENCE_KIND,
        "row_count": len(rows),
        "rows": rows,
        "jsonl": str(jsonl),
        "sqlite": str(sqlite_path),
        "postgres": pg,
        "status_transition": "none (salvage rows never move campaign_nodes.status)",
    }
    (out_root / "evidence" / "backfill_report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report