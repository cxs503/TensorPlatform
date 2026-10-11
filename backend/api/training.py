"""
训练任务 API — 数据库持久化 + Celery异步训练
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from database.config import get_db
from models.core import TrainingJob, TrainingStatus, Organization

router = APIRouter(prefix="/training", tags=["训练任务"])


@router.post("")
def create_training(req: dict, db: Session = Depends(get_db)):
    """创建训练任务并分发到Celery异步执行"""
    name = req.get("name", "训练任务")
    model_id = req.get("model_id")
    dataset_id = req.get("dataset_id")
    hyperparameters = req.get("hyperparameters", {})
    scenario = req.get("scenario", hyperparameters.get("scenario"))

    job = TrainingJob(
        name=name,
        model_id=model_id,
        dataset_id=dataset_id,
        status=TrainingStatus.PENDING,
        hyperparameters=hyperparameters,
        organization_id=req.get("organization_id"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # 分发到 Celery 异步执行
    try:
        from tasks.training_tasks import train_model

        model_type = hyperparameters.get("model_type", "random_forest")
        task_type = req.get("task_type") or hyperparameters.get("task_type", "regression")

        config = {
            "name": name,
            "task_type": task_type,
            "model_type": model_type,
            "scenario": scenario,
            "epochs": hyperparameters.get("epochs", 30),
            "batch_size": hyperparameters.get("batch_size", 32),
            "learning_rate": hyperparameters.get("learning_rate", 0.001),
            "hidden_layers": hyperparameters.get("hidden_layers", [128, 64]),
            "dropout": hyperparameters.get("dropout", 0.2),
            "n_estimators": hyperparameters.get("n_estimators", 100),
            "max_depth": hyperparameters.get("max_depth", 10),
            "normalize": hyperparameters.get("normalize", True),
        }

        # 使用 delay() 或 apply_async() 异步分发到Celery
        async_result = train_model.delay(model_id, dataset_id, config)
        task_id = async_result.id

        # 保存Celery task ID到DB，方便前端轮询状态
        job.training_job_id = task_id
        job.status = TrainingStatus.RUNNING
        job.started_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Celery不可用时：仍标记为running，前端可通过轮询感知
        job.status = TrainingStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.error_message = f"Celery dispatch failed: {e}"
        db.commit()

    return job.to_dict()


@router.get("")
def list_training(organization_id: int = None, workspace: str = None, db: Session = Depends(get_db)):
    """列出训练任务"""
    q = db.query(TrainingJob)
    if organization_id:
        q = q.filter(TrainingJob.organization_id == organization_id)
    if workspace:
        q = q.join(Organization, TrainingJob.organization_id == Organization.id).filter(Organization.workspace == workspace)
    jobs = q.order_by(TrainingJob.id.desc()).all()
    return {"total": len(jobs), "items": [j.to_dict() for j in jobs]}


@router.get("/{job_id}")
def get_training(job_id: int, db: Session = Depends(get_db)):
    """获取训练任务详情（包含Celery异步任务状态）"""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="训练任务不存在")

    result = job.to_dict()

    # 如果有Celery task ID，尝试获取最新状态
    if job.training_job_id and job.status in [TrainingStatus.RUNNING, TrainingStatus.PENDING]:
        try:
            from tasks.training_tasks import train_model
            task_result = train_model.AsyncResult(job.training_job_id)
            if task_result.ready():
                celery_result = task_result.result
                if celery_result and celery_result.get("status") == "completed":
                    job.status = TrainingStatus.COMPLETED
                    job.metrics = celery_result.get("metrics", {})
                    job.completed_at = datetime.utcnow()
                    db.commit()
                    result.update(job.to_dict())
                elif celery_result and celery_result.get("status") == "failed":
                    job.status = TrainingStatus.FAILED
                    job.error_message = celery_result.get("error", "Unknown error")
                    job.completed_at = datetime.utcnow()
                    db.commit()
                    result.update(job.to_dict())
        except Exception:
            pass  # Celery不可用，返回当前DB状态

    return result


@router.put("/{job_id}/status")
def update_status(job_id: int, req: dict, db: Session = Depends(get_db)):
    """更新训练状态"""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="训练任务不存在")

    new_status = req.get("status")
    if new_status:
        try:
            job.status = TrainingStatus(new_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态: {new_status}")

    if req.get("metrics"):
        job.metrics = req["metrics"]

    if new_status == "COMPLETED":
        job.completed_at = datetime.utcnow()
    elif new_status == "RUNNING":
        job.started_at = datetime.utcnow()

    db.commit()
    return job.to_dict()
