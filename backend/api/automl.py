"""
AutoML自动化机器学习 API
支持自动化模型选择、超参搜索、特征工程
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from database.config import get_db
from api.deps import require_auth, require_permission
from api.tenant_access import is_admin
from services.data_loader import load_dataset_data

router = APIRouter(prefix="/automl", tags=["AutoML"])

# 内存存储AutoML任务
_automl_tasks = {}


# ============================================================
# 模型工厂:按实际安装情况注册,未安装的库回退到 sklearn 等价模型
# ============================================================
def _build_model_factory(task_type: str) -> Dict[str, Any]:
    """Return the same canonical implementations exposed in the algorithm store."""
    from services.builtin_algorithm_registry import build_model_factory
    return build_model_factory(task_type)


def _validate_algorithm_params(algorithm: str, task_type: str, params):
    from services.builtin_algorithm_registry import validate_algorithm_params
    return validate_algorithm_params(algorithm, task_type, params)


def _builtin_algorithm_supports(algorithm: str, task_type: str) -> bool:
    """算法是否支持该任务类型（task_type 自动校正后用于兜底清理不兼容的 override）。"""
    from services.builtin_algorithm_registry import BUILTIN_SPECS
    spec = BUILTIN_SPECS.get(algorithm)
    if not spec:
        return False
    norm = "regression" if task_type == "time_series" else task_type
    return norm in spec["tasks"]


def _is_classification(y: Any) -> bool:
    """启发式判断:y 取值数量少且为整数 → 分类任务。"""
    import numpy as np

    uniq = np.unique(y)
    if len(uniq) <= 20:
        # 整数标签视为分类
        try:
            if np.all(np.equal(np.mod(uniq, 1), 0)):
                return True
        except Exception:
            pass
    return False


class AutoMLTaskCreate(BaseModel):
    name: str
    task_type: str  # classification, regression, time_series
    dataset_id: str
    target_column: str
    time_limit_minutes: int = 60
    optimization_metric: str = "accuracy"
    allowed_models: Optional[List[str]] = None  # 限制使用的模型
    model_overrides: Optional[Dict[str, Dict[str, Any]]] = None  # {算法key: {超参}}，覆盖默认参数


class TrialResult(BaseModel):
    trial_id: str
    model: str
    params: Dict[str, Any]
    score: float
    training_time_seconds: int


@router.get("/status")
def get_status():
    """获取AutoML服务状态"""
    return {
        "status": "ready",
        "active_tasks": len(
            [t for t in _automl_tasks.values() if t["status"] == "running"]
        ),
        "completed_tasks": len(
            [t for t in _automl_tasks.values() if t["status"] == "completed"]
        ),
    }


@router.get("/compare-quick")
def compare_quick(
    target: str,
    models: str = "",
    dataset_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """快速多模型对比(同步)。前端 AutoML.vue 调用,返回 {results, features}。

    - 当 dataset_id 提供时:从数据库加载真实数据集 CSV,
      用 load_dataset_data(db, dataset_id, target_col=target) 取 X, y。
    - 否则:回退到 sklearn make_regression 合成数据,并在 data_source 标注 synthetic。

    模型工厂按容器实际安装情况选择 lightgbm/xgboost(已装)或回退到 sklearn。
    results 字段对齐前端:model_name/type/r2_mean/rmse_mean/mae_mean
    """
    import time
    import numpy as np
    from sklearn.datasets import make_regression
    from sklearn.model_selection import cross_val_score
    from sklearn.metrics import make_scorer, mean_squared_error, mean_absolute_error

    model_list = [m for m in (models or "").split(",") if m] or [
        "random_forest",
        "lightgbm",
        "xgboost",
    ]

    # ---- 加载训练数据:优先真实数据集,否则合成 ----
    data_source = None
    features: List[str] = []
    task_type = "regression"
    if dataset_id:
        try:
            did = int(dataset_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"dataset_id 必须为整数,收到: {dataset_id}"
            )
        try:
            X, y, feat_names = load_dataset_data(db, did, target_col=target)
            features = list(feat_names)
            data_source = f"dataset_{did}"
            # 自动判断任务类型
            task_type = "classification" if _is_classification(y) else "regression"
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"无法加载数据集 {dataset_id}(target={target}): {e}",
            )
    else:
        X, y = make_regression(
            n_samples=500, n_features=8, noise=15.0, random_state=42
        )
        features = [f"feature_{i}" for i in range(X.shape[1])]
        data_source = "synthetic(make_regression n=500, features=8, random_state=42)"

    _MODEL_FACTORY = _build_model_factory(task_type)

    # 评分器:回归用 r2/rmse/mae;分类用 accuracy
    is_cls = task_type == "classification"

    results = []
    for m in model_list:
        factory = _MODEL_FACTORY.get(m)
        if not factory:
            raise HTTPException(
                status_code=400,
                detail=f"AutoML 内置算法不存在或不支持当前任务：{m}",
            )
        model_obj = factory()
        t0 = time.time()
        try:
            if is_cls:
                acc_scores = cross_val_score(model_obj, X, y, cv=5, scoring="accuracy")
                r2_mean = float(np.mean(acc_scores))
                # 分类任务下 rmse/mae 不适用,填 0 以保持前端字段对齐
                rmse_mean = 0.0
                mae_mean = 0.0
            else:
                r2_scores = cross_val_score(model_obj, X, y, cv=5, scoring="r2")
                rmse_scorer = make_scorer(
                    lambda yt, yp: float(np.sqrt(mean_squared_error(yt, yp))),
                    greater_is_better=False,
                )
                mae_scorer = make_scorer(
                    lambda yt, yp: float(mean_absolute_error(yt, yp)),
                    greater_is_better=False,
                )
                rmse_scores = cross_val_score(model_obj, X, y, cv=5, scoring=rmse_scorer)
                mae_scores = cross_val_score(model_obj, X, y, cv=5, scoring=mae_scorer)
                r2_mean = float(np.mean(r2_scores))
                rmse_mean = float(np.mean(np.abs(rmse_scores)))
                mae_mean = float(np.mean(np.abs(mae_scores)))
        except Exception:
            r2_mean, rmse_mean, mae_mean = 0.0, 0.0, 0.0
        results.append(
            {
                "model_name": m,
                "type": "sklearn",
                "r2_mean": round(r2_mean, 4),
                "rmse_mean": round(rmse_mean, 4),
                "mae_mean": round(mae_mean, 4),
                "training_time": round(time.time() - t0, 2),
            }
        )
    results.sort(key=lambda x: x["r2_mean"], reverse=True)
    return {
        "results": results,
        "features": features,
        "target": target,
        "dataset_id": dataset_id,
        "task_type": task_type,
        "data_source": data_source,
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "note": "指标由真实 sklearn 5折交叉验证计算,非随机生成",
    }


@router.post("/tasks")
def create_automl_task(
    req: AutoMLTaskCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """创建AutoML任务"""
    task_id = f"aml_{uuid.uuid4().hex[:8]}"

    metric = str(req.optimization_metric or "").lower()
    valid_metrics = (
        {"accuracy", "f1"}
        if req.task_type == "classification"
        else {"r2", "rmse", "mae"}
    )
    if metric not in valid_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"评选指标 {metric or '(空)'} 不适用于 {req.task_type}，可选: {sorted(valid_metrics)}",
        )
    if req.allowed_models and len(req.allowed_models) > 5:
        raise HTTPException(status_code=400, detail="一次AutoML任务最多选择5个候选模型")

    dataset_name = f"数据集 #{req.dataset_id}"
    try:
        dataset_row = db.execute(
            text("SELECT name FROM datasets WHERE id = :id"),
            {"id": int(req.dataset_id)},
        ).fetchone()
        if dataset_row and dataset_row[0]:
            dataset_name = dataset_row[0]
    except (TypeError, ValueError):
        pass

    # 默认允许的模型
    if req.task_type == "classification":
        allowed_models = req.allowed_models or [
            "lightgbm",
            "xgboost",
            "random_forest",
            "logistic_regression",
            "gradient_boosting",
        ]
    elif req.task_type == "regression":
        allowed_models = req.allowed_models or [
            "lightgbm",
            "xgboost",
            "random_forest",
            "linear_regression",
            "ridge",
        ]
    else:
        allowed_models = req.allowed_models or ["lightgbm"]

    registered_models = set(_build_model_factory(req.task_type))
    unknown_models = [model for model in allowed_models if model not in registered_models]
    if unknown_models:
        raise HTTPException(
            status_code=400,
            detail=f"以下算法不是当前任务可用的内置模板：{', '.join(unknown_models)}",
        )

    # 校验并归一化各算法的自定义超参（覆盖默认值）
    model_overrides = {}
    if req.model_overrides:
        for algo, params in req.model_overrides.items():
            if algo not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail=f"参数覆盖引用了未选中的算法：{algo}",
                )
            try:
                model_overrides[algo] = _validate_algorithm_params(algo, req.task_type, params)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{algo}：{exc}")

    task = {
        "id": task_id,
        "name": req.name,
        "task_type": req.task_type,
        "dataset_id": req.dataset_id,
        "dataset_name": dataset_name,
        "organization_id": current_user.get("organization_id"),
        "target_column": req.target_column,
        "time_limit_minutes": req.time_limit_minutes,
        "optimization_metric": metric,
        "allowed_models": allowed_models,
        "model_overrides": model_overrides,
        "status": "pending",
        "progress": 0,
        "current_model": None,
        "completed_models": 0,
        "total_models": min(len(allowed_models), 5),
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "finished_at": None,
        "best_model": None,
        "best_score": None,
        "best_ranking_score": None,
        "best_metrics": None,
        "best_params": None,
        "trials": [],
        "feature_importance": None,
    }

    _automl_tasks[task_id] = task

    return {
        "task_id": task_id,
        "name": req.name,
        "status": "pending",
        "allowed_models": allowed_models,
    }


@router.get("/tasks")
def list_automl_tasks(status: Optional[str] = None):
    """列出AutoML任务"""
    tasks = list(_automl_tasks.values())

    if status:
        tasks = [t for t in tasks if t["status"] == status]

    return {
        "total": len(tasks),
        "items": [
            {
                "id": t["id"],
                "name": t["name"],
                "task_type": t["task_type"],
                "status": t["status"],
                "best_score": t["best_score"],
                "best_model": t["best_model"],
                "created_at": t["created_at"],
            }
            for t in tasks
        ],
    }


@router.get("/tasks/{task_id}")
def get_automl_task(task_id: str):
    """获取AutoML任务详情"""
    task = _automl_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task


@router.post("/tasks/{task_id}/start")
def start_automl_task(task_id: str, background_tasks: BackgroundTasks):
    """启动AutoML任务"""
    task = _automl_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"任务状态错误: {task['status']}")

    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat()

    # 真实 AutoML 运行 — 每个候选模型都使用交叉验证，训练折与验证折始终分离
    def run_automl():
        import time
        import numpy as np
        from sklearn.model_selection import KFold, StratifiedKFold, cross_val_predict, cross_validate
        from sklearn.datasets import make_classification, make_regression
        from sklearn.metrics import (
            accuracy_score, f1_score, mean_absolute_error,
            mean_squared_error, r2_score,
        )

        from database.config import SessionLocal
        from services.data_loader import load_dataset_data

        models = task["allowed_models"]
        models_to_run = models[:5]
        task["total_models"] = len(models_to_run)
        task["completed_models"] = 0
        task["progress"] = 5
        task_type = task["task_type"]
        # 后台任务自带 DB 会话(不依赖请求级 db)
        db_session = SessionLocal()

        # ---- 加载数据:优先真实数据集 ----
        data_source = None
        feature_names: List[str] = []
        did_raw = task.get("dataset_id")
        target_col = task.get("target_column")
        loaded_real = False
        if did_raw:
            try:
                did = int(did_raw)
                X, y, feature_names = load_dataset_data(
                    db_session, did, target_col=target_col
                )
                data_source = f"dataset_{did}"
                loaded_real = True
                # 自动校正任务类型(若用户填了 regression 但 y 实际是分类)
                if task_type not in ("classification",) and _is_classification(y):
                    task_type = "classification"
                    task["task_type"] = task_type
                    # task_type 变更后，清理与新任务类型不兼容的超参覆盖
                    changed = dict(task.get("model_overrides") or {})
                    for algo in list(changed.keys()):
                        if not _builtin_algorithm_supports(algo, task_type):
                            changed.pop(algo)
                    task["model_overrides"] = changed
            except Exception as e:
                # 加载失败 → 回退合成,并记录原因
                data_source = (
                    f"synthetic(make_{task_type}, dataset {did_raw} load failed: {e})"
                )
        if not loaded_real:
            if task_type == "classification":
                X, y = make_classification(
                    n_samples=500,
                    n_features=10,
                    n_informative=6,
                    n_redundant=2,
                    random_state=42,
                )
            else:  # regression / time_series(用回归数据兜底)
                X, y = make_regression(
                    n_samples=500, n_features=10, noise=20.0, random_state=42
                )
            if data_source is None:
                data_source = (
                    f"synthetic(make_{task_type} n=500, random_state=42)"
                )

        optimization_metric = str(task.get("optimization_metric") or (
            "accuracy" if task_type == "classification" else "r2"
        )).lower()
        scoring_map = {
            "accuracy": "accuracy",
            "f1": "f1_weighted",
            "r2": "r2",
            "rmse": "neg_root_mean_squared_error",
            "mae": "neg_mean_absolute_error",
        }
        scoring = scoring_map[optimization_metric]
        cv = (
            StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
            if task_type == "classification"
            else KFold(n_splits=5, shuffle=True, random_state=42)
        )

        # 模型工厂:按实际安装情况注册 lightgbm/xgboost,缺失回退到 sklearn
        factory = _build_model_factory(task_type)
        overrides = task.get("model_overrides") or {}

        def _build(model_name):
            """Build only explicitly registered algorithms; never silently substitute another one."""
            if model_name not in factory:
                raise ValueError(f"AutoML 内置算法不存在或不支持当前任务：{model_name}")
            return factory[model_name](overrides.get(model_name) or {})

        feature_importance = {}

        for model_index, model in enumerate(models_to_run):
            task["current_model"] = model
            task["progress"] = 10 + int(80 * model_index / max(len(models_to_run), 1))
            trial_id = f"{task_id}_trial_{len(task['trials'])}"
            # 确定性的参数(取模型默认值,非随机)
            params = {
                "random_state": 42,
                "cv_folds": 5,
                "scoring": scoring,
            }
            score = None
            ranking_score = -1e308
            training_time = 0
            metrics = {}
            prediction_preview = []
            try:
                t0 = time.time()
                estimator = _build(model)
                cv_result = cross_validate(
                    estimator, X, y, cv=cv, scoring=scoring, return_estimator=True
                )
                scores = cv_result["test_score"]
                predictions = cross_val_predict(estimator, X, y, cv=cv)
                training_time = round(float(time.time() - t0), 3)
                if task_type == "classification":
                    metrics = {
                        "accuracy": round(float(accuracy_score(y, predictions)), 6),
                        "f1": round(float(f1_score(y, predictions, average="weighted", zero_division=0)), 6),
                    }
                else:
                    mse = float(mean_squared_error(y, predictions))
                    metrics = {
                        "r2": round(float(r2_score(y, predictions)), 6),
                        "rmse": round(float(np.sqrt(mse)), 6),
                        "mae": round(float(mean_absolute_error(y, predictions)), 6),
                    }
                score = float(metrics[optimization_metric])
                ranking_score = score if optimization_metric in {"r2", "accuracy", "f1"} else -score
                metrics.update({
                    "primary_metric": optimization_metric,
                    "primary_value": round(score, 6),
                    "cv_std": round(float(np.std(scores)), 6),
                    "cv_folds": 5,
                    "scoring": scoring,
                })
                for index in range(min(10, len(predictions))):
                    actual = y[index].item() if hasattr(y[index], "item") else y[index]
                    predicted = predictions[index].item() if hasattr(predictions[index], "item") else predictions[index]
                    preview = {
                        "index": int(index + 1),
                        "actual": actual,
                        "predicted": predicted,
                    }
                    if task_type != "classification":
                        preview["error"] = round(float(predicted) - float(actual), 6)
                    prediction_preview.append(preview)
                # 对各验证折对应的已训练模型取平均特征重要性，不再额外用
                # 训练+验证全量数据拟合一个没有验证过程的模型。
                fold_importances = [
                    np.asarray(fold_model.feature_importances_, dtype=float)
                    for fold_model in cv_result.get("estimator", [])
                    if hasattr(fold_model, "feature_importances_")
                ]
                if fold_importances:
                    mean_importance = np.mean(np.vstack(fold_importances), axis=0)
                    names = feature_names or [
                        f"feature_{i}" for i in range(len(mean_importance))
                    ]
                    for i, imp in enumerate(mean_importance):
                        key = names[i] if i < len(names) else f"feature_{i}"
                        feature_importance[key] = round(float(imp), 4)
            except Exception as e:
                score = 0.0
                metrics = {"error": str(e)}
                training_time = round(float(time.time() - t0), 3) if "t0" in locals() else 0.0

            trial = {
                "trial_id": trial_id,
                "model": model,
                "params": params,
                "estimator_params": overrides.get(model) or {},
                "score": score,
                "ranking_score": ranking_score,
                "training_time_seconds": training_time,
                "metrics": metrics,
                "prediction_preview": prediction_preview,
                "data_source": data_source,
            }

            task["trials"].append(trial)
            task["completed_models"] = model_index + 1
            task["progress"] = 10 + int(80 * (model_index + 1) / max(len(models_to_run), 1))

            # 更新最佳模型
            if task["best_ranking_score"] is None or ranking_score > task["best_ranking_score"]:
                task["best_score"] = score
                task["best_ranking_score"] = ranking_score
                task["best_model"] = model
                task["best_params"] = params
                task["best_metrics"] = metrics

        task["progress"] = 95
        task["current_model"] = None

        # AutoML在本平台用于算法选型。任务完成后持久化场景、候选算法、
        # 各算法指标和最佳算法，而不是制造一条没有真实权重的部署记录。
        try:
            record_results = [
                {
                    "algorithm": trial["model"],
                    "score": trial["score"],
                    "metrics": trial.get("metrics", {}),
                    "training_time_seconds": trial.get("training_time_seconds", 0),
                }
                for trial in task["trials"]
            ]
            db_session.execute(text("""
                INSERT INTO automl_selection_records (
                    task_id, name, dataset_id, dataset_name, target_column,
                    task_type, optimization_metric, candidate_algorithms,
                    results, best_algorithm, best_score, best_metrics,
                    organization_id, created_at, completed_at
                ) VALUES (
                    :task_id, :name, :dataset_id, :dataset_name, :target_column,
                    :task_type, :optimization_metric,
                    CAST(:candidate_algorithms AS JSONB), CAST(:results AS JSONB),
                    :best_algorithm, :best_score, CAST(:best_metrics AS JSONB),
                    :organization_id, :created_at, NOW()
                )
                ON CONFLICT (task_id) DO UPDATE SET
                    results = EXCLUDED.results,
                    best_algorithm = EXCLUDED.best_algorithm,
                    best_score = EXCLUDED.best_score,
                    best_metrics = EXCLUDED.best_metrics,
                    completed_at = NOW()
            """), {
                "task_id": task["id"],
                "name": task["name"],
                "dataset_id": int(task["dataset_id"]) if task.get("dataset_id") else None,
                "dataset_name": task.get("dataset_name"),
                "target_column": task["target_column"],
                "task_type": task["task_type"],
                "optimization_metric": task["optimization_metric"],
                "candidate_algorithms": json.dumps(task["allowed_models"][:5], ensure_ascii=False),
                "results": json.dumps(record_results, ensure_ascii=False),
                "best_algorithm": task["best_model"],
                "best_score": task["best_score"],
                "best_metrics": json.dumps(task.get("best_metrics") or {}, ensure_ascii=False),
                "organization_id": task.get("organization_id"),
                "created_at": task["created_at"],
            })
            db_session.commit()
            task["selection_record_saved"] = True
        except Exception as exc:
            db_session.rollback()
            task["selection_record_saved"] = False
            task["selection_record_error"] = str(exc)

        # 真实特征重要性(若任一模型提供);否则诚实标注
        if feature_importance:
            task["feature_importance"] = feature_importance
        else:
            task["feature_importance"] = None

        try:
            db_session.close()
        except Exception:
            pass
        task["status"] = "completed"
        task["progress"] = 100
        task["finished_at"] = datetime.now().isoformat()

    background_tasks.add_task(run_automl)

    return {"task_id": task_id, "status": "running", "message": "AutoML任务已启动"}


@router.get("/selection-records")
def list_selection_records(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    safe_limit = min(max(limit, 1), 200)
    where = ""
    params: Dict[str, Any] = {"limit": safe_limit}
    if not is_admin(current_user):
        where = "WHERE organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")
    rows = db.execute(text(f"""
        SELECT id, task_id, name, dataset_id, dataset_name, target_column,
               task_type, optimization_metric, candidate_algorithms, results,
               best_algorithm, best_score, best_metrics, created_at, completed_at
        FROM automl_selection_records
        {where}
        ORDER BY completed_at DESC NULLS LAST, id DESC
        LIMIT :limit
    """), params).fetchall()
    return {
        "total": len(rows),
        "items": [
            {
                "id": row[0], "task_id": row[1], "name": row[2],
                "dataset_id": row[3], "dataset_name": row[4],
                "target_column": row[5], "task_type": row[6],
                "optimization_metric": row[7],
                "candidate_algorithms": row[8] or [], "results": row[9] or [],
                "best_algorithm": row[10], "best_score": row[11],
                "best_metrics": row[12] or {},
                "created_at": row[13].isoformat() if row[13] else None,
                "completed_at": row[14].isoformat() if row[14] else None,
            }
            for row in rows
        ],
    }


@router.delete(
    "/selection-records/{record_id}",
    dependencies=[Depends(require_permission("training:write"))],
)
def delete_selection_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Remove one saved AutoML selection report, not any trained assets."""
    query = "DELETE FROM automl_selection_records WHERE id = :id"
    params: Dict[str, Any] = {"id": record_id}
    if not is_admin(current_user):
        query += " AND organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")
    deleted = db.execute(text(query + " RETURNING id"), params).scalar()
    if not deleted:
        raise HTTPException(status_code=404, detail="选型记录不存在或无权删除")
    db.commit()
    return {"success": True, "id": int(deleted), "message": "选型记录已删除"}


