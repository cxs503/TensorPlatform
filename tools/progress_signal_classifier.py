"""Progress signal classifier — distinguish true stall from waiting/long-compute.

Background (r15 M-R15-02)
-------------------------
96 historical ``no_progress`` failures had ``mean(tool_calls − cap) = −29.2``,
i.e. jobs were terminated ~29 tool calls *before* their budget cap because the
platform mistook "cluster waiting / long computation" for "no progress".

This module adds a **four-class progress signal classifier** that runs alongside
the existing content-hash-based stall detector:

====================  =========================================================
class                 meaning
====================  =========================================================
``true_stall``        agent is genuinely stuck (no artifact change, no external
                      activity signal, consecutive no-progress cycles ≥ N)
``cluster_waiting``   job is queued on the cluster (in_flight_jobs present,
                      no artifact writes expected until job starts)
``long_computing``    a cluster job is running (submitted but not yet fetched;
                      wall-clock advancing but no writes yet — normal for HPC)
``long_write_interval``  computation finished but artifacts not yet flushed
                      (last write was recent enough to be plausible; composite
                      score stable but not zero)
====================  =========================================================

Design rules
------------
* **Zero domain semantics** — only observes generic signals: artifact hashes,
  in-flight job IDs, wall-clock deltas, composite scores.
* **Fail open on ambiguity** — when signals are insufficient to classify, the
  default is ``observe`` (do not early-stop), not ``true_stall``.
* **Composable** — the classifier is a pure function of a ``SignalContext``
  dataclass; callers construct the context from whatever telemetry they have.

Usage
-----
    from tools.progress_signal_classifier import classify_progress, SignalContext

    ctx = SignalContext(
        hard_no_progress=True,
        consecutive_no_progress_cycles=5,
        in_flight_jobs={"cj123"},
        last_write_age_s=300.0,
        composite_score=0.0,
        wall_delta_s=600.0,
    )
    result = classify_progress(ctx)
    # result.category == "long_computing"  →  do NOT early-stop
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

# ── Classification categories ─────────────────────────────────────────────

CATEGORY_TRUE_STALL = "true_stall"
CATEGORY_CLUSTER_WAITING = "cluster_waiting"
CATEGORY_LONG_COMPUTING = "long_computing"
CATEGORY_LONG_WRITE_INTERVAL = "long_write_interval"

VALID_CATEGORIES = (
    CATEGORY_TRUE_STALL,
    CATEGORY_CLUSTER_WAITING,
    CATEGORY_LONG_COMPUTING,
    CATEGORY_LONG_WRITE_INTERVAL,
)

# ── Thresholds ─────────────────────────────────────────────────────────────

#: Minimum consecutive no-progress cycles before we even consider stalling.
MIN_STALL_CYCLES = 3

#: If in_flight_jobs is non-empty and last_write_age_s < this, we assume the
#: job is still running on the cluster (long_computing).
CLUSTER_JOB_RUNNING_WRITE_AGE_S = 3600.0  # 1 hour

#: If in_flight_jobs is non-empty and last_write_age_s >= this, the job may
#: have finished but not flushed yet (long_write_interval).
LONG_WRITE_INTERVAL_MAX_AGE_S = 7200.0  # 2 hours

#: If composite_score is exactly 0 and no in-flight jobs, it's a true stall.
COMPOSITE_ZERO_THRESHOLD = 1e-9

#: Wall-clock delta (seconds) since last heartbeat. If this is very large
#: AND tool_activity_this_cycle is True, the agent is alive but waiting
#: for cluster resources.
CLUSTER_WAIT_MIN_WALL_DELTA_S = 600.0  # 10 minutes


@dataclass(frozen=True)
class SignalContext:
    """All signals available to the classifier at one observation point.

    Every field is optional / has a safe default so that partial telemetry
    does not crash the classifier (fail open on ambiguity).
    """

    #: Whether the artifact scanner detected zero content change this cycle.
    hard_no_progress: bool = False

    #: Number of consecutive cycles with hard_no_progress=True.
    consecutive_no_progress_cycles: int = 0

    #: Set of cluster job IDs currently in-flight (submitted but not fetched).
    in_flight_jobs: Set[str] = field(default_factory=set)

    #: Seconds since the last artifact write (None = no write ever).
    last_write_age_s: Optional[float] = None

    #: Current composite progress score (0.0 = no progress signals at all).
    composite_score: float = 0.0

    #: Wall-clock seconds since last heartbeat / observation.
    wall_delta_s: float = 0.0

    #: Whether the agent reported any tool activity this cycle (e.g. API calls,
    #: shell commands). None = unknown.
    tool_activity_this_cycle: Optional[bool] = None

    #: Cluster queue position if known (None = not queued or unknown).
    queue_position: Optional[int] = None

    #: Previous composite score (for trend detection). None = first observation.
    prev_composite_score: Optional[float] = None


@dataclass(frozen=True)
class ClassificationResult:
    """Output of :func:`classify_progress`."""

    category: str
    confidence: float  # 0.0–1.0
    reason: str
    should_early_stop: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "should_early_stop": self.should_early_stop,
            "detail": self.detail,
        }


def classify_progress(ctx: SignalContext) -> ClassificationResult:
    """Classify the current progress state into one of four categories.

    Decision tree (evaluated in priority order):

    1. If ``hard_no_progress`` is False → there IS progress → ``observe``.
    2. If ``consecutive_no_progress_cycles`` < MIN_STALL_CYCLES → insufficient
       evidence → ``observe``.
    3. If ``in_flight_jobs`` is non-empty → the agent is waiting for or running
       a cluster job → ``cluster_waiting`` or ``long_computing``.
    4. If ``queue_position`` is known → ``cluster_waiting``.
    5. If ``composite_score`` > 0 but no artifact change → ``long_write_interval``.
    6. If tool_activity_this_cycle is True and wall_delta is large → agent is
       alive but waiting → ``cluster_waiting``.
    7. Otherwise → ``true_stall``.
    """
    # ── Guard: if there's actual progress, don't stall ──────────────────
    if not ctx.hard_no_progress:
        return ClassificationResult(
            category="observe",
            confidence=1.0,
            reason="hard_no_progress is False; progress detected",
            should_early_stop=False,
        )

    # ── Guard: insufficient cycles ──────────────────────────────────────
    if ctx.consecutive_no_progress_cycles < MIN_STALL_CYCLES:
        return ClassificationResult(
            category="observe",
            confidence=1.0,
            reason=f"only {ctx.consecutive_no_progress_cycles} consecutive no-progress cycles "
                   f"(need ≥{MIN_STALL_CYCLES})",
            should_early_stop=False,
        )

    # ── Rule 1: in-flight cluster jobs present ──────────────────────────
    if ctx.in_flight_jobs:
        n_jobs = len(ctx.in_flight_jobs)
        if ctx.last_write_age_s is not None and ctx.last_write_age_s < CLUSTER_JOB_RUNNING_WRITE_AGE_S:
            return ClassificationResult(
                category=CATEGORY_LONG_COMPUTING,
                confidence=0.85,
                reason=f"{n_jobs} in-flight cluster job(s); last write {ctx.last_write_age_s:.0f}s ago "
                       f"(within {CLUSTER_JOB_RUNNING_WRITE_AGE_S:.0f}s threshold)",
                should_early_stop=False,
                detail={"in_flight_jobs": sorted(ctx.in_flight_jobs),
                        "last_write_age_s": ctx.last_write_age_s},
            )
        elif ctx.last_write_age_s is not None and ctx.last_write_age_s < LONG_WRITE_INTERVAL_MAX_AGE_S:
            return ClassificationResult(
                category=CATEGORY_LONG_WRITE_INTERVAL,
                confidence=0.7,
                reason=f"{n_jobs} in-flight job(s); last write {ctx.last_write_age_s:.0f}s ago "
                       f"(within {LONG_WRITE_INTERVAL_MAX_AGE_S:.0f}s write-interval threshold)",
                should_early_stop=False,
                detail={"in_flight_jobs": sorted(ctx.in_flight_jobs),
                        "last_write_age_s": ctx.last_write_age_s},
            )
        else:
            # Jobs in-flight but no recent write and age unknown or very old —
            # still don't early-stop because the job might be running.
            return ClassificationResult(
                category=CATEGORY_CLUSTER_WAITING,
                confidence=0.6,
                reason=f"{n_jobs} in-flight cluster job(s); write age unknown or very old",
                should_early_stop=False,
                detail={"in_flight_jobs": sorted(ctx.in_flight_jobs)},
            )

    # ── Rule 2: queue position known → cluster waiting ──────────────────
    if ctx.queue_position is not None and ctx.queue_position >= 0:
        return ClassificationResult(
            category=CATEGORY_CLUSTER_WAITING,
            confidence=0.75,
            reason=f"queued at position {ctx.queue_position}; no in-flight jobs yet",
            should_early_stop=False,
            detail={"queue_position": ctx.queue_position},
        )

    # ── Rule 3: composite score > 0 but no artifact change → long write interval ─
    if ctx.composite_score > COMPOSITE_ZERO_THRESHOLD:
        return ClassificationResult(
            category=CATEGORY_LONG_WRITE_INTERVAL,
            confidence=0.65,
            reason=f"composite score {ctx.composite_score:.6f} > 0 but no artifact change; "
                   f"progress signals present but not yet written",
            should_early_stop=False,
            detail={"composite_score": ctx.composite_score},
        )

    # ── Rule 4: tool activity + long wall delta → agent alive but waiting ─
    if ctx.tool_activity_this_cycle is True and ctx.wall_delta_s >= CLUSTER_WAIT_MIN_WALL_DELTA_S:
        return ClassificationResult(
            category=CATEGORY_CLUSTER_WAITING,
            confidence=0.6,
            reason=f"tool activity detected, wall delta {ctx.wall_delta_s:.0f}s; "
                   f"agent alive but possibly waiting for cluster",
            should_early_stop=False,
            detail={"wall_delta_s": ctx.wall_delta_s},
        )

    # ── Rule 5: true stall ──────────────────────────────────────────────
    return ClassificationResult(
        category=CATEGORY_TRUE_STALL,
        confidence=0.9,
        reason=f"{ctx.consecutive_no_progress_cycles} consecutive no-progress cycles, "
               f"composite score {ctx.composite_score:.6f} ≈ 0, no in-flight jobs",
        should_early_stop=True,
        detail={
            "consecutive_cycles": ctx.consecutive_no_progress_cycles,
            "composite_score": ctx.composite_score,
        },
    )


def should_early_stop(ctx: SignalContext) -> bool:
    """Convenience wrapper: returns True only when classification is true_stall."""
    return classify_progress(ctx).should_early_stop
