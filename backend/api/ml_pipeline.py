"""Tenant-safe ML Pipeline API with fail-closed execution contracts."""
from __future__ import annotations

import json
import ast
import base64
import hashlib
import io
import keyword
import os
import re
import time
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from api.deps import require_auth, require_permission
from api.tenant_access import (
    assert_organization_access,
    create_organization_id,
    is_admin,
    require_organization,
    scope_query,
)
from database.config import get_db
from models.core import (
    AlgorithmModel,
    Dataset,
    Model,
    ModelVersion,
    Pipeline,
    PipelineRun,
    PipelineTask,
    PipelineTaskRun,
)
from services.pipeline_observability import (
    audit_pipeline_event,
    ensure_run_trace,
    ensure_task_trace,
    log_pipeline_event,
    refresh_running_gauge,
)


router = APIRouter(prefix="/pipelines", tags=["ML流水线"])

MAX_TASKS = 100
MAX_CONFIG_BYTES = 128 * 1024
MAX_EXPORT_BYTES = 256 * 1024
MAX_IMPORT_BYTES = 2 * 1024 * 1024
WORKFLOW_IMPORT_MARKER = '# ODF_PIPELINE_DEFINITION_V1:'
WORKFLOW_RELEASE_MARKER = '# ODF_WORKFLOW_RELEASE_V1:'
MAX_DATASET_ROWS = min(max(int(os.getenv("PIPELINE_MAX_DATASET_ROWS", "100000")), 10), 100000)
MAX_DATASET_BYTES = min(
    max(int(os.getenv("PIPELINE_MAX_DATASET_BYTES", str(64 * 1024 * 1024))), 1024),
    256 * 1024 * 1024,
)
VALIDATION_RUN_RE = re.compile(r"^val-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
VALIDATION_ENVIRONMENTS = {"test", "staging"}
VALIDATION_ASSET_TYPES = {"fixture", "synthetic", "replay", "anonymized"}
REAL_NODE_TYPE_ALIASES = {
    "data_load": "data_load",
    "data_source": "data_load",
    "dataset": "data_load",
    "data_process": "data_process",
    "feature_engineering": "feature_engineering",
    "model_evaluate": "model_evaluate",
}
IMPLEMENTED_TASK_TYPES = {
    *REAL_NODE_TYPE_ALIASES,
    "model_train",
    "train",
}
IDENTITY_OUTPUT_KEYS = ("dataset_id", "job_id", "model_id")


class TaskDef(BaseModel):
    name: str
    task_type: str = "custom"
    command: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=1800, ge=30, le=3600)
    retry: int = Field(default=0, ge=0, le=5)


class PipelineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str = "manual"
    organization_id: Optional[int] = None
    tasks: List[TaskDef] = Field(default_factory=list)


class PipelineRunRequest(BaseModel):
    validation_context: Dict[str, Any] = Field(default_factory=dict)


class PipelineTemplateCreate(BaseModel):
    name: str
    organization_id: Optional[int] = None
    tasks: List[TaskDef] = Field(default_factory=list)


class ExecuteNodeReq(BaseModel):
    node_id: str = ""
    task_type: str = "custom"
    config: Dict[str, Any] = Field(default_factory=dict)


class ExportReq(BaseModel):
    format: str = "api"
    pipeline: Dict[str, Any] = Field(default_factory=dict)


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"配置不是有效 JSON: {exc}") from exc


def _normalise_validation_context(value: Any) -> dict:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="validation_context 必须是对象")
    allowed = {"validation_run_id", "scenario_id", "scenario_version", "environment", "asset_type"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail=f"validation_context 含未知字段: {', '.join(unknown)}")
    result = {key: value[key] for key in allowed if value.get(key) not in (None, "")}
    run_id = result.get("validation_run_id")
    if run_id and not VALIDATION_RUN_RE.fullmatch(str(run_id)):
        raise HTTPException(status_code=400, detail="validation_run_id 格式无效")
    if result.get("environment") not in (None, *VALIDATION_ENVIRONMENTS):
        raise HTTPException(status_code=400, detail="离线验证环境只能是 test 或 staging")
    if result.get("asset_type") not in (None, *VALIDATION_ASSET_TYPES):
        raise HTTPException(status_code=400, detail="validation asset_type 无效")
    if _json_size(result) > 4096:
        raise HTTPException(status_code=413, detail="validation_context 超过 4KiB")
    return result


def validate_dag(tasks: list) -> bool:
    names = [task["name"] for task in tasks]
    if len(names) != len(set(names)):
        return False
    graph = {task["name"]: task.get("dependencies", []) for task in tasks}
    if any(dep not in graph for deps in graph.values() for dep in deps):
        return False
    visited, active = set(), set()

    def visit(name: str) -> bool:
        visited.add(name)
        active.add(name)
        for dependency in graph[name]:
            if dependency not in visited and visit(dependency):
                return True
            if dependency in active:
                return True
        active.remove(name)
        return False

    return not any(name not in visited and visit(name) for name in graph)


def topo_sort(tasks: list) -> list:
    graph = {task["name"]: task.get("dependencies", []) for task in tasks}
    degree = {name: len(dependencies) for name, dependencies in graph.items()}
    queue = [name for name, count in degree.items() if count == 0]
    ordered = []
    while queue:
        name = queue.pop(0)
        ordered.append(name)
        for task in tasks:
            if name in task.get("dependencies", []):
                degree[task["name"]] -= 1
                if degree[task["name"]] == 0:
                    queue.append(task["name"])
    return ordered if len(ordered) == len(tasks) else []


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
                raise ValueError(
                    f"上游节点对 {key} 的身份输出冲突: {current!r} != {incoming!r}"
                )
            merged[key] = incoming
    merged["_upstream_outputs"] = upstream
    if _json_size(merged) > MAX_CONFIG_BYTES:
        raise ValueError("合并上游 outputs 后的节点配置超过 128KiB")
    return merged

def propagate_identity_outputs(effective_config: dict, task_outputs: dict) -> dict:
    """Carry inherited identities forward so downstream nodes receive lineage."""
    result = dict(task_outputs or {})
    for key in IDENTITY_OUTPUT_KEYS:
        inherited = effective_config.get(key)
        produced = result.get(key)
        if _present(inherited) and _present(produced) and str(inherited) != str(produced):
            raise ValueError(
                f"节点产生的 {key} 与上游身份冲突: {produced!r} != {inherited!r}"
            )
        if _present(inherited) and not _present(produced):
            result[key] = inherited
    return result


