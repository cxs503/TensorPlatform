#!/usr/bin/env python3
"""Report gate CLI — 终态 ↔ 报告对账门（机器可跑、可入 CI、带审计链）.

Why a CLI when ``/tensor/reconcile`` already exists: the HTTP endpoint is for the
console/守望线, this tool is for machines — cron/CI/轮次开账 can run it, get a
non-zero exit code on debt, and archive a self-hashing reconciliation artifact
that a report can cite without hand-copying numbers.

Contract (the "独占路径" rule):

  * It only READS ``campaign_nodes``, ``campaign_plans``, ``tensor_reports`` and
    ``tensor_dev_missions``.
  * Its only permitted writes are the supplement outbox
    (``tensor_report_supplement_queue``, needs ``--dispatch``) and the KPI series
    (``tensor_guide_tracker``, needs ``--snapshot-kpi``).
  * Any other write statement is refused **before it reaches the database** by
    ``ReadOnlyViolation`` — the guarantee is structural, not a code-review habit.

Usage::

    python -m tools.reconciliation.report_gate --db-url sqlite:///snap.db
    python -m tools.reconciliation.report_gate --db-url $DATABASE_URL --dispatch --snapshot-kpi
    python -m tools.reconciliation.report_gate --db-url ...   # exit 2 blocks CI on RED

Exit codes: 0 = GREEN, 2 = RED (debt and/or forged reports), 1 = error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
for _p in (str(BACKEND_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

SCHEMA_VERSION = "report-gate/1.0"
EXIT_GREEN, EXIT_ERROR, EXIT_RED = 0, 1, 2

# Tables this tool is allowed to write, and what unlocks each one.
OUTBOX_TABLE = "tensor_report_supplement_queue"
KPI_TABLE = "tensor_guide_tracker"
READ_ONLY_TABLES = ("campaign_nodes", "campaign_plans", "tensor_reports",
                    "tensor_dev_missions")

_WRITE_KEYWORDS = (
    "insert", "update", "delete", "create", "alter", "drop", "truncate",
    "replace", "grant", "vacuum",
)


class ReadOnlyViolation(RuntimeError):
    """Raised before a forbidden write statement is sent to the database."""


def _is_write(statement: str) -> bool:
    head = statement.lstrip().split(None, 1)[0].lower() if statement.strip() else ""
    if head in ("with", "("):  # CTE: look for a write inside the text
        lowered = statement.lower()
        return any(kw in lowered for kw in ("insert into", "update ", "delete from"))
    return head in _WRITE_KEYWORDS


def assert_write_allowed(statement: str, allowed_tables: set[str]) -> None:
    """Refuse any write that does not target an allowed table."""
    if not _is_write(statement):
        return
    lowered = statement.lower()
    if any(t in lowered for t in allowed_tables):
        return
    raise ReadOnlyViolation(
        "report_gate is read-only on business tables; refused statement: "
        + " ".join(statement.split())[:200]
    )


@contextmanager
def guarded_session(
    engine,
    allow_outbox: bool = False,
    allow_kpi: bool = False,
) -> Iterator[Session]:
    """Session whose writes are limited to the tables this run is allowed to own."""
    allowed: set[str] = set()
    if allow_outbox:
        allowed.add(OUTBOX_TABLE)
    if allow_kpi:
        allowed.add(KPI_TABLE)

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        assert_write_allowed(statement, allowed)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


# ───────────────────────── provenance helpers ─────────────────────

def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=10, text=True,
        ).strip()
    except Exception:
        return None


def _db_fingerprint(url: str) -> dict[str, str]:
    """Never persist credentials: scheme + a salted-digest of the URL."""
    scheme = url.split(":", 1)[0] if ":" in url else "unknown"
    return {
        "scheme": scheme,
        "url_sha256_16": hashlib.sha256(url.encode()).hexdigest()[:16],
    }


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode()


def build_artifact(
    result,
    *,
    db_url: str,
    sla: dict[str, Any] | None = None,
    dispatch: dict[str, Any] | None = None,
    kpi_snapshot: dict[str, Any] | None = None,
    sample_size: int = 5,
) -> dict[str, Any]:
    """Assemble the auditable reconciliation artifact (self-hashing)."""
    from audit.report_reconcile import gate_status  # local import keeps CLI import light

    gate = gate_status(result, sla=sla)
    ledger = {
        "done_no_report": len(result.done_no_report),
        "blocked_no_report": len(result.blocked_no_report),
        "done_with_report": int(result.kpi.get("done_with_report", 0)),
        "blocked_with_report": int(result.kpi.get("blocked_with_report", 0)),
        "nodes_with_report": int(result.kpi.get("nodes_with_report", 0)),
        "nodes_without_mission": int(result.kpi.get("nodes_without_mission", 0)),
        "report_no_node": len(result.report_no_node),
        "report_hash_mismatch": len(result.report_hash_mismatch),
        "report_orphan_mission": len(result.report_orphan_mission),
        "coverage_rate": result.report_coverage_rate,
        "coverage_rate_done": result.kpi.get("report_coverage_rate_done"),
        "coverage_rate_blocked": result.kpi.get("report_coverage_rate_blocked"),
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": result.scope,
        "ledger": ledger,
        "samples": {
            "done_no_report": [e.to_dict() for e in result.done_no_report[:sample_size]],
            "blocked_no_report": [e.to_dict() for e in result.blocked_no_report[:sample_size]],
            "report_no_node": [e.to_dict() for e in result.report_no_node[:sample_size]],
        },
        "kpi": result.kpi,
        "gate": gate,
        "sla": sla,
        "dispatch": dispatch,
        "kpi_snapshot": kpi_snapshot,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "read_only_tables": list(READ_ONLY_TABLES),
        "provenance": {
            "tool_sha256": _file_sha256(Path(__file__).resolve()),
            "engine_sha256": _file_sha256(BACKEND_DIR / "audit" / "report_reconcile.py"),
            "git_commit": _git_commit(),
            "db": _db_fingerprint(db_url),
        },
    }
    payload["integrity"] = {"payload_sha256": hashlib.sha256(_canonical(payload)).hexdigest()}
    return payload


EVENT_LOG_DEFAULT = REPO_ROOT / "artifacts" / "reconciliation" / "events" / "report_gate_events.jsonl"


def emit_event(artifact: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """
    Append one event to the report-gate event log (append-only JSONL mailbox).

    Why a file and not a table: the platform has no generic event-bus ingest
    endpoint (campaign_events is written by campaign logic, and this tool must
    stay read-only on business tables), so the gate publishes a tail-able event
    line instead of writing one. Consumers (dispatcher / watchdog) poll the file
    and act on ``event_type``; the line carries the artifact digest, so an event
    can never disagree with the ledger it announces.

    Returns the emitted event.
    """
    gate = artifact.get("gate") or {}
    ledger = artifact.get("ledger") or {}
    sla = artifact.get("sla") or {}
    dispatch = artifact.get("dispatch") or {}
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": ("report_gate.supplement_dispatch" if dispatch
                       else "report_gate.reconciliation"),
        "schema_version": SCHEMA_VERSION,
        "scope": artifact.get("scope"),
        "gate_status": gate.get("status"),
        "debt_nodes": gate.get("debt_nodes"),
        "done_no_report": ledger.get("done_no_report"),
        "blocked_no_report": ledger.get("blocked_no_report"),
        "forged_reports": gate.get("forged_reports"),
        "coverage_rate": ledger.get("coverage_rate"),
        "sla_overdue": sla.get("overdue"),
        "dispatched": dispatch.get("dispatched"),
        "escalated": (dispatch.get("sla") or {}).get("escalated"),
        "artifact_sha256": (artifact.get("integrity") or {}).get("payload_sha256"),
    }
    target = Path(path) if path is not None else EVENT_LOG_DEFAULT
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:      # append-only, one JSON per line
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return event


def compact_artifact(artifact: dict[str, Any], max_clickable: int = 20,
                     max_samples: int = 2) -> dict[str, Any]:
    """
    Shrink an artifact for repository storage without touching any number.

    ``gate.clickable`` (the operator worklist) and ``samples`` are the only bulky
    parts; they are truncated with an explicit ``compaction`` record of what was
    dropped, and the integrity digest is recomputed so the stored file still
    verifies. Ledger/KPI/SLA/dispatch counts are never modified — a compacted
    artifact must answer every acceptance question exactly as the full one.
    """
    import copy

    out = copy.deepcopy(artifact)
    gate = out.get("gate") or {}
    clickable = gate.get("clickable") or []
    dropped_clickable = max(0, len(clickable) - max_clickable)
    if dropped_clickable:
        gate["clickable"] = clickable[:max_clickable]
        gate["clickable_total"] = len(clickable)
    dispatch = out.get("dispatch") or {}
    tickets = dispatch.get("tickets") or []
    dropped_tickets = max(0, len(tickets) - max_clickable)
    if dropped_tickets:
        dispatch["tickets"] = tickets[:max_clickable]
        dispatch["tickets_total"] = len(tickets)
    out["dispatch"] = dispatch or None
    samples = out.get("samples") or {}
    dropped_samples = 0
    for key, entries in samples.items():
        if isinstance(entries, list) and len(entries) > max_samples:
            dropped_samples += len(entries) - max_samples
            samples[key] = entries[:max_samples]
    out["gate"] = gate
    out["samples"] = samples
    out["compaction"] = {
        "clickable_kept": len(gate.get("clickable") or []),
        "clickable_total": gate.get("clickable_total", len(clickable)),
        "clickable_dropped": dropped_clickable,
        "samples_dropped": dropped_samples,
        "tickets_kept": len(dispatch.get("tickets") or []),
        "tickets_total": dispatch.get("tickets_total", len(tickets)),
        "tickets_dropped": dropped_tickets,
        "why": "repo size budget; counts and digests unchanged, worklist regenerable "
               "by re-running the gate against the same DB",
    }
    out.pop("integrity", None)
    out["integrity"] = {"payload_sha256": hashlib.sha256(_canonical(out)).hexdigest()}
    return out


def verify_artifact(artifact: dict[str, Any]) -> bool:
    """Recompute the digest of an artifact; False if it was tampered with."""
    stored = (artifact.get("integrity") or {}).get("payload_sha256")
    body = {k: v for k, v in artifact.items() if k != "integrity"}
    return stored == hashlib.sha256(_canonical(body)).hexdigest()


# ───────────────────────────── CLI ────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="report_gate",
        description="Terminal-state ↔ report reconciliation with 72h supplement SLA",
    )
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL", "sqlite:///:memory:"),
                    help="SQLAlchemy URL (default: $DATABASE_URL)")
    ap.add_argument("--org", type=int, default=None, help="organization id (default: all)")
    ap.add_argument("--plan-ids", default="", help="comma-separated campaign plan ids")
    ap.add_argument("--include-non-terminal", action="store_true",
                    help="also reconcile in-flight nodes")
    ap.add_argument("--sla-hours", type=int, default=72, help="supplement SLA in hours")
    ap.add_argument("--sqlite-ram-journal", action="store_true",
                    help="keep the sqlite rollback journal in RAM (disposable snapshots "
                         "on a disk-tight build box); ignored for non-sqlite URLs")
    ap.add_argument("--dispatch", action="store_true",
                    help="file supplement tickets for debt nodes (writes outbox only)")
    ap.add_argument("--snapshot-kpi", action="store_true",
                    help="append report_coverage_rate to tensor_guide_tracker")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --dispatch: compute tickets, write nothing")
    ap.add_argument("--out", default=None,
                    help="artifact path (default: artifacts/reconciliation/report_gate_<ts>.json)")
    ap.add_argument("--event-log", default=str(EVENT_LOG_DEFAULT),
                    help="append-only JSONL event mailbox (dispatcher/watchdog tail it)")
    ap.add_argument("--no-event-log", action="store_true", help="do not emit an event")
    ap.add_argument("--compact", action="store_true",
                    help="truncate the clickable worklist/samples before writing "
                         "(repo-storage budget; all counts and digests stay valid)")
    ap.add_argument("--quiet", action="store_true", help="only print the artifact path")
    return ap.parse_args(argv)


def _default_out() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "artifacts" / "reconciliation" / f"report_gate_{ts}.json"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from audit.report_reconcile import (
        reconcile, dispatch_missing_reports, snapshot_coverage, sla_report,
        SLA_HOURS_DEFAULT,
    )

    sla_hours = int(args.sla_hours or SLA_HOURS_DEFAULT)
    engine = create_engine(args.db_url)
    if args.sqlite_ram_journal and args.db_url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=MEMORY")

    with guarded_session(engine, allow_outbox=args.dispatch and not args.dry_run,
                         allow_kpi=args.snapshot_kpi) as db:
        result = reconcile(db, org_id=args.org, plan_ids=args.plan_ids,
                           include_non_terminal=args.include_non_terminal)
        sla = sla_report(db, args.org, sla_hours=sla_hours)

        dispatch_summary = None
        if args.dispatch:
            dispatch_summary = dispatch_missing_reports(
                db, org_id=args.org, plan_ids=args.plan_ids,
                dry_run=args.dry_run, sla_hours=sla_hours,
            )
            sla = sla_report(db, args.org, sla_hours=sla_hours)

        kpi_snapshot = None
        if args.snapshot_kpi:
            kpi_snapshot = snapshot_coverage(
                db, args.org if args.org is not None else 1, result=result,
                notes=f"report_gate cli sla={sla_hours}h",
            )

        artifact = build_artifact(
            result, db_url=args.db_url, sla=sla,
            dispatch=dispatch_summary, kpi_snapshot=kpi_snapshot,
        )

    if args.compact:
        artifact = compact_artifact(artifact)

    out = Path(args.out) if args.out else _default_out()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
    latest = out.parent / "latest.json"
    try:
        latest.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
    except OSError:
        pass

    gate = artifact["gate"]
    if args.quiet:
        print(out)
    else:
        lg = artifact["ledger"]
        print(f"gate={gate['status']} debt={gate['debt_nodes']} "
              f"(done={lg['done_no_report']} blocked={lg['blocked_no_report']}) "
              f"forged={gate['forged_reports']} coverage={result.report_coverage_rate}% "
              f"sla_overdue={sla.get('overdue')} elapsed={artifact['elapsed_seconds']}s")
        print(f"artifact={out} sha256={artifact['integrity']['payload_sha256']}")

    if not verify_artifact(artifact):
        print("artifact integrity check failed", file=sys.stderr)
        return EXIT_ERROR
    if not args.no_event_log:
        emit_event(artifact, Path(args.event_log))
    if gate["status"] != "GREEN":
        return EXIT_RED
    return EXIT_GREEN


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except ReadOnlyViolation as exc:  # structural read-only guard
        print(f"read-only violation: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)