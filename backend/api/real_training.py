"""
真实训练API - 支持PyTorch和Sklearn模型的真实训练
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from api.deps import require_auth, require_permission
from api.tenant_access import (
    assert_organization_access, is_admin, require_training_job_access,
)
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from services.real_training_service import get_training_service, LIGHTGBM_AVAILABLE, XGBOOST_AVAILABLE, TORCH_AVAILABLE
from services.real_training_service import select_best_metrics
from services.training_adapter_registry import (
    normalize_task_type, resolve_algorithm_model,
)
from services.data_loader import get_training_data, generate_synthetic_data
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/real-training", tags=["真实训练"])

training_service = get_training_service()


class TrainingRequest(BaseModel):
    """训练请求"""

    name: str
    task_type: str  # classification, regression
    model_type: str  # lightgbm, xgboost, random_forest, mlp
    X: List[List[float]]  # 特征数据
    y: List[Any]  # 标签数据
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.001
    hidden_layers: List[int] = [128, 64]
    dropout: float = 0.2
    n_estimators: int = 100
    max_depth: int = 6
    normalize: bool = True



class DatasetJobTrainingRequest(BaseModel):
    """基于数据集的训练请求 — 后端自动加载数据"""
    name: str
    task_type: str = "regression"  # classification, regression
    model_type: str = "random_forest"  # lightgbm, xgboost, random_forest, mlp
    dataset_id: int
    target_column: str = ""  # 为空则自动选最后一列
    feature_columns: Optional[List[str]] = None  # 为空则自动选择数值特征列
    hyperparams: str = ""  # empty means use runtime/script defaults
    hyperparameter_source: Literal["default", "manual", "file"] = "default"
    algorithm_id: Optional[int] = None  # algorithm-store source, if selected
    resume_algorithm_id: Optional[int] = None  # trusted packaged model to continue
    source_execution_mode: Optional[str] = None  # "execute_user_script" = 真正执行用户上传的脚本
    model_folder_id: Optional[int] = None

class PredictRequest(BaseModel):
    """预测请求"""

    X: List[List[float]]


# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, job_id: str):
        await websocket.accept()
        if job_id not in self.active_connections:
            self.active_connections[job_id] = []
        self.active_connections[job_id].append(websocket)

    def disconnect(self, websocket: WebSocket, job_id: str):
        if job_id in self.active_connections:
            self.active_connections[job_id].remove(websocket)

    async def broadcast(self, job_id: str, message: dict):
        if job_id in self.active_connections:
            for connection in self.active_connections[job_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass


manager = ConnectionManager()


_ACTIVE_TRAINING_STATUSES = {"pending", "running", "training", "queued"}


def _merge_live_run_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Merge live progress from a shared run file for an active job only.

    Completed history is already stored in ``training_jobs``. Reading every
    historical run JSON on each list refresh is very expensive, while only
    active jobs need a cross-worker real-time progress refresh.
    """
    status = str(summary.get("status") or "").lower()
    if status not in _ACTIVE_TRAINING_STATUSES:
        return summary
    job_id = summary.get("job_id")
    if not job_id:
        return summary
    run_file = Path(os.getenv("RUNS_DIR", "/app/uploads/runs")) / f"{job_id}.json"
    if not run_file.is_file():
        return summary
    try:
        payload = json.loads(run_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return summary
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    summary.update({
        "name": config.get("name") or summary.get("name") or job_id,
        "status": str(payload.get("status") or status).lower(),
        "progress": payload.get("progress", summary.get("progress", 0)),
        "current_epoch": payload.get("current_epoch", summary.get("current_epoch", 0)),
        "total_epochs": payload.get("total_epochs", summary.get("total_epochs")),
        "best_metrics": payload.get("best_metrics") or summary.get("best_metrics"),
        "model_path": payload.get("model_path") or summary.get("model_path"),
        "started_at": payload.get("started_at") or summary.get("started_at"),
        "finished_at": payload.get("finished_at") or summary.get("finished_at"),
        "created_at": payload.get("created_at") or summary.get("created_at"),
        "mlflow_run_id": config.get("mlflow_run_id") or summary.get("mlflow_run_id"),
        "registered_model_id": config.get("registered_model_id") or summary.get("registered_model_id"),
        "dataset_id": config.get("dataset_id") or summary.get("dataset_id"),
        "progress_kind": config.get("source_progress_kind") or summary.get("progress_kind") or "epoch",
    })
    return summary


@router.post("/jobs", dependencies=[Depends(require_permission("training:write"))])
def create_training_job(req: TrainingRequest, current_user: dict = Depends(require_auth)):
    """创建并启动训练任务"""
    # 创建任务
    config = {
        "name": req.name,
        "task_type": req.task_type,
        "model_type": req.model_type,
        "epochs": req.epochs,
        "batch_size": req.batch_size,
        "learning_rate": req.learning_rate,
        "hidden_layers": req.hidden_layers,
        "dropout": req.dropout,
        "n_estimators": req.n_estimators,
        "max_depth": req.max_depth,
        "normalize": req.normalize,
        "organization_id": current_user.get("organization_id"),
    }

    job_id = training_service.create_job(config)

    # 启动训练
    data = {"X": req.X, "y": req.y}
    training_service.start_training(job_id, data)

    return {"job_id": job_id, "status": "started", "message": "训练任务已启动"}



@router.post("/jobs-from-dataset", dependencies=[Depends(require_permission("training:write"))])
def create_training_job_from_dataset(req: DatasetJobTrainingRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """从数据集创建训练任务 — 后端自动加载数据、校验数据质量、并训练"""
    from services.data_loader import load_dataset_data
    from services.data_validator import validate_dataset
    from models.core import Dataset as DBDataset, ModelFolder

    dataset = db.query(DBDataset).filter(DBDataset.id == req.dataset_id).first()
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    assert_organization_access(dataset.organization_id, current_user)
    if req.model_folder_id is not None:
        folder = db.query(ModelFolder).filter(ModelFolder.id == req.model_folder_id).first()
        if not folder:
            raise HTTPException(404, "模型分类不存在")
        assert_organization_access(folder.organization_id, current_user)

    # 解析超参
    try:
        hp = json.loads(req.hyperparams) if req.hyperparams else {}
    except json.JSONDecodeError:
        raise HTTPException(400, "超参JSON格式错误")

    # 内置算法走受控适配器；用户上传的 Python 源码在隔离子进程中真实执行。
    selected_algorithm = None
    selected_adapter = None
    resume_metadata = None
    source_contract = None
    if req.resume_algorithm_id is not None:
        from models.core import AlgorithmModel
        selected_algorithm = db.query(AlgorithmModel).filter(
            AlgorithmModel.id == req.resume_algorithm_id
        ).first()
        if not selected_algorithm:
            raise HTTPException(404, "所选封装模型不存在")
        resume_metadata = (
            selected_algorithm.metrics
            if isinstance(selected_algorithm.metrics, dict) else {}
        )
        if not resume_metadata.get("artifact_trainable"):
            raise HTTPException(400, "该封装模型不支持继续训练")
        artifact_path = Path(str(resume_metadata.get("artifact_path") or "")).resolve()
        artifact_root = Path("/app/uploads/algorithms").resolve()
        if artifact_root not in artifact_path.parents or not artifact_path.is_file():
            raise HTTPException(400, "继续训练产物不存在或路径无效")
        raw = artifact_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != resume_metadata.get("artifact_sha256"):
            raise HTTPException(400, "继续训练产物完整性校验失败")
        req.model_type = resume_metadata.get("runtime_model_type") or req.model_type
        req.task_type = resume_metadata.get("task_type") or req.task_type
    elif req.algorithm_id is not None:
        from models.core import AlgorithmModel
        selected_algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == req.algorithm_id).first()
        if not selected_algorithm:
            raise HTTPException(404, "所选算法不存在")
        if req.source_execution_mode == "execute_user_script":
            selected_adapter = None  # 执行用户上传脚本，不走内置 adapter
        else:
            selected_adapter = resolve_algorithm_model(selected_algorithm)
            if not selected_adapter:
                raise HTTPException(400, "该算法的任务类型与算法实现无法自动匹配训练适配器，请回到算法商店补充配置。")
            requested_task = normalize_task_type(req.task_type)
            if requested_task != selected_adapter.task_type:
                raise HTTPException(
                    400,
                    f"任务类型与算法不匹配：算法要求 {selected_adapter.task_type}，当前为 {req.task_type}",
                )
            unavailable = (
                selected_adapter.runtime_model_type == "xgboost" and not XGBOOST_AVAILABLE
                or selected_adapter.runtime_model_type == "lightgbm" and not LIGHTGBM_AVAILABLE
                or selected_adapter.runtime_model_type == "mlp" and not TORCH_AVAILABLE
            )
            if unavailable:
                raise HTTPException(409, f"适配器 {selected_adapter.id} 的运行依赖当前不可用")
            req.model_type = selected_adapter.runtime_model_type
        metadata = (
            selected_algorithm.metrics
            if isinstance(selected_algorithm.metrics, dict) else {}
        )
        candidate_contract = metadata.get("source_contract")
        if isinstance(candidate_contract, dict):
            source_contract = candidate_contract

    # 加载数据
    target = req.target_column if req.target_column else None
    try:
        X, y, feature_names = load_dataset_data(
            db,
            req.dataset_id,
            feature_cols=req.feature_columns or None,
            target_col=target,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # ── 训练前数据质量校验 (Great Expectations 式, 在清洗前的原始 CSV 上跑) ──
    # load_dataset_data 内部已做哨兵值清洗 + 删 NaN 行, 这里对原始 CSV 跑校验,
    # 把"数据到底有多少问题"如实暴露给用户, 不影响实际训练数据。
    validation_report = None
    validation_target = req.target_column or (feature_names[-1] if feature_names else None)
    try:
        import os as _os
        import pandas as _pd
        from sqlalchemy import text as _text
        row = db.execute(
            _text("SELECT file_path FROM datasets WHERE id = :did"),
            {"did": req.dataset_id},
        ).fetchone()
        raw_csv = None
        if row and row[0] and _os.path.exists(row[0]):
            raw_csv = row[0]
        else:
            from pathlib import Path as _Path
            _cands = sorted(_Path("/app/uploads/datasets").glob(f"{req.dataset_id}_*.csv"))
            if _cands:
                raw_csv = str(_cands[0])
        if raw_csv:
            raw_df = _pd.read_csv(raw_csv)
            validation_report = validate_dataset(raw_df, target_col=validation_target)
            # 目标列严重缺失 (>50%) → 拒绝训练 (clean 数据也已 dropna, 但说明数据本身不可信)
            target_check = next(
                (c for c in validation_report["checks"] if c["name"] == "target_distribution"),
                None,
            )
            if validation_target and target_check and not target_check["passed"]:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "数据质量校验未通过, 拒绝训练",
                        "reason": target_check["detail"].get("fatal") or "目标列存在严重问题",
                        "validation_report": validation_report,
                    },
                )
    except HTTPException:
        raise
    except Exception as e:
        # 校验失败不应阻塞训练 (降级: 仅记录警告)
        print(f"[jobs-from-dataset] data validation skipped: {e}")
        validation_report = {"error": f"validation skipped: {e}"}

    # 将numpy转为list
    X_list = X.tolist() if hasattr(X, 'tolist') else list(X)
    y_list = y.tolist() if hasattr(y, 'tolist') else list(y)

    # 构建训练配置
    config = {
        "name": req.name,
        "task_type": req.task_type,
        "model_type": req.model_type,
        "dataset_id": req.dataset_id,
        "target_column": req.target_column,
        "feature_columns": feature_names,
        "validation_report": validation_report,
        "organization_id": current_user.get("organization_id"),
        "algorithm_id": req.algorithm_id,
        "algorithm_name": selected_algorithm.name if selected_algorithm else None,
        "model_folder_id": req.model_folder_id,
        "hyperparameter_source": req.hyperparameter_source,
    }
    effective_hyperparameters = {}
    # 源码真实执行时保留源码声明的原始参数名；别名只属于内置适配器。
    if source_contract:
        source_defaults = (
            source_contract.get("declared_defaults")
            if req.source_execution_mode == "execute_user_script"
            else source_contract.get("defaults")
        )
        if isinstance(source_defaults, dict):
            effective_hyperparameters.update(source_defaults)
            if req.source_execution_mode != "execute_user_script":
                config.update(source_defaults)
        if req.source_execution_mode != "execute_user_script":
            aliases = source_contract.get("parameter_aliases")
            if isinstance(aliases, dict):
                hp = {aliases.get(key, key): value for key, value in hp.items()}
        config.update({
            "source_execution_mode": source_contract.get("execution_mode"),
            "source_entry_function": source_contract.get("entry_function"),
        })
    effective_hyperparameters.update(hp)
    if req.source_execution_mode == "execute_user_script":
        # 列表展示仍读取常用字段；任意源码参数则完整保存在 effective_hyperparameters。
        display_keys = {
            "epochs", "batch_size", "learning_rate", "hidden_layers", "dropout",
            "n_estimators", "max_depth", "normalize", "batch_norm", "normalize_target",
        }
        config.update({key: value for key, value in effective_hyperparameters.items() if key in display_keys})
        source_steps = next(
            (
                effective_hyperparameters.get(key)
                for key in ("epochs", "EPOCHS", "NUM_EPOCHS", "N_TRIALS")
                if effective_hyperparameters.get(key) is not None
            ),
            None,
        )
        if isinstance(source_steps, int) and not isinstance(source_steps, bool) and source_steps > 0:
            config["epochs"] = source_steps
    else:
        config.update(hp)
    # 执行用户脚本模式（覆盖 source_contract 的 execution_mode）
    if req.source_execution_mode:
        config["source_execution_mode"] = req.source_execution_mode
        config["effective_hyperparameters"] = effective_hyperparameters
        data_contract = (
            source_contract.get("data_contract")
            if isinstance(source_contract, dict) and isinstance(source_contract.get("data_contract"), dict)
            else {}
        )
        config["source_data_mode"] = "full_dataset"
        config["source_time_column"] = data_contract.get("time_column")
        config["source_data_contract"] = data_contract
        source_metadata = (
            selected_algorithm.metrics
            if selected_algorithm and isinstance(selected_algorithm.metrics, dict)
            else {}
        )
        if source_metadata.get("dependency_id"):
            config["runtime_environment_id"] = source_metadata["dependency_id"]
    # Parameter content must not forge its provenance metadata.
    config["hyperparameter_source"] = req.hyperparameter_source
    if resume_metadata:
        # 可信产物路径、摘要和恢复模式由服务端元数据决定，禁止超参数覆盖。
        config.update({
            "algorithm_id": req.resume_algorithm_id,
            "algorithm_name": selected_algorithm.name,
            "model_type": resume_metadata.get("runtime_model_type"),
            "task_type": resume_metadata.get("task_type"),
            "resume_artifact_path": resume_metadata.get("artifact_path"),
            "resume_artifact_sha256": resume_metadata.get("artifact_sha256"),
            "resume_training_mode": resume_metadata.get("resume_training_mode"),
            "source_model_id": resume_metadata.get("source_model_id"),
            "training_adapter": resume_metadata.get("training_adapter"),
        })
    if selected_adapter:
        # Authoritative adapter fields must not be overridden by hyperparameters.
        config.update({
            "model_type": selected_adapter.runtime_model_type,
            "task_type": selected_adapter.runtime_task_type,
            "business_task_type": selected_adapter.task_type,
            "training_adapter": selected_adapter.id,
            "split_strategy": selected_adapter.split_strategy,
        })

    # Persist an immutable snapshot of the code that actually executes.
    import inspect
    import uuid
    from services.real_training_service import (
        PyTorchPredictor, PyTorchTrainer, SklearnTrainer,
    )

    runtime_model_type = str(config.get("model_type") or "random_forest")
    runtime_parts = [
        "# Open Foundation immutable training runtime snapshot\n",
        f"# adapter: {config.get('training_adapter') or runtime_model_type}\n",
        f"# model_type: {runtime_model_type}\n",
        f"# algorithm_id: {req.algorithm_id or req.resume_algorithm_id or 'builtin'}\n\n",
    ]
    if source_contract:
        runtime_parts.extend([
            f"# source_entry: {source_contract.get('entry_function') or 'unknown'}\n",
            "# source_runtime_defaults: ",
            json.dumps(source_contract.get("defaults") or {}, ensure_ascii=False, sort_keys=True),
            "\n\n",
        ])
    if req.source_execution_mode == "execute_user_script":
        pass
    elif runtime_model_type in ("mlp", "neural_network"):
        runtime_parts.extend([
            inspect.getsource(PyTorchPredictor), "\n\n",
            inspect.getsource(PyTorchTrainer), "\n",
        ])
    else:
        runtime_parts.extend([inspect.getsource(SklearnTrainer), "\n"])

    source_metadata = (
        selected_algorithm.metrics
        if selected_algorithm and isinstance(selected_algorithm.metrics, dict)
        else {}
    )
    uploaded_source = source_metadata.get("source_code") or source_metadata.get("code")
    # Plain Python uploads keep their source on disk instead of duplicating it
    # in algorithm metadata. Include that exact entry file in the immutable
    # snapshot so custom classes in a joblib artifact can be restored later.
    if not uploaded_source and req.source_execution_mode == "execute_user_script":
        upload_root = Path("/app/uploads/algorithms").resolve()
        artifact_path = Path(str(source_metadata.get("artifact_path") or "")).resolve()
        source_root = Path(str(source_metadata.get("source_project_root") or "")).resolve()
        candidates = []
        entry_name = str(source_metadata.get("source_entry_point") or "").strip()
        if entry_name and source_root.is_dir() and (source_root == upload_root or upload_root in source_root.parents):
            candidate = (source_root / entry_name).resolve()
            if source_root in candidate.parents:
                candidates.append(candidate)
        candidates.append(artifact_path)
        for candidate in candidates:
            if (
                candidate.suffix.lower() == ".py"
                and candidate.is_file()
                and upload_root in candidate.parents
            ):
                uploaded_source = candidate.read_text(encoding="utf-8", errors="replace")
                break
    if isinstance(uploaded_source, str) and uploaded_source:
        runtime_parts.extend([
            "\n\n# ===== Uploaded source executed by the isolated trainer =====\n",
            uploaded_source,
            "\n",
        ])

    snapshot_code = "".join(runtime_parts)
    snapshot_dir = Path("/app/uploads/code_snapshots")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{uuid.uuid4().hex}.py"
    snapshot_path.write_text(snapshot_code, encoding="utf-8")
    config.update({
        "code_snapshot_path": str(snapshot_path),
        "code_snapshot_sha256": hashlib.sha256(snapshot_code.encode("utf-8")).hexdigest(),
        "code_snapshot_filename": f"runtime_{runtime_model_type}.py",
        "algorithm_version": selected_algorithm.version if selected_algorithm else "builtin",
    })

    job_id = training_service.create_job(config)
    data = {"X": X_list, "y": y_list}
    training_service.start_training(job_id, data)

    return {
        "job_id": job_id, "status": "started",
        "message": f"训练任务已启动，使用数据集{req.dataset_id}，{len(y_list)}条数据",
        "dataset_id": req.dataset_id, "rows": len(y_list), "features": len(feature_names),
        "hyperparameter_source": req.hyperparameter_source,
        "validation_report": validation_report,
    }

