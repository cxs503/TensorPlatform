"""
Report Reconciliation Gate — 终态↔报告双向对账器

Scans all terminal campaign_nodes (done/blocked) and cross-references with
tensor_reports(mission_id) to produce four ledger categories:
  - done_no_report:     done nodes without a matching report
  - blocked_no_report:  blocked nodes without a matching report (honest-FAIL counts)
  - report_no_node:     reports whose mission_id has no corresponding campaign node
  - report_hash_mismatch: reports whose mission_id hash doesn't match node metadata

Design principles:
  - Zero domain semantics: only reads mission/report metadata
  - Idempotent dispatch:补报单 uses mission_id as idempotency key
  - Performance: full reconciliation ≤ 30s
  - Anti-forgery: reuses p130 ownership verification工装
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


# ───────────────────────── Data structures ─────────────────────────

@dataclass
class ReconcileEntry:
    """Single ledger entry."""
    category: str          # done_no_report | blocked_no_report | report_no_node | report_hash_mismatch
    node_id: int | None    # campaign_nodes.id (None for report_no_node)
    mission_id: int | None # tensor_dev_missions.id
    report_id: int | None  # tensor_reports.id (None for *_no_report)
    plan_id: int | None
    node_name: str | None
    node_status: str | None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReconcileResult:
    """Full reconciliation result."""
    elapsed_seconds: float = 0.0
    total_terminal_nodes: int = 0
    total_reports: int = 0
    done_no_report: list[ReconcileEntry] = field(default_factory=list)
    blocked_no_report: list[ReconcileEntry] = field(default_factory=list)
    report_no_node: list[ReconcileEntry] = field(default_factory=list)
    report_hash_mismatch: list[ReconcileEntry] = field(default_factory=list)
    # Reports whose mission exists but is attached to no campaign node at all.
    # Informational only: such reports come from plain (non-campaign) missions and
    # must NOT turn the gate red, otherwise every non-campaign report is a false alarm.
    report_orphan_mission: list[ReconcileEntry] = field(default_factory=list)
    report_coverage_rate: float = 0.0
    scope: dict[str, Any] = field(default_factory=dict)
    kpi: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "total_terminal_nodes": self.total_terminal_nodes,
            "total_reports": self.total_reports,
            "done_no_report": [e.to_dict() for e in self.done_no_report],
            "blocked_no_report": [e.to_dict() for e in self.blocked_no_report],
            "report_no_node": [e.to_dict() for e in self.report_no_node],
            "report_hash_mismatch": [e.to_dict() for e in self.report_hash_mismatch],
            "report_orphan_mission": [e.to_dict() for e in self.report_orphan_mission],
            "report_coverage_rate": self.report_coverage_rate,
            "scope": self.scope,
            "kpi": self.kpi,
        }


# ───────────────────────── Core reconciler ─────────────────────────

def _node_mission_hash(mission_id: int) -> str:
    """Deterministic hash for mission_id (used for mismatch detection)."""
    return hashlib.sha256(f"mission:{mission_id}".encode()).hexdigest()[:16]


TERMINAL_STATUSES: tuple[str, ...] = ("done", "blocked")


def _normalize_plan_ids(plan_ids: Any) -> list[int]:
    """Accept None | int | 'a,b' | iterable of ints → sorted unique list."""
    if plan_ids is None:
        return []
    if isinstance(plan_ids, (int,)):
        return [int(plan_ids)]
    if isinstance(plan_ids, str):
        return sorted({int(x) for x in plan_ids.replace(" ", "").split(",") if x})
    return sorted({int(x) for x in plan_ids})


def reconcile(
    db: Session,
    org_id: int | None = None,
    plan_ids: Any = None,
    include_non_terminal: bool = False,
    verify_ownership: bool = False,
) -> ReconcileResult:
    """
    Run full bidirectional reconciliation.

    Args:
        db: SQLAlchemy session
        org_id: optional organization filter (None = all orgs)
        plan_ids: optional campaign-plan scope (int / csv str / iterable).
                  Empty/None = every plan of the org (full-DB sweep).
        include_non_terminal: also reconcile in-flight nodes (default False —
                  the gate is defined on terminal states only).
        verify_ownership: additionally re-verify each report's submitter against the
                  mission claimant by reusing the submission-time ownership tooling
                  (``services.report_ownership.ownership_verdict``, p130 IN50-W3-N07).
                  Default False: rows written before that gate existed cannot be
                  judged retroactively, enabling it on the whole history would
                  retro-flag legacy rows and drown the real debt.

    Returns:
        ReconcileResult with four gate categories + informational orphans + KPI
    """
    t0 = time.monotonic()
    result = ReconcileResult()
    scope_plans = _normalize_plan_ids(plan_ids)

    # ── 1. Fetch all terminal campaign_nodes ──────────────────────────
    if include_non_terminal:
        node_filters = ["1=1"]
    else:
        placeholders = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
        node_filters = [f"n.status IN ({placeholders})"]
    node_params: dict[str, Any] = {}
    if org_id is not None:
        node_filters.append("p.organization_id = :org")
        node_params["org"] = org_id
    if scope_plans:
        node_filters.append("n.plan_id IN :plans")

    nodes_sql = f"""
        SELECT n.id, n.plan_id, n.mission_id, n.name, n.status,
               n.metadata, p.organization_id
        FROM campaign_nodes n
        JOIN campaign_plans p ON n.plan_id = p.id
        WHERE {' AND '.join(node_filters)}
        ORDER BY n.id
    """
    stmt = text(nodes_sql)
    if scope_plans:
        stmt = stmt.bindparams(bindparam("plans", expanding=True))
        node_params["plans"] = scope_plans
    nodes = db.execute(stmt, node_params).mappings().all()
    node_list = [dict(r) for r in nodes]
    result.total_terminal_nodes = len(node_list)

    # Build lookup: mission_id -> list of nodes
    mission_to_nodes: dict[int, list[dict]] = {}
    for n in node_list:
        mid = n.get("mission_id")
        if mid is not None:
            mission_to_nodes.setdefault(mid, []).append(n)

    # ── 2. Fetch all reports with mission_id ──────────────────────────
    report_filters = ["r.mission_id IS NOT NULL"]
    report_params: dict[str, Any] = {}
    if org_id is not None:
        report_filters.append("r.organization_id = :org")
        report_params["org"] = org_id

    reports_sql = f"""
        SELECT r.id, r.mission_id, r.organization_id, r.status,
               r.title, r.scenario_key, r.created_by
        FROM tensor_reports r
        WHERE {' AND '.join(report_filters)}
        ORDER BY r.id
    """
    reports = db.execute(text(reports_sql), report_params).mappings().all()
    report_list = [dict(r) for r in reports]
    result.total_reports = len(report_list)

    # Build lookup: mission_id -> list of reports
    mission_to_reports: dict[int, list[dict]] = {}
    for r in report_list:
        mid = r.get("mission_id")
        if mid is not None:
            mission_to_reports.setdefault(mid, []).append(r)

    # ── 3. Forward scan: terminal nodes → reports ─────────────────────
    # Track which missions have at least one report
    missions_with_report: set[int] = set()

    for n in node_list:
        mid = n.get("mission_id")
        if mid is None:
            # Node without mission_id — skip (not reconcilable via mission)
            continue

        has_report = mid in mission_to_reports and len(mission_to_reports[mid]) > 0
        if has_report:
            missions_with_report.add(mid)

        if not has_report:
            entry = ReconcileEntry(
                category="done_no_report" if n["status"] == "done" else "blocked_no_report",
                node_id=n["id"],
                mission_id=mid,
                report_id=None,
                plan_id=n.get("plan_id"),
                node_name=n.get("name"),
                node_status=n["status"],
                detail=f"Node {n['id']} ({n['name']}) status={n['status']} has no report",
            )
            if n["status"] == "done":
                result.done_no_report.append(entry)
            else:
                result.blocked_no_report.append(entry)

    # ── 4. Reverse scan: reports → nodes ──────────────────────────────
    # Phantom = mission_id exists in no mission row at all (fabricated id).
    # Orphan  = mission exists but carries no campaign node (plain mission report):
    #           informational, must not gate.
    suspect_mids = {r["mission_id"] for r in report_list if r["mission_id"] not in mission_to_nodes}
    known_missions: set[int] = set()
    if suspect_mids:
        suspects = sorted(suspect_mids)
        for i in range(0, len(suspects), 500):
            chunk = suspects[i:i + 500]
            try:
                rows = db.execute(
                    text("SELECT id FROM tensor_dev_missions WHERE id IN :ids")
                    .bindparams(bindparam("ids", expanding=True)),
                    {"ids": chunk},
                ).scalars().all()
            except Exception:  # mission table absent in a minimal deployment
                rows = []
            known_missions.update(int(x) for x in rows)

    for r in report_list:
        mid = r["mission_id"]

        if mid not in mission_to_nodes:
            phantom = mid not in known_missions
            result.report_no_node.append(ReconcileEntry(
                category="report_no_node",
                node_id=None,
                mission_id=mid,
                report_id=r["id"],
                plan_id=None,
                node_name=None,
                node_status=None,
                detail=(
                    f"Report {r['id']} cites mission_id={mid} which exists in no mission row "
                    f"(fabricated / cross-tenant id)" if phantom else
                    f"Report {r['id']} belongs to mission {mid} that carries no campaign node"
                ),
            )) if phantom else result.report_orphan_mission.append(ReconcileEntry(
                category="report_orphan_mission",
                node_id=None,
                mission_id=mid,
                report_id=r["id"],
                plan_id=None,
                node_name=None,
                node_status=None,
                detail=f"Report {r['id']} mission {mid} is not part of any campaign DAG",
            ))
        else:
            # Ownership check: report tenant must equal the plan tenant of its node
            nodes_for_mission = mission_to_nodes[mid]
            for n in nodes_for_mission:
                rep_org = r.get("organization_id")
                node_org = n.get("organization_id")
                if rep_org is not None and node_org is not None and int(rep_org) != int(node_org):
                    result.report_hash_mismatch.append(ReconcileEntry(
                        category="report_hash_mismatch",
                        node_id=n["id"],
                        mission_id=mid,
                        report_id=r["id"],
                        plan_id=n.get("plan_id"),
                        node_name=n.get("name"),
                        node_status=n["status"],
                        detail=(
                            f"Ownership mismatch: report org={rep_org} vs node org={node_org} "
                            f"(mission_id={mid})"
                        ),
                    ))
                    continue
                meta = n.get("metadata") or "{}"
                try:
                    meta_dict = json.loads(meta) if isinstance(meta, str) else meta
                except (json.JSONDecodeError, TypeError):
                    meta_dict = {}

                expected_hash = _node_mission_hash(mid)
                stored_hash = meta_dict.get("mission_hash", "")
                if stored_hash and stored_hash != expected_hash:
                    result.report_hash_mismatch.append(ReconcileEntry(
                        category="report_hash_mismatch",
                        node_id=n["id"],
                        mission_id=mid,
                        report_id=r["id"],
                        plan_id=n.get("plan_id"),
                        node_name=n.get("name"),
                        node_status=n["status"],
                        detail=f"Hash mismatch for mission_id={mid}: expected={expected_hash}, stored={stored_hash}",
                    ))

    # ── 4b. Ownership re-verification (reuse p130 tooling, no rewrite) ─
    ownership_report: dict[str, Any] | None = None
    if verify_ownership:
        ownership_report = _reverify_ownership(db, result, report_list, mission_to_nodes)

    # ── 5. Compute KPI ────────────────────────────────────────────────
    # Coverage is a NODE ratio: done_with_report / (done_with_report + done_no_report).
    # The earlier form divided distinct-missions-with-report by nodes-with-mission,
    # i.e. numerator and denominator in different units — it under-reported whenever
    # one mission carried several terminal nodes. Nodes sharing a mission are counted
    # per node now; mission-level counts stay in the KPI for traceability.
    total_nodes_with_mission = sum(1 for n in node_list if n.get("mission_id") is not None)
    nodes_with_report = sum(
        1 for n in node_list
        if n.get("mission_id") is not None and n["mission_id"] in mission_to_reports
    )
    done_with_report = sum(
        1 for n in node_list
        if n.get("mission_id") is not None and n["status"] == "done"
        and n["mission_id"] in mission_to_reports
    )
    blocked_with_report = sum(
        1 for n in node_list
        if n.get("mission_id") is not None and n["status"] == "blocked"
        and n["mission_id"] in mission_to_reports
    )
    # Nodes with no mission_id cannot be joined to a report at all — they are
    # counted separately instead of silently shrinking the denominator.
    nodes_without_mission = len(node_list) - total_nodes_with_mission
    if total_nodes_with_mission > 0:
        result.report_coverage_rate = round(
            nodes_with_report / total_nodes_with_mission * 100, 2
        )
    else:
        result.report_coverage_rate = 100.0  # vacuously true
    done_terminal = done_with_report + len(result.done_no_report)
    blocked_terminal = blocked_with_report + len(result.blocked_no_report)
    done_rate = round(done_with_report / done_terminal * 100, 2) if done_terminal else 100.0
    blocked_rate = (
        round(blocked_with_report / blocked_terminal * 100, 2) if blocked_terminal else 100.0
    )

    result.kpi = {
        "report_coverage_rate": result.report_coverage_rate,
        "coverage_basis": "node",
        "nodes_with_report": nodes_with_report,
        # spec form: done_with_report / (done_with_report + done_no_report)
        "done_with_report": done_with_report,
        "done_no_report": len(result.done_no_report),
        "report_coverage_rate_done": done_rate,
        "blocked_with_report": blocked_with_report,
        "blocked_no_report": len(result.blocked_no_report),
        "report_coverage_rate_blocked": blocked_rate,
        "nodes_without_mission": nodes_without_mission,
        "missions_with_report": len(missions_with_report),
        "total_terminal_nodes": result.total_terminal_nodes,
        "total_reports": result.total_reports,
        "done_no_report_count": len(result.done_no_report),
        "blocked_no_report_count": len(result.blocked_no_report),
        "report_no_node_count": len(result.report_no_node),
        "report_hash_mismatch_count": len(result.report_hash_mismatch),
        "report_orphan_mission_count": len(result.report_orphan_mission),
        "total_nodes_with_mission": total_nodes_with_mission,
    }
    if ownership_report is not None:
        result.kpi["ownership"] = ownership_report
    result.scope = {
        "organization_id": org_id,
        "plan_ids": scope_plans,
        "statuses": list(TERMINAL_STATUSES) if not include_non_terminal else "all",
        "include_non_terminal": include_non_terminal,
    }

    result.elapsed_seconds = time.monotonic() - t0
    return result


def _reverify_ownership(db: Session, result: ReconcileResult,
                        report_list: list[dict],
                        mission_to_nodes: dict[int, list[dict]]) -> dict[str, Any]:
    """
    Re-check report↔mission attribution with the p130 submission-time tooling.

    The judgement (whose token may attach a report to a mission) lives in
    ``services/report_ownership.ownership_verdict`` — this module only wires it to
    already-persisted rows, it does not re-implement the rule.
    """
    try:
        from services.report_ownership import ownership_verdict
    except Exception:  # pragma: no cover - tooling unavailable in a minimal build
        return {"available": False, "checked": 0, "flagged": 0}

    mids = sorted({r["mission_id"] for r in report_list
                   if r["mission_id"] in mission_to_nodes})
    missions: dict[int, dict] = {}
    for i in range(0, len(mids), 500):
        chunk = mids[i:i + 500]
        try:
            rows = db.execute(
                text("SELECT id, organization_id, status, claimed_by, claimed_user_id "
                     "FROM tensor_dev_missions WHERE id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": chunk},
            ).mappings().all()
        except Exception:  # older schema without claimed_user_id
            db.rollback()
            rows = db.execute(
                text("SELECT id, organization_id, status, claimed_by, "
                     "NULL AS claimed_user_id FROM tensor_dev_missions WHERE id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": chunk},
            ).mappings().all()
        missions.update({int(r["id"]): dict(r) for r in rows})

    checked = flagged = 0
    for r in report_list:
        mid = r["mission_id"]
        if mid not in missions:
            continue
        checked += 1
        verdict = ownership_verdict(
            {"id": r.get("created_by")}, missions[mid],
            mission_id=int(mid), org_id=r.get("organization_id"),
        )
        if not verdict.get("ok"):
            node = (mission_to_nodes.get(mid) or [{}])[0]
            flagged += 1
            result.report_hash_mismatch.append(ReconcileEntry(
                category="report_hash_mismatch",
                node_id=node.get("id"),
                mission_id=mid,
                report_id=r["id"],
                plan_id=node.get("plan_id"),
                node_name=node.get("name"),
                node_status=node.get("status"),
                detail=(f"Ownership rejected by p130 tooling: {verdict.get('code')} "
                        f"(report by user {r.get('created_by')}, mission owner "
                        f"{verdict.get('owner')})"),
            ))
    return {"available": True, "checked": checked, "flagged": flagged}


# ───────────────────────── Gold standard evaluation ──────────────────

def evaluate_gold_standard(
    db: Session,
    gold_node_ids: list[int],
    org_id: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate reconciliation against a gold standard of known zero-report nodes.

    Args:
        db: SQLAlchemy session
        gold_node_ids: list of node IDs known to have no reports (ground truth)
        org_id: optional organization filter

    Returns:
        Evaluation dict with recall, precision, false_positives
    """
    result = reconcile(db, org_id)

    # All missing-report entries (done + blocked)
    all_missing = result.done_no_report + result.blocked_no_report
    detected_ids = {e.node_id for e in all_missing if e.node_id is not None}
    gold_set = set(gold_node_ids)

    true_positives = gold_set & detected_ids
    false_negatives = gold_set - detected_ids
    false_positives = detected_ids - gold_set

    recall = len(true_positives) / len(gold_set) if gold_set else 1.0
    precision = len(true_positives) / len(detected_ids) if detected_ids else 1.0

    return {
        "gold_total": len(gold_set),
        "detected_total": len(detected_ids),
        "true_positives": sorted(true_positives),
        "false_negatives": sorted(false_negatives),
        "false_positives": sorted(false_positives),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "recall_pct": f"{recall * 100:.1f}%",
        "elapsed_seconds": round(result.elapsed_seconds, 3),
    }


