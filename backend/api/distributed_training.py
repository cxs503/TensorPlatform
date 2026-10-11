"""
分布式训练 API — Ray 引擎版
基于 Ray 实现真实数据并行 / 超参搜索 / 集成训练
"""
import io, json, os, threading, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from database.config import SessionLocal

router = APIRouter(prefix="/distributed-training", tags=["分布式训练"])

_training_jobs: Dict[str, Dict] = {}
_ray_lock = threading.Lock()
_ray_ready = False
_ray_info = {"nodes": 1, "cpus": 0, "gpus": 0, "status": "not_initialized"}

# ── Ray 懒初始化 ──
def _ensure_ray():
    """Connect to the explicitly configured Ray cluster, without local fallback."""
    global _ray_ready, _ray_info
    if _ray_ready:
        return _ray_info.get("status") == "ready"
    try:
        import ray
        # Use Ray Client rather than the GCS control port. A web API process is
        # a Ray driver, not a cluster node; its Client endpoint is stable in Docker.
        address = os.environ.get("RAY_ADDRESS", "ray://ray-head:10001").strip()
        if not address:
            raise RuntimeError("RAY_ADDRESS 未配置")
        if not ray.is_initialized():
            ray.init(address=address, ignore_reinit_error=True, logging_level=30)
        resources = ray.cluster_resources()
        _ray_info = {
            "nodes": len(ray.nodes()),
            "cpus": int(resources.get("CPU", 0)),
            "gpus": int(resources.get("GPU", 0)),
            "status": "ready",
            "address": address,
        }
        _ray_ready = True
        return True
    except Exception as exc:
        _ray_ready = False
        _ray_info = {
            "nodes": 0, "cpus": 0, "gpus": 0,
            "status": "unavailable", "reason": str(exc)[:300],
        }
        return False

# ── Ray remote 训练函数 ──
def _ray_train_worker(X, y, X_val, y_val, params, task_type, algorithm_type):
    """Train one real algorithm/parameter combination on a Ray worker."""
    from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
    from sklearn.neural_network import MLPRegressor, MLPClassifier
    from sklearn.metrics import r2_score, accuracy_score

    if algorithm_type == "random_forest":
        model_cls = RandomForestClassifier if task_type == "classification" else RandomForestRegressor
    elif algorithm_type == "xgboost":
        from xgboost import XGBClassifier, XGBRegressor
        model_cls = XGBClassifier if task_type == "classification" else XGBRegressor
    elif algorithm_type == "lightgbm":
        from lightgbm import LGBMClassifier, LGBMRegressor
        model_cls = LGBMClassifier if task_type == "classification" else LGBMRegressor
    elif algorithm_type == "mlp":
        model_cls = MLPClassifier if task_type == "classification" else MLPRegressor
    else:
        raise ValueError(f"不支持的并行搜索算法: {algorithm_type}")

    # 处理数据
    if hasattr(X, 'tolist'):
        X_np = np.array(X)
    else:
        X_np = X
    if hasattr(y, 'tolist'):
        y_np = np.array(y)
    else:
        y_np = y

    init_params = dict(params or {})
    # Prevent each worker from creating its own parallel pool.
    if algorithm_type in {"random_forest", "xgboost", "lightgbm"}:
        init_params.setdefault("n_jobs", 1)
        init_params.setdefault("random_state", 42)
    elif algorithm_type == "mlp":
        init_params.setdefault("random_state", 42)
        init_params.setdefault("max_iter", 300)
    if algorithm_type == "xgboost":
        init_params.setdefault("verbosity", 0)
        init_params.setdefault("eval_metric", "logloss" if task_type == "classification" else "rmse")
    elif algorithm_type == "lightgbm":
        init_params.setdefault("verbosity", -1)
    model = model_cls(**init_params)
    model.fit(X_np, y_np)

    if X_val is not None and y_val is not None:
        if hasattr(X_val, 'tolist'):
            Xv = np.array(X_val)
        else:
            Xv = X_val
        if hasattr(y_val, 'tolist'):
            yv = np.array(y_val)
        else:
            yv = y_val
        y_pred = model.predict(Xv)
        if task_type == "classification":
            score = float(accuracy_score(yv, y_pred))
        else:
            score = float(r2_score(yv, y_pred))
    else:
        score = 0.0

    # 序列化模型
    buf = io.BytesIO()
    joblib.dump(model, buf)
    return {"score": score, "model_bytes": buf.getvalue(), "params": params}