@router.get("/tasks/{task_id}/trials")
def get_task_trials(task_id: str):
    """获取任务的所有试验"""
    task = _automl_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "task_id": task_id,
        "total_trials": len(task["trials"]),
        "best_score": task["best_score"],
        "best_model": task["best_model"],
        "trials": task["trials"],
    }


@router.get("/tasks/{task_id}/best")
def get_best_model(task_id: str):
    """获取最佳模型"""
    task = _automl_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] != "completed":
        return {"status": task["status"], "message": "任务尚未完成"}

    return {
        "task_id": task_id,
        "best_model": task["best_model"],
        "best_score": task["best_score"],
        "best_metrics": task.get("best_metrics"),
        "optimization_metric": task.get("optimization_metric"),
        "best_params": task["best_params"],
        "feature_importance": task["feature_importance"],
    }


@router.get("/tasks/{task_id}/leaderboard")
def get_leaderboard(task_id: str):
    """获取模型排行榜"""
    task = _automl_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 按分数排序
    trials = sorted(task["trials"], key=lambda x: x.get("ranking_score", x["score"]), reverse=True)

    return {
        "task_id": task_id,
        "total_trials": len(trials),
        "leaderboard": [
            {
                "rank": i + 1,
                "model": t["model"],
                "score": t["score"],
                "metrics": t.get("metrics", {}),
                "params": t["params"],
                "training_time": t["training_time_seconds"],
            }
            for i, t in enumerate(trials)
        ],
    }