@router.get("/jobs", dependencies=[Depends(require_permission("training:read"))])
def list_training_jobs(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """列出所有训练任务(轻量摘要)。
    注意:training_jobs 表 hyperparameters 列累计 1GB+,全量读取会导致端点挂死,
    故此处只选列表所需列,不返回 hyperparameters / metrics_history / 完整 metrics。"""
    summary = []
    seen = set()
    # 内存任务 → 摘要
    try:
        for j in training_service.list_jobs(summary_only=True):
            if not is_admin(current_user) and j.get("organization_id") != current_user.get("organization_id"):
                continue
            jid = j.get("job_id")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            item = {
                "job_id": jid, "name": j.get("name") or jid, "status": j.get("status"),
                "progress": j.get("progress", 0), "current_epoch": j.get("current_epoch", 0),
                "total_epochs": j.get("total_epochs"), "best_metrics": j.get("best_metrics"),
                "model_path": j.get("model_path"), "created_at": j.get("created_at"),
                "dataset_id": j.get("dataset_id") or (j.get("config") or {}).get("dataset_id"),
                "progress_kind": j.get("progress_kind") or (j.get("config") or {}).get("source_progress_kind") or "epoch",
                "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"), "source": "memory",
                "mlflow_run_id": j.get("mlflow_run_id"),
                "model_id": j.get("registered_model_id") or j.get("model_id"),
                "registered_model_id": j.get("registered_model_id"),
            }
            summary.append(_merge_live_run_summary(item))
    except Exception as e:
        print(f"[API] list_jobs in-memory failed: {e}")
    # DB 任务 → 只选轻量列(跳过 hyperparameters/metrics 大列)
    try:
        sql = (
            "SELECT training_job_id, name, status, started_at, completed_at, model_id, dataset_id, "
            "mlflow_run_id, registered_model_id, created_at "
            "FROM training_jobs WHERE training_job_id IS NOT NULL"
        )
        params = None
        if not is_admin(current_user):
            sql += " AND organization_id=:organization_id"
            params = {"organization_id": current_user.get("organization_id")}
        sql += " ORDER BY created_at DESC"
        rows = (db.execute(text(sql), params) if params else db.execute(text(sql))).fetchall()
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            st = (r[2] or "unknown")
            item = {
                "job_id": r[0], "name": r[1], "status": st.lower(),
                "progress": 100.0 if st.lower() == "completed" else 0.0,
                "started_at": str(r[3]) if r[3] else None,
                "finished_at": str(r[4]) if r[4] else None,
                "model_id": r[5], "dataset_id": r[6], "source": "db",
                "mlflow_run_id": r[7], "registered_model_id": r[8],
                "created_at": str(r[9]) if r[9] else None,
            }
            summary.append(_merge_live_run_summary(item))
    except Exception as e:
        print(f"[API] list_jobs DB fallback failed: {e}")

    summary.sort(
        key=lambda item: item.get("created_at") or item.get("started_at") or "",
        reverse=True,
    )
    return {"total": len(summary), "items": summary}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read")), Depends(require_training_job_access)])
