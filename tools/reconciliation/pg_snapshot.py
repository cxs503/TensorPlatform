#!/usr/bin/env python3
"""Offline snapshot builder — replay the reconciliation gate on production data.

The gate has to be judged on real scale (thousands of terminal nodes × thousands
of reports), but a build sandbox usually has no Postgres driver. This tool turns
a read-only CSV export of four tables into a SQLite file with the same shape the
gate queries, so ``report_gate`` runs at production scale offline, byte-for-byte
deterministic, and the numbers in a report can be re-derived by anyone.

Export (read-only, prod side)::

    psql "$DATABASE_URL" -c "\\copy (select n.id,n.plan_id,n.mission_id,n.name,n.status,
        n.metadata::text as metadata,p.organization_id
        from campaign_nodes n join campaign_plans p on p.id=n.plan_id
        where n.status in ('done','blocked')) to 'nodes.csv' csv header"
    psql "$DATABASE_URL" -c "\\copy (select id,mission_id,organization_id,status,title,
        scenario_key,created_by from tensor_reports where mission_id is not null)
        to 'reports.csv' csv header"
    psql "$DATABASE_URL" -c "\\copy (select id from tensor_dev_missions)
        to 'missions.csv' csv header"

Then::

    python -m tools.reconciliation.pg_snapshot --from-csv artifacts/…/snapshot \
        --out artifacts/…/snapshot/prod_snapshot.db

Zero domain semantics: only ids/statuses/metadata are carried, never domain data.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_plans (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER
);
CREATE TABLE IF NOT EXISTS campaign_nodes (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER,
    mission_id INTEGER,
    name TEXT,
    status TEXT,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS tensor_reports (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER,
    organization_id INTEGER,
    status TEXT,
    title TEXT,
    scenario_key TEXT,
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS tensor_dev_missions (id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS tensor_guide_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER,
    metric_key TEXT,
    metric_value REAL,
    target_value REAL,
    notes TEXT,
    snapshot_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_campaign_nodes_mission ON campaign_nodes (mission_id);
CREATE INDEX IF NOT EXISTS ix_campaign_nodes_status ON campaign_nodes (status);
CREATE INDEX IF NOT EXISTS ix_tensor_reports_mission ON tensor_reports (mission_id);
"""


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _int(value: Any) -> int | None:
    if value is None or value == "" or str(value).upper() == "NULL":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_snapshot(csv_dir: Path, out_db: Path) -> dict[str, int]:
    """Load the CSV export into a fresh SQLite file; returns per-table row counts."""
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()
    conn = sqlite3.connect(out_db)
    try:
        # Disposable snapshot: keep the rollback journal in RAM so a loaded build
        # box with a full disk can still materialise it (and it is faster).
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.executescript(SCHEMA)
        nodes = _rows(csv_dir / "nodes.csv")
        conn.executemany(
            "INSERT OR REPLACE INTO campaign_nodes "
            "(id, plan_id, mission_id, name, status, metadata) VALUES (?,?,?,?,?,?)",
            [(_int(r["id"]), _int(r.get("plan_id")), _int(r.get("mission_id")),
              r.get("name"), r.get("status"), r.get("metadata") or "{}") for r in nodes],
        )
        plans = {(r.get("plan_id"), r.get("organization_id")) for r in nodes}
        conn.executemany(
            "INSERT OR REPLACE INTO campaign_plans (id, organization_id) VALUES (?,?)",
            [(_int(p), _int(o)) for p, o in plans if _int(p) is not None],
        )
        reports = _rows(csv_dir / "reports.csv")
        conn.executemany(
            "INSERT OR REPLACE INTO tensor_reports "
            "(id, mission_id, organization_id, status, title, scenario_key, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            [(_int(r["id"]), _int(r.get("mission_id")), _int(r.get("organization_id")),
              r.get("status"), (r.get("title") or "")[:200], r.get("scenario_key"),
              _int(r.get("created_by"))) for r in reports],
        )
        missions = _rows(csv_dir / "missions.csv")
        conn.executemany(
            "INSERT OR REPLACE INTO tensor_dev_missions (id) VALUES (?)",
            [(_int(r["id"]),) for r in missions if _int(r["id"]) is not None],
        )
        conn.commit()
        counts = {
            "campaign_nodes": conn.execute("SELECT COUNT(*) FROM campaign_nodes").fetchone()[0],
            "campaign_plans": conn.execute("SELECT COUNT(*) FROM campaign_plans").fetchone()[0],
            "tensor_reports": conn.execute("SELECT COUNT(*) FROM tensor_reports").fetchone()[0],
            "tensor_dev_missions": conn.execute("SELECT COUNT(*) FROM tensor_dev_missions").fetchone()[0],
        }
    finally:
        conn.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build an offline reconciliation snapshot (SQLite)")
    ap.add_argument("--from-csv", required=True, help="directory with nodes.csv/reports.csv/missions.csv")
    ap.add_argument("--out", required=True, help="output .db path")
    args = ap.parse_args(argv)
    counts = build_snapshot(Path(args.from_csv), Path(args.out))
    print(f"snapshot={args.out} " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())