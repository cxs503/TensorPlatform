"""
分布式训练API - 基于Ray
POST /api/v1/distributed/train - 启动分布式训练
GET  /api/v1/distributed/jobs - 列出分布式任务
GET  /api/v1/distributed/jobs/{job_id} - 获取任务状态
"""
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.config import get_db
from services.ray_trainer import (
    RayDistributedTrainer,
    DistributedJobManager,
    RAY_AVAILABLE,
    TORCH_AVAILABLE,
    LIGHTGBM_AVAILABLE,
    _ensure_ray_initialized,
)
from services.data_loader import get_training_data

router = APIRouter(prefix="/distributed", tags=["分布式训练"])


class DistributedTrainRequest(BaseModel):
    """分布式训练请求"""
    scenario: Optional[str] = None       # 业务场景
    dataset_id: Optional[int] = None     # 数据集ID
    model_type: str = "random_forest"    # random_forest, lightgbm, mlp
    task_type: str = "regression"        # regression, classification
    n_samples: int = 1000               # 合成数据样本量
    n_workers: int = 2                  # worker数量
    n_estimators: int = 100             # 树模型数量
    max_depth: int = 6                   # 最大深度
    epochs: int = 10                     # 训练轮数
    batch_size: int = 32                # 批次大小
    learning_rate: float = 0.001        # 学习率
    hidden_layers: List[int] = [128, 64]  # 隐藏层
    dropout: float = 0.2                # dropout
    normalize: bool = True              # 是否标准化
    use_gpu: bool = False               # 是否使用GPU


@router.post("/train")
def train_distributed(req: DistributedTrainRequest, db: Session = Depends(get_db)):
    """
    启动分布式训练任务

    示例:
      POST /api/v1/distributed/train
      {"scenario": "dust_optimization", "model_type": "random_forest",
       "n_samples": 1000, "n_workers": 2}
    """
    config = {
        "scenario": req.scenario,
        "dataset_id": req.dataset_id,
        "model_type": req.model_type,
        "task_type": req.task_type,
        "n_workers": req.n_workers,
        "n_estimators": req.n_estimators,
        "max_depth": req.max_depth,
        "epochs": req.epochs,
        "batch_size": req.batch_size,
        "learning_rate": req.learning_rate,
        "hidden_layers": req.hidden_layers,
        "dropout": req.dropout,
        "normalize": req.normalize,
        "use_gpu": req.use_gpu,
    }

    # 加载数据
    try:
        X, y, feature_names = get_training_data(
            db, dataset_id=req.dataset_id, scenario=req.scenario,
            n_samples=req.n_samples,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.task_type == "classification":
        y = y.astype(int)

    # 创建并启动分布式训练任务
    job_id = DistributedJobManager.create_job(config)
    DistributedJobManager.start_job(job_id, {"X": X.tolist(), "y": y.tolist()})

    return {
        "job_id": job_id,
        "status": "started",
        "message": "分布式训练任务已启动",
        "config": {
            "scenario": req.scenario,
            "model_type": req.model_type,
            "n_workers": req.n_workers,
            "n_samples": len(X),
            "features": len(feature_names),
            "feature_names": feature_names,
        },
    }


@router.get("/jobs")
def list_distributed_jobs():
    """列出所有分布式训练任务"""
    jobs = DistributedJobManager.list_jobs()
    return {"total": len(jobs), "items": jobs}


@router.get("/jobs/{job_id}")
def get_distributed_job(job_id: str):
    """获取分布式训练任务状态"""
    try:
        job = DistributedJobManager.get_job(job_id)
        return job
    except ValueError:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")


@router.get("/status")
def get_ray_status():
    """获取Ray集群状态"""
    cluster_info = RayDistributedTrainer.get_cluster_info()
    return {
        "ray_available": RAY_AVAILABLE,
        "torch_available": TORCH_AVAILABLE,
        "lightgbm_available": LIGHTGBM_AVAILABLE,
        "cluster": cluster_info,
    }