def _task_payload(tasks: List[TaskDef]) -> list[dict]:
    if len(tasks) > MAX_TASKS:
        raise HTTPException(status_code=413, detail=f"流水线最多 {MAX_TASKS} 个节点")
    payload = [task.model_dump() for task in tasks]
    for item in payload:
        item["name"] = item["name"].strip()
        if not item["name"] or len(item["name"]) > 255:
            raise HTTPException(status_code=400, detail="节点名称必须为 1-255 个字符")
        if _json_size(item["config"]) > MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail=f"节点 {item['name']} 配置超过 128KiB")
    if payload and not validate_dag(payload):
        raise HTTPException(status_code=400, detail="DAG 节点重名、依赖不存在或存在循环")
    return payload


def pipeline_access_allowed(pipeline: Pipeline, user: dict, *, write: bool = False) -> bool:
    if is_admin(user):
        return True
    if pipeline.organization_id != user.get("organization_id"):
        return False
    return not write or pipeline.owner_id == user.get("id")


def run_resume_allowed(status: Any) -> bool:
    return str(status or "").upper() in {"PENDING", "RUNNING"}


def _pipeline_query(db: Session, user: dict):
    return scope_query(db.query(Pipeline), Pipeline.organization_id, user)


def _get_pipeline(db: Session, pipeline_id: int, user: dict, *, write: bool = False) -> Pipeline:
    pipeline = (
        _pipeline_query(db, user)
        .options(joinedload(Pipeline.tasks))
        .filter(Pipeline.id == pipeline_id)
        .first()
    )
    if not pipeline or not pipeline_access_allowed(pipeline, user, write=write):
        raise HTTPException(status_code=404, detail="流水线不存在")
    return pipeline


def _get_run(db: Session, run_id: int, user: dict) -> PipelineRun:
    query = db.query(PipelineRun).join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
    if not is_admin(user):
        query = query.filter(PipelineRun.organization_id == require_organization(user))
    run = query.filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="流水线运行不存在")
    return run


def pipeline_run_history_item(run: PipelineRun) -> dict:
    """Serialize one durable run with the node outputs needed by the history UI."""
    result = run.to_dict()
    pipeline = getattr(run, "pipeline", None)
    result["pipeline_name"] = getattr(pipeline, "name", None) or f"Pipeline #{run.pipeline_id}"
    task_runs = sorted(getattr(run, "task_runs", None) or [], key=lambda item: item.id)
    result["task_runs"] = [task.to_dict() for task in task_runs]
    return result


def _dataset(db: Session, dataset_id: int, user: dict) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == int(dataset_id)).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(dataset.organization_id, user)
    rows = int(dataset.row_count or 0)
    size = int(dataset.size_bytes or 0)
    if dataset.file_path:
        try:
            size = max(size, Path(dataset.file_path).stat().st_size)
        except OSError:
            pass
    if rows > MAX_DATASET_ROWS or size > MAX_DATASET_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"单次流水线数据上限为 {MAX_DATASET_ROWS} 行/{MAX_DATASET_BYTES} 字节",
        )
    return dataset


def _model(db: Session, model_id: int, user: dict) -> Model:
    model = db.query(Model).filter(Model.id == int(model_id)).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    assert_organization_access(model.organization_id, user)
    return model


def _model_version_path(db: Session, model: Model) -> str | None:
    """Use a registered version only when the model has no database artifact."""
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
    db: Session, config: dict, organization_id: int
) -> dict:
    """Replace a saved rule id with the current, tenant-scoped definition."""
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


def _execute_real_node(
    db: Session,
    task_type: str,
    config: dict,
    current_user: dict,
) -> dict:
    """Execute one real node using resources already checked by this API."""
    from services.pipeline_node_executor import (
        AuthorizedDataset,
        AuthorizedModel,
        SQLAlchemyDerivedDatasetSink,
        VerifiedResourceAccess,
        execute_pipeline_node,
    )

    normalized_type = REAL_NODE_TYPE_ALIASES[task_type]
    dataset_id = (
        config.get("evaluation_dataset_id")
        if normalized_type == "model_evaluate" and config.get("evaluation_dataset_id") not in (None, "")
        else config.get("dataset_id")
    )
    if dataset_id in (None, ""):
        raise ValueError(f"{normalized_type} 必须配置 dataset_id")
    dataset = _dataset(db, int(dataset_id), current_user)
    if normalized_type == "data_process":
        config = _enrich_pipeline_cleaning_rule(
            db, config, int(dataset.organization_id)
        )
    datasets = {
        dataset.id: AuthorizedDataset.from_record(dataset),
    }
    models = {}
    model_contract = {}
    if normalized_type == "model_evaluate":
        if config.get("model_id") in (None, ""):
            raise ValueError("model_evaluate 必须配置 model_id")
        model = _model(db, int(config["model_id"]), current_user)
        model_contract = _model_evaluation_contract(db, model)
        models[model.id] = AuthorizedModel.from_record(
            model,
            file_path=_model_version_path(db, model),
        )
    organization_ids = {
        int(resource.organization_id) for resource in (*datasets.values(), *models.values())
    }
    if len(organization_ids) != 1:
        raise ValueError("节点资源必须属于同一非空组织")
    access = VerifiedResourceAccess(
        organization_id=organization_ids.pop(),
        datasets=datasets,
        models=models,
    )
    sink = None
    if normalized_type in {"data_process", "feature_engineering"}:
        sink = SQLAlchemyDerivedDatasetSink(db, max_bytes=MAX_DATASET_BYTES)
    execution_config = dict(config)
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


def _check_resource_access(db: Session, config: dict, user: dict) -> None:
    if config.get("dataset_id") not in (None, ""):
        _dataset(db, int(config["dataset_id"]), user)
    if config.get("evaluation_dataset_id") not in (None, ""):
        _dataset(db, int(config["evaluation_dataset_id"]), user)
    if config.get("model_id") not in (None, ""):
        _model(db, int(config["model_id"]), user)


def _mark_training_failed(db: Session, job_id: str, error: str) -> None:
    db.execute(
        text(
            "UPDATE training_jobs SET status='FAILED', error_message=:error, completed_at=NOW() "
            "WHERE training_job_id=:job_id"
        ),
        {"job_id": job_id, "error": error[:500]},
    )
    db.commit()