def get_training_job(job_id: str, db: Session = Depends(get_db)):
    """获取训练任务状态 — 优先读 run JSON(训练实际落盘的最新状态),其次内存/DB"""
    import os as _os
    from pathlib import Path as _Path
    run_file = _Path(_os.getenv("RUNS_DIR", "/app/uploads/runs")) / f"{job_id}.json"
    if run_file.exists():
        try:
            d = json.loads(run_file.read_text())
            config = d.get("config") or {}
            history = d.get("metrics_history") or []
            best_metrics = select_best_metrics(history, config) or d.get("best_metrics") or d.get("metrics") or {}
            latest_metrics = history[-1] if history else d.get("metrics") or best_metrics
            return {
                "job_id": job_id,
                "name": config.get("name") or job_id,
                "status": d.get("status"),
                "progress": d.get("progress", 0),
                "current_epoch": d.get("current_epoch", 0),
                "total_epochs": d.get("total_epochs", config.get("epochs", 0)),
                "metrics": latest_metrics,
                "best_metrics": best_metrics,
                "metrics_history": history,
                "model_path": d.get("model_path"),
                "config": config,
                "dataset_id": config.get("dataset_id"),
                "mlflow_run_id": config.get("mlflow_run_id"),
                "model_id": config.get("registered_model_id") or config.get("model_id"),
                "registered_model_id": config.get("registered_model_id"),
                "error": d.get("error"),
                "error_traceback": d.get("error_traceback"),
                "created_at": d.get("created_at") or d.get("started_at"),
                "started_at": d.get("started_at"),
                "finished_at": d.get("finished_at"),
            }
        except Exception:
            pass
    # 回退:内存
    try:
        return training_service.get_job_status(job_id)
    except ValueError:
        pass
    # 回退:数据库
    try:
        from models.core import TrainingJob as DBTrainingJob
        db_job = db.query(DBTrainingJob).filter(
            DBTrainingJob.training_job_id == job_id
        ).first()
        if db_job:
            return db_job.to_dict()
    except Exception as e:
        print(f"[API] DB fallback for get_job {job_id} failed: {e}")
    raise HTTPException(status_code=404, detail=f"训练任务 {job_id} 不存在")


