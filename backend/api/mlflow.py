"""
MLflow实验追踪API
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db
from api.deps import require_auth, require_permission
from api.tenant_access import is_admin
from services.mlflow_service import get_mlflow_service
from services.mlflow_tracker import get_mlflow_tracker

router = APIRouter(prefix="/mlflow", tags=["实验追踪"])


class TransitionStageReq(BaseModel):
    stage: str  # None / Staging / Production / Archived


class CreateExperimentReq(BaseModel):
    name: str
    tags: Optional[Dict] = {}


class StartRunReq(BaseModel):
    experiment_id: str
    run_name: Optional[str] = None
    tags: Optional[Dict] = {}


class LogMetricReq(BaseModel):
    key: str
    value: float
    step: Optional[int] = 0


class LogParamsReq(BaseModel):
    params: Dict


class CompareModelsReq(BaseModel):
    model_ids: List[int] = Field(min_length=2)
    dataset_id: int
    task_type: str = "regression"
    target_column: Optional[str] = None
    feature_columns: Optional[List[str]] = None
    test_size: float = 1.0  # legacy compatibility; evaluation always uses the full dataset


class ReproduceReq(BaseModel):
    name: Optional[str] = None


@router.get("/status")
def get_mlflow_status():
    """获取MLflow服务状态"""
    from services.mlflow_service import MLFLOW_AVAILABLE

    return {
        "available": MLFLOW_AVAILABLE,
        "tracking_uri": get_mlflow_service().tracking_uri,
    }


# ── Model Registry(2026-07-15:启用,后端已切 postgres 支持 Registry)──
def _json_dict(value: Any) -> Dict:
    """兼容 PostgreSQL JSON 字段和历史字符串字段。"""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _tracking_params(values: Dict) -> Dict:
    """追踪页只返回配置；历史 Run 中误写入的训练矩阵不下发到浏览器。"""
    excluded = {"X", "y", "feature_baseline", "validation_report"}
    result = {}
    for key, value in (values or {}).items():
        if key in excluded:
            continue
        try:
            if len(json.dumps(value, ensure_ascii=False, default=str)) > 2000:
                continue
        except (TypeError, ValueError):
            continue
        result[key] = value
    return result


def _parse_list(value: Any) -> Optional[List[str]]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return [str(item) for item in parsed] if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            return None
    return None


def _version_context(name: str, version: int, db: Session) -> Dict:
    registry = get_mlflow_tracker().get_registry_model(name)
    registry_version = next(
        (item for item in (registry or {}).get("versions", []) if int(item.get("version") or 0) == version),
        None,
    )
    if not registry_version:
        raise HTTPException(status_code=404, detail="模型版本不存在")

    run_id = registry_version.get("run_id")
    row = db.execute(text("""
        SELECT m.id, m.name, m.model_type, m.framework,
               mv.id, mv.parameters, mv.metrics, mv.file_path,
               tj.name, tj.dataset_id, tj.hyperparameters, tj.training_job_id
        FROM models m
        LEFT JOIN model_versions mv ON mv.model_id = m.id
          AND (mv.mlflow_run_id = :run_id OR mv.mlflow_model_version = :version)
        LEFT JOIN training_jobs tj ON tj.id = (
            SELECT candidate.id FROM training_jobs candidate
            WHERE candidate.mlflow_run_id = :run_id
               OR candidate.registered_model_id = m.id
            ORDER BY CASE WHEN candidate.mlflow_run_id = :run_id THEN 0 ELSE 1 END,
                     candidate.id DESC
            LIMIT 1
        )
        WHERE m.mlflow_name = :name
        ORDER BY mv.id DESC NULLS LAST
        LIMIT 1
    """), {"name": name, "version": str(version), "run_id": run_id}).fetchone()

    run = get_mlflow_service().get_run(run_id) if run_id else {}
    run_params = {} if "error" in run else (run.get("params") or {})
    local_params = _json_dict(row[5]) if row else {}
    job_params = _json_dict(row[10]) if row else {}
    return {
        "registry": registry_version,
        "run": run,
        "row": row,
        # MLflow params are serialized as strings; keep the DB training snapshot
        # authoritative so reproduction preserves integer/list/bool types.
        "params": {**run_params, **local_params, **job_params},
    }


def _reconstructed_runtime_source(model_type: str) -> Optional[str]:
    """Return current reviewed adapter source for legacy runs without a snapshot."""
    try:
        import inspect
        from services.real_training_service import (
            PyTorchPredictor, PyTorchTrainer, SklearnTrainer,
        )
        normalized = str(model_type or "").lower()
        header = (
            "# Open Foundation reconstructed runtime adapter source\n"
            "# This legacy run did not persist an immutable source file.\n"
            "# The code below is the current implementation for its recorded adapter.\n\n"
        )
        if normalized in ("mlp", "neural_network"):
            return header + inspect.getsource(PyTorchPredictor) + "\n\n" + inspect.getsource(PyTorchTrainer)
        if normalized in ("random_forest", "lightgbm", "xgboost"):
            return header + inspect.getsource(SklearnTrainer)
    except Exception:
        return None
    return None


def _status_value(value: Any) -> str:
    """Normalize SQLAlchemy enums and historical strings for log output."""
    raw = getattr(value, "value", value)
    return str(raw or "unknown").lower()


def _log_metric_text(metrics: Dict) -> str:
    """Render one epoch without leaking large training payloads into the UI."""
    parts = []
    for key, value in (metrics or {}).items():
        if key in {"epoch", "timestamp"}:
            continue
        if isinstance(value, float):
            parts.append(f"{key}={value:.6g}")
        elif isinstance(value, (int, str, bool)) or value is None:
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "本轮未记录指标"


def _training_log_payload(context: Dict, db: Session) -> Dict:
    """Build a durable, structured training log from the persisted run facts.

    Real-training stores its immutable configuration, state transitions and every
    epoch metric under ``uploads/runs/<job_id>.json``.  This endpoint deliberately
    reads that fact store instead of container stdout, so logs remain available
    after a backend restart.  Older jobs without a run file fall back to the DB
    summary and MLflow run metadata.
    """
    from pathlib import Path

    row = context.get("row")
    params = context.get("params") or {}
    registry = context.get("registry") or {}
    run = context.get("run") or {}
    job_id = (row[11] if row else None) or params.get("job_id") or params.get("training_job_id")
    run_id = registry.get("run_id") or params.get("mlflow_run_id")

    db_job = None
    if job_id:
        db_job = db.execute(text("""
            SELECT name, status, started_at, completed_at, error_message,
                   created_at, dataset_id, metrics
            FROM training_jobs
            WHERE training_job_id = :job_id
            ORDER BY id DESC
            LIMIT 1
        """), {"job_id": str(job_id)}).fetchone()

    run_data: Dict = {}
    if job_id:
        run_path = Path("/app/uploads/runs") / f"{job_id}.json"
        try:
            allowed_root = Path("/app/uploads/runs").resolve()
            candidate = run_path.resolve()
            if candidate.parent == allowed_root and candidate.is_file():
                parsed = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    run_data = parsed
        except (OSError, TypeError, ValueError):
            run_data = {}

    config = _tracking_params({**params, **_json_dict(run_data.get("config"))})
    history = run_data.get("metrics_history") or []
    if not isinstance(history, list):
        history = []
    status = _status_value(
        run_data.get("status") or (db_job[1] if db_job else None) or run.get("status")
    )
    total_epochs = run_data.get("total_epochs") or config.get("epochs") or len(history)
    current_epoch = run_data.get("current_epoch") or (history[-1].get("epoch") if history else 0)
    started_at = run_data.get("started_at") or (db_job[2].isoformat() if db_job and db_job[2] else None)
    finished_at = run_data.get("finished_at") or (db_job[3].isoformat() if db_job and db_job[3] else None)
    created_at = run_data.get("created_at") or (db_job[5].isoformat() if db_job and db_job[5] else None)
    error = run_data.get("error") or (db_job[4] if db_job else None) or config.get("mlflow_error")
    best_metrics = (
        _json_dict(run_data.get("best_metrics"))
        or (_json_dict(db_job[7]) if db_job else {})
        or (run.get("metrics") if isinstance(run, dict) else {})
    )

    entries: List[Dict[str, Any]] = []

    def add(timestamp: Optional[str], level: str, stage: str, message: str) -> None:
        entries.append({
            "timestamp": timestamp,
            "level": level,
            "stage": stage,
            "message": message,
        })

    if job_id:
        add(created_at, "INFO", "任务", f"创建训练任务 {db_job[0] if db_job else job_id}（{job_id}）")
    if config:
        summary_keys = (
            "model_type", "task_type", "dataset_id", "target_column", "epochs",
            "batch_size", "learning_rate", "hidden_layers", "dropout",
            "n_estimators", "max_depth", "split_strategy",
        )
        summary = {key: config[key] for key in summary_keys if config.get(key) is not None}
        add(created_at, "INFO", "配置", json.dumps(summary, ensure_ascii=False, default=str))
    if started_at:
        add(started_at, "INFO", "训练", "开始训练")
    for item in history:
        if not isinstance(item, dict):
            continue
        epoch = item.get("epoch", "-")
        add(
            item.get("timestamp"), "INFO", "轮次",
            f"Epoch {epoch}/{total_epochs} · {_log_metric_text(item)}",
        )
    if status == "completed":
        add(
            finished_at, "INFO", "完成",
            "训练完成" + (
                f" · 最佳指标: {_log_metric_text(best_metrics)}" if best_metrics else ""
            ),
        )
    elif status in {"failed", "error"}:
        add(finished_at, "ERROR", "失败", f"训练失败: {error or '未记录错误详情'}")
    elif status not in {"unknown", "pending"}:
        add(None, "INFO", "状态", f"当前状态: {status}")
    if run_id:
        add(finished_at, "INFO", "MLflow", f"已关联 MLflow Run {run_id}")
    if error and status not in {"failed", "error"}:
        add(finished_at, "WARNING", "追踪", str(error))

    def render(entry: Dict[str, Any]) -> str:
        timestamp = entry.get("timestamp") or "----/--/-- --:--:--"
        return f"[{timestamp}] [{entry['level']}] [{entry['stage']}] {entry['message']}"

    source = "persisted_run" if run_data else "database_and_mlflow"
    return {
        "available": bool(entries),
        "source": source,
        "source_text": "训练运行记录" if run_data else "数据库与 MLflow 摘要",
        "notice": (
            "日志来自训练任务持久化记录，包含实际配置、状态和逐轮指标；不依赖容器控制台。"
            if run_data else
            "该历史任务没有逐轮运行文件，当前展示数据库与 MLflow 中仍可恢复的摘要。"
        ),
        "training_job_id": str(job_id) if job_id else None,
        "run_id": run_id,
        "status": status,
        "current_epoch": current_epoch,
        "total_epochs": total_epochs,
        "entries": entries,
        "content": "\n".join(render(entry) for entry in entries),
    }


@router.get("/registry")
def list_registry(db: Session = Depends(get_db)):
    """列出注册模型，并补充平台中的可读名称供前端下拉选择。"""
    registry_models = get_mlflow_tracker().list_registry_models()
    names = [item.get("name") for item in registry_models if item.get("name")]
    local_models = {}
    if names:
        rows = db.execute(text("""
            SELECT id, name, mlflow_name, framework, model_type
            FROM models
            WHERE mlflow_name = ANY(:names)
        """), {"names": names}).fetchall()
        local_models = {row[2]: row for row in rows}

    for item in registry_models:
        local = local_models.get(item.get("name"))
        item["model_id"] = local[0] if local else None
        item["display_name"] = local[1] if local else item.get("name")
        item["framework"] = local[3] if local else None
        item["model_type"] = local[4] if local else None

    registry_models.sort(
        key=lambda item: max(
            [v.get("created_at") or 0 for v in item.get("versions", [])] or [0]
        ),
        reverse=True,
    )
    return {"models": registry_models}


@router.get("/registry/{name}/versions")
def get_registry_versions(name: str, db: Session = Depends(get_db)):
    """按注册模型聚合版本、训练任务、数据集和 MLflow Run 追踪信息。"""
    tracker = get_mlflow_tracker()
    registry_model = tracker.get_registry_model(name)
    if not registry_model:
        raise HTTPException(status_code=404, detail="注册模型不存在")

    model_row = db.execute(text("""
        SELECT id, name, description, framework, model_type, stage, created_at
        FROM models
        WHERE mlflow_name = :name
        ORDER BY id DESC
        LIMIT 1
    """), {"name": name}).fetchone()

    local_versions = []
    if model_row:
        local_versions = db.execute(text("""
            SELECT mv.id, mv.version_number, mv.file_path, mv.metrics,
                   mv.parameters, mv.is_deployed, mv.created_at,
                   mv.mlflow_run_id, mv.mlflow_model_version,
                   tj.name AS training_name, tj.training_job_id,
                   tj.status AS training_status, tj.dataset_id,
                   d.name AS dataset_name
            FROM model_versions mv
            LEFT JOIN training_jobs tj ON tj.id = (
                SELECT matched.id
                FROM training_jobs matched
                WHERE matched.mlflow_run_id = mv.mlflow_run_id
                   OR (matched.mlflow_run_id IS NULL AND
                       (matched.registered_model_id = mv.model_id OR matched.model_id = mv.model_id))
                ORDER BY CASE WHEN matched.mlflow_run_id = mv.mlflow_run_id THEN 0 ELSE 1 END,
                         matched.id DESC
                LIMIT 1
            )
            LEFT JOIN datasets d ON d.id = tj.dataset_id
            WHERE mv.model_id = :model_id
            ORDER BY mv.id DESC
        """), {"model_id": model_row[0]}).fetchall()

    by_run = {row[7]: row for row in local_versions if row[7]}
    by_registry_version = {str(row[8]): row for row in local_versions if row[8]}
    service = get_mlflow_service()
    versions = []

    for registry_version in registry_model.get("versions", []):
        run_id = registry_version.get("run_id")
        local = by_run.get(run_id) or by_registry_version.get(str(registry_version.get("version")))
        run = service.get_run(run_id) if run_id else {}
        if "error" in run:
            run = {}
        local_metrics = _json_dict(local[3]) if local else {}
        local_params = _json_dict(local[4]) if local else {}
        params = _tracking_params({**local_params, **(run.get("params") or {})})
        metrics = {**local_metrics, **(run.get("metrics") or {})}

        dataset_id = local[12] if local else params.get("dataset_id")
        dataset_name = local[13] if local else None
        if dataset_id and not dataset_name:
            dataset_row = db.execute(
                text("SELECT name FROM datasets WHERE id = :dataset_id"),
                {"dataset_id": dataset_id},
            ).fetchone()
            dataset_name = dataset_row[0] if dataset_row else None

        versions.append({
            "version": registry_version.get("version"),
            "stage": registry_version.get("stage"),
            "registry_status": registry_version.get("status"),
            "registry_created_at": registry_version.get("created_at"),
            "run_id": run_id,
            "run_status": run.get("status"),
            "start_time": run.get("start_time"),
            "end_time": run.get("end_time"),
            "metrics": metrics,
            "params": params,
            "artifacts": tracker.get_artifacts(run_id) if run_id else [],
            "local_version_id": local[0] if local else None,
            "version_number": local[1] if local else None,
            "file_path": local[2] if local else None,
            "is_deployed": bool(local[5]) if local else False,
            "created_at": local[6].isoformat() if local and local[6] else registry_version.get("created_at"),
            "training_name": local[9] if local else None,
            "training_job_id": local[10] if local else params.get("job_id") or params.get("training_job_id"),
            "training_status": local[11] if local else None,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
        })

    versions.sort(key=lambda item: int(item.get("version") or 0), reverse=True)
    return {
        "model": {
            "registry_name": name,
            "model_id": model_row[0] if model_row else None,
            "display_name": model_row[1] if model_row else name,
            "description": model_row[2] if model_row else None,
            "framework": model_row[3] if model_row else None,
            "model_type": model_row[4] if model_row else None,
            "stage": model_row[5] if model_row else None,
            "created_at": model_row[6].isoformat() if model_row and model_row[6] else None,
            "version_count": len(versions),
        },
        "versions": versions,
    }


@router.get("/registry/{name}/versions/{version}/snapshot")
def get_code_snapshot(name: str, version: int, db: Session = Depends(get_db)):
    """Return immutable executed code, or a clearly marked legacy reconstruction."""
    context = _version_context(name, version, db)
    params = context["params"]
    algorithm_id = params.get("algorithm_id")
    algorithm = None
    source_code = None
    source_filename = params.get("code_snapshot_filename")
    snapshot_type = None
    reconstructed = False
    snapshot_path = params.get("code_snapshot_path")
    if snapshot_path:
        from pathlib import Path
        allowed_root = Path("/app/uploads/code_snapshots").resolve()
        candidate = Path(str(snapshot_path)).resolve()
        if candidate.is_relative_to(allowed_root) and candidate.is_file():
            try:
                source_code = candidate.read_text(encoding="utf-8")
                snapshot_type = "immutable_runtime"
            except OSError:
                source_code = None
    if algorithm_id:
        algorithm = db.execute(text("""
            SELECT id, name, version, framework, entry_point, metrics, updated_at
            FROM algorithm_models WHERE id = :algorithm_id
        """), {"algorithm_id": int(algorithm_id)}).fetchone()
        if algorithm:
            metadata = _json_dict(algorithm[5])
            if not source_code:
                source_code = metadata.get("source_code") or metadata.get("code")
                if source_code:
                    snapshot_type = "uploaded_reference"
            source_filename = source_filename or metadata.get("source_filename") or algorithm[4]

    if not source_code:
        source_code = _reconstructed_runtime_source(params.get("model_type"))
        if source_code:
            source_filename = f"reconstructed_runtime_{params.get('model_type') or 'adapter'}.py"
            snapshot_type = "reconstructed_runtime"
            reconstructed = True

    return {
        "registry_name": name,
        "version": version,
        "run_id": context["registry"].get("run_id"),
        "available": bool(source_code),
        "language": "python" if source_code else None,
        "filename": source_filename,
        "sha256": params.get("code_snapshot_sha256"),
        "source_code": source_code,
        "snapshot_type": snapshot_type,
        "reconstructed": reconstructed,
        "algorithm": {
            "id": algorithm[0], "name": algorithm[1], "version": algorithm[2],
            "framework": algorithm[3], "entry_point": algorithm[4],
            "updated_at": algorithm[6].isoformat() if algorithm[6] else None,
        } if algorithm else None,
        "notice": (
            "该历史任务未保存不可变源码；当前展示的是同类训练适配器的现行实现。"
            if reconstructed else
            "该快照记录了训练任务实际调用的运行时适配器代码。"
            if snapshot_type == "immutable_runtime" else
            "该内容来自上传算法，仅作为参考；平台训练实际使用安全适配器。"
            if snapshot_type == "uploaded_reference" else None
        ),
        "reason": None if source_code else "没有可恢复的训练代码或适配器信息。",
    }


@router.get("/registry/{name}/versions/{version}/logs")
def get_version_training_logs(name: str, version: int, db: Session = Depends(get_db)):
    """Read the durable training log associated with one Registry version."""
    context = _version_context(name, version, db)
    payload = _training_log_payload(context, db)
    return {
        "registry_name": name,
        "version": version,
        **payload,
    }


@router.post(
    "/registry/{name}/versions/{version}/reproduce",
    dependencies=[Depends(require_permission("training:write"))],
)
def reproduce_model(
    name: str,
    version: int,
    req: ReproduceReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """用原数据集、算法和超参数创建一条新的真实训练任务。"""
    from api.real_training import DatasetJobTrainingRequest, create_training_job_from_dataset

    context = _version_context(name, version, db)
    row = context["row"]
    if not row:
        raise HTTPException(status_code=400, detail="该 Registry 版本没有平台模型记录，无法复现")
    params = context["params"]
    dataset_id = row[9] or params.get("dataset_id")
    if not dataset_id:
        raise HTTPException(status_code=400, detail="原训练未记录数据集，无法复现")

    hyperparameter_keys = {
        "epochs", "batch_size", "learning_rate", "hidden_layers", "dropout",
        "n_estimators", "max_depth", "min_samples_split", "min_samples_leaf",
        "normalize", "num_leaves", "subsample",
    }
    hyperparameters = {
        key: value for key, value in params.items() if key in hyperparameter_keys
    }
    algorithm_id = params.get("algorithm_id")
    task_type = params.get("business_task_type") or params.get("task_type") or row[2] or "regression"
    model_type = params.get("model_type") or "random_forest"
    target_column = params.get("target_column") or params.get("target_col") or ""
    feature_columns = _parse_list(params.get("feature_columns") or params.get("feature_cols"))
    if not target_column and feature_columns:
        import csv
        import os
        dataset_file = db.execute(
            text("SELECT file_path FROM datasets WHERE id = :dataset_id"),
            {"dataset_id": int(dataset_id)},
        ).fetchone()
        if dataset_file and dataset_file[0] and os.path.exists(dataset_file[0]):
            try:
                with open(dataset_file[0], "r", encoding="utf-8-sig", newline="") as source:
                    columns = next(csv.reader(source), [])
                target_column = next(
                    (column for column in reversed(columns) if column not in feature_columns),
                    "",
                )
            except OSError:
                target_column = ""
        if not target_column:
            feature_columns = None
    task_name = req.name or f"复现-{row[1]}-v{version}-{datetime.now().strftime('%m%d-%H%M')}"

    result = create_training_job_from_dataset(
        DatasetJobTrainingRequest(
            name=task_name,
            task_type=str(task_type),
            model_type=str(model_type),
            dataset_id=int(dataset_id),
            target_column=str(target_column),
            feature_columns=feature_columns,
            hyperparams=json.dumps(hyperparameters, ensure_ascii=False),
            algorithm_id=int(algorithm_id) if algorithm_id else None,
        ),
        db=db,
        current_user=current_user,
    )
    return {
        "message": "复现任务已创建并开始训练",
        "source": {"registry_name": name, "version": version},
        "training": result,
    }


@router.post("/registry/{name}/versions/{version}/stage")
def transition_stage(name: str, version: int, req: TransitionStageReq):
    """切换某模型版本的 stage(Staging/Production/Archived)"""
    ok = get_mlflow_tracker().transition_model_stage(name, int(version), req.stage)
    if not ok:
        raise HTTPException(400, f"切换失败: {name} v{version} → {req.stage}")
    return {"name": name, "version": int(version), "stage": req.stage}



@router.post("/experiments")
def create_experiment(req: CreateExperimentReq):
    """创建实验"""
    service = get_mlflow_service()
    exp_id = service.create_experiment(req.name, req.tags)
    return {"experiment_id": exp_id, "name": req.name}


@router.get("/experiments")
def list_experiments():
    """列出所有实验"""
    from services.mlflow_service import MLFLOW_AVAILABLE
    if not MLFLOW_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="MLflow 依赖未安装，无法读取实验事实库",
        )
    service = get_mlflow_service()
    experiments = service.list_experiments()
    return {"total": len(experiments), "items": experiments, "source": "mlflow"}


@router.delete("/experiments/{experiment_id}", dependencies=[Depends(require_permission("models:write"))])
def delete_experiment(
    experiment_id: str,
    current_user: dict = Depends(require_auth),
):
    """Soft-delete an MLflow experiment and its tracking history only."""
    _require_mlflow_admin(current_user)
    if str(experiment_id) == "0":
        raise HTTPException(status_code=400, detail="MLflow 默认实验不能删除")
    client = get_mlflow_tracker().client
    if not client:
        raise HTTPException(status_code=503, detail="MLflow 客户端不可用")
    try:
        experiment = client.get_experiment(experiment_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="MLflow 实验不存在") from exc
    if not experiment or str(getattr(experiment, "lifecycle_stage", "active")).lower() == "deleted":
        raise HTTPException(status_code=404, detail="MLflow 实验不存在或已删除")
    try:
        client.delete_experiment(experiment_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MLflow 删除实验失败：{exc}") from exc
    return {"success": True, "experiment_id": experiment_id, "message": "MLflow 实验及其追踪运行已软删除"}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str):
    """获取实验详情"""
    service = get_mlflow_service()
    exp = service.get_experiment(experiment_id=experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="实验不存在")
    return {"id": exp.experiment_id, "name": exp.name, "status": exp.lifecycle_stage}


@router.post("/runs/start")
def start_run(req: StartRunReq):
    """开始运行"""
    service = get_mlflow_service()
    result = service.start_run(req.experiment_id, req.run_name, req.tags)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post("/runs/end")
def end_run(status: str = "FINISHED"):
    """结束运行"""
    service = get_mlflow_service()
    service.end_run(status)
    return {"message": "Run ended"}


@router.post("/metrics")
def log_metric(req: LogMetricReq):
    """记录指标"""
    service = get_mlflow_service()
    success = service.log_metric(req.key, req.value, req.step)
    if success:
        return {"message": "Metric logged"}
    raise HTTPException(status_code=500, detail="Failed to log metric")


@router.post("/params")
def log_params(req: LogParamsReq):
    """记录参数"""
    service = get_mlflow_service()
    service.log_params(req.params)
    return {"message": "Params logged"}


def _require_mlflow_admin(current_user: dict) -> None:
    # MLflow itself has no organization_id on experiments/runs. Until its
    # records carry tenant metadata, destructive cleanup must be admin-only.
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="MLflow 追踪记录仅管理员可删除")


@router.delete("/runs/{run_id}", dependencies=[Depends(require_permission("models:write"))])
def delete_run(
    run_id: str,
    current_user: dict = Depends(require_auth),
):
    """Soft-delete one MLflow tracking run without deleting platform assets."""
    _require_mlflow_admin(current_user)
    client = get_mlflow_tracker().client
    if not client:
        raise HTTPException(status_code=503, detail="MLflow 客户端不可用")
    try:
        run = client.get_run(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="MLflow 运行记录不存在") from exc
    if str(getattr(run.info, "lifecycle_stage", "active")).lower() == "deleted":
        raise HTTPException(status_code=404, detail="MLflow 运行记录已删除")
    try:
        client.delete_run(run_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MLflow 删除运行记录失败：{exc}") from exc
    return {"success": True, "run_id": run_id, "message": "MLflow 运行记录已软删除"}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """获取运行详情"""
    service = get_mlflow_service()
    run = service.get_run(run_id)
    if "error" in run:
        raise HTTPException(status_code=404, detail=run["error"])
    return run


@router.get("/experiments/{experiment_id}/runs")
def search_runs(experiment_id: str, max_results: int = 100):
    """搜索运行"""
    service = get_mlflow_service()
    runs = service.search_runs(experiment_id, max_results=max_results)
    return {"total": len(runs), "items": runs}


@router.get("/runs/{run_id}/artifacts")
def get_artifacts(run_id: str, path: Optional[str] = None):
    """获取artifacts"""
    service = get_mlflow_service()
    artifacts = service.get_artifacts(run_id, path)
    return {"total": len(artifacts), "items": artifacts}


@router.post(
    "/compare",
    dependencies=[Depends(require_permission("models:read"))],
)
def compare_models(req: CompareModelsReq, db: Session = Depends(get_db)):
    """在同一个数据集和拆分规则上逐一运行真实模型，返回可直接比较的指标。"""
    from api.model_evaluation import EvaluationRequest, evaluate_model

    model_ids = list(dict.fromkeys(req.model_ids))
    if len(model_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择两个不同模型")
    if len(model_ids) > 10:
        raise HTTPException(status_code=400, detail="一次最多对比10个模型")

    rows = db.execute(text("""
        SELECT m.id, m.name, m.model_type, mv.parameters
        FROM models m
        LEFT JOIN model_versions mv ON mv.id = (
            SELECT latest.id FROM model_versions latest
            WHERE latest.model_id = m.id ORDER BY latest.id DESC LIMIT 1
        )
        WHERE m.id = ANY(:model_ids)
    """), {"model_ids": model_ids}).fetchall()
    models_by_id = {row[0]: row for row in rows}
    results = []

    for model_id in model_ids:
        model = models_by_id.get(model_id)
        if not model:
            results.append({"model_id": model_id, "status": "failed", "error": "模型不存在"})
            continue
        params = _json_dict(model[3])
        target_column = req.target_column or params.get("target_column") or params.get("target_col")
        feature_columns = req.feature_columns or _parse_list(
            params.get("feature_columns") or params.get("feature_cols")
        )
        task_type = req.task_type or model[2] or "regression"
        try:
            evaluation = evaluate_model(
                EvaluationRequest(
                    model_id=model_id,
                    dataset_id=req.dataset_id,
                    task_type=task_type,
                    target_column=target_column or None,
                    feature_columns=feature_columns,
                    cross_validation=False,
                    test_size=req.test_size,
                ),
                db=db,
            )
            results.append({
                "model_id": model_id,
                "model_name": model[1],
                "status": "completed",
                "metrics": evaluation.get("metrics", {}),
                "test_samples": evaluation.get("test_samples"),
                "feature_columns": evaluation.get("feature_columns", []),
                "evaluation_id": evaluation.get("id"),
            })
        except HTTPException as exc:
            db.rollback()
            results.append({
                "model_id": model_id, "model_name": model[1],
                "status": "failed", "error": str(exc.detail),
            })
        except Exception as exc:
            db.rollback()
            results.append({
                "model_id": model_id, "model_name": model[1],
                "status": "failed", "error": str(exc),
            })

    dataset = db.execute(
        text("SELECT name FROM datasets WHERE id = :dataset_id"),
        {"dataset_id": req.dataset_id},
    ).fetchone()
    return {
        "dataset_id": req.dataset_id,
        "dataset_name": dataset[0] if dataset else None,
        "task_type": req.task_type,
        "completed": sum(1 for item in results if item["status"] == "completed"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }


@router.get("/compare")
def compare_runs(run_ids: List[str]):
    """对比多个运行"""
    service = get_mlflow_service()
    runs = []
    for run_id in run_ids:
        run = service.get_run(run_id)
        runs.append(run)

    # 对比指标
    comparison = {"runs": []}
    metrics_set = set()
    for run in runs:
        metrics_set.update(run.get("metrics", {}).keys())

    for run in runs:
        run_metrics = {}
        for metric in metrics_set:
            run_metrics[metric] = run.get("metrics", {}).get(metric, None)
        comparison["runs"].append({"run_id": run.get("run_id"), "metrics": run_metrics})

    return comparison


@router.get("/registered-models")
def list_registered_models():
    """列出MLflow注册模型"""
    service = get_mlflow_service()
    try:
        models = service.list_registered_models()
        return {"items": models}
    except Exception:
        return {"items": []}
