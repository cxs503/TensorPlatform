"""Durable, tenant-scoped runtime shared by model-factory experiments.

TrainingJob is the durable state machine.  Adapters are pure callables and no
job state is kept in a process dictionary or background thread.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from models.core import Dataset, Model, ModelStatus, ModelVersion, TrainingJob, TrainingStatus
from utils.experiment_metrics import (
    log_experiment_event,
    observe_created,
    observe_dispatch_failure,
    observe_terminal,
)

SCHEMA_VERSION = 1
METADATA_KEY = "_experiment_runtime"
ACTIVE = {TrainingStatus.PENDING, TrainingStatus.RUNNING}
TERMINAL = {TrainingStatus.COMPLETED, TrainingStatus.FAILED, TrainingStatus.CANCELLED}
MAX_CONFIG_BYTES = 64 * 1024
MAX_METRICS_BYTES = 32 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_DATASET_ROWS = 100_000
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_MODEL_BLOB_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ACTIVE = min(max(int(os.getenv("EXPERIMENT_MAX_ACTIVE_PER_ORG", "8")), 1), 64)
_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_SECRETS = {"api_key", "authorization", "password", "private_key", "secret", "token"}


class ExperimentRuntimeError(RuntimeError): pass
class ExperimentNotFound(ExperimentRuntimeError): pass
class ExperimentAccessDenied(ExperimentRuntimeError): pass
class ExperimentValidationError(ExperimentRuntimeError): pass
class ExperimentCapacityError(ExperimentRuntimeError): pass
class ExperimentCancelled(ExperimentRuntimeError): pass


@dataclass(frozen=True)
class ExperimentContext:
    job_id: int
    organization_id: int
    dataset_id: int | None
    dataset_name: str | None
    dataset_path: str | None
    dataset_blob: bytes | None
    dataset_columns: Mapping[str, Any]
    config: Mapping[str, Any]
    work_dir: Path
    progress: Callable[..., None]

    @property
    def artifact_dir(self) -> Path:
        return self.work_dir


@dataclass(frozen=True)
class ExperimentResult:
    metrics: Mapping[str, int | float]
    artifact_path: str | None = None
    model_name: str | None = None
    model_type: str | None = None
    framework: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    model_blob: bytes | None = None
    mlflow_run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


ExperimentAdapter = Callable[[ExperimentContext], ExperimentResult | Mapping[str, Any]]
_ADAPTERS: dict[str, ExperimentAdapter] = {}  # code registry, never job state
_BUILTINS = {
    "causal": ("services.experiment_algorithms_causal_recsys", "run_causal"),
    "recsys": ("services.experiment_algorithms_causal_recsys", "run_recsys"),
    "recommender": ("services.experiment_algorithms_causal_recsys", "run_recsys"),
    "gnn": ("services.experiment_algorithms_gnn_gan", "run_gnn"),
    "gan": ("services.experiment_algorithms_gnn_gan", "run_gan"),
}


def _now(): return datetime.utcnow()


def _audit(job, *, action, outcome, actor_id, audit_writer=None):
    """Emit a sanitized audit event without changing the durable job truth."""
    if audit_writer is None:
        from services.experiment_audit import emit_experiment_audit

        audit_writer = emit_experiment_audit
    try:
        return bool(audit_writer(
            actor_id=actor_id,
            organization_id=int(job.organization_id),
            job_id=int(job.id),
            kind=experiment_kind(job),
            action=action,
            outcome=outcome,
        ))
    except Exception:
        # Custom writers follow the same best-effort contract as the default
        # adapter. The primary transaction has already recorded the true state.
        return False


def _status(value: Any) -> TrainingStatus:
    if isinstance(value, TrainingStatus): return value
    try: return TrainingStatus(str(getattr(value, "value", value)).upper())
    except ValueError as exc: raise ExperimentRuntimeError(f"invalid persisted status: {value}") from exc


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool): raise ExperimentValidationError(f"{name} must be a positive integer")
    try: parsed = int(value)
    except (TypeError, ValueError) as exc: raise ExperimentValidationError(f"{name} must be a positive integer") from exc
    if parsed <= 0: raise ExperimentValidationError(f"{name} must be a positive integer")
    return parsed


def _kind(value: Any) -> str:
    value = str(value or "").strip().lower()
    if not _KIND_RE.fullmatch(value): raise ExperimentValidationError("invalid experiment kind")
    return value


def _normalize_json(value: Any, depth=0, key=None):
    if depth > 8: raise ExperimentValidationError("config nesting exceeds 8 levels")
    if key and key.lower() in _SECRETS: raise ExperimentValidationError(f"secret-like field is forbidden: {key}")
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ExperimentValidationError("numbers must be finite")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 1000: raise ExperimentValidationError("config list is too large")
        return [_normalize_json(x, depth + 1) for x in value]
    if isinstance(value, Mapping):
        if len(value) > 256: raise ExperimentValidationError("config object is too large")
        result = {}
        for k, v in value.items():
            if not isinstance(k, str) or not k or len(k) > 128: raise ExperimentValidationError("invalid config key")
            result[k] = _normalize_json(v, depth + 1, k)
        return result
    raise ExperimentValidationError(f"non-JSON config value: {type(value).__name__}")


def validate_config(config: Any) -> dict:
    if not isinstance(config, Mapping): raise ExperimentValidationError("config must be an object")
    value = _normalize_json(config)
    try: size = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
    except (TypeError, ValueError) as exc: raise ExperimentValidationError("config must be JSON-safe") from exc
    if size > MAX_CONFIG_BYTES: raise ExperimentValidationError("config exceeds 64KiB")
    return value


def register_adapter(kind: str, adapter: ExperimentAdapter, *, replace=False):
    kind = _kind(kind)
    if not callable(adapter): raise TypeError("adapter must be callable")
    if kind in _ADAPTERS and _ADAPTERS[kind] is not adapter and not replace:
        raise ExperimentValidationError(f"adapter already registered: {kind}")
    _ADAPTERS[kind] = adapter
    return adapter


def unregister_adapter(kind: str): _ADAPTERS.pop(_kind(kind), None)


def resolve_adapter(kind: str) -> ExperimentAdapter:
    kind = _kind(kind)
    if kind in _ADAPTERS: return _ADAPTERS[kind]
    target = _BUILTINS.get(kind)
    if not target: raise ExperimentValidationError(f"unknown experiment kind: {kind}")
    try: adapter = getattr(importlib.import_module(target[0]), target[1])
    except (ImportError, AttributeError) as exc: raise ExperimentValidationError(f"adapter unavailable: {kind}") from exc
    return register_adapter(kind, adapter)


def _metadata(job):
    hp = job.hyperparameters or {}
    meta = hp.get(METADATA_KEY) if isinstance(hp, Mapping) else None
    if not isinstance(meta, Mapping) or meta.get("schema_version") != SCHEMA_VERSION:
        raise ExperimentRuntimeError("training job is not an experiment job")
    return dict(meta)


def experiment_kind(job): return _kind(_metadata(job).get("kind"))
def experiment_config(job): return validate_config(_metadata(job).get("config", {}))


def _set_runtime(job, **updates):
    hp = dict(job.hyperparameters or {})
    meta = dict(hp.get(METADATA_KEY) or {})
    runtime = dict(meta.get("runtime") or {})
    runtime.update(updates)
    meta["runtime"] = runtime
    hp[METADATA_KEY] = meta
    job.hyperparameters = hp


def _dataset(db, dataset_id, org_id):
    item = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if item is None: raise ExperimentValidationError("dataset does not exist")
    if item.organization_id is None or int(item.organization_id) != org_id:
        raise ExperimentAccessDenied("dataset belongs to another organization")
    if item.size_bytes and int(item.size_bytes) > MAX_DATASET_BYTES: raise ExperimentValidationError("dataset byte limit exceeded")
    if item.row_count and int(item.row_count) > MAX_DATASET_ROWS: raise ExperimentValidationError("dataset row limit exceeded")
    return item


def _job(db, job_id, org_id, lock=False):
    q = db.query(TrainingJob).filter(TrainingJob.id == int(job_id))
    if lock: q = q.with_for_update()
    job = q.first()
    if job is None: raise ExperimentNotFound("experiment does not exist")
    if job.organization_id is None or int(job.organization_id) != org_id: raise ExperimentAccessDenied("experiment belongs to another organization")
    _metadata(job)
    return job


def serialize_experiment(job):
    result = job.to_dict()
    meta = _metadata(job)
    runtime = meta.get("runtime", {})
    result.update(
        kind=meta["kind"],
        config=meta.get("config", {}),
        runtime=runtime,
        artifact=runtime.get("artifact"),
        metadata=runtime.get("result_metadata", {}),
        progress=(runtime.get("progress") or {}).get("percent", 0),
        error=job.error_message,
    )
    return result


def enqueue_experiment(db, job, *, publisher=None, actor_id="system/api", audit_writer=None):
    if _status(job.status) != TrainingStatus.PENDING: raise ExperimentValidationError("only PENDING jobs may be queued")
    if not job.id or not job.training_job_id: raise ExperimentRuntimeError("job and task ids must be persisted first")
    if publisher is None:
        from tasks.experiment_tasks import execute_experiment_job
        publisher = execute_experiment_job.apply_async
    try:
        sent = publisher(args=[int(job.id), int(job.organization_id)], task_id=str(job.training_job_id), retry=False)
        if str(getattr(sent, "id", job.training_job_id)) != str(job.training_job_id): raise RuntimeError("broker task id mismatch")
        _set_runtime(job, dispatch_state="ENQUEUED", enqueued_at=_now().isoformat())
        db.commit()
        log_experiment_event(
            "queued", kind=experiment_kind(job), job_id=job.id,
            organization_id=job.organization_id, status="PENDING",
        )
    except Exception as exc:
        job.status, job.completed_at = TrainingStatus.FAILED, _now()
        job.error_message = f"experiment queue dispatch failed: {exc}"[:4000]
        _set_runtime(job, dispatch_state="FAILED")
        db.commit()
        kind = experiment_kind(job)
        observe_dispatch_failure(kind)
        observe_terminal(
            kind, "failed", created_at=job.created_at,
            completed_at=job.completed_at,
        )
        log_experiment_event(
            "dispatch_failed", kind=kind, job_id=job.id,
            organization_id=job.organization_id, status="FAILED",
            error_type=type(exc).__name__,
        )
        _audit(job, action="dispatch", outcome="failed", actor_id=actor_id, audit_writer=audit_writer)
        raise ExperimentRuntimeError("experiment queue dispatch failed") from exc
    return job


def create_experiment(db, *, kind: str, config: dict, dataset_id: int | None, organization_id: int, name=None, publisher=None, max_active=None, actor_id="system/api", audit_writer=None):
    kind, config = _kind(kind), validate_config(config)
    resolve_adapter(kind)
    org_id = _positive(organization_id, "organization_id")
    fixture_mode = config.get("fixture_mode") is True
    if dataset_id is None:
        if not fixture_mode: raise ExperimentValidationError("dataset_id is required unless fixture_mode=true")
        normalized_dataset_id = None
    else:
        normalized_dataset_id = _positive(dataset_id, "dataset_id")
        _dataset(db, normalized_dataset_id, org_id)
    capacity = DEFAULT_MAX_ACTIVE if max_active is None else int(max_active)
    if capacity < 1 or capacity > 64: raise ExperimentValidationError("max_active must be 1-64")
    active_jobs = (
        db.query(TrainingJob)
        .filter(
            TrainingJob.organization_id == org_id,
            TrainingJob.status.in_(list(ACTIVE)),
            TrainingJob.training_job_id.like("experiment-%"),
        )
        .limit(65)
        .all()
    )
    count = sum(
        1 for item in active_jobs
        if isinstance((item.hyperparameters or {}).get(METADATA_KEY), Mapping)
    )
    if count >= capacity: raise ExperimentCapacityError("organization experiment capacity exhausted")
    name = str(name or f"{kind}-experiment").strip()
    if not name or len(name) > 128: raise ExperimentValidationError("name must be 1-128 characters")
    job = TrainingJob(name=name, dataset_id=normalized_dataset_id, organization_id=org_id, training_job_id=f"experiment-{uuid.uuid4().hex}", status=TrainingStatus.PENDING, hyperparameters={"kind": kind, "config": config, METADATA_KEY: {"schema_version": 1, "kind": kind, "config": config, "runtime": {"dispatch_state": "INTENT_RECORDED"}}})
    db.add(job); db.commit(); db.refresh(job)
    observe_created(kind)
    log_experiment_event(
        "created", kind=kind, job_id=job.id,
        organization_id=job.organization_id, status="PENDING",
    )
    _audit(job, action="create", outcome="success", actor_id=actor_id, audit_writer=audit_writer)
    return enqueue_experiment(db, job, publisher=publisher, actor_id=actor_id, audit_writer=audit_writer)


def list_experiments(db, *, organization_id: int, kind=None, status=None, limit=50, offset=0):
    org_id = _positive(organization_id, "organization_id")
    if limit < 1 or limit > 200 or offset < 0: raise ExperimentValidationError("invalid pagination")
    q = db.query(TrainingJob).filter(
        TrainingJob.organization_id == org_id,
        TrainingJob.training_job_id.like("experiment-%"),
    )
    if status is not None: q = q.filter(TrainingJob.status == _status(status))
    if kind is not None:
        kind = _kind(kind)
        q = q.filter(TrainingJob.hyperparameters[METADATA_KEY]["kind"].as_string() == kind)
    jobs = q.order_by(TrainingJob.id.desc()).offset(offset).limit(limit).all()
    jobs = [j for j in jobs if isinstance((j.hyperparameters or {}).get(METADATA_KEY), Mapping)]
    return jobs if kind is None else [j for j in jobs if experiment_kind(j) == kind]


def get_experiment(db, job_id: int, *, organization_id: int):
    return _job(db, job_id, _positive(organization_id, "organization_id"))


def cancel_experiment(db, job_id: int, *, organization_id: int, revoker=None, actor_id="system/api", audit_writer=None):
    job = _job(db, job_id, _positive(organization_id, "organization_id"), True)
    state = _status(job.status)
    if state in {TrainingStatus.COMPLETED, TrainingStatus.FAILED}: raise ExperimentValidationError("terminal job cannot be cancelled")
    if state == TrainingStatus.CANCELLED: return job
    job.status, job.completed_at = TrainingStatus.CANCELLED, _now()
    _set_runtime(job, cancel_requested_at=job.completed_at.isoformat()); db.commit()
    kind = experiment_kind(job)
    observe_terminal(
        kind, "cancelled", created_at=job.created_at,
        completed_at=job.completed_at,
    )
    log_experiment_event(
        "cancelled", kind=kind, job_id=job.id,
        organization_id=job.organization_id, status="CANCELLED",
    )
    _audit(job, action="cancel", outcome="cancelled", actor_id=actor_id, audit_writer=audit_writer)
    try:
        if revoker is None:
            from tasks.celery_app import celery_app
            revoker = celery_app.control.revoke
        revoker(str(job.training_job_id), terminate=False)
    except Exception as exc:
        _set_runtime(job, revoke_warning=str(exc)[:512]); db.commit()
        log_experiment_event(
            "revoke_warning", kind=kind, job_id=job.id,
            organization_id=job.organization_id, status="CANCELLED",
            error_type=type(exc).__name__,
        )
    return job


def _result(raw):
    if isinstance(raw, ExperimentResult): result = raw
    elif isinstance(raw, Mapping):
        model = raw.get("model") if isinstance(raw.get("model"), Mapping) else {}
        artifact = raw.get("artifact") if isinstance(raw.get("artifact"), Mapping) else {}
        metadata = dict(raw.get("metadata") or {})
        if raw.get("source") is not None and "data_source" not in metadata:
            metadata["data_source"] = raw.get("source")
        result = ExperimentResult(raw.get("metrics") or {}, raw.get("artifact_path") or artifact.get("path"), raw.get("model_name") or model.get("name"), raw.get("model_type") or model.get("model_type"), raw.get("framework") or model.get("framework"), raw.get("parameters") or model.get("parameters") or {}, raw.get("model_blob"), raw.get("mlflow_run_id"), metadata)
    else: raise ExperimentValidationError("adapter returned invalid result")
    metrics = {}
    if not isinstance(result.metrics, Mapping) or len(result.metrics) > 256: raise ExperimentValidationError("invalid metrics")
    for key, value in result.metrics.items():
        if not isinstance(key, str) or not key or len(key) > 128 or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ExperimentValidationError(f"invalid metric: {key}")
        metrics[key] = value.item() if hasattr(value, "item") else value
    if len(json.dumps(metrics).encode()) > MAX_METRICS_BYTES: raise ExperimentValidationError("metrics exceed 32KiB")
    blob = bytes(result.model_blob) if result.model_blob is not None else None
    if blob is not None and len(blob) > MAX_MODEL_BLOB_BYTES: raise ExperimentValidationError("model blob too large")
    return ExperimentResult(metrics, result.artifact_path, result.model_name, result.model_type, result.framework, validate_config(result.parameters or {}), blob, result.mlflow_run_id, validate_config(result.metadata or {}))


def _artifact(path_value, work_dir):
    if not path_value: return None
    path, root = Path(path_value).resolve(strict=True), work_dir.resolve()
    if root not in path.parents or not path.is_file() or path.is_symlink(): raise ExperimentValidationError("artifact escaped job work directory")
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES: raise ExperimentValidationError("artifact too large")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "size_bytes": size, "sha256": digest}


def _progress(db, job):
    def report(value, details=None):
        db.refresh(job)
        if _status(job.status) == TrainingStatus.CANCELLED: raise ExperimentCancelled("experiment cancelled")
        if isinstance(value, bool) or not isinstance(value, (int, float)): raise ExperimentValidationError("invalid progress")
        number = float(value); percent = number * 100 if isinstance(value, float) and 0 <= number <= 1 else number
        if not math.isfinite(percent) or not 0 <= percent <= 100: raise ExperimentValidationError("progress out of bounds")
        previous = (((_metadata(job).get("runtime") or {}).get("progress") or {}).get("percent"))
        item = {"percent": round(percent, 2), "updated_at": _now().isoformat()}
        if isinstance(details, str): item["stage"] = details[:256]
        elif isinstance(details, Mapping): item["details"] = validate_config(details)
        _set_runtime(job, progress=item); db.commit()
        if previous is None or int(float(previous) // 10) != int(percent // 10) or percent == 100:
            log_experiment_event(
                "progress", kind=experiment_kind(job), job_id=job.id,
                organization_id=job.organization_id, status="RUNNING",
                progress_percent=percent,
            )
    return report


def _persist(db, job, result, work_dir):
    artifact = _artifact(result.artifact_path, work_dir)
    model = version = None
    if artifact or result.model_blob is not None or result.model_name:
        model = Model(name=str(result.model_name or f"{experiment_kind(job)}-experiment-{job.id}")[:128], description=f"Experiment job {job.id}", model_type=str(result.model_type or experiment_kind(job))[:64], framework=str(result.framework or "custom")[:64], status=ModelStatus.READY, model_blob=result.model_blob, organization_id=job.organization_id)
        db.add(model); db.flush()
        version = ModelVersion(model_id=model.id, version_number="v1.0", file_path=artifact["path"] if artifact else None, metrics=dict(result.metrics), parameters=dict(result.parameters), mlflow_run_id=result.mlflow_run_id)
        db.add(version); db.flush(); job.model_id = job.registered_model_id = model.id
    job.metrics, job.mlflow_run_id = dict(result.metrics), result.mlflow_run_id
    job.status, job.completed_at, job.error_message = TrainingStatus.COMPLETED, _now(), None
    _set_runtime(job, progress={"percent": 100.0, "stage": "completed"}, artifact=artifact, model_version_id=version.id if version else None, result_metadata=dict(result.metadata))


def mark_experiment_failed(db, job_id, organization_id, error, *, actor_id="system/celery", audit_writer=None):
    job = _job(db, job_id, _positive(organization_id, "organization_id"), True)
    if _status(job.status) in TERMINAL: return job
    job.status, job.completed_at, job.error_message = TrainingStatus.FAILED, _now(), str(error)[:4000]
    _set_runtime(job, failed_at=job.completed_at.isoformat()); db.commit()
    kind = experiment_kind(job)
    observe_terminal(
        kind, "failed", created_at=job.created_at,
        completed_at=job.completed_at,
    )
    log_experiment_event(
        "failed", kind=kind, job_id=job.id,
        organization_id=job.organization_id, status="FAILED",
        error_type=type(error).__name__,
    )
    _audit(job, action="execute", outcome="failed", actor_id=actor_id, audit_writer=audit_writer)
    return job


def execute_persisted_experiment(db, job_id, organization_id, *, task_id: str, artifact_root=None, adapter=None, actor_id="system/celery", audit_writer=None):
    org_id = _positive(organization_id, "organization_id")
    job = _job(db, job_id, org_id, True); state = _status(job.status)
    if state in TERMINAL: return job
    if not task_id or str(job.training_job_id) != str(task_id): raise ExperimentAccessDenied("delivery task id mismatch")
    config = experiment_config(job)
    if job.dataset_id is None:
        if config.get("fixture_mode") is not True: raise ExperimentValidationError("persisted job has no authorized dataset")
        dataset = None
    else:
        dataset = _dataset(db, int(job.dataset_id), org_id)
    runtime = _metadata(job).get("runtime") or {}
    if state == TrainingStatus.RUNNING and str(runtime.get("executor_task_id")) != str(task_id): raise ExperimentRuntimeError("job owned by another delivery")
    job.status, job.started_at = TrainingStatus.RUNNING, job.started_at or _now()
    _set_runtime(job, executor_task_id=task_id, attempt=int(runtime.get("attempt", 0)) + 1, started_at=job.started_at.isoformat()); db.commit()
    log_experiment_event(
        "started", kind=experiment_kind(job), job_id=job.id,
        organization_id=job.organization_id, status="RUNNING",
    )
    root = Path(artifact_root or os.getenv("EXPERIMENT_ARTIFACT_ROOT", "/app/uploads/experiments")).resolve(); root.mkdir(parents=True, exist_ok=True)
    work_dir = (root / f"org-{org_id}" / f"job-{job.id}").resolve(); work_dir.mkdir(parents=True, exist_ok=True)
    if root not in work_dir.parents: raise ExperimentRuntimeError("invalid work directory")
    blob = bytes(dataset.data_blob) if dataset is not None and dataset.data_blob is not None else None
    if blob is not None and len(blob) > MAX_DATASET_BYTES: raise ExperimentValidationError("dataset blob too large")
    context = ExperimentContext(
        int(job.id), org_id,
        int(dataset.id) if dataset is not None else None,
        str(dataset.name) if dataset is not None else None,
        str(dataset.file_path) if dataset is not None and dataset.file_path else None,
        blob,
        dict(dataset.columns_info or {}) if dataset is not None else {},
        config, work_dir, _progress(db, job),
    )
    result = _result((adapter or resolve_adapter(experiment_kind(job)))(context))
    db.refresh(job)
    if _status(job.status) == TrainingStatus.CANCELLED: return job
    _persist(db, job, result, work_dir); db.commit(); db.refresh(job)
    kind = experiment_kind(job)
    observe_terminal(
        kind, "completed", created_at=job.created_at,
        completed_at=job.completed_at,
    )
    log_experiment_event(
        "completed", kind=kind, job_id=job.id,
        organization_id=job.organization_id, status="COMPLETED",
        progress_percent=100,
    )
    _audit(job, action="execute", outcome="completed", actor_id=actor_id, audit_writer=audit_writer)
    return job


__all__ = ["ExperimentAccessDenied", "ExperimentAdapter", "ExperimentCancelled", "ExperimentCapacityError", "ExperimentContext", "ExperimentNotFound", "ExperimentResult", "ExperimentRuntimeError", "ExperimentValidationError", "cancel_experiment", "create_experiment", "enqueue_experiment", "execute_persisted_experiment", "get_experiment", "list_experiments", "mark_experiment_failed", "register_adapter", "resolve_adapter", "serialize_experiment", "unregister_adapter", "validate_config"]