# ─────────────────── Negative control injection helpers ──────────────

def inject_synthetic_done_node(db: Session, plan_id: int, mission_id: int, name: str = "synth_test_node") -> int:
    """
    Inject a synthetic done node for negative control testing.
    Returns the new node ID.
    """
    row = db.execute(
        text("""
            INSERT INTO campaign_nodes (plan_id, mission_id, name, node_type, status, metadata)
            VALUES (:pid, :mid, :name, 'task', 'done', '{}')
            RETURNING id
        """),
        {"pid": plan_id, "mid": mission_id, "name": name},
    ).mappings().first()
    db.commit()
    return row["id"]


def inject_synthetic_report(db: Session, mission_id: int, org_id: int = 1, title: str = "synth_test_report") -> int:
    """
    Inject a synthetic report with a fake mission_id for negative control testing.
    Returns the new report ID.
    """
    row = db.execute(
        text("""
            INSERT INTO tensor_reports (organization_id, title, summary, mission_id, status, markdown, created_by)
            VALUES (:org, :title, 'synthetic', :mid, 'published', '# synthetic report', 1)
            RETURNING id
        """),
        {"org": org_id, "title": title, "mid": mission_id},
    ).mappings().first()
    db.commit()
    return row["id"]


def cleanup_synthetic(db: Session, node_id: int | None = None, report_id: int | None = None) -> None:
    """Clean up synthetic test data."""
    if node_id is not None:
        db.execute(text("DELETE FROM campaign_nodes WHERE id = :nid"), {"nid": node_id})
    if report_id is not None:
        db.execute(text("DELETE FROM tensor_reports WHERE id = :rid"), {"rid": report_id})
    db.commit()