@router.delete(
    "/jobs/{job_id}",
    dependencies=[
        Depends(require_permission("training:write")),
        Depends(require_training_job_access),
    ],
)
def delete_training_job(
    job_id: str,
    purge_generated: bool = False,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """删除训练任务。

    ``purge_generated=true`` 专供短生命周期生产门禁使用：只有确认模型由该
    job 自动注册、且没有部署或其他训练任务引用时，才同时删除模型版本、
    本地权重、Run JSON、MLflow Registry 模型和 Run。
    """
    from sqlalchemy import text
    from services.real_training_service import _model_registry, _training_jobs
    from models.core import Model as DBModel
    from models.core import TrainingJob as DBTrainingJob

    if purge_generated and not is_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail="只有管理员可以级联清理训练生成的模型与 MLflow 记录",
        )

    memory_job = _training_jobs.get(job_id)
    db_job = db.query(DBTrainingJob).filter(
        DBTrainingJob.training_job_id == job_id
    ).first()
    if not memory_job and not db_job:
        raise HTTPException(status_code=404, detail=f"训练任务 {job_id} 不存在")

    memory_config = (memory_job.config or {}) if memory_job else {}
    model_id = (
        (db_job.registered_model_id or db_job.model_id) if db_job else None
    ) or memory_config.get("registered_model_id")
    mlflow_run_id = (
        db_job.mlflow_run_id if db_job else None
    ) or memory_config.get("mlflow_run_id")
    model_path = getattr(memory_job, "model_path", None) if memory_job else None
    model = db.query(DBModel).filter(DBModel.id == model_id).first() if model_id else None
    model_mlflow_name = model.mlflow_name if model else None

    if purge_generated and model_id:
        generated_prefix = f"训练自动注册 (job={job_id}"
        if not model or not (model.description or "").startswith(generated_prefix):
            raise HTTPException(
                status_code=409,
                detail="拒绝清理：关联模型不是该训练任务自动注册的独占模型",
            )
        deployment_count = db.execute(
            text("SELECT COUNT(*) FROM deployments WHERE model_id = :model_id"),
            {"model_id": model_id},
        ).scalar() or 0
        other_job_count = db.execute(
            text(
                "SELECT COUNT(*) FROM training_jobs "
                "WHERE training_job_id <> :job_id "
                "AND (registered_model_id = :model_id OR model_id = :model_id)"
            ),
            {"job_id": job_id, "model_id": model_id},
        ).scalar() or 0
        if deployment_count or other_job_count:
            raise HTTPException(
                status_code=409,
                detail=(
                    "拒绝清理：关联模型仍被引用 "
                    f"(deployments={deployment_count}, training_jobs={other_job_count})"
                ),
            )

    # 数据库删除放在同一事务中；模型版本由 ORM relationship 级联删除。
    try:
        if db_job:
            db.delete(db_job)
        if purge_generated and model:
            db.delete(model)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"训练任务清理失败: {e}")

    _training_jobs.pop(job_id, None)
    _model_registry.pop(job_id, None)

    cleanup = {
        "purged_generated_model": bool(purge_generated and model),
        "files_deleted": [],
        "mlflow": None,
        "errors": [],
    }
    if purge_generated:
        from pathlib import Path

        candidates = [
            Path("/app/uploads/runs") / f"{job_id}.json",
            Path("/app/uploads/models") / f"{job_id}_model.pkl",
            Path("/app/uploads/models") / f"{job_id}_model.pt",
            Path("/app/uploads/models") / f"{job_id}_config.json",
        ]
        if model_path:
            candidates.append(Path(model_path))
        for candidate in dict.fromkeys(candidates):
            try:
                resolved = candidate.resolve()
                allowed = (
                    resolved.name.startswith(job_id)
                    and resolved.parent in {
                        Path("/app/uploads/runs").resolve(),
                        Path("/app/uploads/models").resolve(),
                    }
                )
                if allowed and resolved.exists():
                    resolved.unlink()
                    cleanup["files_deleted"].append(str(resolved))
            except Exception as exc:
                cleanup["errors"].append(f"file cleanup failed for {candidate}: {exc}")

        try:
            from services.mlflow_tracker import get_mlflow_tracker

            mlflow_cleanup = get_mlflow_tracker().purge_run(
                mlflow_run_id,
                model_mlflow_name,
            )
            cleanup["mlflow"] = mlflow_cleanup
            cleanup["errors"].extend(mlflow_cleanup.get("errors") or [])
        except Exception as exc:
            cleanup["errors"].append(f"MLflow cleanup failed: {exc}")

    return {
        "job_id": job_id,
        "status": "deleted",
        "message": "训练任务已删除",
        "cleanup": cleanup,
    }