# ── Uploaded-source parameter-search worker ──
class _SourceTrialJob:
    """Small job facade used only inside a Ray worker for user-script execution."""

    def __init__(self, config):
        self.job_id = f"raytrial_{uuid.uuid4().hex[:12]}"
        self.config = config
        self.status = "running"
        self.progress = 0
        self.current_epoch = 0
        self.total_epochs = 1
        self.model_path = None
        self.best_metrics = {}

    def _save_run(self):
        # Trial progress is aggregated by the API process; Ray workers must not
        # write a competing task-history record.
        return None


def _source_trial_score(metrics, task_type):
    if task_type == "classification":
        for key in ("accuracy", "f1", "f1_score"):
            if isinstance(metrics.get(key), (int, float)):
                return float(metrics[key])
    else:
        if isinstance(metrics.get("r2"), (int, float)):
            return float(metrics["r2"])
        for key in ("rmse", "mae", "mse", "loss", "val_loss"):
            if isinstance(metrics.get(key), (int, float)):
                return -float(metrics[key])
    raise ValueError("上传算法未输出可比较指标；请写入 metrics.r2 / metrics.accuracy / metrics.rmse / metrics.mae")


def _ray_source_train_worker(trial_config, params):
    """Run one uploaded algorithm source trial with its own parameter file."""
    from services.user_script_trainer import train as train_user_script

    config = dict(trial_config)
    effective = dict(config.get("source_defaults") or {})
    effective.update(params or {})
    config["effective_hyperparameters"] = effective
    config["source_execution_mode"] = "execute_user_script"
    trial = _SourceTrialJob(config)
    model_path, model_bytes, metrics, artifact_context = train_user_script(trial)
    try:
        Path(model_path).unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "score": _source_trial_score(metrics, config.get("model_type")),
        "metrics": metrics,
        "model_bytes": model_bytes,
        "params": effective,
        "artifact_context": artifact_context,
    }


# ══════════ Pydantic ══════════
class TrainingJobCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    model_type: str = "regression"
    algorithm_id: int
    # 当前已实现的是通用 sklearn 参数并行搜索；其他策略不再伪装为可用。
    parallel_strategy: str = "data_parallel"
    dataset_id: int
    target_column: str
    num_workers: int = 4
    param_grid: Optional[List[Dict]] = None
    cv: int = 3

class NodeRegistration(BaseModel):
    node_id: str
    gpu_info: List[Dict[str, Any]] = []
    status: str = "available"

# ══════════ 端点 ══════════

@router.get("/status")
def get_status():
    _ensure_ray()
    active = len([j for j in _training_jobs.values() if j["status"] == "running"])
    return {
        "status": _ray_info["status"],
        "cluster": _ray_info,
        "active_jobs": active,
        "total_jobs": len(_training_jobs),
    }

@router.post("/jobs")
def create_training_job(req: TrainingJobCreate):
    if req.parallel_strategy != "data_parallel":
        raise HTTPException(status_code=400, detail="当前仅支持“参数并行搜索”策略")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="请填写任务名称")
    if req.num_workers < 1 or req.num_workers > 12:
        raise HTTPException(status_code=400, detail="Worker 数须在 1 至 12 之间")
    algorithm = _get_parallel_algorithm(req.algorithm_id)
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    job = {
        "job_id": job_id, "name": req.name, "model_type": req.model_type,
        "algorithm_id": algorithm["id"], "algorithm_name": algorithm["name"],
        "algorithm_type": algorithm["algorithm_type"], "execution_kind": algorithm["execution_kind"],
        "source_defaults": algorithm.get("source_defaults") or {},
        "source_data_contract": algorithm.get("source_data_contract") or {},
        "source_time_column": algorithm.get("source_time_column"),
        "runtime_environment_id": algorithm.get("runtime_environment_id"),
        "parallel_strategy": req.parallel_strategy, "dataset_id": req.dataset_id,
        "target_column": req.target_column, "num_workers": req.num_workers,
        "param_grid": req.param_grid, "cv": req.cv,
        "status": "pending", "created_at": datetime.now().isoformat(),
        "started_at": None, "finished_at": None,
        "logs": [], "metrics": {}, "results": None,
    }
    _training_jobs[job_id] = job
    return {"job_id": job_id, "name": req.name, "status": "pending"}