# ─────────────── Closure: idempotent supplement dispatch ────────────

QUEUE_TABLE = "tensor_report_supplement_queue"

# Portable DDL: identical text is shipped as migrations/112_*.sql so that
# "migration ⇔ code" never drifts (Postgres and SQLite both accept it).
# IN50-W4-N10 (SLA): migrations/113_*.sql adds the four SLA columns below as
# additive ALTERs, so 112 (base table) + 113 (SLA) == this DDL for fresh installs.
QUEUE_DDL = f"""
CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
    dispatch_id     TEXT PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    dispatch_key    TEXT NOT NULL,
    dispatch_type   TEXT NOT NULL DEFAULT 'supplement_report',
    mission_id      BIGINT,
    node_id         BIGINT,
    plan_id         BIGINT,
    priority        TEXT NOT NULL DEFAULT 'normal',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    note            TEXT NOT NULL DEFAULT '',
    sla_hours       INTEGER NOT NULL DEFAULT 72,
    due_at          TIMESTAMP,
    escalation_level INTEGER NOT NULL DEFAULT 0,
    escalated_at    TIMESTAMP,
    UNIQUE (organization_id, dispatch_key)
)
"""

# SLA for a supplement report: 72h from ticket creation to a report appearing.
SLA_HOURS_DEFAULT = 72