def _enqueue_training(db: Session, config: dict, user: dict, validation_context: dict):
    dataset_id = config.get("dataset_id")
    if dataset_id in (None, ""):
        raise HTTPException(status_code=400, detail="model_train 必须配置 dataset_id")
    _dataset(db, int(dataset_id), user)
    from services.pipeline_training_config import enrich_pipeline_training_config
    from services.real_training_service import TrainingService

    training_input_config = dict(config)
    training_input_config.pop("_upstream_outputs", None)
    training_input_config.pop("input_bindings", None)
    try:
        effective_config = enrich_pipeline_training_config(db, training_input_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    training_config = {
        **effective_config,
        "name": effective_config.get("name") or "pipeline-training",
        "task_type": effective_config.get(
            "training_task_type",
            effective_config.get("task_type", "regression"),
        ),
        "model_type": effective_config.get("model_type", "random_forest"),
        "dataset_id": int(dataset_id),
        "organization_id": user.get("organization_id"),
        "validation_context": validation_context,
        "validation_run_id": validation_context.get("validation_run_id"),
    }
    job_id = TrainingService.create_job(training_config)
    try:
        from tasks.training_tasks import train_model

        celery_task = train_model.delay(
            model_id=0,
            dataset_id=int(dataset_id),
            config=training_config,
            job_id=job_id,
        )
    except Exception as exc:
        _mark_training_failed(db, job_id, f"训练队列提交失败: {exc}")
        raise HTTPException(status_code=503, detail="训练队列不可用，任务未被接受") from exc
    return job_id, celery_task.id


def _safe_identifier(value: Any, fallback: str, *, used: set[str] | None = None) -> str:
    identifier = re.sub(r"[^0-9A-Za-z_]", "_", str(value)).strip("_") or fallback
    if identifier[0].isdigit():
        identifier = f"{fallback}_{identifier}"
    if keyword.iskeyword(identifier):
        identifier = f"{identifier}_{fallback}"
    if used is not None:
        base, suffix = identifier, 2
        while identifier in used:
            identifier = f"{base}_{suffix}"
            suffix += 1
        used.add(identifier)
    return identifier


def _validate_export(req: ExportReq) -> tuple[dict, list, str]:
    pipeline = req.pipeline
    if _json_size(pipeline) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="导出流水线定义超过 256KiB")
    tasks = pipeline.get("tasks") or pipeline.get("nodes") or []
    if not isinstance(tasks, list) or len(tasks) > MAX_TASKS or any(not isinstance(t, dict) for t in tasks):
        raise HTTPException(status_code=400, detail=f"导出 tasks 必须是最多 {MAX_TASKS} 项的对象数组")
    name = str(pipeline.get("name") or "unnamed")[:255]
    return pipeline, tasks, name