@router.get("/jobs")
def list_training_jobs(status: Optional[str] = None):
    jobs = list(_training_jobs.values())
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    return {"total": len(jobs), "items": jobs}

@router.get("/jobs/{job_id}")
def get_training_job(job_id: str):
    job = _training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job

@router.post("/jobs/{job_id}/start")
def start_training_job(job_id: str):
    job = _training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] not in ("pending", "stopped"):
        raise HTTPException(status_code=400, detail=f"状态错误: {job['status']}")

    job["status"] = "running"
    job["started_at"] = datetime.now().isoformat()
    job["logs"] = [f"[{datetime.now().strftime('%H:%M:%S')}] 启动分布式训练..."]

    # 后台线程执行真实 Ray 训练
    def _run():
        try:
            if not _ensure_ray():
                job["status"] = "failed"
                job["logs"].append("Ray 初始化失败")
                return
            import ray

            # 加载数据
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 加载数据集...")
            ds_id = job.get("dataset_id")
            target = job.get("target_column")
            X_np, y_np, feat_names = _load_dataset(ds_id, target)
            # 标注数据来源:无 dataset_id 时 _load_dataset 返回合成演示数据,
            # 但训练/评估(sklearn on Ray workers)为真实计算
            if not ds_id:
                job["data_source"] = "synthetic"
                job["data_note"] = "未提供 dataset_id,使用合成演示数据;训练与 R² 评估为真实 sklearn 计算,但指标反映合成数据而非真实业务数据"
            else:
                job["data_source"] = "real_dataset"

            job["feature_columns"] = (
                list((job.get("source_data_contract") or {}).get("feature_columns") or [])
                if job.get("execution_kind") == "source" else feat_names
            ) or feat_names
            X_ref = y_ref = Xv_ref = yv_ref = None
            if job.get("execution_kind") == "source":
                # Do not split or replace data in the platform for uploaded
                # source. The source script receives the selected full dataset
                # and applies its own documented train/validation logic.
                job["logs"].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 已把完整数据集与参数文件交给上传源码执行"
                )
            else:
                X_train, X_val, y_train, y_val = _split_data(X_np, y_np)
                job["logs"].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 训练集: {len(X_train)} 条, 验证集: {len(X_val)} 条"
                )
                X_ref = ray.put(X_train.tolist())
                y_ref = ray.put(y_train.tolist())
                Xv_ref = ray.put(X_val.tolist())
                yv_ref = ray.put(y_val.tolist())

            # Parameter candidates are specific to the selected real algorithm
            # or to an uploaded source's declared training contract.
            if job["execution_kind"] == "source":
                params = job.get("param_grid") or _default_source_param_grid(job.get("source_defaults") or {})
            else:
                params = job.get("param_grid") or _default_param_grid(job["algorithm_type"])
            if not params:
                raise ValueError("该上传算法没有可搜索的参数。请先在算法源码中声明可配置常量，或在页面提供参数组合。")
            _validate_param_grid(params, job)
            n_workers = min(job.get("num_workers", 4), len(params))
            job["logs"].append(
                f"[{datetime.now().strftime('%H:%M:%S')}] {job['algorithm_name']}："
                f"{n_workers} 个 worker 并行训练 {len(params)} 组参数..."
            )

            # Keep at most num_workers tasks active.  Previously every parameter
            # set was submitted immediately and the final completed future was not
            # collected, so the page could say “completed” with progress 3/4.
            remote_worker = ray.remote(
                _ray_source_train_worker if job["execution_kind"] == "source" else _ray_train_worker
            )
            total = len(params)
            max_parallel = max(1, min(job.get("num_workers", 4), total))
            remaining = iter(params)
            pending = {}

            def _submit_next():
                try:
                    param = next(remaining)
                except StopIteration:
                    return False
                if job["execution_kind"] == "source":
                    source_trial_config = {
                        "dataset_id": job["dataset_id"],
                        "algorithm_id": job["algorithm_id"],
                        "algorithm_name": job["algorithm_name"],
                        "model_type": job["model_type"],
                        "target_column": job["target_column"],
                        "feature_columns": job.get("feature_columns"),
                        "source_data_mode": "full_dataset",
                        "source_time_column": job.get("source_time_column"),
                        "source_data_contract": job.get("source_data_contract") or {},
                        "source_defaults": job.get("source_defaults") or {},
                        "runtime_environment_id": job.get("runtime_environment_id"),
                    }
                    future = remote_worker.options(num_cpus=1).remote(source_trial_config, param)
                else:
                    future = remote_worker.options(num_cpus=1).remote(
                        X_ref, y_ref, Xv_ref, yv_ref, param, job["model_type"], job["algorithm_type"]
                    )
                pending[future] = param
                return True

            for _ in range(max_parallel):
                _submit_next()

            # Poll progress and keep the requested degree of parallelism until
            # every parameter combination has a recorded outcome.
            finished = 0
            results_list = []
            while pending:
                done, _ = ray.wait(list(pending), num_returns=1, timeout=30)
                if not done:
                    job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 等待 worker 返回...")
                    continue
                for future in done:
                    pending.pop(future, None)
                    finished += 1
                    try:
                        result = ray.get(future)
                        results_list.append(result)
                        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Worker {finished}/{total}: score={result['score']:.4f}")
                    except Exception as e:
                        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Worker {finished}/{total}: ERROR {e}")
                    _submit_next()
                job["metrics"]["progress"] = f"{finished}/{total}"

            # 汇总结果
            if results_list:
                best = max(results_list, key=lambda r: r["score"])
                job["results"] = {
                    "best_score": best["score"],
                    "best_params": best["params"],
                    "n_trials": len(results_list),
                }
                job["metrics"]["best_score"] = best["score"]
                # Source algorithms may use RMSE/MAE as their primary metric.
                # Preserve the metrics they actually returned instead of
                # mislabelling every regression score as R2.
                if isinstance(best.get("metrics"), dict):
                    job["metrics"].update(best["metrics"])
                else:
                    metric_name = "accuracy" if job["model_type"] == "classification" else "r2"
                    job["metrics"][metric_name] = best["score"]
                job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 最佳得分: {best['score']:.4f} 参数: {best['params']}")

                # 注册模型
                try:
                    registered_features = (
                        job.get("feature_columns") if job.get("execution_kind") == "source" else feat_names
                    )
                    _register_model(job, best["model_bytes"], best["score"], best["params"], registered_features,
                                metrics=best.get("metrics"), artifact_context=best.get("artifact_context"))
                except Exception as e:
                    job["logs"].append(f"模型注册失败: {e}")

            job["status"] = "completed"
            job["finished_at"] = datetime.now().isoformat()
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 训练完成")
        except Exception as e:
            job["status"] = "failed"
            job["logs"].append(f"ERROR: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running", "message": "分布式训练已启动"}