# Additive columns brought in by migration 113. Applied one-by-one through
# tiny transactions because SQLite has no "ADD COLUMN IF NOT EXISTS"; a
# duplicate-column error is the idempotent no-op path on both engines.
SLA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sla_hours", "INTEGER NOT NULL DEFAULT 72"),
    ("due_at", "TIMESTAMP"),
    ("escalation_level", "INTEGER NOT NULL DEFAULT 0"),
    ("escalated_at", "TIMESTAMP"),
)


def _utcnow() -> datetime:
    """Naive UTC now — TIMESTAMP columns are naive on both Postgres and SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: Any) -> datetime | None:
    """Coerce a DB timestamp (naive / aware / ISO string) to naive UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

KPI_METRIC = "report_coverage_rate"


def dispatch_key_for(mission_id: int) -> str:
    """Idempotency key: one supplement ticket per mission, forever."""
    return f"supplement_report:{int(mission_id)}"


def dispatch_id_for(org_id: int, mission_id: int) -> str:
    """Deterministic dispatch id (no sequence needed → portable)."""
    return hashlib.sha256(f"supplement:{org_id}:{int(mission_id)}".encode()).hexdigest()[:32]


def ensure_supplement_queue(db: Session) -> None:
    """Create the dispatch outbox if the migration has not run yet.

    Also widens an outbox created by migration 112 (no SLA columns) to the
    migration-113 shape, so a deployment that only ran the old migration keeps
    working without a manual ALTER.
    """
    db.execute(text(QUEUE_DDL))
    db.commit()
    for column, decl in SLA_COLUMNS:
        try:
            db.execute(text(f"ALTER TABLE {QUEUE_TABLE} ADD COLUMN {column} {decl}"))
            db.commit()
        except Exception:  # column already present (or engine refused) → no-op
            db.rollback()


