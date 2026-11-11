"""Campaign-node status vs mission final-state drift detection (p338w3-n07).

A campaign DAG node can sit at ``blocked`` while the mission it points at is
already merged and finished.  Nothing in the node row changes, so the node is
invisible to routing: it looks actionable, or it looks stuck, but never
"done".  This module makes that class of drift explicit and convergent.

Mapping (``expected_node_status``)
----------------------------------
==========================  =====================================
mission state               expected node status
==========================  =====================================
done/completed/succeeded,   ``done``
or ``merged: true``
failed/error/cancelled      ``failed``
running/in_progress/active  ``in-flight``
pending/queued              ``pending``
blocked                     ``blocked``
unknown / missing mission   ``None`` (never guessed)
==========================  =====================================

Drift = observed node status != expected status for a node that has a mission.
A missing mission is not drift but an ``ORPHAN`` finding (visible, not
converged).  When ``auto_converge`` is on, the detector emits the corrective
status per node; the caller applies it.  The plan-level drift ratio drives an
ALERT when it exceeds ``alert_threshold``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .evidence import canonical_json, digest_json

NODE_STATUSES = ("pending", "in-flight", "done", "blocked", "failed", "orphan")

DONE_STATES = {"done", "completed", "complete", "succeeded", "success", "merged", "finished"}
FAILED_STATES = {"failed", "failure", "error", "cancelled", "canceled", "aborted", "timeout"}
RUNNING_STATES = {"running", "in-flight", "in_flight", "in_progress", "in-progress",
                  "active", "claimed", "started"}
PENDING_STATES = {"pending", "queued", "new", "created"}
BLOCKED_STATES = {"blocked", "on_hold", "on-hold", "waiting"}


@dataclass
class NodeState:
    node_id: Any
    status: str
    mission_id: Optional[Any] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"node_id": self.node_id, "status": self.status,
                "mission_id": self.mission_id}


@dataclass
class MissionState:
    mission_id: Any
    status: str = ""
    result: Dict[str, Any] = field(default_factory=dict)

    def merged(self) -> bool:
        result = self.result or {}
        return bool(result.get("merged")) or result.get("merge_status") in {"merged", "done"}

    def effective_status(self) -> str:
        result = self.result or {}
        for key in ("status", "state", "result", "verdict"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return (self.status or "").strip().lower()

    def as_dict(self) -> Dict[str, Any]:
        return {"mission_id": self.mission_id, "status": self.status,
                "result": dict(self.result or {})}


def expected_node_status(mission: MissionState) -> Optional[str]:
    """Map a mission final state to the node status it implies (None = unknown)."""
    state = mission.effective_status()
    if mission.merged() or state in DONE_STATES:
        return "done"
    if state in FAILED_STATES:
        return "failed"
    if state in RUNNING_STATES:
        return "in-flight"
    if state in PENDING_STATES:
        return "pending"
    if state in BLOCKED_STATES:
        return "blocked"
    return None


@dataclass
class DriftFinding:
    node_id: Any
    mission_id: Any
    observed: str
    expected: str
    kind: str = "STATUS_DRIFT"
    route: str = ""
    reason: str = ""
    merged_but_blocked: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "mission_id": self.mission_id,
            "observed": self.observed,
            "expected": self.expected,
            "kind": self.kind,
            "route": self.route,
            "reason": self.reason,
            "merged_but_blocked": self.merged_but_blocked,
        }


@dataclass
class DriftReport:
    findings: List[DriftFinding] = field(default_factory=list)
    orphans: List[Dict[str, Any]] = field(default_factory=list)
    updates: List[Dict[str, Any]] = field(default_factory=list)
    scanned: int = 0
    drift_ratio: float = 0.0
    alert: bool = False
    digest: str = ""

    @property
    def detected(self) -> int:
        return len(self.findings)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned,
            "detected": self.detected,
            "converged": len(self.updates),
            "orphans": list(self.orphans),
            "drift_ratio": round(self.drift_ratio, 6),
            "alert": self.alert,
            "findings": [f.as_dict() for f in self.findings],
            "updates": list(self.updates),
            "digest": self.digest,
        }


class DriftDetector:
    def __init__(self, *, auto_converge: bool = True, alert_threshold: float = 0.3):
        if not (0.0 <= alert_threshold <= 1.0):
            raise ValueError("alert_threshold must be in [0, 1]")
        self.auto_converge = auto_converge
        self.alert_threshold = alert_threshold

    def scan(self, nodes: Iterable[NodeState],
             missions: Iterable[MissionState]) -> DriftReport:
        mission_by_id = {str(m.mission_id): m for m in missions}
        nodes = list(nodes)
        report = DriftReport(scanned=len(nodes))

        for node in nodes:
            if node.mission_id is None or str(node.mission_id) not in mission_by_id:
                report.orphans.append({
                    "node_id": node.node_id,
                    "status": node.status,
                    "mission_id": node.mission_id,
                    "reason": "no mission row for node",
                })
                continue
            mission = mission_by_id[str(node.mission_id)]
            expected = expected_node_status(mission)
            if expected is None:
                report.orphans.append({
                    "node_id": node.node_id,
                    "status": node.status,
                    "mission_id": node.mission_id,
                    "reason": f"unmapped mission state {mission.effective_status()!r}",
                })
                continue
            if node.status == expected:
                continue
            merged_blocked = (node.status == "blocked"
                              and expected == "done" and mission.merged())
            finding = DriftFinding(
                node_id=node.node_id,
                mission_id=node.mission_id,
                observed=node.status,
                expected=expected,
                route=f"converge:{expected}",
                reason=("node blocked while mission already merged"
                        if merged_blocked else
                        f"mission state {mission.effective_status()!r} implies {expected}"),
                merged_but_blocked=merged_blocked,
            )
            report.findings.append(finding)
            if self.auto_converge:
                report.updates.append({
                    "node_id": node.node_id,
                    "mission_id": node.mission_id,
                    "before": node.status,
                    "after": expected,
                    "route": finding.route,
                })

        report.drift_ratio = (report.detected / report.scanned) if report.scanned else 0.0
        report.alert = report.drift_ratio > self.alert_threshold
        report.digest = digest_json({
            "findings": [f.as_dict() for f in report.findings],
            "updates": report.updates,
            "orphans": report.orphans,
        })
        return report


def converge_states(nodes: Sequence[NodeState], missions: Sequence[MissionState],
                    *, auto_converge: bool = True,
                    alert_threshold: float = 0.3) -> Dict[str, Any]:
    """One-shot helper: scan, then report the post-convergence node statuses."""
    report = DriftDetector(auto_converge=auto_converge,
                           alert_threshold=alert_threshold).scan(nodes, missions)
    after = {str(n.node_id): n.status for n in nodes}
    for update in report.updates:
        after[str(update["node_id"])] = update["after"]
    return {"report": report.as_dict(), "after_statuses": after,
            "residual_drift": sum(
                1 for f in report.findings
                if after.get(str(f.node_id)) != f.expected)}