@router.post("/jobs/{job_id}/stop")
def stop_training_job(job_id: str):
    job = _training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    job["status"] = "stopped"
    job["finished_at"] = datetime.now().isoformat()
    job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 已停止")
    return {"job_id": job_id, "status": "stopped"}

@router.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str, lines: int = 100):
    job = _training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"job_id": job_id, "total_lines": len(job["logs"]), "logs": job["logs"][-lines:]}

@router.get("/jobs/{job_id}/metrics")
def get_job_metrics(job_id: str):
    job = _training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"job_id": job_id, "metrics": job.get("metrics", {}), "status": job["status"]}

@router.get("/strategies")
def list_parallel_strategies():
    _ensure_ray()
    return {"strategies": [
        {"id": "data_parallel", "name": "参数并行搜索", "description": "多个 worker 分别训练同一算法的不同超参数组合，并保留验证集得分最优的模型", "best_for": "表格回归、分类的超参数搜索"},
    ]}


@router.get("/algorithms")
def list_parallel_algorithms():
    """Expose built-ins and uploaded sources without replacing source code."""
    from models.core import AlgorithmModel
    from services.algorithm_source_service import algorithm_asset_type, ensure_algorithm_source_contract
    db = SessionLocal()
    try:
        items = []
        reusable_statuses = ["published", "training", "trained", "failed"]
        algorithms = db.query(AlgorithmModel).filter(
            AlgorithmModel.status.in_(reusable_statuses)
        ).order_by(AlgorithmModel.id.desc()).all()
        for algorithm in algorithms:
            # Control algorithms produce simulation artifacts, not a supervised
            # model-selection trial. Keep them visible with an explicit route
            # instead of wrongly reporting a missing training entry.
            if str(algorithm.category or "").strip().lower() == "control_optimization":
                items.append({
                    "id": algorithm.id,
                    "name": algorithm.name,
                    "algorithm_type": "control_simulation",
                    "framework": algorithm.framework,
                    "execution_kind": "simulation",
                    "parameter_defaults": {},
                    "searchable_parameters": [],
                    "source_contract_ready": True,
                })
                continue
            # Test for an uploaded source before normalizing the declared family.
            # A source tagged "mlp" must run its own Python, not a built-in MLP.
            if algorithm_asset_type(algorithm) == "uploaded_source":
                contract = ensure_algorithm_source_contract(db, algorithm) or {}
                defaults = contract.get("declared_defaults") if isinstance(contract, dict) else {}
                defaults = defaults if isinstance(defaults, dict) else {}
                items.append({
                    "id": algorithm.id,
                    "name": algorithm.name,
                    "algorithm_type": "uploaded_source",
                    "framework": algorithm.framework,
                    "execution_kind": "source",
                    "parameter_defaults": defaults,
                    "searchable_parameters": _source_searchable_params(defaults),
                    "source_contract_ready": bool(contract.get("entry_function")),
                })
                continue
            algorithm_type = _normalize_parallel_algorithm(algorithm.algorithm)
            if not algorithm_type:
                continue
            items.append({
                "id": algorithm.id,
                "name": algorithm.name,
                "algorithm_type": algorithm_type,
                "framework": algorithm.framework,
                "execution_kind": "builtin",
                "parameter_defaults": _default_param_grid(algorithm_type)[0],
            })
        return {"items": items, "total": len(items)}
    finally:
        db.close()