def _insert_dispatch(
    db: Session,
    org_id: int,
    entry: ReconcileEntry,
    priority: str,
    sla_hours: int = SLA_HOURS_DEFAULT,
    now: datetime | None = None,
) -> bool:
    """Insert one ticket; returns True if newly created, False if already queued.

    The ticket carries its own SLA deadline (``due_at = created + sla_hours``),
    so lateness is a property of the row and survives restarts — the scheduler
    never has to remember when a ticket was filed.
    """
    key = dispatch_key_for(entry.mission_id)
    did = dispatch_id_for(org_id, entry.mission_id)
    existing = db.execute(
        text(f"SELECT dispatch_id FROM {QUEUE_TABLE} "
             "WHERE organization_id = :org AND dispatch_key = :key"),
        {"org": org_id, "key": key},
    ).first()
    if existing:
        return False
    created = (now or _utcnow())
    due = created + timedelta(hours=int(sla_hours))
    try:
        db.execute(
            text(f"""
                INSERT INTO {QUEUE_TABLE}
                    (dispatch_id, organization_id, dispatch_key, dispatch_type,
                     mission_id, node_id, plan_id, priority, status, note,
                     sla_hours, due_at, escalation_level)
                VALUES (:did, :org, :key, 'supplement_report',
                        :mid, :nid, :pid, :prio, 'pending', :note,
                        :sla, :due, 0)
            """),
            {
                "did": did, "org": org_id, "key": key, "mid": entry.mission_id,
                "nid": entry.node_id, "pid": entry.plan_id, "prio": priority,
                "sla": int(sla_hours), "due": due.isoformat(sep=" "),
                "note": f"{entry.category}: node {entry.node_id} ({entry.node_status}) "
                        f"terminal without report; sla={int(sla_hours)}h due={due.isoformat()}",
            },
        )
        db.commit()
        return True
    except Exception:
        # Either a concurrent dispatcher won the UNIQUE(org, dispatch_key) race,
        # or the insert genuinely failed. Distinguish the two: a silent False on a
        # real error would hide lost tickets, so re-verify before swallowing.
        db.rollback()
        still_absent = db.execute(
            text(f"SELECT dispatch_id FROM {QUEUE_TABLE} "
                 "WHERE organization_id = :org AND dispatch_key = :key"),
            {"org": org_id, "key": key},
        ).first() is None
        if still_absent:
            raise
        return False