def _render_export(req: ExportReq):
    pipeline, tasks, name = _validate_export(req)
    fmt = req.format.lower()
    if fmt == "json":
        return {"format": "json", "pipeline": pipeline}
    if fmt == "api":
        payload = json.dumps(pipeline, ensure_ascii=False, separators=(",", ":"))
        import_marker = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        code = "\n".join(
            [
                WORKFLOW_IMPORT_MARKER + import_marker,
                '"""Generated foundation pipeline API wrapper."""',
                "import json",
                "import os",
                "import requests",
                "from fastapi import FastAPI, Header, HTTPException",
                "",
                f"PIPELINE = json.loads({payload!r})",
                "PLATFORM_URL = os.getenv('ODF_PLATFORM_URL', 'http://localhost:8080/api/v1')",
                "app = FastAPI(title='foundation Pipeline API')",
                "",
                "@app.post('/run')",
                "def run_pipeline(authorization: str | None = Header(default=None), wait: bool = True, timeout: int = 3600):",
                "    if not authorization:",
                "        raise HTTPException(status_code=401, detail='Bearer token required')",
                "    pipeline_id = PIPELINE.get('id')",
                "    if not pipeline_id:",
                "        raise HTTPException(status_code=400, detail='Persist the pipeline before export')",
                "    timeout = min(max(int(timeout), 1), 3600)",
                "    response = requests.post(",
                "        f'{PLATFORM_URL}/pipelines/{pipeline_id}/run',",
                "        params={'wait': str(bool(wait)).lower(), 'timeout': timeout},",
                "        headers={'Authorization': authorization}, timeout=timeout + 30,",
                "    )",
                "    response.raise_for_status()",
                "    return response.json()",
                "",
            ]
        )
        return {
            "format": "api",
            "service_name": name,
            "code": code,
            "note": "导出物不包含凭据；运行时必须注入平台地址和 Bearer token",
        }
    if fmt == "sdk":
        class_name = _safe_identifier(name, "PipelineSDK")
        import_marker = base64.b64encode(json.dumps(pipeline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
        used: set[str] = set()
        lines = [
            WORKFLOW_IMPORT_MARKER + import_marker,
            '"""Generated foundation Pipeline SDK."""',
            "import requests",
            "",
            'BASE_URL = "http://localhost:8080/api/v1"',
            "",
            f"class {class_name}:",
            "    def __init__(self, token: str, base_url: str = BASE_URL):",
            "        if not token:",
            '            raise ValueError("token is required")',
            '        self.base = base_url.rstrip("/")',
            '        self.headers = {"Authorization": f"Bearer {token}"}',
            "",
        ]
        for task in tasks:
            task_name = str(task.get("name") or task.get("id") or "step")[:255]
            task_type = str(task.get("task_type") or task.get("type") or "custom")[:64]
            method = _safe_identifier(task_name, "step", used=used)
            lines.extend(
                [
                    f"    def {method}(self, **kwargs):",
                    f"        # Node: {task_name!r} ({task_type!r})",
                    '        response = requests.post(f"{self.base}/pipelines/execute-node",',
                    "            headers=self.headers,",
                    f'            json={{"task_type": {task_type!r}, "config": kwargs}}, timeout=30)',
                    "        response.raise_for_status()",
                    "        return response.json()",
                    "",
                ]
            )
        lines.extend(
            [
                "    def run_all(self, pipeline_id: int, wait: bool = True, timeout: int = 3600):",
                "        timeout = min(max(int(timeout), 1), 3600)",
                '        response = requests.post(f"{self.base}/pipelines/{pipeline_id}/run",',
                "            headers=self.headers,",
                "            params={'wait': str(bool(wait)).lower(), 'timeout': timeout},",
                "            timeout=timeout + 30)",
                "        response.raise_for_status()",
                "        return response.json()",
            ]
        )
        return {"format": "sdk", "language": "python", "code": "\n".join(lines)}
    if fmt == "dockerfile":
        return {
            "format": "dockerfile",
            "code": "\n".join(
                [
                    "FROM python:3.11-slim",
                    "WORKDIR /app",
                    "COPY pipeline_api.py /app/pipeline_api.py",
                    "RUN pip install --no-cache-dir fastapi uvicorn requests",
                    "USER 65532:65532",
                    "EXPOSE 8080",
                    'CMD ["uvicorn", "pipeline_api:app", "--host", "0.0.0.0", "--port", "8080"]',
                ]
            ),
            "note": "token 只在运行时请求头中传递",
        }
    raise HTTPException(status_code=400, detail=f"不支持的格式: {fmt}(可选: api/sdk/dockerfile/json)")


WORKFLOW_PACKAGE_FILES = {
    "pipeline.json",
    "pipeline_sdk.py",
    "pipeline_api.py",
    "run_pipeline.py",
    "Dockerfile",
    "requirements.txt",
    "README.md",
    "manifest.json",
}


def _portable_pipeline_definition(pipeline: dict) -> dict:
    tasks = []
    for raw in pipeline.get("tasks") or []:
        tasks.append(
            {
                "name": raw.get("name"),
                "task_type": raw.get("task_type", "custom"),
                "command": raw.get("command"),
                "dependencies": list(raw.get("dependencies") or []),
                "config": dict(raw.get("config") or {}),
                "timeout": int(raw.get("timeout") or 1800),
                "retry": int(raw.get("retry") or 0),
            }
        )
    definition = {
        "name": pipeline.get("name") or "exported-pipeline",
        "description": pipeline.get("description"),
        "trigger_type": pipeline.get("trigger_type") or "manual",
        "tasks": tasks,
    }
    _validate_export(ExportReq(format="json", pipeline=definition))
    return definition


def _workflow_sdk_source() -> str:
    return textwrap.dedent(
        '''\
        """Portable foundation Pipeline client: import, run, wait and fetch results."""
        from __future__ import annotations

        import json
        import os
        import time
        from pathlib import Path
        from typing import Any

        import requests


        class foundationPipelineClient:
            def __init__(
                self,
                token: str,
                base_url: str = "http://localhost:8080/api/v1",
                pipeline_id: int | None = None,
                definition_path: str | Path = "pipeline.json",
                poll_interval: float = 2.0,
            ):
                if not token:
                    raise ValueError("ODF_TOKEN/token is required")
                self.base_url = base_url.rstrip("/")
                self.pipeline_id = int(pipeline_id) if pipeline_id else None
                self.poll_interval = max(float(poll_interval), 0.5)
                self.headers = {"Authorization": f"Bearer {token}"}
                self.definition = json.loads(Path(definition_path).read_text(encoding="utf-8"))

            def _request(self, method: str, path: str, **kwargs: Any) -> dict:
                response = requests.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    timeout=kwargs.pop("timeout", 30),
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()

            def import_pipeline(self) -> dict:
                result = self._request("POST", "/pipelines", json=self.definition)
                self.pipeline_id = int(result["id"])
                return result

            def ensure_pipeline(self) -> int:
                if self.pipeline_id is None:
                    self.import_pipeline()
                return int(self.pipeline_id)

            def start(self) -> dict:
                pipeline_id = self.ensure_pipeline()
                return self._request("POST", f"/pipelines/{pipeline_id}/run", json={})

            def get_run(self, run_id: int) -> dict:
                return self._request("GET", f"/pipelines/runs/{int(run_id)}")

            def wait(self, run_id: int, timeout: float = 3600) -> dict:
                deadline = time.monotonic() + float(timeout)
                while True:
                    result = self.get_run(run_id)
                    status = str(result.get("status") or "").upper()
                    if status == "SUCCESS":
                        return result
                    if status in {"FAILED", "CANCELLED"}:
                        raise RuntimeError(json.dumps(result, ensure_ascii=False))
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Run #{run_id} did not finish within {timeout} seconds")
                    time.sleep(self.poll_interval)

            def run(self, wait: bool = True, timeout: float = 3600) -> dict:
                accepted = self.start()
                if not wait:
                    return accepted
                return self.wait(int(accepted["run_id"]), timeout=timeout)


        def from_environment() -> foundationPipelineClient:
            pipeline_id = os.getenv("ODF_PIPELINE_ID")
            return foundationPipelineClient(
                token=os.getenv("ODF_TOKEN", ""),
                base_url=os.getenv("ODF_PLATFORM_URL", "http://localhost:8080/api/v1"),
                pipeline_id=int(pipeline_id) if pipeline_id else None,
                definition_path=os.getenv("ODF_PIPELINE_FILE", "pipeline.json"),
            )
        '''
    )


def _workflow_api_source() -> str:
    return textwrap.dedent(
        '''\
        """HTTP service for the exported foundation workflow."""
        from __future__ import annotations

        import os

        from fastapi import FastAPI, Header, HTTPException

        from pipeline_sdk import foundationPipelineClient

        app = FastAPI(title="Exported foundation Workflow")


        def _client(authorization: str | None) -> foundationPipelineClient:
            token = (authorization or "").strip()
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            token = token or os.getenv("ODF_TOKEN", "")
            if not token:
                raise HTTPException(status_code=401, detail="Bearer token required")
            pipeline_id = os.getenv("ODF_PIPELINE_ID")
            return foundationPipelineClient(
                token=token,
                base_url=os.getenv("ODF_PLATFORM_URL", "http://localhost:8080/api/v1"),
                pipeline_id=int(pipeline_id) if pipeline_id else None,
            )


        @app.get("/health")
        def health():
            return {"status": "healthy"}


        @app.post("/run")
        def run_pipeline(
            wait: bool = True,
            timeout: float = 3600,
            authorization: str | None = Header(default=None),
        ):
            return _client(authorization).run(wait=wait, timeout=timeout)


        @app.get("/runs/{run_id}")
        def get_run(run_id: int, authorization: str | None = Header(default=None)):
            return _client(authorization).get_run(run_id)
        '''
    )


def _workflow_cli_source() -> str:
    return textwrap.dedent(
        '''\
        """Run the exported workflow and print its final node outputs as JSON."""
        import json
        import sys

        from pipeline_sdk import from_environment


        if __name__ == "__main__":
            try:
                result = from_environment().run(wait=True)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f"Workflow failed: {exc}", file=sys.stderr)
                raise SystemExit(1)
        '''
    )


def _workflow_readme(pipeline: dict, original_pipeline_id: int | None) -> str:
    return textwrap.dedent(
        f'''\
        # 开放底座 AI 完整工作流包

        流水线：{pipeline.get("name") or "未命名"}
        来源 Pipeline ID：{original_pipeline_id or "无"}

        本包包含完整 DAG 定义、节点配置、API 服务、Python SDK、命令行运行器和容器构建文件。
        算法执行仍由开放底座 AI 平台完成；包内不包含平台凭据、数据集原文件或模型权重。

        ## 文件

        - `pipeline.json`：可移植的流水线定义（节点、参数、依赖、超时和重试）
        - `pipeline_sdk.py`：导入、启动、轮询和获取最终节点结果
        - `pipeline_api.py`：提供 `/run`、`/runs/{{run_id}}` 和 `/health`
        - `run_pipeline.py`：命令行执行并输出最终实验指标
        - `Dockerfile`、`requirements.txt`：容器运行环境
        - `manifest.json`：文件大小和 SHA-256 校验值

        ## 命令行运行

        ```bash
        pip install -r requirements.txt
        export ODF_PLATFORM_URL=http://localhost:8080/api/v1
        export ODF_TOKEN=你的访问令牌
        python run_pipeline.py
        ```

        未设置 `ODF_PIPELINE_ID` 时，SDK 会先把 `pipeline.json` 导入目标平台，再运行。
        若复用平台上已有流水线，请设置 `ODF_PIPELINE_ID={original_pipeline_id or "对应ID"}`。

        ## API 服务

        ```bash
        docker build -t foundation-exported-workflow .
        docker run --rm -p 8080:8080 \\
          -e ODF_PLATFORM_URL=http://host.docker.internal:8080/api/v1 \\
          -e ODF_TOKEN=你的访问令牌 \\
          foundation-exported-workflow
        curl -X POST 'http://localhost:8080/run?wait=true'
        ```

        `/run` 默认等待整条流水线结束，响应中包含每个节点输出、模型 ID 和实验指标。
        '''
    )


def build_workflow_package(pipeline: dict) -> bytes:
    portable = _portable_pipeline_definition(pipeline)
    original_pipeline_id = pipeline.get("id")
    files: dict[str, bytes] = {
        "pipeline.json": json.dumps(portable, ensure_ascii=False, indent=2).encode("utf-8"),
        "pipeline_sdk.py": _workflow_sdk_source().encode("utf-8"),
        "pipeline_api.py": _workflow_api_source().encode("utf-8"),
        "run_pipeline.py": _workflow_cli_source().encode("utf-8"),
        "Dockerfile": b"FROM python:3.11-slim\nWORKDIR /app\nCOPY . /app\nRUN pip install --no-cache-dir -r requirements.txt\nUSER 65532:65532\nEXPOSE 8080\nCMD [\"uvicorn\", \"pipeline_api:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n",
        "requirements.txt": b"fastapi>=0.104,<1\nuvicorn>=0.24,<1\nrequests>=2.31,<3\n",
        "README.md": _workflow_readme(portable, original_pipeline_id).encode("utf-8"),
    }
    manifest = {
        "schema_version": "foundation.workflow-package/v1",
        "source_pipeline_id": original_pipeline_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "files": {
            name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(files.items())
        },
    }
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _normalise_import_definition(value: Any) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="封装文件中未找到有效的流程定义")
    definition = _portable_pipeline_definition(value)
    _validate_export(ExportReq(format="json", pipeline=definition))
    return definition


def _extract_release_token_from_python(source: str) -> str | None:
    for line in source.splitlines():
        if line.startswith(WORKFLOW_RELEASE_MARKER):
            return line[len(WORKFLOW_RELEASE_MARKER):].strip() or None
    return None


def _extract_definition_from_python(source: str) -> dict:
    for line in source.splitlines():
        if line.startswith(WORKFLOW_IMPORT_MARKER):
            encoded = line[len(WORKFLOW_IMPORT_MARKER):].strip()
            try:
                payload = base64.b64decode(encoded, validate=True).decode("utf-8")
                return _normalise_import_definition(json.loads(payload))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail=f"流程定义标记无效: {exc}") from exc
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise HTTPException(status_code=422, detail=f"Python 封装文件语法无效: {exc.msg}") from exc
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "PIPELINE" for target in statement.targets):
            continue
        value = statement.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Name) and value.func.value.id == "json" and value.func.attr == "loads" and value.args:
            try:
                literal = ast.literal_eval(value.args[0])
                return _normalise_import_definition(json.loads(literal))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail=f"API 文件中的流程定义无效: {exc}") from exc
    raise HTTPException(status_code=422, detail="该 API/SDK 文件未包含可导入的流程定义。请上传完整工作流 ZIP；旧版 SDK 无法可靠恢复节点参数。")