# ══════════ 辅助函数 ══════════
_PARALLEL_ALGORITHMS = {
    "randomforest": "random_forest", "random_forest": "random_forest", "random forest": "random_forest",
    "rf": "random_forest", "xgboost": "xgboost", "xgb": "xgboost",
    "lightgbm": "lightgbm", "lgbm": "lightgbm",
    "mlp": "mlp", "neural_network": "mlp", "neural network": "mlp",
}


def _normalize_parallel_algorithm(value):
    return _PARALLEL_ALGORITHMS.get(str(value or "").strip().lower().replace("-", "_"))


def _get_parallel_algorithm(algorithm_id):
    from models.core import AlgorithmModel
    db = SessionLocal()
    try:
        algorithm = db.query(AlgorithmModel).filter(
            AlgorithmModel.id == algorithm_id,
            AlgorithmModel.status.in_(["published", "training", "trained", "failed"]),
        ).first()
        if not algorithm:
            raise HTTPException(status_code=404, detail="算法不存在或未发布")
        if str(algorithm.category or "").strip().lower() == "control_optimization":
            raise HTTPException(status_code=400, detail="控制仿真算法请在“控制仿真任务”中运行，不适用于并行参数搜索")
        from services.algorithm_source_service import algorithm_asset_type, ensure_algorithm_source_contract
        if algorithm_asset_type(algorithm) == "uploaded_source":
            contract = ensure_algorithm_source_contract(db, algorithm) or {}
        else:
            algorithm_type = _normalize_parallel_algorithm(algorithm.algorithm)
            if algorithm_type:
                return {
                    "id": algorithm.id, "name": algorithm.name, "algorithm_type": algorithm_type,
                    "execution_kind": "builtin",
                }
            raise HTTPException(status_code=400, detail="该算法不是可执行的 Python 上传源码，不能用于参数搜索")
        entry = contract.get("entry_function") if isinstance(contract, dict) else None
        if not entry:
            raise HTTPException(status_code=400, detail="上传算法未识别到训练入口；请在源码中提供 train_* 函数或训练脚本入口")
        defaults = contract.get("declared_defaults") if isinstance(contract.get("declared_defaults"), dict) else {}
        metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
        return {
            "id": algorithm.id, "name": algorithm.name, "algorithm_type": "uploaded_source",
            "execution_kind": "source", "source_defaults": defaults,
            "source_data_contract": contract.get("data_contract") or {},
            "source_time_column": (contract.get("data_contract") or {}).get("time_column"),
            "runtime_environment_id": metadata.get("dependency_id"),
        }
    finally:
        db.close()