@router.post("/jobs/{job_id}/predict", dependencies=[Depends(require_permission("training:read")), Depends(require_training_job_access)])
def predict(job_id: str, req: PredictRequest):
    """使用训练好的模型进行预测"""
    try:
        X = np.array(req.X)
        predictions = training_service.predict(job_id, X)
        return {"job_id": job_id, "predictions": predictions.tolist()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """WebSocket实时监控训练进度"""
    await manager.connect(websocket, job_id)

    try:
        while True:
            # 获取最新状态
            try:
                status = training_service.get_job_status(job_id)
                await websocket.send_json(status)

                # 如果训练完成，关闭连接
                if status["status"] in ["completed", "failed"]:
                    break

            except ValueError:
                await websocket.send_json({"error": "Job not found"})
                break

            await asyncio.sleep(1)  # 每秒更新一次

    except WebSocketDisconnect:
        manager.disconnect(websocket, job_id)


@router.get("/models/supported")
def get_supported_models():
    """获取支持的模型列表"""
    models = {
        "classification": [],
        "regression": [],
    }
    # LightGBM
    if LIGHTGBM_AVAILABLE:
        for task in ["classification", "regression"]:
            models[task].append({
                "id": "lightgbm", "name": "LightGBM",
                "type": "gradient_boosting", "framework": "sklearn",
            })
    # XGBoost
    if XGBOOST_AVAILABLE:
        for task in ["classification", "regression"]:
            models[task].append({
                "id": "xgboost", "name": "XGBoost",
                "type": "gradient_boosting", "framework": "sklearn",
            })
    # RandomForest (always available via sklearn)
    for task in ["classification", "regression"]:
        models[task].append({
            "id": "random_forest", "name": "Random Forest",
            "type": "ensemble", "framework": "sklearn",
        })
    # PyTorch MLP
    if TORCH_AVAILABLE:
        for task in ["classification", "regression"]:
            models[task].append({
                "id": "mlp", "name": "MLP",
                "type": "neural_network", "framework": "pytorch",
            })
    return models


@router.get("/models/available", dependencies=[Depends(require_permission("training:read"))])
def get_available_models(db: Session = Depends(get_db)):
    """Return built-ins plus algorithm-store entries with explicit trainability metadata."""
    models = get_supported_models()
    items = []
    for task in ("classification", "regression"):
        for model in models[task]:
            items.append({**model, "source": "builtin", "task_types": [task], "trainable": True})
    from models.core import AlgorithmModel
    # 训练状态不能决定算法是否可再次使用。已训练或上次训练失败的算法
    # 仍应保留在路径1的选择器中；仅排除明确下架/未发布的算法。
    reusable_statuses = ["published", "training", "trained", "failed"]
    for algorithm in db.query(AlgorithmModel).filter(
        AlgorithmModel.status.in_(reusable_statuses)
    ).order_by(AlgorithmModel.id.desc()).all():
        spec = resolve_algorithm_model(algorithm)
        runtime_available = bool(spec) and not (
            spec.runtime_model_type == "xgboost" and not XGBOOST_AVAILABLE
            or spec.runtime_model_type == "lightgbm" and not LIGHTGBM_AVAILABLE
            or spec.runtime_model_type == "mlp" and not TORCH_AVAILABLE
        )
        reason = None
        if not spec:
            reason = "任务类型或算法实现信息不足，无法自动配置训练适配器"
        elif not runtime_available:
            reason = f"适配器 {spec.id} 的运行依赖当前不可用"
        items.append({"id": f"algorithm:{algorithm.id}", "algorithm_id": algorithm.id,
                      "name": algorithm.name, "framework": algorithm.framework or "custom",
                      "source": "algorithm_store", "task_types": [spec.task_type] if spec else [],
                      "adapter": spec.id if spec else None,
                      "adapter_name": spec.name if spec else None,
                      "runtime_model_type": spec.runtime_model_type if spec else None,
                      "split_strategy": spec.split_strategy if spec else None,
                      "trainable": runtime_available, "reason": reason})
    return {"items": items}


@router.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "real-training",
        "available_frameworks": {
            "lightgbm": LIGHTGBM_AVAILABLE,
            "xgboost": XGBOOST_AVAILABLE,
            "pytorch": TORCH_AVAILABLE,
            "sklearn": True,
        },
    }