def dispatch_missing_reports(
    db: Session,
    org_id: int | None = None,
    plan_ids: Any = None,
    priority: str = "normal",
    dry_run: bool = False,
    sla_hours: int = SLA_HOURS_DEFAULT,
) -> dict[str, Any]:
    """
    Auto-generate one supplement ticket per terminal node that has no report.

    Idempotency: dispatch_key = "supplement_report:<mission_id>", enforced by a
    UNIQUE(organization_id, dispatch_key) constraint — a second run creates zero
    new rows (J3).

    Every ticket is stamped with ``due_at = created_at + sla_hours`` (default
    72h) and overdue pending tickets are escalated in the same pass, so one call
    both files work and chases what is late.

    Returns a summary dict with the clickable node list the gate must publish.
    """
    result = reconcile(db, org_id, plan_ids=plan_ids)
    effective_org = org_id if org_id is not None else 1
    candidates = [e for e in (result.done_no_report + result.blocked_no_report) if e.mission_id is not None]
    summary = {
        "dry_run": dry_run,
        "organization_id": effective_org,
        "plan_ids": result.scope.get("plan_ids", []),
        "candidates": len(candidates),
        "dispatched": 0,
        "already_queued": 0,
        "resolved": 0,
        "sla_hours": int(sla_hours),
        "tickets": [],
    }
    if dry_run:
        summary["tickets"] = [
            {"mission_id": e.mission_id, "node_id": e.node_id, "plan_id": e.plan_id,
             "category": e.category, "would_dispatch": True,
             "due_at": (_utcnow() + timedelta(hours=int(sla_hours))).isoformat(sep=" ")}
            for e in candidates
        ]
        summary["sla"] = {"sla_hours": int(sla_hours), "dry_run": True}
        return summary

    ensure_supplement_queue(db)
    for e in candidates:
        created = _insert_dispatch(db, effective_org, e, priority, sla_hours=sla_hours)
        summary["dispatched" if created else "already_queued"] += 1
        summary["tickets"].append({
            "mission_id": e.mission_id,
            "node_id": e.node_id,
            "plan_id": e.plan_id,
            "category": e.category,
            "dispatch_id": dispatch_id_for(effective_org, e.mission_id),
            "new": created,
        })

    summary["resolved"] = close_resolved_dispatches(db, effective_org)["resolved"]
    summary["sla"] = {
        **sla_report(db, effective_org),
        "escalated": escalate_overdue(db, effective_org)["escalated"],
    }
    return summary


