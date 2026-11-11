"""Root-cause classification for stalled missions (six classes, no domain vocabulary).

Contract
--------
Input: a stalled row (see :mod:`tools.self_heal.detector`) plus a *context*
snapshot (owner-session liveness, queue saturation, duplicate task keys,
repeated acceptance failures).  Output: one of

``infra_lost`` · ``timeout`` · ``criteria_infeasible`` · ``duplicate_seed`` ·
``resource_contention`` · ``unknown``

Why not reuse ``scripts/mission_failure_analysis.py``
-----------------------------------------------------
That module classifies *terminal* failures from ten log-oriented categories
(``budget_exhausted``/``worktree_empty``/``revoked`` …).  Its inputs are final
error strings, which a stalled-running mission does not have yet, and its label
space is incompatible with the recovery policy required here (six causes, each
with a bounded action).  Reusing it would have meant mapping 10 labels onto 6
with no signal for the two structural causes (``duplicate_seed`` uses task-key
grouping, ``resource_contention`` uses queue saturation).  So this module is an
independent implementation; the *philosophy* (evidence first, explicit
``unknown`` instead of a guess) is borrowed.

Decision order (first match wins, each step needs positive evidence)
--------------------------------------------------------------------
1. ``duplicate_seed``     — another *older active* mission owns the same task_key;
2. ``infra_lost``         — owner session closed / unseen beyond ``session_stale_s``
                            / explicit infrastructure keywords / active-but-unclaimed;
3. ``criteria_infeasible``— explict infeasibility keywords or ≥3 acceptance rejections;
4. ``timeout``            — wall-clock budget exhausted or deadline keywords;
5. ``resource_contention``— allocation keywords, scheduler wait rounds, or a saturated
                            queue for a queue-wait stall;
6. ``unknown``            — no positive evidence → manual review, never a guess.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .detector import REASON_QUEUE_WAIT, Detection
from .model import DEFAULT_THRESHOLDS, MissionRow, Thresholds

CAUSE_ORDER = (
    "duplicate_seed",
    "infra_lost",
    "criteria_infeasible",
    "timeout",
    "resource_contention",
    "unknown",
)

_KEYWORDS: Dict[str, Sequence[str]] = {
    "infra_lost": (
        "executor_lost",
        "runner_lost",
        "agent_lost",
        "worker_lost",
        "heartbeat_lost",
        "no_heartbeat",
        "container_killed",
        "node_lost",
        "orchestrator_lost",
        "host_down",
        "executor_disconnected",
    ),
    "timeout": (
        "timeout",
        "timed_out",
        "wall_exhausted",
        "deadline_exceeded",
        "超时",
    ),
    "criteria_infeasible": (
        "criteria_infeasible",
        "acceptance_impossible",
        "criterion_unreachable",
        "criteria_conflict",
        "no_admissible_result",
        "infeasible",
        "判据不可达",
        "验收不可行",
    ),
    "resource_contention": (
        "resource_contention",
        "no_capacity",
        "queue_full",
        "insufficient_cores",
        "out_of_memory",
        "oom_killed",
        "quota_exceeded",
        "capacity_exhausted",
        "alloc_failed",
        "no_worker_available",
        "资源不足",
        "排队超限",
    ),
}


@dataclass
class HealContext:
    """Everything the classifier may look at beyond the mission row itself."""

    now: float
    window_id: str = "W0"
    #: session id → last-seen epoch. When ``session_index_complete`` is true, an
    #: id missing from this map means "no such live session" (= lost).
    session_last_seen: Dict[str, float] = field(default_factory=dict)
    session_index_complete: bool = False
    #: session ids whose row is closed/failed; always treated as lost.
    closed_sessions: Sequence[str] = field(default_factory=tuple)
    #: session ids that are known to exist but carry no liveness timestamp.
    known_sessions: Sequence[str] = field(default_factory=tuple)
    #: queue snapshot: {"queue_len","queue_capacity","cpu_pct","mem_pct"} (any may be absent).
    load: Dict[str, float] = field(default_factory=dict)
    #: task_key → number of active missions carrying it.
    active_task_keys: Dict[str, int] = field(default_factory=dict)
    #: task_key → oldest active mission id (the canonical owner of the key).
    task_key_owner: Dict[str, int] = field(default_factory=dict)
    #: window_id → nothing; kept for forward compatibility of callers.
    extra: Dict[str, Any] = field(default_factory=dict)

    def queue_saturation(self) -> Optional[float]:
        cap = self.load.get("queue_capacity")
        length = self.load.get("queue_len")
        if not cap or length is None:
            return None
        return float(length) / float(cap)

    def saturated(self) -> bool:
        ratio = self.queue_saturation()
        if ratio is not None and ratio >= 0.9:
            return True
        return float(self.load.get("cpu_pct") or 0.0) >= 95.0 or float(self.load.get("mem_pct") or 0.0) >= 95.0


@dataclass(frozen=True)
class Classification:
    mission_id: int
    cause: str
    confidence: float
    evidence: Dict[str, Any]
    features: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "cause": self.cause,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "features": self.features,
        }


def _result_text(result: Mapping[str, Any]) -> str:
    try:
        return json.dumps(result, ensure_ascii=False, default=str).lower()
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(result).lower()


def _hit_keywords(text: str, cause: str) -> List[str]:
    return [kw for kw in _KEYWORDS.get(cause, ()) if kw.lower() in text]


def _session_state(row: MissionRow, ctx: HealContext, thresholds: Thresholds) -> Dict[str, Any]:
    key = row.session_key
    if not key:
        return {"session": None, "state": "unclaimed" if row.status in ("running", "claimed") else "no_session"}
    closed = {str(s) for s in ctx.closed_sessions}
    if key in closed:
        return {"session": key, "state": "closed"}
    last = ctx.session_last_seen.get(key)
    if last is not None:
        age = ctx.now - float(last)
        if age >= thresholds.session_stale_s:
            return {"session": key, "state": "stale", "age_s": round(age, 3)}
        return {"session": key, "state": "live", "age_s": round(age, 3)}
    if key in {str(s) for s in ctx.known_sessions}:
        return {"session": key, "state": "quiet"}
    if ctx.session_index_complete:
        return {"session": key, "state": "missing"}
    return {"session": key, "state": "unknown"}


def _features(row: MissionRow, ctx: HealContext, thresholds: Thresholds) -> Dict[str, Any]:
    text = _result_text(row.result)
    wall_limit = row.wall_limit_s()
    wall_used = row.wall_used_s()
    return {
        "status": row.status,
        "session": _session_state(row, ctx, thresholds),
        "wall_ratio": (
            round(wall_used / wall_limit, 4)
            if wall_limit and wall_used is not None and wall_limit > 0
            else None
        ),
        "acceptance_failures": row.acceptance_failures(),
        "queue_saturation": ctx.queue_saturation(),
        "keywords": {cause: _hit_keywords(text, cause) for cause in _KEYWORDS},
        "scheduler_wait_rounds": (
            row.result.get("scheduler", {}).get("wait_rounds")
            if isinstance(row.result.get("scheduler"), Mapping)
            else None
        ),
        "duplicate_count": ctx.active_task_keys.get(row.task_key or "", 0),
    }


def classify(
    row: MissionRow,
    detection: Detection,
    ctx: HealContext,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Classification:
    feats = _features(row, ctx, thresholds)

    # 1. duplicate_seed ────────────────────────────────────────────────────
    if row.task_key:
        count = ctx.active_task_keys.get(row.task_key, 0)
        owner = ctx.task_key_owner.get(row.task_key)
        if count > 1 and owner is not None and row.id != owner:
            return Classification(
                mission_id=row.id,
                cause="duplicate_seed",
                confidence=0.95,
                evidence={
                    "rule": "task_key_shared_by_active_missions",
                    "task_key": row.task_key,
                    "active_count": count,
                    "canonical_mission_id": owner,
                },
                features=feats,
            )
        if isinstance(row.result.get("duplicate_of"), (int, str)) or row.result.get("duplicate_seed") is True:
            return Classification(
                mission_id=row.id,
                cause="duplicate_seed",
                confidence=0.95,
                evidence={"rule": "result_marks_duplicate", "marker": row.result.get("duplicate_of")},
                features=feats,
            )

    # 2. infra_lost ────────────────────────────────────────────────────────
    session = feats["session"]
    kw = feats["keywords"]["infra_lost"]
    if session["state"] in ("closed", "stale", "missing"):
        return Classification(
            mission_id=row.id,
            cause="infra_lost",
            confidence=0.95,
            evidence={"rule": f"session_{session['state']}", "session": session},
            features=feats,
        )
    if kw:
        return Classification(
            mission_id=row.id,
            cause="infra_lost",
            confidence=0.92,
            evidence={"rule": "infra_keyword", "keywords": kw},
            features=feats,
        )
    if session["state"] == "unclaimed":
        return Classification(
            mission_id=row.id,
            cause="infra_lost",
            confidence=0.75,
            evidence={"rule": "active_without_owner", "status": row.status},
            features=feats,
        )

    # 3. criteria_infeasible ───────────────────────────────────────────────
    kw = feats["keywords"]["criteria_infeasible"]
    if kw:
        return Classification(
            mission_id=row.id,
            cause="criteria_infeasible",
            confidence=0.95,
            evidence={"rule": "criteria_keyword", "keywords": kw},
            features=feats,
        )
    if feats["acceptance_failures"] >= 3:
        return Classification(
            mission_id=row.id,
            cause="criteria_infeasible",
            confidence=0.88,
            evidence={"rule": "repeated_acceptance_rejections",
                      "failures": feats["acceptance_failures"]},
            features=feats,
        )

    # 4. timeout ───────────────────────────────────────────────────────────
    ratio = feats["wall_ratio"]
    kw = feats["keywords"]["timeout"]
    if kw or (ratio is not None and ratio >= 0.98):
        return Classification(
            mission_id=row.id,
            cause="timeout",
            confidence=0.95 if kw else 0.88,
            evidence={
                "rule": "timeout_keyword" if kw else "wall_clock_exhausted",
                "keywords": kw,
                "wall_ratio": ratio,
                "fail_stage": row.fail_stage,
            },
            features=feats,
        )

    # 5. resource_contention ───────────────────────────────────────────────
    kw = feats["keywords"]["resource_contention"]
    wait_rounds = feats["scheduler_wait_rounds"]
    if kw or (isinstance(wait_rounds, int) and wait_rounds >= 3):
        return Classification(
            mission_id=row.id,
            cause="resource_contention",
            confidence=0.92,
            evidence={"rule": "allocation_signal", "keywords": kw, "wait_rounds": wait_rounds},
            features=feats,
        )
    if detection.reason == REASON_QUEUE_WAIT and ctx.saturated():
        return Classification(
            mission_id=row.id,
            cause="resource_contention",
            confidence=0.8,
            evidence={
                "rule": "queue_wait_under_saturation",
                "queue_saturation": feats["queue_saturation"],
                "load": dict(ctx.load),
            },
            features=feats,
        )

    # 6. unknown ───────────────────────────────────────────────────────────
    return Classification(
        mission_id=row.id,
        cause="unknown",
        confidence=0.4,
        evidence={"rule": "no_positive_signal", "detection_reason": detection.reason},
        features=feats,
    )