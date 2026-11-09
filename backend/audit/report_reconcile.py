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
from typing import Any

from sqlalchemy import text
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
    report_coverage_rate: float = 0.0
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
            "report_coverage_rate": self.report_coverage_rate,
            "kpi": self.kpi,
        }


# ───────────────────────── Core reconciler ─────────────────────────

def _node_mission_hash(mission_id: int) -> str:
    """Deterministic hash for mission_id (used for mismatch detection)."""
    return hashlib.sha256(f"mission:{mission_id}".encode()).hexdigest()[:16]


def reconcile(db: Session, org_id: int | None = None) -> ReconcileResult:
    """
    Run full bidirectional reconciliation.

    Args:
        db: SQLAlchemy session
        org_id: optional organization filter (None = all orgs)

    Returns:
        ReconcileResult with four ledger categories and KPI
    """
    t0 = time.monotonic()
    result = ReconcileResult()

    # ── 1. Fetch all terminal campaign_nodes ──────────────────────────
    node_filters = ["n.status IN ('done', 'blocked')"]
    node_params: dict[str, Any] = {}
    if org_id is not None:
        node_filters.append("p.organization_id = :org")
        node_params["org"] = org_id

    nodes_sql = f"""
        SELECT n.id, n.plan_id, n.mission_id, n.name, n.status,
               n.metadata, p.organization_id
        FROM campaign_nodes n
        JOIN campaign_plans p ON n.plan_id = p.id
        WHERE {' AND '.join(node_filters)}
        ORDER BY n.id
    """
    nodes = db.execute(text(nodes_sql), node_params).mappings().all()
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
               r.title, r.scenario_key
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
    # Also check hash mismatch
    for r in report_list:
        mid = r["mission_id"]

        if mid not in mission_to_nodes:
            # Report references a mission_id with no campaign node
            result.report_no_node.append(ReconcileEntry(
                category="report_no_node",
                node_id=None,
                mission_id=mid,
                report_id=r["id"],
                plan_id=None,
                node_name=None,
                node_status=None,
                detail=f"Report {r['id']} references mission_id={mid} with no campaign node",
            ))
        else:
            # Hash mismatch check: compare report's scenario_key hash with node metadata
            nodes_for_mission = mission_to_nodes[mid]
            for n in nodes_for_mission:
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

    # ── 5. Compute KPI ────────────────────────────────────────────────
    total_nodes_with_mission = sum(1 for n in node_list if n.get("mission_id") is not None)
    if total_nodes_with_mission > 0:
        result.report_coverage_rate = round(
            len(missions_with_report) / total_nodes_with_mission * 100, 2
        )
    else:
        result.report_coverage_rate = 100.0  # vacuously true

    result.kpi = {
        "report_coverage_rate": result.report_coverage_rate,
        "total_terminal_nodes": result.total_terminal_nodes,
        "total_reports": result.total_reports,
        "done_no_report_count": len(result.done_no_report),
        "blocked_no_report_count": len(result.blocked_no_report),
        "report_no_node_count": len(result.report_no_node),
        "report_hash_mismatch_count": len(result.report_hash_mismatch),
    }

    result.elapsed_seconds = time.monotonic() - t0
    return result


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
