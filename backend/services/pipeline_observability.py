"""Safe, low-cardinality observability for durable pipeline runs.

Correlation identifiers are persisted in ``PipelineRun.parameters`` and in
``PipelineTaskRun.outputs`` because the current schema deliberately has no
observability columns.  User configuration, dataset values, broker payloads
and exception messages are never included in logs, metrics, or audit events.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import text

logger = logging.getLogger("foundation.pipeline")

PIPELINE_EVENTS = frozenset({
    "accepted", "coordinator_enqueue", "coordinator_recovery", "node_start",
    "node_success", "node_failed", "node_skipped", "terminal",
})
PIPELINE_RESULTS = frozenset({"accepted", "success", "failed", "skipped", "recovered"})
PIPELINE_EVENT_COUNT = Counter(
    "ml_pipeline_events_total", "Durable ML pipeline lifecycle events.",
    ("event", "task_type", "result"),
)
PIPELINE_NODE_DURATION = Histogram(
    "ml_pipeline_node_duration_seconds", "Terminal pipeline node duration.",
    ("task_type", "result"), buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900, 3600),
)
PIPELINE_RUN_DURATION = Histogram(
    "ml_pipeline_run_duration_seconds", "Terminal pipeline run duration.",
    ("result",), buckets=(0.1, 1, 5, 30, 60, 300, 900, 3600, 21600, 86400),
)
PIPELINE_RUNNING = Gauge("ml_pipeline_runs_running", "Persisted non-terminal pipeline runs.")


def metric_task_type(value: Any) -> str:
    value = str(value or "").strip().lower()
    aliases = {"data_source": "data_load", "dataset": "data_load", "train": "model_train"}
    value = aliases.get(value, value)
    return value if value in {"data_load", "data_process", "feature_engineering", "model_evaluate", "model_train"} else "other"


def ensure_run_trace(run: Any) -> str:
    """Get or persist a random opaque trace id without accepting client input."""
    parameters = dict(getattr(run, "parameters", None) or {})
    observability = dict(parameters.get("observability") or {})
    trace_id = str(observability.get("trace_id") or "")
    if not trace_id or len(trace_id) > 64:
        trace_id = uuid.uuid4().hex
        observability["trace_id"] = trace_id
        parameters["observability"] = observability
        run.parameters = parameters
    return trace_id


def ensure_task_trace(run: Any, task_run: Any) -> str:
    trace_id = ensure_run_trace(run)
    outputs = dict(getattr(task_run, "outputs", None) or {})
    obs = dict(outputs.get("observability") or {})
    task_trace = str(obs.get("trace_id") or "")
    if not task_trace:
        task_trace = f"{trace_id}:task:{int(task_run.id)}"
        obs["trace_id"] = task_trace
        outputs["observability"] = obs
        task_run.outputs = outputs
    return task_trace


def log_pipeline_event(event: str, *, run: Any, task_run: Any | None = None, result: str = "accepted", error: BaseException | None = None) -> None:
    """Emit structured metadata only; exception/config payloads stay out of logs."""
    normalized_event = event if event in PIPELINE_EVENTS else "terminal"
    normalized_result = result if result in PIPELINE_RESULTS else "failed"
    trace_id = ensure_run_trace(run)
    fields: dict[str, Any] = {
        "pipeline_event": normalized_event, "pipeline_result": normalized_result,
        "pipeline_run_id": int(run.id), "pipeline_organization_id": int(run.organization_id),
        "pipeline_trace_id": trace_id,
    }
    task_type = "other"
    if task_run is not None:
        task_type = metric_task_type(task_run.task_type)
        fields.update({"pipeline_task_run_id": int(task_run.id), "pipeline_task_type": task_type,
                       "pipeline_task_trace_id": ensure_task_trace(run, task_run)})
    if error is not None:
        fields["pipeline_error_type"] = type(error).__name__[:128]
    PIPELINE_EVENT_COUNT.labels(event=normalized_event, task_type=task_type, result=normalized_result).inc()
    logger.info("ml_pipeline_event", extra=fields)


def observe_task_terminal(run: Any, task_run: Any, result: str) -> None:
    started, finished = getattr(task_run, "started_at", None), getattr(task_run, "finished_at", None)
    if isinstance(started, datetime) and isinstance(finished, datetime):
        PIPELINE_NODE_DURATION.labels(task_type=metric_task_type(task_run.task_type), result=result).observe(max(0.0, (finished - started).total_seconds()))


def observe_run_terminal(run: Any, result: str) -> None:
    started, finished = getattr(run, "started_at", None), getattr(run, "finished_at", None)
    if isinstance(started, datetime) and isinstance(finished, datetime):
        PIPELINE_RUN_DURATION.labels(result=result).observe(max(0.0, (finished - started).total_seconds()))


def refresh_running_gauge(db: Any) -> None:
    """Set from durable state so worker restart does not leave a stale gauge."""
    try:
        count = db.execute(text("SELECT COUNT(*) FROM pipeline_runs WHERE status IN ('PENDING', 'RUNNING')")).scalar()
        PIPELINE_RUNNING.set(int(count or 0))
    except Exception:
        logger.warning("pipeline_running_metric_refresh_failed", exc_info=False)


def emit_pipeline_audit(*, actor_id: Any, organization_id: Any, run_id: Any, task_run_id: Any | None, action: str, result: str, trace_id: str, session_factory: Callable | None = None) -> bool:
    """Best-effort independent audit write containing only searchable metadata."""
    event = {
        "actor_id": str(actor_id or "system/unknown")[:128], "organization_id": int(organization_id),
        "run_id": int(run_id), "task_run_id": int(task_run_id) if task_run_id is not None else None,
        "action": str(action)[:64], "result": str(result)[:32], "trace_id": str(trace_id)[:64],
    }
    if session_factory is None:
        from database.config import SessionLocal
        session_factory = SessionLocal
    db = None
    try:
        db = session_factory()
        db.execute(text("INSERT INTO audit_logs (action, entity_type, entity_id, detail, user_name) VALUES (:action, :entity_type, :entity_id, :detail, :user_name)"), {
            "action": f"pipeline.{event['action']}", "entity_type": "pipeline_run",
            "entity_id": str(event["run_id"]), "detail": json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "user_name": event["actor_id"][:64],
        })
        db.commit()
        return True
    except Exception as exc:
        if db is not None:
            try: db.rollback()
            except Exception: pass
        logger.warning("pipeline_audit_write_failed action=%s result=%s run_id=%s error_type=%s", event["action"], event["result"], event["run_id"], type(exc).__name__)
        return False
    finally:
        if db is not None:
            try: db.close()
            except Exception: pass


def audit_pipeline_event(event: str, *, run: Any, task_run: Any | None = None, result: str = "accepted") -> bool:
    return emit_pipeline_audit(actor_id=getattr(run, "triggered_by", None), organization_id=run.organization_id,
        run_id=run.id, task_run_id=getattr(task_run, "id", None), action=event, result=result,
        trace_id=ensure_task_trace(run, task_run) if task_run is not None else ensure_run_trace(run))

