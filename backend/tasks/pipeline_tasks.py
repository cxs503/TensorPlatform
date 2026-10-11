"""Durable, idempotent orchestration for persisted ML pipeline runs.

The coordinator performs one short reconciliation pass and re-enqueues itself
while asynchronous training is outstanding. Durable state lives in the
PipelineRun/PipelineTaskRun tables, never in Celery result state.
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError as DatabaseOperationalError

from database.config import SessionLocal, engine
from models.core import (
    Dataset,
    Model,
    ModelVersion,
    Pipeline,
    PipelineRun,
    PipelineTask,
    PipelineTaskRun,
)
from tasks.celery_app import celery_app
from services.pipeline_observability import (
    audit_pipeline_event,
    ensure_run_trace,
    ensure_task_trace,
    log_pipeline_event,
    observe_run_terminal,
    observe_task_terminal,
    refresh_running_gauge,
)


TERMINAL_TASK_STATES = {"SUCCESS", "FAILED", "SKIPPED"}
FAILED_TASK_STATES = {"FAILED", "SKIPPED"}
TRAINING_TASK_TYPES = {"model_train", "train"}
DATA_TASK_TYPES = {"data_load", "data_source", "dataset"}
ADAPTER_TASK_TYPE_ALIASES = {
    "data_load": "data_load",
    "data_source": "data_load",
    "dataset": "data_load",
    "data_process": "data_process",
    "feature_engineering": "feature_engineering",
    "model_evaluate": "model_evaluate",
}
DERIVED_DATA_TASK_TYPES = {"data_process", "feature_engineering"}
IDENTITY_OUTPUT_KEYS = ("dataset_id", "job_id", "model_id")
POLL_SECONDS = min(max(int(os.getenv("PIPELINE_COORDINATOR_POLL_SECONDS", "5")), 2), 60)
MAX_RUN_SECONDS = min(
    max(int(os.getenv("PIPELINE_MAX_RUN_SECONDS", str(6 * 60 * 60))), 300),
    24 * 60 * 60,
)
MIN_TASK_TIMEOUT = 30
MAX_TASK_TIMEOUT = 3600
MAX_CONFIG_BYTES = 128 * 1024
ADVISORY_LOCK_NAMESPACE = 1263554636  # Stable "KPPL" namespace.
MAX_DATASET_ROWS = min(max(int(os.getenv("PIPELINE_MAX_DATASET_ROWS", "100000")), 10), 100000)
MAX_DATASET_BYTES = min(
    max(int(os.getenv("PIPELINE_MAX_DATASET_BYTES", str(64 * 1024 * 1024))), 1024),
    256 * 1024 * 1024,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _present(value: Any) -> bool:
    return value not in (None, "")


def merge_dependency_outputs(config: dict, outputs_by_dependency: dict[str, dict]) -> dict:
    """Merge only the explicitly selected identities from direct dependencies."""
    merged = dict(config or {})
    upstream: dict[str, dict] = {}
    for dependency_name, raw_outputs in outputs_by_dependency.items():
        if not isinstance(raw_outputs, dict):
            raise ValueError(f"上游节点 {dependency_name} 未产生有效 outputs")
        upstream[dependency_name] = dict(raw_outputs)

    bindings = merged.get("input_bindings")
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict):
        raise ValueError("input_bindings 必须是对象")
    binding_key = {
        "dataset_id": "dataset_from",
        "model_id": "model_from",
        "job_id": "model_from",
    }
    for identity_key, source_key in binding_key.items():
        selected_source = bindings.get(source_key)
        if not _present(selected_source):
            continue
        selected_source = str(selected_source)
        if selected_source not in upstream:
            raise ValueError(f"输入映射指定的上游节点不存在: {selected_source}")
        if not _present(upstream[selected_source].get(identity_key)):
            raise ValueError(
                f"输入映射节点 {selected_source} 未输出 {identity_key}"
            )

    for dependency_name, raw_outputs in upstream.items():
        for key in IDENTITY_OUTPUT_KEYS:
            selected_source = bindings.get(binding_key[key])
            if _present(selected_source) and str(selected_source) != dependency_name:
                continue
            incoming = raw_outputs.get(key)
            if not _present(incoming):
                continue
            current = merged.get(key)
            if _present(current) and str(current) != str(incoming):
                raise ValueError(f"上游节点对 {key} 的身份输出冲突")
            merged[key] = incoming
    merged["_upstream_outputs"] = upstream
    if _json_size(merged) > MAX_CONFIG_BYTES:
        raise ValueError("合并上游 outputs 后的节点配置超过 128KiB")
    return merged

def propagate_identity_outputs(effective_config: dict, task_outputs: dict) -> dict:
    result = dict(task_outputs or {})
    for key in IDENTITY_OUTPUT_KEYS:
        inherited = effective_config.get(key)
        produced = result.get(key)
        if _present(inherited) and _present(produced) and str(inherited) != str(produced):
            raise ValueError(f"节点产生的 {key} 与上游身份冲突")
        if _present(inherited) and not _present(produced):
            result[key] = inherited
    return result


def node_action(status: str, dependency_states: list[str]) -> str:
    """Pure scheduling decision used by reconciliation and unit tests."""
    status = str(status or "PENDING").upper()
    dependencies = [str(item or "PENDING").upper() for item in dependency_states]
    if status in TERMINAL_TASK_STATES:
        return "terminal"
    if any(item in FAILED_TASK_STATES for item in dependencies):
        return "skip"
    if any(item != "SUCCESS" for item in dependencies):
        return "wait"
    return "resume" if status == "RUNNING" else "execute"


def _timeout_seconds(spec: dict) -> int:
    raw = spec.get("timeout", (spec.get("config") or {}).get("timeout", 1800))
    try:
        return min(max(int(raw), MIN_TASK_TIMEOUT), MAX_TASK_TIMEOUT)
    except (TypeError, ValueError):
        return 1800


def _retry_limit(spec: dict) -> int:
    try:
        return min(max(int(spec.get("retry", 0)), 0), 5)
    except (TypeError, ValueError):
        return 0


def retry_allowed(task_type: str, attempt: int, retry_limit: int) -> bool:
    """Only side-effect-free data validation/load nodes are auto-retryable."""
    return task_type in DATA_TASK_TYPES and attempt <= retry_limit


def coordinator_message_current(parameters: dict, task_id: str | None) -> bool:
    expected = (parameters or {}).get("orchestrator", {}).get("coordinator_task_id")
    if not expected:
        return True
    return bool(task_id) and str(expected) == str(task_id)


def coordinator_intent_repairable(parameters: dict, task_id: str | None) -> bool:
    orchestrator = (parameters or {}).get("orchestrator", {})
    expected = orchestrator.get("coordinator_task_id")
    parent = orchestrator.get("dispatch_parent_task_id")
    return bool(
        task_id
        and expected
        and str(expected) != str(task_id)
        and orchestrator.get("dispatch_state") == "INTENT_RECORDED"
        and str(parent) == str(task_id)
    )


def _publish_coordinator_intent(
    db,
    run: PipelineRun,
    run_id: int,
    next_task_id: str,
    publisher=None,
):
    publisher = publisher or execute_pipeline_run.apply_async
    published = publisher(
        args=[int(run_id)],
        task_id=next_task_id,
        countdown=POLL_SECONDS,
        expires=POLL_SECONDS + 300,
        retry=False,
    )
    parameters = dict(run.parameters or {})
    orchestrator = dict(parameters.get("orchestrator") or {})
    if orchestrator.get("coordinator_task_id") == next_task_id:
        orchestrator["dispatch_state"] = "ENQUEUED"
        orchestrator["last_enqueued_at"] = _utcnow().isoformat()
        parameters["orchestrator"] = orchestrator
        run.parameters = parameters
        db.commit()
    # Emit only durable identifiers; Celery payload/configuration is excluded.
    try:
        log_pipeline_event("coordinator_enqueue", run=run, result="accepted")
        audit_pipeline_event("coordinator_enqueue", run=run, result="accepted")
    except Exception:
        pass
    return published


def enqueue_next_coordinator(
    db,
    run: PipelineRun,
    run_id: int,
    parent_task_id: str,
    *,
    publisher=None,
    next_task_id: str | None = None,
):
    """Persist the next generation before a fast worker can consume it."""
    if not parent_task_id:
        raise RuntimeError("协调任务缺少 Celery task_id，拒绝续调")
    next_task_id = next_task_id or f"pipeline-run-{run_id}-{uuid.uuid4().hex}"
    parameters = dict(run.parameters or {})
    orchestrator = dict(parameters.get("orchestrator") or {})
    orchestrator["coordinator_task_id"] = next_task_id
    orchestrator["dispatch_parent_task_id"] = str(parent_task_id)
    orchestrator["dispatch_state"] = "INTENT_RECORDED"
    orchestrator["intent_recorded_at"] = _utcnow().isoformat()
    parameters["orchestrator"] = orchestrator
    run.parameters = parameters
    db.commit()
    return _publish_coordinator_intent(
        db, run, run_id, next_task_id, publisher=publisher
    )


def _timed_out(started_at: datetime | None, timeout_seconds: int, now: datetime) -> bool:
    return bool(started_at and now >= started_at + timedelta(seconds=timeout_seconds))


@contextmanager
def coordinator_lock(run_id: int):
    """Serialize duplicate Celery deliveries without a schema migration."""
    connection = engine.connect()
    is_postgres = connection.dialect.name == "postgresql"
    acquired = True
    try:
        if is_postgres:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:namespace, :run_id)"),
                    {"namespace": ADVISORY_LOCK_NAMESPACE, "run_id": int(run_id)},
                ).scalar()
            )
        yield acquired
    finally:
        if is_postgres and acquired:
            connection.execute(
                text("SELECT pg_advisory_unlock(:namespace, :run_id)"),
                {"namespace": ADVISORY_LOCK_NAMESPACE, "run_id": int(run_id)},
            )
        connection.close()


def _run_specs(db, run: PipelineRun) -> list[dict]:
    """Prefer the immutable API-time snapshot; recover legacy runs if absent."""
    parameters = run.parameters or {}
    snapshot = parameters.get("orchestrator", {}).get("tasks")
    if isinstance(snapshot, list) and snapshot:
        return [dict(item) for item in snapshot]
    rows = (
        db.query(PipelineTaskRun, PipelineTask)
        .join(PipelineTask, PipelineTask.id == PipelineTaskRun.task_id)
        .filter(PipelineTaskRun.pipeline_run_id == run.id)
        .order_by(PipelineTask.order_index, PipelineTaskRun.id)
        .all()
    )
    return [
        {
            "task_run_id": task_run.id,
            "name": task_run.task_name,
            "task_type": task_run.task_type,
            "config": definition.config or {},
            "dependencies": definition.dependencies or [],
            "timeout": definition.timeout or 1800,
            "retry": definition.retry or 0,
        }
        for task_run, definition in rows
    ]


def _mark_task_failed(db, task_run: PipelineTaskRun, error: Exception | str) -> None:
    task_run.status = "FAILED"
    task_run.error = str(error)[:2000]
    task_run.finished_at = _utcnow()
    db.commit()


def _record_event(run: PipelineRun, event: str, *, task_run: PipelineTaskRun | None = None,
                  result: str = "accepted", error: Exception | None = None) -> None:
    """Best-effort telemetry must never change durable scheduling semantics."""
    try:
        log_pipeline_event(event, run=run, task_run=task_run, result=result, error=error)
        if task_run is not None and event in {"node_success", "node_failed", "node_skipped"}:
            observe_task_terminal(run, task_run, result)
        if event == "terminal":
            observe_run_terminal(run, result)
        audit_pipeline_event(event, run=run, task_run=task_run, result=result)
    except Exception:
        # Observability is non-authoritative; a telemetry outage cannot alter a run.
        pass


def mark_run_dispatch_failed(db, run: PipelineRun, error: Exception | str) -> None:
    """Persist broker submission failure so an accepted run cannot stay RUNNING."""
    now = _utcnow()
    message = f"流水线协调队列提交失败: {error}"[:2000]
    run.status = "FAILED"
    run.error_message = message
    run.finished_at = now
    for task_run in run.task_runs:
        if task_run.status not in TERMINAL_TASK_STATES:
            task_run.status = "FAILED" if task_run.status == "RUNNING" else "SKIPPED"
            task_run.error = message
            task_run.finished_at = now
    db.commit()
    _record_event(run, "terminal", result="failed", error=error if isinstance(error, Exception) else None)
    refresh_running_gauge(db)


def _validated_dataset(db, dataset_id: int, run: PipelineRun) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == int(dataset_id)).first()
    if not dataset or dataset.organization_id != run.organization_id:
        raise ValueError("数据集不存在或不属于当前组织")
    actual_size = int(dataset.size_bytes or 0)
    if dataset.file_path:
        try:
            actual_size = max(actual_size, Path(dataset.file_path).stat().st_size)
        except OSError:
            pass
    if int(dataset.row_count or 0) > MAX_DATASET_ROWS or actual_size > MAX_DATASET_BYTES:
        raise ValueError("数据集超过流水线容量上限")
    return dataset


def _validated_model(db, model_id: int, run: PipelineRun) -> Model:
    model = db.query(Model).filter(Model.id == int(model_id)).first()
    if not model or model.organization_id != run.organization_id:
        raise ValueError("模型不存在或不属于当前组织")
    return model


def _model_version_path(db, model: Model) -> str | None:
    if model.model_blob:
        return None
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.model_id == model.id)
        .order_by(ModelVersion.id.desc())
        .first()
    )
    return str(version.file_path) if version and version.file_path else None


def _model_evaluation_contract(db, model: Model) -> dict:
    """Return the persisted training schema for a registered model."""
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.model_id == model.id)
        .order_by(ModelVersion.id.desc())
        .first()
    )
    parameters = getattr(version, "parameters", None) if version else None
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except (TypeError, ValueError):
            parameters = {}
    if not isinstance(parameters, dict):
        return {}
    feature_columns = parameters.get("feature_columns")
    target_column = parameters.get("target_column")
    contract = {}
    if (
        isinstance(feature_columns, list)
        and feature_columns
        and all(isinstance(item, str) and item for item in feature_columns)
    ):
        contract["feature_columns"] = list(feature_columns)
    if isinstance(target_column, str) and target_column:
        contract["target_column"] = target_column
    return contract

def _enrich_pipeline_cleaning_rule(
    db, config: dict, organization_id: int
) -> dict:
    """Resolve the stored rule ID immediately before the durable node executes."""
    effective_config = dict(config or {})
    if str(effective_config.get("operation") or "").lower() != "clean_rule":
        return effective_config
    from services.data_cleaning_service import (
        CleaningRuleError,
        resolve_active_rule_for_pipeline,
    )

    try:
        effective_config.update(
            resolve_active_rule_for_pipeline(
                db,
                effective_config.get("cleaning_rule_id"),
                organization_id,
            )
        )
    except CleaningRuleError as exc:
        raise ValueError(str(exc)) from exc
    return effective_config


def _execute_adapter_node(db, config: dict, run: PipelineRun, task_type: str) -> dict:
    """Re-authorize durable resources against the immutable run organisation."""
    from services.pipeline_node_executor import (
        AuthorizedDataset,
        AuthorizedModel,
        SQLAlchemyDerivedDatasetSink,
        VerifiedResourceAccess,
        execute_pipeline_node,
    )

    normalized_type = ADAPTER_TASK_TYPE_ALIASES[task_type]
    # 评估节点必须使用用户明确选择的独立测试集；历史运行未提供时才兼容继承数据集。
    dataset_id = (
        config.get("evaluation_dataset_id")
        if normalized_type == "model_evaluate" and config.get("evaluation_dataset_id") not in (None, "")
        else config.get("dataset_id")
    )
    if dataset_id in (None, ""):
        raise ValueError(f"{normalized_type} 必须配置 dataset_id")
    dataset = _validated_dataset(db, int(dataset_id), run)
    if normalized_type == "data_process":
        config = _enrich_pipeline_cleaning_rule(
            db, config, int(run.organization_id)
        )
    datasets = {dataset.id: AuthorizedDataset.from_record(dataset)}
    models = {}
    model_contract = {}
    if normalized_type == "model_evaluate":
        model_id = config.get("model_id")
        if model_id in (None, ""):
            raise ValueError("model_evaluate 必须配置 model_id")
        model = _validated_model(db, int(model_id), run)
        model_contract = _model_evaluation_contract(db, model)
        models[model.id] = AuthorizedModel.from_record(
            model,
            file_path=_model_version_path(db, model),
        )
    access = VerifiedResourceAccess(
        organization_id=int(run.organization_id),
        datasets=datasets,
        models=models,
    )
    sink = None
    if normalized_type in DERIVED_DATA_TASK_TYPES:
        sink = SQLAlchemyDerivedDatasetSink(db, max_bytes=MAX_DATASET_BYTES)
    execution_config = dict(config)
    # 下游训练节点会传递训练数据集 identity；执行评估时需明确替换为测试集。
    if normalized_type == "model_evaluate":
        execution_config["dataset_id"] = int(dataset.id)
        # Use the schema captured during training unless this node explicitly overrides it.
        if not execution_config.get("feature_columns") and model_contract.get("feature_columns"):
            execution_config["feature_columns"] = model_contract["feature_columns"]
        if not execution_config.get("target_column") and model_contract.get("target_column"):
            execution_config["target_column"] = model_contract["target_column"]
    return execute_pipeline_node(
        normalized_type,
        execution_config,
        access=access,
        derived_dataset_sink=sink,
    )


def assert_adapter_dispatch_safe(task_type: str, action: str) -> None:
    """Never repeat a derived-data write after an uncertain worker outcome."""
    if task_type in DERIVED_DATA_TASK_TYPES and action == "resume":
        raise RuntimeError("派生数据节点执行结果不确定，为避免重复写入拒绝自动重放")


def _training_row(db, job_id: str):
    return db.execute(
        text(
            "SELECT status, metrics, model_id, error_message FROM training_jobs "
            "WHERE training_job_id=:job_id"
        ),
        {"job_id": job_id},
    ).fetchone()


def _poll_training(
    db, task_run: PipelineTaskRun, config: dict, now: datetime, timeout_seconds: int
) -> str:
    outputs = dict(task_run.outputs or {})
    job_id = outputs.get("job_id")
    if not job_id:
        raise RuntimeError("训练节点处于 RUNNING 但缺少 job_id，拒绝重复派发")
    if outputs.get("dispatch_state") != "DISPATCHED":
        raise RuntimeError("训练派发状态不确定，为避免重复训练拒绝重投")
    row = _training_row(db, str(job_id))
    status = str(row[0]).upper() if row else "MISSING"
    if status in {"COMPLETED", "SUCCESS"}:
        metrics = row[1] if isinstance(row[1], dict) else {}
        if isinstance(row[1], str):
            try:
                metrics = json.loads(row[1])
            except (TypeError, ValueError):
                metrics = {}
        model_id = row[2]
        model_name = None
        if model_id not in (None, ""):
            model = db.query(Model).filter(Model.id == int(model_id)).first()
            model_name = str(model.name) if model and model.name else None
        completed_outputs = {
            **outputs, "status": status, "metrics": metrics, "model_id": model_id,
            "model_name": model_name,
            "saved_to_model_management": bool(config.get("save_model", True)),
        }
        if not bool(config.get("save_model", True)) and row[2] not in (None, ""):
            completed_outputs["transient_model_id"] = int(row[2])
        task_run.outputs = propagate_identity_outputs(config, completed_outputs)
        task_run.status = "SUCCESS"
        task_run.logs = f"训练任务 {job_id} 已完成"
        task_run.finished_at = now
        db.commit()
        return "SUCCESS"
    if status in {"FAILED", "CANCELLED"}:
        raise RuntimeError((row[3] if row else None) or f"训练任务 {status}")
    if _timed_out(task_run.started_at, timeout_seconds, now):
        raise TimeoutError(f"训练任务在 {timeout_seconds} 秒内未完成")
    return "RUNNING"


def _dispatch_training(
    db, task_run: PipelineTaskRun, config: dict, run: PipelineRun, timeout_seconds: int
) -> None:
    dataset_id = config.get("dataset_id")
    if dataset_id in (None, ""):
        raise ValueError("model_train 必须配置 dataset_id")
    dataset = _validated_dataset(db, int(dataset_id), run)

    from services.real_training_service import TrainingService
    from tasks.training_tasks import train_model

    context = run.validation_context or {}
    # ``_upstream_outputs`` is orchestration-only lineage metadata. It is
    # useful while resolving the DAG, but must never cross the trust boundary
    # into TrainingService's user-config validation.
    user_training_config = dict(config)
    user_training_config.pop("_upstream_outputs", None)
    # Input bindings are orchestration metadata, never algorithm hyperparameters.
    user_training_config.pop("input_bindings", None)
    # Output-saving switches belong to the pipeline, not to the training
    # service's algorithm configuration. Keep them on the original node config
    # for completion/cleanup handling, but never pass them through the strict
    # TrainingService allow-list.
    for pipeline_output_key in (
        "save_model",
        "save_dataset",
        "save_evaluation_record",
        "evaluation_name",
    ):
        user_training_config.pop(pipeline_output_key, None)
    from services.pipeline_training_config import enrich_pipeline_training_config

    user_training_config = enrich_pipeline_training_config(db, user_training_config)
    training_task_type = user_training_config.pop(
        "training_task_type",
        user_training_config.get("task_type", "regression"),
    )
    training_config = {
        **user_training_config,
        "name": user_training_config.get("name") or "pipeline-training",
        "task_type": training_task_type,
        "model_type": user_training_config.get("model_type", "random_forest"),
        "dataset_id": int(dataset_id),
        "organization_id": run.organization_id,
        "validation_context": context,
        "validation_run_id": context.get("validation_run_id"),
    }
    job_id = TrainingService.create_job(training_config)
    celery_task_id = f"pipeline-training-{task_run.id}-{job_id}"
    # Persist intent before touching the broker. If the process dies in the
    # intent/send gap, reconciliation fails closed instead of training twice.
    task_run.status = "RUNNING"
    task_run.started_at = task_run.started_at or _utcnow()
    task_run.celery_task_id = celery_task_id
    task_run.outputs = {
        "job_id": job_id,
        "dataset_id": int(dataset.id),
        "dataset_name": str(dataset.name or "dataset"),
        "celery_task_id": celery_task_id,
        "dispatch_state": "INTENT_RECORDED",
    }
    db.commit()
    try:
        train_model.apply_async(
            kwargs={
                "model_id": 0,
                "dataset_id": int(dataset_id),
                "config": training_config,
                "job_id": job_id,
            },
            task_id=celery_task_id,
            expires=timeout_seconds,
        )
    except Exception as exc:
        db.execute(
            text(
                "UPDATE training_jobs SET status='FAILED', error_message=:error, completed_at=NOW() "
                "WHERE training_job_id=:job_id"
            ),
            {"job_id": job_id, "error": f"训练队列提交失败: {exc}"[:500]},
        )
        db.commit()
        raise RuntimeError(f"训练队列提交失败: {exc}") from exc
    task_run.outputs = {**(task_run.outputs or {}), "dispatch_state": "DISPATCHED"}
    task_run.logs = f"训练任务已进入异步队列: {job_id}"
    db.commit()


def _persist_pipeline_evaluation_record(db, config: dict, outputs: dict) -> int | None:
    if not bool(config.get("save_evaluation_record", True)):
        return None
    model_id, dataset_id, metrics = outputs.get("model_id"), outputs.get("dataset_id"), outputs.get("metrics")
    if model_id in (None, "") or dataset_id in (None, "") or not isinstance(metrics, dict):
        raise ValueError("评估结果缺少模型、测试数据集或指标，无法保存评估记录")
    evaluation_name = str(config.get("evaluation_name") or "流水线评测记录").strip()[:255]
    row = db.execute(text(
        "INSERT INTO model_evaluation_results "
        "(name, model_id, dataset_id, task_type, metrics, cross_validation, cv_folds, created_at) "
        "VALUES (:name, :model_id, :dataset_id, :task_type, CAST(:metrics AS jsonb), CAST('false' AS jsonb), NULL, NOW()) "
        "RETURNING id"
    ), {"name": evaluation_name, "model_id": int(model_id), "dataset_id": int(dataset_id),
        "task_type": str(outputs.get("task_type") or "regression"),
        "metrics": json.dumps(metrics, ensure_ascii=False)}).fetchone()
    db.commit()
    return int(row[0]) if row else None


def _cleanup_transient_pipeline_outputs(db, task_runs: list[PipelineTaskRun]) -> None:
    transient_dataset_ids, transient_model_ids = set(), set()
    for task_run in task_runs:
        outputs = task_run.outputs if isinstance(task_run.outputs, dict) else {}
        for key, bucket in (("transient_dataset_id", transient_dataset_ids), ("transient_model_id", transient_model_ids)):
            try:
                if outputs.get(key) not in (None, ""):
                    bucket.add(int(outputs[key]))
            except (TypeError, ValueError):
                continue
    paths_to_remove = []
    for dataset_id in sorted(transient_dataset_ids):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not dataset:
            continue
        if dataset.file_path:
            paths_to_remove.append(Path(dataset.file_path))
        db.execute(text("UPDATE training_jobs SET dataset_id=NULL WHERE dataset_id=:dataset_id"), {"dataset_id": dataset_id})
        db.execute(text("UPDATE model_evaluation_results SET dataset_id=NULL WHERE dataset_id=:dataset_id"), {"dataset_id": dataset_id})
        db.delete(dataset)
    if transient_dataset_ids:
        db.commit()
    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if transient_model_ids:
        from api.models import delete_model
        for model_id in sorted(transient_model_ids):
            if not db.query(Model).filter(Model.id == model_id).first():
                continue
            try:
                delete_model(model_id, db)
            except Exception:
                db.rollback()


def _finalize_run(db, run: PipelineRun, task_runs: list[PipelineTaskRun]) -> dict:
    _cleanup_transient_pipeline_outputs(db, task_runs)
    failed = [item for item in task_runs if item.status == "FAILED"]
    run.status = "FAILED" if failed else "SUCCESS"
    run.finished_at = _utcnow()
    if run.started_at:
        run.duration_seconds = max(0, int((run.finished_at - run.started_at).total_seconds()))
    run.error_message = "流水线节点失败，后续依赖未执行" if failed else None
    db.commit()
    result = "failed" if failed else "success"
    _record_event(run, "terminal", result=result)
    refresh_running_gauge(db)
    return {"run_id": run.id, "status": run.status, "pending": False}


def reconcile_pipeline_run(db, run_id: int) -> dict:
    """Reconcile a persisted run once; safe to call repeatedly."""
    run = db.query(PipelineRun).filter(PipelineRun.id == int(run_id)).with_for_update().first()
    if not run:
        return {"run_id": run_id, "status": "NOT_FOUND", "pending": False}
    if run.status in {"SUCCESS", "FAILED", "CANCELLED"}:
        return {"run_id": run.id, "status": run.status, "pending": False}
    pipeline = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
    if not pipeline:
        run.status = "FAILED"
        run.error_message = "流水线定义不存在"
        run.finished_at = _utcnow()
        db.commit()
        return {"run_id": run.id, "status": run.status, "pending": False}

    now = _utcnow()
    run.status = "RUNNING"
    run.started_at = run.started_at or now
    ensure_run_trace(run)
    if now >= run.started_at + timedelta(seconds=MAX_RUN_SECONDS):
        for item in run.task_runs:
            if item.status not in TERMINAL_TASK_STATES:
                item.status = "FAILED" if item.status == "RUNNING" else "SKIPPED"
                item.error = f"流水线超过最长运行时间 {MAX_RUN_SECONDS} 秒"
                item.finished_at = now
        return _finalize_run(db, run, list(run.task_runs))
    db.commit()

    specs = _run_specs(db, run)
    task_runs = {
        item.id: item
        for item in db.query(PipelineTaskRun)
        .filter(PipelineTaskRun.pipeline_run_id == run.id)
        .all()
    }
    by_name = {item.task_name: item for item in task_runs.values()}
    if not specs or len(specs) != len(task_runs):
        run.status = "FAILED"
        run.error_message = "运行快照与任务记录不一致"
        run.finished_at = _utcnow()
        db.commit()
        return {"run_id": run.id, "status": run.status, "pending": False}

    for spec in specs:
        task_run = task_runs.get(int(spec["task_run_id"]))
        if not task_run:
            continue
        dependencies = list(spec.get("dependencies") or [])
        missing = [name for name in dependencies if name not in by_name]
        if missing:
            _mark_task_failed(db, task_run, f"运行快照依赖不存在: {', '.join(missing)}")
            continue
        action = node_action(task_run.status, [by_name[name].status for name in dependencies])
        if action in {"terminal", "wait"}:
            continue
        if action == "skip":
            task_run.status = "SKIPPED"
            task_run.error = "依赖节点失败，未执行: " + ", ".join(
                sorted(name for name in dependencies if by_name[name].status in FAILED_TASK_STATES)
            )
            task_run.finished_at = _utcnow()
            db.commit()
            _record_event(run, "node_skipped", task_run=task_run, result="skipped")
            continue

        dependency_outputs = {name: dict(by_name[name].outputs or {}) for name in dependencies}
        attempt = int((task_run.outputs or {}).get("_orchestrator_attempt", 0)) + 1
        try:
            config = merge_dependency_outputs(spec.get("config") or {}, dependency_outputs)
            timeout_seconds = _timeout_seconds(spec)
            if task_run.task_type in ADAPTER_TASK_TYPE_ALIASES:
                assert_adapter_dispatch_safe(task_run.task_type, action)
                task_run.status = "RUNNING"
                task_run.started_at = task_run.started_at or _utcnow()
                task_run.outputs = {
                    **(task_run.outputs or {}),
                    "_orchestrator_attempt": attempt,
                    "dispatch_state": "INTENT_RECORDED",
                }
                ensure_task_trace(run, task_run)
                db.commit()
                _record_event(run, "node_start", task_run=task_run, result="accepted")
                outputs = _execute_adapter_node(db, config, run, task_run.task_type)
                lineage_config = dict(config)
                if task_run.task_type in DERIVED_DATA_TASK_TYPES:
                    # A transformation legitimately replaces its source dataset.
                    lineage_config.pop("dataset_id", None)
                elif task_run.task_type == "model_evaluate" and config.get("evaluation_dataset_id") not in (None, ""):
                    # The evaluation output belongs to the explicit test dataset, not the training input.
                    lineage_config["dataset_id"] = int(config["evaluation_dataset_id"])
                completed_outputs = {
                    **outputs,
                    "_orchestrator_attempt": attempt,
                    "dispatch_state": "COMPLETED",
                }
                if task_run.task_type == "model_evaluate":
                    evaluation_record_id = _persist_pipeline_evaluation_record(db, config, outputs)
                    completed_outputs["saved_to_evaluation_records"] = evaluation_record_id is not None
                    if evaluation_record_id is not None:
                        completed_outputs["evaluation_record_id"] = evaluation_record_id
                        completed_outputs["evaluation_name"] = str(config.get("evaluation_name") or "流水线评测记录").strip()
                task_run.outputs = propagate_identity_outputs(lineage_config, completed_outputs)
                task_run.status = "SUCCESS"
                task_run.error = None
                task_run.logs = f"真实节点 {task_run.task_type} 执行成功"
                task_run.finished_at = _utcnow()
                db.commit()
                _record_event(run, "node_success", task_run=task_run, result="success")
            elif task_run.task_type in TRAINING_TASK_TYPES:
                if action == "execute":
                    _dispatch_training(db, task_run, config, run, timeout_seconds)
                    ensure_task_trace(run, task_run)
                    _record_event(run, "node_start", task_run=task_run, result="accepted")
                else:
                    polled = _poll_training(db, task_run, config, _utcnow(), timeout_seconds)
                    if polled == "SUCCESS":
                        _record_event(run, "node_success", task_run=task_run, result="success")
            else:
                raise NotImplementedError(f"节点 {task_run.task_type} 尚无生产执行器")
        except Exception as exc:
            db.rollback()
            if isinstance(exc, DatabaseOperationalError):
                raise
            current = db.query(PipelineTaskRun).filter(PipelineTaskRun.id == task_run.id).first()
            if current and current.status not in TERMINAL_TASK_STATES:
                if retry_allowed(current.task_type, attempt, _retry_limit(spec)):
                    current.status = "PENDING"
                    current.error = (
                        f"第 {attempt} 次执行失败，将在下一协调周期重试: {str(exc)[:1500]}"
                    )
                    current.finished_at = None
                    db.commit()
                else:
                    _mark_task_failed(db, current, exc)
                    _record_event(run, "node_failed", task_run=current, result="failed", error=exc)

    current_runs = db.query(PipelineTaskRun).filter(PipelineTaskRun.pipeline_run_id == run.id).all()
    if all(item.status in TERMINAL_TASK_STATES for item in current_runs):
        return _finalize_run(db, run, current_runs)
    db.commit()
    return {"run_id": run.id, "status": "RUNNING", "pending": True}


@celery_app.task(
    bind=True,
    name="pipeline.execute_run",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=75,
    time_limit=90,
    autoretry_for=(DatabaseOperationalError,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=5,
    retry_kwargs={"max_retries": 5},
)
def execute_pipeline_run(self, run_id: int):
    """Run one recovery pass and schedule another only while work is pending."""
    with coordinator_lock(int(run_id)) as acquired:
        if not acquired:
            raise self.retry(countdown=1, max_retries=10)
        db = SessionLocal()
        try:
            message_run = db.query(PipelineRun).filter(PipelineRun.id == int(run_id)).first()
            if message_run and not coordinator_message_current(
                message_run.parameters or {}, self.request.id
            ):
                if coordinator_intent_repairable(
                    message_run.parameters or {}, self.request.id
                ):
                    try:
                        expected = (message_run.parameters or {})["orchestrator"][
                            "coordinator_task_id"
                        ]
                        repaired = _publish_coordinator_intent(
                            db, message_run, int(run_id), str(expected)
                        )
                        _record_event(message_run, "coordinator_recovery", result="recovered")
                        return {
                            "run_id": int(run_id),
                            "status": "INTENT_REPAIRED",
                            "pending": True,
                            "coordinator_task_id": repaired.id,
                        }
                    except DatabaseOperationalError:
                        raise
                    except Exception as exc:
                        db.rollback()
                        failed = (
                            db.query(PipelineRun)
                            .filter(PipelineRun.id == int(run_id))
                            .first()
                        )
                        if failed and failed.status not in {
                            "SUCCESS",
                            "FAILED",
                            "CANCELLED",
                        }:
                            mark_run_dispatch_failed(db, failed, exc)
                        return {
                            "run_id": int(run_id),
                            "status": "FAILED",
                            "pending": False,
                            "error": str(exc),
                        }
                return {"run_id": int(run_id), "status": "STALE", "pending": False}
            result = reconcile_pipeline_run(db, int(run_id))
            if not result.get("pending"):
                return result
            try:
                run = db.query(PipelineRun).filter(PipelineRun.id == int(run_id)).first()
                if not run or run.status != "RUNNING":
                    return {**result, "pending": False}
                next_task = enqueue_next_coordinator(
                    db,
                    run,
                    int(run_id),
                    self.request.id,
                )
                return {**result, "coordinator_task_id": next_task.id}
            except DatabaseOperationalError:
                raise
            except Exception as exc:
                db.rollback()
                run = db.query(PipelineRun).filter(PipelineRun.id == int(run_id)).first()
                if run and run.status not in {"SUCCESS", "FAILED", "CANCELLED"}:
                    mark_run_dispatch_failed(db, run, exc)
                return {
                    "run_id": int(run_id),
                    "status": "FAILED",
                    "pending": False,
                    "error": str(exc),
                }
        finally:
            db.close()