@router.get("/models")
def list_supported_models():
    """List the canonical built-ins shared with the algorithm store and sandbox."""
    from services.builtin_algorithm_registry import supported_models
    return supported_models()


# ============================================================
# 增强版 AutoML 路由（需要 optuna + lightgbm）
# ============================================================

try:
    import numpy as np
    from services.automl_service import get_enhanced_automl_service

    _enhanced_service = get_enhanced_automl_service()

    enhanced_router = APIRouter(
        prefix="/enhanced-automl", tags=["增强AutoML"]
    )

    class EnhancedAutoMLRequest(BaseModel):
        """增强AutoML请求"""

        name: str
        task_type: str  # classification, regression
        X: List[List[float]]
        y: List[Any]
        n_trials: int = 20
        time_limit_minutes: int = 10

    class EnhancedPredictRequest(BaseModel):
        """预测请求"""

        X: List[List[float]]

    @enhanced_router.post("/jobs")
    def create_automl_job(req: EnhancedAutoMLRequest):
        """创建并启动AutoML任务"""
        config = {
            "name": req.name,
            "task_type": req.task_type,
            "n_trials": req.n_trials,
            "time_limit_minutes": req.time_limit_minutes,
        }

        job_id = _enhanced_service.create_job(config)

        data = {"X": req.X, "y": req.y}
        _enhanced_service.start_training(job_id, data)

        return {
            "job_id": job_id,
            "status": "started",
            "message": "AutoML任务已启动，正在使用Optuna进行超参优化",
        }

    @enhanced_router.get("/jobs")
    def list_automl_jobs():
        """列出所有AutoML任务"""
        jobs = _enhanced_service.list_jobs()
        return {"total": len(jobs), "items": jobs}

    @enhanced_router.get("/jobs/{job_id}")
    def get_automl_job(job_id: str):
        """获取AutoML任务状态"""
        try:
            status = _enhanced_service.get_job_status(job_id)
            return status
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @enhanced_router.get("/jobs/{job_id}/leaderboard")
    def get_leaderboard(job_id: str):
        """获取模型排行榜"""
        try:
            status = _enhanced_service.get_job_status(job_id)
            trials = status.get("trials", [])

            # 按分数排序
            sorted_trials = sorted(
                trials, key=lambda x: x.get("score", 0), reverse=True
            )

            leaderboard = [
                {
                    "rank": i + 1,
                    "model_type": t.get("model_type"),
                    "score": t.get("score"),
                    "params": t.get("params"),
                }
                for i, t in enumerate(sorted_trials)
            ]

            return {
                "job_id": job_id,
                "total_trials": len(trials),
                "leaderboard": leaderboard,
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @enhanced_router.post("/jobs/{job_id}/predict")
    def predict(job_id: str, req: EnhancedPredictRequest):
        """使用最佳模型进行预测"""
        try:
            X = np.array(req.X)
            predictions = _enhanced_service.predict(job_id, X)
            return {"job_id": job_id, "predictions": predictions.tolist()}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @enhanced_router.get("/health")
    def health_check():
        """健康检查"""
        return {
            "status": "healthy",
            "service": "enhanced-automl",
            "features": [
                "optuna",
                "auto_feature_engineering",
                "model_selection",
                "hyperparameter_optimization",
            ],
        }

except ImportError:
    enhanced_router = None