def _source_searchable_params(defaults):
    ignored = {"seed", "random_state", "train_ratio", "val_ratio", "test_ratio", "n_trials", "num_trials", "trials"}
    return [
        key for key, value in (defaults or {}).items()
        if str(key).lower() not in ignored and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _default_source_param_grid(defaults):
    base = dict(defaults or {})
    searchable = _source_searchable_params(base)
    if not searchable:
        return []
    preferred = next(
        (key for key in searchable if any(token in key.lower() for token in ("learning_rate", "lr"))),
        next((key for key in searchable if "batch" in key.lower()), searchable[0]),
    )
    value = base[preferred]
    if isinstance(value, int) and not isinstance(value, bool):
        variants = sorted({max(1, int(round(value * 0.5))), value, max(1, int(round(value * 1.5))), max(1, value * 2)})
    else:
        variants = sorted({max(1e-8, float(value) * 0.5), float(value), float(value) * 1.5, float(value) * 2})
    return [{**base, preferred: variant} for variant in variants]


def _validate_param_grid(params, job):
    if not isinstance(params, list) or not params or not all(isinstance(item, dict) for item in params):
        raise ValueError("参数组合必须是至少一项 JSON 对象数组")
    if len(params) > 24:
        raise ValueError("单次参数搜索最多支持 24 组组合")
    if job.get("execution_kind") == "source":
        allowed = set((job.get("source_defaults") or {}).keys())
        unknown = sorted({key for item in params for key in item if key not in allowed})
        if unknown:
            raise ValueError("上传算法参数未在源码契约中声明：" + "、".join(map(str, unknown)))


def _default_param_grid(algorithm_type):
    if algorithm_type == "random_forest":
        return [{"n_estimators": 100, "max_depth": depth} for depth in (5, 10, 15, 20)]
    if algorithm_type == "xgboost":
        return [{"n_estimators": trees, "max_depth": depth, "learning_rate": rate}
                for trees, depth, rate in ((100, 4, 0.05), (160, 5, 0.05), (160, 6, 0.1), (240, 6, 0.05))]
    if algorithm_type == "lightgbm":
        return [{"n_estimators": trees, "num_leaves": leaves, "learning_rate": rate}
                for trees, leaves, rate in ((100, 15, 0.05), (160, 31, 0.05), (160, 31, 0.1), (240, 63, 0.05))]
    if algorithm_type == "mlp":
        return [{"hidden_layer_sizes": layers, "learning_rate_init": rate}
                for layers in ((64,), (128,), (64, 32), (128, 64))
                for rate in (0.001,)]
    raise ValueError(f"不支持的并行搜索算法: {algorithm_type}")

def _load_dataset(dataset_id, target_column):
    if not dataset_id:
        # Demo data
        np.random.seed(42)
        X = np.random.randn(500, 8)
        y = X[:, 0] * 2 + X[:, 1] - X[:, 2] * 0.5 + np.random.randn(500) * 0.3
        return X, y, [f"feature_{i}" for i in range(8)]
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT file_path FROM datasets WHERE id = :id"), {"id": dataset_id}).fetchone()
        if not row or not row[0]:
            raise ValueError("数据集不存在")
        path = str(row[0])
        suffix = os.path.splitext(path)[1].lower()
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        elif suffix in {".parquet", ".pq"}:
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path)
        if target_column not in df.columns:
            raise ValueError(f"目标列 '{target_column}' 不在数据集中")
        time_cols = [c for c in df.columns if '时间' in str(c) or str(c).lower() in {"timestamp", "datetime", "date", "time"}]
        skip = set(time_cols) | {target_column}
        feat_cols = [c for c in df.columns if c not in skip and pd.api.types.is_numeric_dtype(df[c])]
        if not feat_cols:
            raise ValueError("数据集中没有可用于训练的数值特征列")
        valid = df[target_column].notna()
        X = df.loc[valid, feat_cols].apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median(numeric_only=True)).fillna(0).values
        y = df.loc[valid, target_column].values
        if len(X) < 5:
            raise ValueError("有效样本不足，至少需要 5 行")
        return X, y, feat_cols
    finally:
        db.close()