def _extract_import_definition(filename: str, payload: bytes) -> tuple[dict, str]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                candidates = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).name.lower() == "pipeline.json"]
                if len(candidates) != 1:
                    raise HTTPException(status_code=422, detail="工作流 ZIP 必须且只能包含一个 pipeline.json")
                info = candidates[0]
                if info.file_size > MAX_EXPORT_BYTES:
                    raise HTTPException(status_code=413, detail="pipeline.json 超过 256KiB 上限")
                content = archive.read(info)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=422, detail="上传文件不是有效的工作流 ZIP") from exc
        try:
            return _normalise_import_definition(json.loads(content.decode("utf-8-sig"))), "完整工作流 ZIP"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"pipeline.json 不是有效 UTF-8 JSON: {exc}") from exc
    if suffix == ".json":
        try:
            return _normalise_import_definition(json.loads(payload.decode("utf-8-sig"))), "流程 JSON"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"JSON 文件不是有效流程定义: {exc}") from exc
    if suffix == ".py":
        try:
            return _extract_definition_from_python(payload.decode("utf-8-sig")), "API/SDK Python 封装"
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="Python 封装文件必须使用 UTF-8 编码") from exc
    raise HTTPException(status_code=415, detail="仅支持 .zip、.json、.py 格式的工作流封装")


@router.post("/import-definition", dependencies=[Depends(require_permission("training:write"))])
async def inspect_workflow_import(file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
    del current_user
    filename = str(file.filename or "")
    payload = await file.read(MAX_IMPORT_BYTES + 1)
    if filename.lower().endswith(".py"):
        try:
            release_token = _extract_release_token_from_python(payload.decode("utf-8-sig"))
        except UnicodeDecodeError:
            release_token = None
        if release_token:
            from services.workflow_release_service import release_summary
            summary = release_summary(release_token)
            return {
                "source_type": "可复现工作流 SDK",
                "source_filename": filename,
                "release_token": release_token,
                "node_count": summary["node_count"],
                "release": summary,
            }
    if not payload:
        raise HTTPException(status_code=422, detail="上传文件为空")
    if len(payload) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="工作流封装文件不能超过 2MiB")
    definition, source_type = _extract_import_definition(filename, payload)
    return {"source_type": source_type, "source_filename": filename, "definition": definition, "node_count": len(definition.get("tasks") or [])}


@router.get("/reproducible-releases/{token}")
def get_reproducible_release(token: str):
    """Public metadata only. The opaque release token is the sharing capability."""
    from services.workflow_release_service import release_summary
    try:
        return release_summary(token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/reproducible-releases/{token}/import",
    dependencies=[Depends(require_permission("training:write"))],
)
def import_reproducible_release(
    token: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    from services.workflow_release_service import WorkflowReleaseError, import_release
    try:
        definition = import_release(
            db,
            token,
            current_user.get("organization_id"),
            current_user.get("id"),
        )
    except WorkflowReleaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"definition": definition, "message": "已复制数据集与算法快照，可在画布中修改后重新运行"}