def close_resolved_dispatches(db: Session, org_id: int | None = None) -> dict[str, Any]:
    """
    Auto-消账: pending tickets whose mission has since produced a report go to
    'resolved'. The ledger is append-only (status transition, not delete).
    """
    org = org_id if org_id is not None else 1
    try:
        rows = db.execute(
            text(f"""
                SELECT q.dispatch_id, q.mission_id
                FROM {QUEUE_TABLE} q
                WHERE q.organization_id = :org AND q.status = 'pending'
                  AND EXISTS (SELECT 1 FROM tensor_reports r WHERE r.mission_id = q.mission_id)
            """),
            {"org": org},
        ).mappings().all()
    except Exception:
        db.rollback()
        return {"resolved": 0, "dispatch_ids": []}
    ids = [r["dispatch_id"] for r in rows]
    if ids:
        db.execute(
            text(f"UPDATE {QUEUE_TABLE} SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP "
                 f"WHERE dispatch_id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
        db.commit()
    return {"resolved": len(ids), "dispatch_ids": ids}


# ─────────────────────── SLA: lateness accounting ───────────────────

SLA_BUCKETS: tuple[tuple[str, float], ...] = (
    ("<=24h", 24.0), ("24-48h", 48.0), ("48-72h", 72.0), (">72h", float("inf")),
)


def _load_pending_tickets(db: Session, org_id: int) -> list[dict[str, Any]]:
    """Pending supplement tickets with their deadline, robust to a 112-era table."""
    try:
        rows = db.execute(
            text(f"""
                SELECT dispatch_id, mission_id, node_id, plan_id, priority,
                       status, created_at, due_at, resolved_at, sla_hours,
                       escalation_level, escalated_at
                FROM {QUEUE_TABLE}
                WHERE organization_id = :org
            """),
            {"org": org_id},
        ).mappings().all()
    except Exception:  # outbox not created yet
        db.rollback()
        return []
    return [dict(r) for r in rows]


def sla_report(
    db: Session,
    org_id: int | None = None,
    now: datetime | None = None,
    sla_hours: int = SLA_HOURS_DEFAULT,
) -> dict[str, Any]:
    """
    Lateness ledger over the supplement outbox.

    A ticket's deadline is ``due_at`` when the row carries one; rows written
    before migration 113 fall back to ``created_at + sla_hours`` so the SLA clock
    starts at filing time for legacy rows too (no silent exemption).

    Returns counts, an aging histogram over *pending* tickets, the on-time rate
    over *resolved* tickets, and the overdue ticket list (clickable).
    """
    org = org_id if org_id is not None else 1
    moment = now or _utcnow()
    tickets = _load_pending_tickets(db, org)

    pending: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for t in tickets:
        created = _as_naive_utc(t.get("created_at")) or moment
        due = _as_naive_utc(t.get("due_at")) or (
            created + timedelta(hours=int(t.get("sla_hours") or sla_hours))
        )
        rec = {
            "dispatch_id": t["dispatch_id"],
            "mission_id": t.get("mission_id"),
            "node_id": t.get("node_id"),
            "plan_id": t.get("plan_id"),
            "priority": t.get("priority"),
            "status": t.get("status"),
            "created_at": created.isoformat(sep=" "),
            "due_at": due.isoformat(sep=" "),
            "sla_hours": int(t.get("sla_hours") or sla_hours),
            "escalation_level": int(t.get("escalation_level") or 0),
        }
        if str(t.get("status") or "") == "pending":
            rec["age_hours"] = round((moment - created).total_seconds() / 3600.0, 2)
            rec["hours_to_due"] = round((due - moment).total_seconds() / 3600.0, 2)
            rec["overdue"] = rec["hours_to_due"] < 0
            pending.append(rec)
        else:
            resolved_at = _as_naive_utc(t.get("resolved_at"))
            rec["resolved_at"] = resolved_at.isoformat(sep=" ") if resolved_at else None
            rec["on_time"] = bool(resolved_at and resolved_at <= due)
            resolved.append(rec)

    overdue = [t for t in pending if t["overdue"]]
    due_soon = [t for t in pending if 0 <= t["hours_to_due"] <= 12]
    aging = {label: 0 for label, _ in SLA_BUCKETS}
    for t in pending:
        for label, upper in SLA_BUCKETS:
            if t["age_hours"] <= upper:
                aging[label] += 1
                break

    on_time_rate = (
        round(sum(1 for t in resolved if t["on_time"]) / len(resolved) * 100, 2)
        if resolved else None
    )
    return {
        "sla_hours": int(sla_hours),
        "total_tickets": len(tickets),
        "pending": len(pending),
        "resolved": len(resolved),
        "overdue": len(overdue),
        "due_within_12h": len(due_soon),
        "on_time_rate": on_time_rate,
        "aging_hours": aging,
        "max_age_hours": max((t["age_hours"] for t in pending), default=0.0),
        "overdue_tickets": sorted(overdue, key=lambda t: t["hours_to_due"]),
    }


def escalate_overdue(
    db: Session,
    org_id: int | None = None,
    now: datetime | None = None,
    sla_hours: int = SLA_HOURS_DEFAULT,
    max_level: int = 3,
) -> dict[str, Any]:
    """
    Bump every overdue pending ticket that has not been bumped yet.

    Idempotent: the guard is ``escalated_at IS NULL``, so a second run in the
    same overdue window touches zero rows (no level inflation, no event storm).
    Overdue at/above ``max_level`` is left alone but still reported.
    """
    org = org_id if org_id is not None else 1
    moment = now or _utcnow()
    report = sla_report(db, org, now=moment, sla_hours=sla_hours)
    ids: list[str] = []
    for t in report["overdue_tickets"]:
        if t["escalation_level"] >= max_level:
            continue
        if t["escalation_level"] > 0:
            # already escalated once for this ticket; the row is only re-armed
            # when a human clears escalated_at, so do not double-bump.
            continue
        ids.append(t["dispatch_id"])
    if ids:
        db.execute(
            text(f"UPDATE {QUEUE_TABLE} SET priority = 'urgent', "
                 f"escalation_level = escalation_level + 1, escalated_at = :ts "
                 f"WHERE organization_id = :org AND status = 'pending' "
                 f"AND dispatch_id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ts": moment.isoformat(sep=" "), "org": org, "ids": ids},
        )
        db.commit()
    return {"escalated": len(ids), "dispatch_ids": ids}


# ─────────────────── KPI: snapshot + trend readback ─────────────────

def gate_status(result: ReconcileResult, sla: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Gate verdict. A red gate must always publish a clickable node list — never
    a silent red (curl-able /tensor/campaigns/plans/{plan_id}/nodes).
    """
    debt = result.done_no_report + result.blocked_no_report
    clickable = [
        {"node_id": e.node_id, "mission_id": e.mission_id, "plan_id": e.plan_id,
         "node_name": e.node_name, "status": e.node_status, "category": e.category,
         "href": f"/tensor/campaigns/plans/{e.plan_id}/nodes" if e.plan_id else None}
        for e in debt
    ]
    forged = result.report_no_node + result.report_hash_mismatch
    status = "GREEN" if not debt and not forged else "RED"
    verdict = {
        "status": status,
        "debt_nodes": len(debt),
        "done_no_report": len(result.done_no_report),
        "blocked_no_report": len(result.blocked_no_report),
        "forged_reports": len(forged),
        "clickable": clickable,
    }
    if sla is not None:
        # SLA lateness colours the verdict amber-ish but must not mask the debt
        # count: debt drives RED, overdue drives "SLA_BREACH" in the same object.
        verdict["sla"] = {
            "sla_hours": sla.get("sla_hours"),
            "pending": sla.get("pending"),
            "overdue": sla.get("overdue"),
            "due_within_12h": sla.get("due_within_12h"),
            "on_time_rate": sla.get("on_time_rate"),
            "aging_hours": sla.get("aging_hours"),
            "breach": bool(sla.get("overdue")),
        }
    return verdict


def snapshot_coverage(
    db: Session,
    org_id: int,
    result: ReconcileResult | None = None,
    plan_ids: Any = None,
    notes: str = "",
) -> dict[str, Any]:
    """Persist report_coverage_rate (%) into tensor_guide_tracker (J6)."""
    if result is None:
        result = reconcile(db, org_id, plan_ids=plan_ids)
    gate = gate_status(result, sla=sla_report(db, org_id))
    note = (f"unit=percent;basis={result.kpi.get('coverage_basis', 'node')};"
            f"scope=plans:{result.scope.get('plan_ids') or 'ALL'}; "
            f"debt={gate['debt_nodes']} (done={gate['done_no_report']}, "
            f"blocked={gate['blocked_no_report']}); forged={gate['forged_reports']}; "
            f"gate={gate['status']}")
    if notes:
        note = f"{notes}|{note}"
    db.execute(
        text("""
            INSERT INTO tensor_guide_tracker
                (organization_id, metric_key, metric_value, target_value, notes)
            VALUES (:org, :key, :val, 100.0, :note)
        """),
        {"org": org_id, "key": KPI_METRIC, "val": float(result.report_coverage_rate), "note": note},
    )
    db.commit()
    row = db.execute(
        text("SELECT id, metric_value, snapshot_at FROM tensor_guide_tracker "
             "WHERE organization_id = :org AND metric_key = :key "
             "ORDER BY snapshot_at DESC LIMIT 1"),
        {"org": org_id, "key": KPI_METRIC},
    ).mappings().first()
    return {
        "snapshot_id": row["id"] if row else None,
        "metric_key": KPI_METRIC,
        "metric_value": float(row["metric_value"]) if row else None,
        "target_value": 100.0,
        "snapshot_at": str(row["snapshot_at"]) if row else None,
        "coverage_rate": result.report_coverage_rate,
        "debt_nodes": gate["debt_nodes"],
        "gate": gate["status"],
        "sla": gate.get("sla"),
        "clickable": gate["clickable"],
    }


def _to_percent(value: Any, target: Any) -> tuple[float | None, str]:
    """
    Put a stored KPI point on the percent scale this module writes.

    Both codings exist in the historical series: percent-coded rows (value 0..100
    with target 100) and ratio-coded rows written by early wave scripts (value
    0..1 with target 1). Plotting them raw mixes units — a 0.73 next to a 76.19.
    A row is read as ratio-coded only when *both* its value and target are <= 1,
    which cannot happen for a genuine percent point below 1% with target 100.
    """
    if value is None:
        return None, "unknown"
    val = float(value)
    tgt = float(target) if target is not None else None
    if abs(val) <= 1.0 and (tgt is not None and abs(tgt) <= 1.0):
        return round(val * 100, 2), "ratio->percent"
    return val, "percent"


def coverage_trend(db: Session, org_id: int, limit: int = 60) -> dict[str, Any]:
    """Read back the KPI series for the trend line (J6), normalized to percent."""
    rows = db.execute(
        text("SELECT metric_value, target_value, snapshot_at, notes FROM tensor_guide_tracker "
             "WHERE organization_id = :org AND metric_key = :key "
             # id breaks ties: several snapshots in the same second must keep
             # their insertion order or a trend line scrambles them.
             "ORDER BY snapshot_at DESC, id DESC LIMIT :lim"),
        {"org": org_id, "key": KPI_METRIC, "lim": int(limit)},
    ).mappings().all()
    series = []
    for r in reversed(rows):
        value, scale = _to_percent(r["metric_value"], r["target_value"])
        series.append({
            "value": value,
            "raw_value": float(r["metric_value"]) if r["metric_value"] is not None else None,
            "scale": scale,
            "unit": "percent",
            "target": 100.0,
            "at": str(r["snapshot_at"]), "notes": r["notes"],
        })
    return {"metric_key": KPI_METRIC, "points": len(series), "unit": "percent",
            "series": series}