def _split_data(X, y, test_size=0.2):
    from sklearn.model_selection import train_test_split
    return train_test_split(X, y, test_size=test_size, random_state=42)

def _register_model(job, model_bytes, score, params, feature_names, metrics=None, artifact_context=None):
    db = SessionLocal()
    try:
        from datetime import datetime as dt
        from pathlib import Path
        model_path = Path("/app/uploads/models") / f"ray_{job['job_id']}.pkl"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(model_bytes)
        row = db.execute(text("""
            INSERT INTO models (name, description, model_type, framework, status, model_blob, created_at, updated_at)
            VALUES (:name, :desc, :mt, :framework, 'READY', :blob, NOW(), NOW()) RETURNING id
        """), {"name": f"参数搜索-{job['name']}", "desc": f"Ray参数并行搜索 (job={job['job_id']})",
                "mt": job["model_type"], "framework": "user_script" if job.get("execution_kind") == "source" else "scikit-learn",
                "blob": model_bytes}).fetchone()
        mid = row[0]
        version_parameters = {
            "feature_cols": feature_names, "params": params,
            "strategy": job["parallel_strategy"], "algorithm_id": job["algorithm_id"],
            "algorithm_type": job["algorithm_type"], "execution_kind": job.get("execution_kind"),
        }
        if job.get("execution_kind") == "source":
            # Keep the same top-level artifact contract used by ordinary
            # uploaded-source training. Deployment can then recreate custom
            # predictor classes in the source runtime before loading the pickle.
            context = artifact_context or {}
            version_parameters.update({
                "artifact_kind": "user_script_predictor",
                "script_source": context.get("script_source"),
                "python_path": context.get("python_path"),
                "environment_id": context.get("environment_id"),
                "environment_name": context.get("environment_name"),
                "runtime_digest": context.get("runtime_digest"),
                "n_features_in": context.get("n_features_in"),
                "feature_columns": context.get("feature_columns") or feature_names,
                "time_column": job.get("source_time_column"),
                "predictor_type": context.get("predictor_type"),
                "target_column": job.get("target_column"),
                "effective_hyperparameters": params,
            })
        db.execute(text("""
            INSERT INTO model_versions (model_id, version_number, file_path, metrics, parameters, is_deployed, created_at)
            VALUES (:mid, 'v1', :fp, :met, :par, false, NOW())
        """), {"mid": mid, "fp": str(model_path),
                "met": json.dumps(metrics or ({"accuracy": score} if job["model_type"] == "classification" else {"r2": score})),
                "par": json.dumps(version_parameters, ensure_ascii=False)})
        db.commit()
        job["logs"].append(f"模型已注册: id={mid}")
    finally:
        db.close()