@router.post(
    "/{pid}/reproducible-release",
    dependencies=[Depends(require_permission("training:write"))],
)
def create_reproducible_release(
    pid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    from services.workflow_release_service import WorkflowReleaseError, create_release
    pipeline = _get_pipeline(db, pid, current_user, write=True)
    try:
        manifest = create_release(db, pipeline)
    except WorkflowReleaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = manifest["release_id"]
    sdk_code = "\n".join([
        WORKFLOW_RELEASE_MARKER + token,
        '"""foundation reproducible workflow SDK. Upload this file to restore the frozen workflow."""',
        "WORKFLOW_RELEASE_TOKEN = " + repr(token),
        "WORKFLOW_RELEASE_URL = " + repr("/api/v1/pipelines/reproducible-releases/" + token),
        "",
        "def import_hint():",
        "    return 'Upload this SDK to foundation Pipeline, or paste WORKFLOW_RELEASE_URL into Import Workflow.'",
        "",
    ])
    return {
        "release_id": token,
        "api_path": "/api/v1/pipelines/reproducible-releases/" + token,
        "sdk_code": sdk_code,
        "node_count": len(manifest["pipeline"].get("tasks") or []),
        "dataset_count": len(manifest.get("datasets") or []),
        "cleaning_rule_count": len(manifest.get("cleaning_rules") or []),
        "algorithm_count": len(manifest.get("algorithms") or []),
    }


@router.get(
    "/trainable-algorithms",
    dependencies=[Depends(require_permission("training:read"))],
)
def list_trainable_algorithms(db: Session = Depends(get_db)):
    """Algorithms that the pipeline can run through a real training path."""
    from services.pipeline_training_config import list_pipeline_trainable_algorithms

    return {"items": list_pipeline_trainable_algorithms(db)}


@router.get("/templates/list", dependencies=[Depends(require_permission("training:read"))])
def templates():
    return [
        {
            "name": "模型训练流水线",
            "tasks": [
                {"name": "load", "task_type": "data_load", "dependencies": [], "config": {}},
                {
                    "name": "train",
                    "task_type": "model_train",
                    "dependencies": ["load"],
                    "config": {"model_type": "random_forest"},
                },
            ],
        }
    ]


@router.post("/execute-node", dependencies=[Depends(require_permission("training:write"))])
def execute_node(
    req: ExecuteNodeReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    logs: list[str] = []
    try:
        if _json_size(req.config) > MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail="节点配置超过 128KiB")
        task_type = req.task_type
        if task_type in REAL_NODE_TYPE_ALIASES:
            outputs = _execute_real_node(db, task_type, req.config, current_user)
            return {
                "node_id": req.node_id,
                "task_type": task_type,
                "status": "success",
                "logs": [f"真实节点 {task_type} 执行成功"],
                "outputs": outputs,
            }
        if task_type in ("model_train", "train"):
            context = _normalise_validation_context(req.config.get("validation_context"))
            job_id, celery_task_id = _enqueue_training(db, req.config, current_user, context)
            return {
                "node_id": req.node_id,
                "task_type": task_type,
                "status": "accepted",
                "job_id": job_id,
                "celery_task_id": celery_task_id,
                "logs": [f"训练任务已进入异步队列: {job_id}"],
                "outputs": {"job_id": job_id},
            }
        raise ValueError(f"未实现的流水线节点类型: {task_type}")
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "node_id": req.node_id,
            "task_type": req.task_type,
            "status": "error",
            "logs": logs,
            "error": str(exc)[:200],
        }


@router.post("/export", dependencies=[Depends(require_permission("training:read"))])
def export_pipeline(req: ExportReq):
    return _render_export(req)


