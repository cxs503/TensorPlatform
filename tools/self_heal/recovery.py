"""Bounded recovery policy: root cause → one concrete, capped action.

The policy is intentionally small and total (every cause maps to an action):

===================  ==========================  =====================================
cause                action                      cap
===================  ==========================  =====================================
``infra_lost``        ``reschedule``             ``max_reschedules`` (3) → escalate
``timeout``           ``extend_timeout``         ``max_timeout_bumps`` (2) → escalate
``criteria_infeasible`` ``escalate`` (proposer)  uncapped by design: judgment work
``duplicate_seed``    ``archive``                once — redundant copy is retired
``resource_contention`` ``degrade_resources``    ``max_resource_degrades`` (2) → escalate
``unknown``           ``manual_review``          escalated to a human queue
===================  ==========================  =====================================

Attempt counting is *window-ordered*: only ledger entries from strictly earlier
windows count.  That is what makes a re-run of the same window idempotent — the
same window always derives the same attempt numbers, therefore the same
idempotency keys, therefore no duplicate actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .model import DEFAULT_THRESHOLDS, MissionRow, Thresholds, digest

ACTION_RESCHEDULE = "reschedule"
ACTION_EXTEND_TIMEOUT = "extend_timeout"
ACTION_ESCALATE = "escalate"
ACTION_ARCHIVE = "archive"
ACTION_DEGRADE_RESOURCES = "degrade_resources"
ACTION_MANUAL_REVIEW = "manual_review"
ACTIONS = (
    ACTION_RESCHEDULE,
    ACTION_EXTEND_TIMEOUT,
    ACTION_ESCALATE,
    ACTION_ARCHIVE,
    ACTION_DEGRADE_RESOURCES,
    ACTION_MANUAL_REVIEW,
)

_CAUSE_ACTION = {
    "infra_lost": ACTION_RESCHEDULE,
    "timeout": ACTION_EXTEND_TIMEOUT,
    "criteria_infeasible": ACTION_ESCALATE,
    "duplicate_seed": ACTION_ARCHIVE,
    "resource_contention": ACTION_DEGRADE_RESOURCES,
    "unknown": ACTION_MANUAL_REVIEW,
}


@dataclass(frozen=True)
class RecoveryPlan:
    mission_id: int
    cause: str
    action: str
    params: Dict[str, Any]
    attempt: int
    idempotency_key: str
    rationale: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "cause": self.cause,
            "action": self.action,
            "params": self.params,
            "attempt": self.attempt,
            "idempotency_key": self.idempotency_key,
            "rationale": self.rationale,
        }


def idempotency_key(window_id: str, mission_id: int, action: str, attempt: int) -> str:
    return digest(
        {"window_id": str(window_id), "mission_id": int(mission_id),
         "action": action, "attempt": int(attempt)}
    )


def _cap_for(cause: str, thresholds: Thresholds) -> Optional[int]:
    if cause == "infra_lost":
        return thresholds.max_reschedules
    if cause == "timeout":
        return thresholds.max_timeout_bumps
    if cause == "resource_contention":
        return thresholds.max_resource_degrades
    return None


def _requested_cores(row: MissionRow) -> Optional[int]:
    for source in (row.budget.get("cores"), row.budget.get("max_cores"), row.raw.get("requested_cores")):
        if isinstance(source, (int, float)) and source > 0:
            return int(source)
    return None


def plan_recovery(
    row: MissionRow,
    cause: str,
    *,
    window_id: str,
    prior_attempts: Callable[[int, str], int],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    duplicate_of: Optional[int] = None,
) -> RecoveryPlan:
    """Turn a classified stall into one action, honouring the per-cause cap."""
    action = _CAUSE_ACTION.get(cause, ACTION_MANUAL_REVIEW)
    params: Dict[str, Any] = {}
    rationale = ""

    attempt = prior_attempts(row.id, action) + 1  # attempt number of *this* action
    cap = _cap_for(cause, thresholds)
    if cap is not None and attempt > cap:
        rationale = (
            f"{cause}: '{action}' already attempted {attempt - 1}x (cap {cap}); "
            "escalating instead of repeating a failing action"
        )
        action = ACTION_ESCALATE
        params = {"target": "manual_review", "reason": "recovery_cap_exhausted", "origin_action": _CAUSE_ACTION[cause]}
        attempt = prior_attempts(row.id, action) + 1
    else:
        if action == ACTION_RESCHEDULE:
            params = {"requeue": True, "reset_claim": True,
                      "from_status": row.status, "attempt": attempt}
            rationale = f"infra_lost: owner session gone → re-dispatch (attempt {attempt}/{cap})"
        elif action == ACTION_EXTEND_TIMEOUT:
            old = row.wall_limit_s()
            new = None if old is None else int(round(old * thresholds.timeout_bump_ratio))
            params = {
                "previous_max_wall_seconds": old,
                "new_max_wall_seconds": new,
                "ratio": thresholds.timeout_bump_ratio,
                "attempt": attempt,
            }
            rationale = (
                f"timeout: wall budget exhausted → extend by {thresholds.timeout_bump_ratio:.0%} "
                f"(attempt {attempt}/{cap})"
            )
        elif action == ACTION_ESCALATE:
            params = {
                "target": "proposer",
                "reason": "criteria_infeasible",
                "acceptance_failures": row.acceptance_failures(),
            }
            rationale = "criteria_infeasible: recurring rejection needs criteria re-evaluation by the proposer"
        elif action == ACTION_ARCHIVE:
            params = {"canonical_mission_id": duplicate_of, "redundant": True}
            rationale = (
                f"duplicate_seed: task_key '{row.task_key}' already owned by mission "
                f"{duplicate_of}; retiring the redundant copy"
            )
        elif action == ACTION_DEGRADE_RESOURCES:
            cores = _requested_cores(row)
            new_cores = max(1, cores // 2) if cores else None
            params = {"previous_cores": cores, "new_cores": new_cores, "attempt": attempt}
            rationale = f"resource_contention: halve the request (attempt {attempt}/{cap})"
        else:  # ACTION_MANUAL_REVIEW
            params = {"reason": "unclassified_stall", "detection_only": True}
            rationale = "unknown: no positive root-cause signal — a human must look"

    key = idempotency_key(window_id, row.id, action, attempt)
    return RecoveryPlan(
        mission_id=row.id,
        cause=cause,
        action=action,
        params=params,
        attempt=attempt,
        idempotency_key=key,
        rationale=rationale,
    )