import os
"""
训练任务 - 异步执行（真实训练版本）
"""
import json
import time
from datetime import datetime

import numpy as np

from tasks.celery_app import celery_app


@celery_app.task(name="train_model")
def train_model(model_id: int, dataset_id: int, config: dict, job_id: str = None):
    """
    异步模型训练任务 — 使用真实训练服务

    config 参数:
      - model_type: random_forest, mlp
      - task_type: classification, regression
      - epochs, batch_size, learning_rate, n_estimators, max_depth, etc.
    """
    task_id = train_model.request.id

    model_type = config.get("model_type", "random_forest")
    task_type = config.get("task_type", "regression")
    scenario = config.get("scenario", "dust_optimization")

    result = {
        "task_id": task_id,
        "model_id": model_id,
        "dataset_id": dataset_id,
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
    }

    try:
        # 清除该 model 的所有旧 run JSON，避免重复训练残留
        from pathlib import Path as _Path
        import json as _json
        runs_dir = _Path("/app/uploads/runs")
        for old_run in runs_dir.glob("train_*.json"):
            try:
                d = _json.loads(old_run.read_text())
                if (d.get("config", {}) or {}).get("model_id") == model_id:
                    old_run.unlink()
            except: pass

        # 加载数据
        from services.data_loader import get_training_data
        from database.config import get_db

        db = next(get_db())
        try:
            X, y, feature_names = get_training_data(
                db, dataset_id=dataset_id, scenario=scenario,
                target_col=config.get("target_col"),
                feature_cols=config.get("feature_cols"),
            )
        finally:
            db.close()

        # 启动训练
        from services.real_training_service import (
            MAX_TRAINING_SECONDS, get_training_service, TrainingJob as _TJ, _training_jobs as _tj,
        )

        train_service = get_training_service()
        if config.get("source_execution_mode") == "execute_user_script":
            # 流水线上传算法已完成源码契约解析。绝不能在异步 worker
            # 中重新套用内置随机森林模板，否则算法 ID、目标/特征选择和
            # 用户源码执行标记都会丢失，最终变成一次假训练。
            job_cfg = dict(config)
            job_cfg.update({
                "name": config.get("name", f"异步训练-{model_id}"),
                "model_id": model_id,
                "dataset_id": dataset_id,
                "organization_id": config.get("organization_id"),
                "validation_context": config.get("validation_context") or {},
                "validation_run_id": config.get("validation_run_id"),
            })
        else:
            job_cfg = {
                "name": config.get("name", f"异步训练-{model_id}"),
                "task_type": task_type,
                "model_type": model_type,
                "model_id": model_id,
                "dataset_id": dataset_id,
                "target_col": config.get("target_col"),
                "feature_cols": config.get("feature_cols"),
                "feature_names": feature_names,
                "epochs": config.get("epochs", 50),
                "batch_size": config.get("batch_size", 32),
                "learning_rate": config.get("learning_rate", 0.001),
                "hidden_layers": config.get("hidden_layers", [128, 64]),
                "dropout": config.get("dropout", 0.2),
                "n_estimators": config.get("n_estimators", 100),
                "max_depth": config.get("max_depth", 6),
                "normalize": config.get("normalize", True),
                "organization_id": config.get("organization_id"),
                "validation_context": config.get("validation_context") or {},
                "validation_run_id": config.get("validation_run_id"),
            }
        # 复用 /train 生成的 job_id,保证 API 返回的 id 与实际训练/落盘一致
        if not job_id:
            job_id = train_service.create_job(job_cfg)
        elif job_id not in _tj:
            _tj[job_id] = _TJ(job_id, job_cfg)
        train_service.start_training(job_id, {
            "X": X.tolist(), "y": y.tolist(),
        })

        # 等待训练完成（同步等待，最多5分钟）
        max_wait = MAX_TRAINING_SECONDS
        while max_wait > 0:
            status = train_service.get_job_status(job_id)
            if status["status"] in ["completed", "failed"]:
                result["status"] = status["status"]
                result["metrics"] = status.get("best_metrics", {})
                result["model_path"] = status.get("model_path")
                result["job_id"] = job_id
                break
            time.sleep(2)
            max_wait -= 2

        if max_wait <= 0:
            result["status"] = "timeout"
            result["error"] = "Training did not complete within 5 minutes"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        import traceback
        traceback.print_exc()

    # 更新 API 的 training_jobs 状态（Celery task UUID 对应的记录）
    try:
        from database.config import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text(
            "UPDATE training_jobs SET status = :st, metrics = :mt, completed_at = NOW() WHERE training_job_id = :tid"
        ), {"st": result["status"].upper(), "mt": json.dumps(result.get("metrics", {})), "tid": task_id})
        db.commit()
        db.close()
    except Exception:
        pass

    result["finished_at"] = datetime.utcnow().isoformat()
    return result
    # 修复模型文件权限 (Celery worker 和 uvicorn 用户不一致)
    if result.get("model_path"):
        try:
            model_path = result["model_path"]
            os.chmod(model_path, 0o666)
            model_dir = os.path.dirname(model_path)
            for f in os.listdir(model_dir):
                os.chmod(os.path.join(model_dir, f), 0o666)
        except Exception:
            pass



@celery_app.task(name="process_dataset")
def process_dataset(dataset_id: int, operation: str):
    """
    异步数据处理任务
    """
    task_id = process_dataset.request.id

    from database.config import get_db
    from services.data_loader import get_training_data

    db = next(get_db())
    try:
        X, y, feature_names = get_training_data(
            db, dataset_id=dataset_id, n_samples=5000,
        )
        return {
            "task_id": task_id,
            "dataset_id": dataset_id,
            "operation": operation,
            "status": "completed",
            "rows": len(X),
            "features": len(feature_names),
            "feature_names": feature_names,
        }
    except Exception as e:
        return {
            "task_id": task_id,
            "dataset_id": dataset_id,
            "operation": operation,
            "status": "failed",
            "error": str(e),
        }
    finally:
        db.close()


@celery_app.task(name="deploy_model")
def deploy_model(model_id: int, deployment_type: str = "online"):
    """
    异步模型部署任务
    """
    task_id = deploy_model.request.id

    result = {
        "task_id": task_id,
        "model_id": model_id,
        "deployment_type": deployment_type,
        "status": "deploying",
    }

    try:
        # 查找最新训练完成的模型
        from pathlib import Path
        from services.real_training_service import _training_jobs, _model_registry

        # 找最近完成的训练
        completed_jobs = [
            (jid, job) for jid, job in _training_jobs.items()
            if hasattr(job, 'status') and job.status == "completed"
        ]
        if not completed_jobs:
            result["status"] = "failed"
            result["error"] = "No completed training jobs found"
            return result

        # 选最新的
        latest_job_id, latest_job = completed_jobs[-1]

        result["status"] = "deployed"
        result["job_id"] = latest_job_id
        result["endpoint"] = f"/api/v1/real-training/jobs/{latest_job_id}/predict"
        result["model_path"] = str(latest_job.model_path) if latest_job.model_path else None

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result