class DatasetTrainingRequest(BaseModel):
    """基于数据集的训练请求"""
    name: str = "训练任务"
    dataset_id: Optional[int] = None     # 数据库数据集ID
    scenario: Optional[str] = None       # 业务场景: pv_power, fault_prediction, energy_consumption, load_forecasting, dust_optimization
    task_type: str = "regression"        # classification, regression
    model_type: str = "random_forest"    # random_forest, mlp
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.001
    hidden_layers: List[int] = [128, 64]
    dropout: float = 0.2
    n_estimators: int = 100
    max_depth: int = 6
    normalize: bool = True
    n_samples: int = 1000                # 合成数据时的样本量
    target_col: Optional[str] = None       # 目标列名(不指定则自动取最后数值列)
    feature_cols: Optional[List[str]] = None  # 特征列名(不指定则用全部数值列)


@router.post("/train", dependencies=[Depends(require_permission("training:write"))])
def train_with_dataset(req: DatasetTrainingRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """
    基于数据集或业务场景执行真实训练。

    优先使用 dataset_id 从数据库/CSV加载真实数据，
    如果数据集不可用，使用 scenario 生成有意义的合成数据。

    示例:
      POST /api/v1/real-training/train
      {"name": "除尘能耗预测", "scenario": "dust_optimization",
       "model_type": "random_forest", "task_type": "regression", "n_estimators": 100}
    """
    config = {
        "name": req.name,
        "task_type": req.task_type,
        "model_type": req.model_type,
        "epochs": req.epochs,
        "batch_size": req.batch_size,
        "learning_rate": req.learning_rate,
        "hidden_layers": req.hidden_layers,
        "dropout": req.dropout,
        "n_estimators": req.n_estimators,
        "max_depth": req.max_depth,
        "normalize": req.normalize,
        "dataset_id": req.dataset_id,
        # NOTE: 真实 model_id 在训练完成、向 models 表注册后才生成,
        # 由 real_training_service._save_to_db 回填到 config["registered_model_id"]
        # 并通过 job._save_run() 写入 run JSON。推理服务(_find_model_path /
        # _load_latest_run_for_model)以 registered_model_id 为首选键检索。
        # 此处绝不能写 dataset_id —— 那会让推理按一个错误的 id 反查训练 run,
        # 要么对不上,要么匹配到完全不相关的模型。留 None 即可。
        "model_id": None,
        "scenario": req.scenario,
        "target_col": req.target_col,
        "feature_cols": req.feature_cols,
        "organization_id": current_user.get("organization_id"),
    }

    if req.dataset_id is not None:
        from models.core import Dataset as DBDataset
        dataset = db.query(DBDataset).filter(DBDataset.id == req.dataset_id).first()
        if not dataset:
            raise HTTPException(status_code=404, detail="数据集不存在")
        assert_organization_access(dataset.organization_id, current_user)
    # 训练后回填真实特征名(无论显式指定还是自动选择),供推理页渲染输入
    config["feature_names"] = None  # 占位,加载后更新

    # 加载数据
    try:
        X, y, feature_names = get_training_data(
            db, dataset_id=req.dataset_id, scenario=req.scenario,
            target_col=req.target_col, feature_cols=req.feature_cols,
            n_samples=req.n_samples,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    config["feature_names"] = feature_names

    # 对于分类任务，确保标签是整数
    if req.task_type == "classification":
        y = y.astype(int)

    job_id = training_service.create_job(config)
    
    # 优先使用 Celery 异步，失败则回退到线程
    use_celery = req.model_type != "mlp"  # MLP 用线程（需要 torch）
    celery_task_id = None
    if use_celery:
        try:
            from tasks.training_tasks import train_model as celery_train
            # model_id 传 0:真实 model_id 训练完成注册 models 表后才生成,
            # 由 real_training_service._save_to_db 回填为 registered_model_id。
            # 绝不传 dataset_id(会污染 Celery 清理逻辑,误删其它训练的 run JSON)。
            task = celery_train.delay(
                model_id=0,
                dataset_id=req.dataset_id or 0,
                config=config,
                job_id=job_id,
            )
            celery_task_id = task.id
        except Exception:
            pass  # Celery 不可用时回退到线程
    
    if celery_task_id is None:
        training_service.start_training(job_id, {"X": X.tolist(), "y": y.tolist()})

    return {
        "job_id": job_id,
        "status": "started",
        "message": "训练任务已启动" + (" (Celery异步)" if celery_task_id else " (线程同步)"),
        "celery_task_id": celery_task_id,
        "data_info": {
            "samples": len(X),
            "features": len(feature_names),
            "feature_names": feature_names,
            "source": f"dataset_{req.dataset_id}" if req.dataset_id else f"scenario_{req.scenario}",
        },
    }


@router.get("/scenarios")
def list_scenarios():
    """列出支持的业务场景"""
    return {
        "scenarios": [
            {"id": "pv_power", "name": "光伏功率预测", "task_type": "regression", "features": ["辐照度", "温度", "湿度"], "target": "功率(kW)"},
            {"id": "fault_prediction", "name": "设备故障预测", "task_type": "classification", "features": ["振动", "温度", "压力"], "target": "故障(0/1)"},
            {"id": "energy_consumption", "name": "能耗预测", "task_type": "regression", "features": ["产量", "温度", "湿度"], "target": "能耗(kWh)"},
            {"id": "load_forecasting", "name": "负荷预测", "task_type": "regression", "features": ["小时", "星期几", "温度"], "target": "负荷(kW)"},
            {"id": "dust_optimization", "name": "除尘优化", "task_type": "regression", "features": ["压差", "粉尘浓度", "转速", "温度"], "target": "能耗"},
            {"id": "dust_collector", "name": "除尘器运行数据", "task_type": "regression", "features": ["产能负荷", "粉尘浓度", "过滤压差", "温度", "转速"], "target": "能耗(kWh)"},
            {"id": "steel_quality", "name": "钢铁质量预测", "task_type": "regression", "features": ["碳含量", "锰含量", "温度", "冷却速率"], "target": "质量分数"},
            {"id": "sinter_temperature", "name": "生产过程终点温度预测", "task_type": "regression", "features": ["机速", "料层厚度", "透气率", "压力1", "压力2", "烟道温度"], "target": "终点温度(°C)"},
            {"id": "sinter_feo", "name": "生产过程FeO含量预测", "task_type": "regression", "features": ["工艺参数优化比例", "环冷斜率"], "target": "FeO含量(%)"},
        ],
    }