@router.get("/meta/statistics", dependencies=[Depends(require_permission("training:read"))])
def stats(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    pipelines = _pipeline_query(db, current_user)
    pipeline_ids = pipelines.with_entities(Pipeline.id).subquery()
    runs = db.query(PipelineRun).filter(PipelineRun.pipeline_id.in_(pipeline_ids))
    return {"total": pipelines.count(), "runs": runs.count()}


@router.get("/runs", dependencies=[Depends(require_permission("training:read"))])
def list_pipeline_run_history(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    query = (
        db.query(PipelineRun)
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .options(joinedload(PipelineRun.pipeline), joinedload(PipelineRun.task_runs))
    )
    query = scope_query(query, Pipeline.organization_id, current_user)
    # The history library is for reusable, completed experiments only. Failed
    # and in-progress runs remain available to the live run endpoint so their
    # state and error can still be shown while executing.
    query = query.filter(PipelineRun.status == "SUCCESS")
    total = query.count()
    runs = query.order_by(PipelineRun.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [pipeline_run_history_item(run) for run in runs],
    }


@router.get("/runs/{rid}", dependencies=[Depends(require_permission("training:read"))])
def get_run(rid: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    run = _get_run(db, rid, current_user)
    task_runs = (
        db.query(PipelineTaskRun)
        .filter(PipelineTaskRun.pipeline_run_id == rid)
        .order_by(PipelineTaskRun.id)
        .all()
    )
    result = run.to_dict()
    result["task_runs"] = [task.to_dict() for task in task_runs]
    return result


@router.delete("/runs/{rid}", dependencies=[Depends(require_permission("training:write"))])
def delete_pipeline_run(
    rid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Delete a completed pipeline run and its node-level history only."""
    run = _get_run(db, rid, current_user)
    _get_pipeline(db, run.pipeline_id, current_user, write=True)
    if run_resume_allowed(run.status):
        raise HTTPException(status_code=409, detail="运行中的流水线不能删除，请等待其结束后再清理记录")
    db.query(PipelineTaskRun).filter(PipelineTaskRun.pipeline_run_id == rid).delete(
        synchronize_session=False
    )
    db.delete(run)
    db.commit()
    return {"success": True, "id": rid, "message": "流水线运行记录已删除"}


@router.post("/runs/{rid}/resume", dependencies=[Depends(require_permission("training:write"))])
def resume_pipeline_run(
    rid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    run = _get_run(db, rid, current_user)
    _get_pipeline(db, run.pipeline_id, current_user, write=True)
    if not run_resume_allowed(run.status):
        raise HTTPException(status_code=409, detail=f"终态运行 {run.status} 不允许恢复")

    from tasks.pipeline_tasks import (
        coordinator_lock,
        enqueue_next_coordinator,
        mark_run_dispatch_failed,
    )

    with coordinator_lock(rid) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="流水线正在协调，无需重复恢复")
        parent_id = f"api-resume:{current_user.get('id')}:{datetime.utcnow().isoformat()}"
        try:
            coordinator = enqueue_next_coordinator(db, run, rid, parent_id)
        except Exception as exc:
            db.rollback()
            failed_run = (
                db.query(PipelineRun)
                .options(joinedload(PipelineRun.task_runs))
                .filter(PipelineRun.id == rid)
                .first()
            )
            if failed_run and run_resume_allowed(failed_run.status):
                mark_run_dispatch_failed(db, failed_run, exc)
            raise HTTPException(
                status_code=503,
                detail={"message": "流水线恢复队列不可用，运行已标记失败", "run_id": rid},
            ) from exc
    return {
        "run_id": rid,
        "pipeline_id": run.pipeline_id,
        "status": run.status,
        "coordinator_task_id": coordinator.id,
        "accepted": True,
    }


@router.get("/templates", dependencies=[Depends(require_permission("training:read"))])
def list_workflow_templates(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    templates = (
        _pipeline_query(db, current_user)
        .options(joinedload(Pipeline.tasks))
        .filter(Pipeline.trigger_type == "template")
        .order_by(Pipeline.updated_at.desc(), Pipeline.id.desc())
        .all()
    )
    return {"items": [item.to_dict() for item in templates]}


@router.post("/templates", dependencies=[Depends(require_permission("training:write"))])
def create_workflow_template(
    req: PipelineTemplateCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="流程模板名称不能为空")
    tasks = _task_payload(req.tasks)
    if not tasks:
        raise HTTPException(status_code=400, detail="流程模板至少需要一个节点")
    organization_id = create_organization_id(req.organization_id, current_user)
    if organization_id is None:
        raise HTTPException(status_code=400, detail="创建流程模板必须指定组织")
    # A template retains the editable defaults chosen on the canvas. Runtime
    # outputs and historical results are never copied; loading the template puts
    # those defaults back onto the canvas where the user may change them.
    template = Pipeline(
        name=name[:255],
        description="可复用流程模板：保留节点、连线和可编辑配置，不含运行结果",
        trigger_type="template",
        status="template",
        owner_id=current_user.get("id"),
        organization_id=organization_id,
    )
    db.add(template)
    db.flush()
    for index, task in enumerate(tasks):
        db.add(PipelineTask(
            pipeline_id=template.id,
            name=task["name"],
            task_type=task["task_type"],
            command=None,
            config=task["config"],
            dependencies=task["dependencies"],
            timeout=1800,
            retry=0,
            order_index=index,
        ))
    db.commit()
    return (
        db.query(Pipeline).options(joinedload(Pipeline.tasks))
        .filter(Pipeline.id == template.id).first().to_dict()
    )


@router.delete("/templates/{template_id}", dependencies=[Depends(require_permission("training:write"))])
def delete_workflow_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    template = _get_pipeline(db, template_id, current_user, write=True)
    if template.trigger_type != "template":
        raise HTTPException(status_code=400, detail="该记录不是流程模板")
    db.delete(template)
    db.commit()
    return {"status": "deleted", "id": template_id}


@router.get("", dependencies=[Depends(require_permission("training:read"))])
def list_pipelines(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    pipelines = (
        _pipeline_query(db, current_user)
        .filter(Pipeline.trigger_type != "template")
        .order_by(Pipeline.id.desc()).all()
    )
    return {"total": len(pipelines), "items": [pipeline.to_dict() for pipeline in pipelines]}


@router.post("", dependencies=[Depends(require_permission("training:write"))])
def create_pipeline(
    req: PipelineCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    tasks = _task_payload(req.tasks)
    organization_id = create_organization_id(req.organization_id, current_user)
    if organization_id is None:
        raise HTTPException(status_code=400, detail="创建流水线必须指定组织")
    pipeline = Pipeline(
        name=req.name.strip(),
        description=req.description,
        trigger_type=req.trigger_type,
        owner_id=current_user.get("id"),
        organization_id=organization_id,
    )
    if not pipeline.name:
        raise HTTPException(status_code=400, detail="流水线名称不能为空")
    db.add(pipeline)
    db.flush()
    for index, task in enumerate(tasks):
        db.add(
            PipelineTask(
                pipeline_id=pipeline.id,
                name=task["name"],
                task_type=task["task_type"],
                command=task["command"],
                config=task["config"],
                dependencies=task["dependencies"],
                timeout=task["timeout"],
                retry=task["retry"],
                order_index=index,
            )
        )
    db.commit()
    return (
        db.query(Pipeline)
        .options(joinedload(Pipeline.tasks))
        .filter(Pipeline.id == pipeline.id)
        .first()
        .to_dict()
    )


@router.get("/{pid}", dependencies=[Depends(require_permission("training:read"))])
def get_pipeline(pid: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    return _get_pipeline(db, pid, current_user).to_dict()


@router.put("/{pid}", dependencies=[Depends(require_permission("training:write"))])
def update_pipeline(
    pid: int,
    req: PipelineCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    pipeline = _get_pipeline(db, pid, current_user, write=True)
    tasks = _task_payload(req.tasks)
    pipeline.name = req.name.strip()
    if not pipeline.name:
        raise HTTPException(status_code=400, detail="流水线名称不能为空")
    pipeline.description = req.description
    pipeline.trigger_type = req.trigger_type
    pipeline.updated_at = datetime.utcnow()
    db.query(PipelineTask).filter(PipelineTask.pipeline_id == pid).delete()
    for index, task in enumerate(tasks):
        db.add(
            PipelineTask(
                pipeline_id=pid,
                name=task["name"],
                task_type=task["task_type"],
                command=task["command"],
                config=task["config"],
                dependencies=task["dependencies"],
                timeout=task["timeout"],
                retry=task["retry"],
                order_index=index,
            )
        )
    db.commit()
    return (
        db.query(Pipeline)
        .options(joinedload(Pipeline.tasks))
        .filter(Pipeline.id == pid)
        .first()
        .to_dict()
    )


@router.delete("/{pid}", dependencies=[Depends(require_permission("training:write"))])
def delete_pipeline(
    pid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    pipeline = _get_pipeline(db, pid, current_user, write=True)
    db.delete(pipeline)
    db.commit()
    return {"status": "deleted"}


@router.post("/{pid}/run", dependencies=[Depends(require_permission("training:write"))])
def run_pipeline(
    pid: int,
    req: PipelineRunRequest = Body(default_factory=PipelineRunRequest),
    wait: bool = Query(False, description="是否等待流水线完成后返回最终结果"),
    timeout: int = Query(300, ge=1, le=3600, description="等待最终结果的最长秒数"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    pipeline = _get_pipeline(db, pid, current_user, write=True)
    if pipeline.trigger_type == "template":
        raise HTTPException(status_code=400, detail="流程模板不能直接运行，请先加载到画布并配置数据、算法和参数")
    if not pipeline.tasks:
        raise HTTPException(status_code=400, detail="流水线无任务")
    context = _normalise_validation_context(req.validation_context)
    tasks = [
        {
            "name": task.name,
            "task_type": task.task_type,
            "config": task.config or {},
            "dependencies": task.dependencies or [],
            "timeout": task.timeout or 1800,
            "retry": task.retry or 0,
        }
        for task in pipeline.tasks
    ]
    if not validate_dag(tasks):
        raise HTTPException(status_code=400, detail="DAG 节点重名、依赖不存在或存在循环")
    for task in tasks:
        if task["task_type"] not in IMPLEMENTED_TASK_TYPES:
            raise HTTPException(status_code=422, detail=f"节点 {task['name']} 尚无生产执行器")
        if _json_size(task["config"]) > MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail=f"节点 {task['name']} 配置过大")
        _check_resource_access(db, task["config"], current_user)
    order = topo_sort(tasks)
    run = PipelineRun(
        pipeline_id=pid,
        organization_id=pipeline.organization_id,
        status="PENDING",
        triggered_by=str(current_user.get("username") or current_user.get("id") or "manual")[:64],
        validation_context=context,
    )
    db.add(run)
    db.flush()
    trace_id = ensure_run_trace(run)
    by_name = {task["name"]: task for task in tasks}
    task_run_specs: list[dict] = []
    definitions_by_name = {task.name: task for task in pipeline.tasks}
    for name in order:
        definition = by_name[name]
        task = definitions_by_name[name]
        task_run = PipelineTaskRun(
            pipeline_run_id=run.id,
            task_id=task.id,
            task_name=name,
            task_type=definition["task_type"],
        )
        db.add(task_run)
        db.flush()
        ensure_task_trace(run, task_run)
        task_run_specs.append(
            {
                "task_run_id": task_run.id,
                "name": name,
                "task_type": definition["task_type"],
                "config": definition["config"],
                "dependencies": list(definition["dependencies"]),
                "timeout": min(max(int(task.timeout or 1800), 30), 3600),
                "retry": min(max(int(task.retry or 0), 0), 5),
            }
        )
    initial_coordinator_task_id = f"pipeline-run-{run.id}-initial"
    run.parameters = {
        "orchestrator": {
            "version": 1,
            "tasks": task_run_specs,
            "created_at": datetime.utcnow().isoformat(),
            "coordinator_task_id": initial_coordinator_task_id,
            "dispatch_state": "INTENT_RECORDED",
        }
    }
    db.commit()
    run_id = run.id
    # Accepted is durable before broker publication.  Only opaque IDs are audited.
    try:
        log_pipeline_event("accepted", run=run, result="accepted")
        audit_pipeline_event("accepted", run=run, result="accepted")
        refresh_running_gauge(db)
    except Exception:
        pass
    from tasks.pipeline_tasks import execute_pipeline_run, mark_run_dispatch_failed

    try:
        coordinator = execute_pipeline_run.apply_async(
            args=[run_id], task_id=initial_coordinator_task_id, retry=False
        )
    except Exception as exc:
        db.rollback()
        failed_run = (
            db.query(PipelineRun)
            .options(joinedload(PipelineRun.task_runs))
            .filter(PipelineRun.id == run_id)
            .first()
        )
        if failed_run:
            mark_run_dispatch_failed(db, failed_run, exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "流水线协调队列不可用，运行已标记失败", "run_id": run_id},
        ) from exc

    try:
        persisted = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if persisted:
            parameters = dict(persisted.parameters or {})
            orchestrator = dict(parameters.get("orchestrator") or {})
            # The worker may already have advanced to the next generation.
            if orchestrator.get("coordinator_task_id") == coordinator.id:
                orchestrator["dispatch_state"] = "ENQUEUED"
                orchestrator["enqueued_at"] = datetime.utcnow().isoformat()
                parameters["orchestrator"] = orchestrator
                persisted.parameters = parameters
                db.commit()
    except Exception:
        # The immutable snapshot and dispatch intent were committed before
        # publishing. Metadata acknowledgement failure must not make the API
        # report a false rejection for an already queued run.
        db.rollback()
    accepted = {
        "run_id": run_id,
        "pipeline_id": pid,
        "status": "PENDING",
        "coordinator_task_id": coordinator.id,
        "tasks": len(task_run_specs),
        "order": order,
        "trace_id": trace_id,
    }
    if not wait:
        return accepted

    deadline = time.monotonic() + float(timeout)
    while True:
        db.expire_all()
        completed_run = _get_run(db, run_id, current_user)
        completed_status = str(completed_run.status or "").upper()
        if completed_status in {"SUCCESS", "FAILED", "CANCELLED"}:
            task_runs = (
                db.query(PipelineTaskRun)
                .filter(PipelineTaskRun.pipeline_run_id == run_id)
                .order_by(PipelineTaskRun.id)
                .all()
            )
            result = completed_run.to_dict()
            result["task_runs"] = [task.to_dict() for task in task_runs]
            result["waited"] = True
            return result
        if time.monotonic() >= deadline:
            accepted["status"] = completed_status or "PENDING"
            accepted["waited"] = False
            accepted["message"] = "等待超时；请使用 GET /api/v1/pipelines/runs/{run_id} 查询最终结果"
            return accepted
        time.sleep(1)


@router.get("/{pid}/runs", dependencies=[Depends(require_permission("training:read"))])
def list_runs(
    pid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    _get_pipeline(db, pid, current_user)
    runs = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_id == pid)
        .order_by(PipelineRun.id.desc())
        .limit(20)
        .all()
    )
    return {"items": [run.to_dict() for run in runs]}


@router.post("/{pid}/export", dependencies=[Depends(require_permission("training:read"))])
def export_saved_pipeline(
    pid: int,
    req: ExportReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    if pid > 0:
        pipeline = _get_pipeline(db, pid, current_user)
        if not req.pipeline:
            req = ExportReq(format=req.format, pipeline=pipeline.to_dict())
        else:
            req.pipeline["id"] = pid
    return _render_export(req)


@router.get("/{pid}/export-package", dependencies=[Depends(require_permission("training:read"))])
def download_workflow_package(
    pid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    pipeline = _get_pipeline(db, pid, current_user)
    payload = build_workflow_package(pipeline.to_dict())
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pipeline_{pid}_workflow.zip"'},
    )
