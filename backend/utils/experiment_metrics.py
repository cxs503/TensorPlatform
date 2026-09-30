"""Low-cardinality observability for durable model-factory experiments.

The metric label vocabulary is deliberately bounded.  Experiment names,
database identifiers, organization identifiers and user supplied configuration
must never become metric labels.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from prometheus_client import Counter, Histogram


EXPERIMENT_KINDS = frozenset({"causal", "recsys", "gnn", "gan"})
EXPERIMENT_OUTCOMES = frozenset(
    {"created", "completed", "failed", "cancelled"}
)

EXPERIMENT_JOBS = Counter(
    "model_factory_experiment_jobs_total",
    "Durable model-factory experiment lifecycle transitions.",
    ("kind", "outcome"),
)
EXPERIMENT_DURATION = Histogram(
    "model_factory_experiment_job_duration_seconds",
    "Wall-clock duration of terminal model-factory experiments.",
    ("kind", "outcome"),
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900, 3600),
)
EXPERIMENT_DISPATCH_FAILURES = Counter(
    "model_factory_experiment_dispatch_failures_total",
    "Experiment jobs that could not be dispatched to the task broker.",
    ("kind",),
)

logger = logging.getLogger("foundation.experiment")


def metric_kind(kind: Any) -> str:
    """Map aliases and all extension kinds into a bounded label vocabulary."""
    normalized = str(kind or "").strip().lower()
    if normalized == "recommender":
        normalized = "recsys"
    return normalized if normalized in EXPERIMENT_KINDS else "other"


def observe_created(kind: Any) -> None:
    normalized = metric_kind(kind)
    EXPERIMENT_JOBS.labels(kind=normalized, outcome="created").inc()


def observe_dispatch_failure(kind: Any) -> None:
    EXPERIMENT_DISPATCH_FAILURES.labels(kind=metric_kind(kind)).inc()


def observe_terminal(
    kind: Any,
    outcome: str,
    *,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    normalized_outcome = str(outcome or "").strip().lower()
    if normalized_outcome not in EXPERIMENT_OUTCOMES - {"created"}:
        raise ValueError(f"invalid experiment terminal outcome: {outcome}")
    normalized_kind = metric_kind(kind)
    EXPERIMENT_JOBS.labels(
        kind=normalized_kind, outcome=normalized_outcome
    ).inc()
    if isinstance(created_at, datetime) and isinstance(completed_at, datetime):
        elapsed = max(0.0, (completed_at - created_at).total_seconds())
        EXPERIMENT_DURATION.labels(
            kind=normalized_kind, outcome=normalized_outcome
        ).observe(elapsed)


def log_experiment_event(
    event: str,
    *,
    kind: Any,
    job_id: int | None = None,
    organization_id: int | None = None,
    status: str | None = None,
    progress_percent: float | None = None,
    error_type: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit structured fields without serialising config or exception text."""
    fields: dict[str, Any] = {
        "experiment_event": str(event),
        "experiment_kind": metric_kind(kind),
    }
    if job_id is not None:
        fields["experiment_job_id"] = int(job_id)
    if organization_id is not None:
        fields["experiment_organization_id"] = int(organization_id)
    if status is not None:
        fields["experiment_status"] = str(status).upper()
    if progress_percent is not None:
        fields["experiment_progress_percent"] = round(float(progress_percent), 2)
    if error_type is not None:
        fields["experiment_error_type"] = str(error_type)[:128]
    logger.log(level, "model_factory_experiment", extra=fields)


__all__ = [
    "EXPERIMENT_DISPATCH_FAILURES",
    "EXPERIMENT_DURATION",
    "EXPERIMENT_JOBS",
    "EXPERIMENT_KINDS",
    "EXPERIMENT_OUTCOMES",
    "log_experiment_event",
    "metric_kind",
    "observe_created",
    "observe_dispatch_failure",
    "observe_terminal",
]
