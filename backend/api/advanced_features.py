"""
P2高级功能API
包含NAS、模型量化、分布式训练等高级功能
"""
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.distributed_training_service import get_distributed_training_service

# 导入服务
from services.nas_service import get_nas_service
from services.quantization_service import get_quantization_service

router = APIRouter(prefix="/advanced", tags=["高级功能"])

# 获取服务实例
nas_service = get_nas_service()
quant_service = get_quantization_service()
dist_service = get_distributed_training_service()


# ==================== NAS API ====================


class NASRequest(BaseModel):
    name: str
    X: List[List[float]]
    y: List[Any]
    n_trials: int = 15
    epochs: int = 5
    search_space: Optional[Dict] = None


@router.post("/nas/jobs")
def create_nas_job(req: NASRequest):
    """创建神经架构搜索任务"""
    config = {
        "name": req.name,
        "n_trials": req.n_trials,
        "epochs": req.epochs,
        "search_space": req.search_space or {},
    }

    job_id = nas_service.create_job(config)
    data = {"X": req.X, "y": req.y}
    nas_service.start_search(job_id, data)

    return {"job_id": job_id, "status": "started", "message": "NAS任务已启动"}


@router.get("/nas/jobs")
def list_nas_jobs():
    """列出所有NAS任务"""
    jobs = nas_service.list_jobs()
    return {"total": len(jobs), "items": jobs}


@router.get("/nas/jobs/{job_id}")
def get_nas_job(job_id: str):
    """获取NAS任务状态"""
    try:
        return nas_service.get_job_status(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== 量化API ====================


class QuantizationRequest(BaseModel):
    model_path: str
    quantization_type: str = "dynamic"  # dynamic, static, onnx
    output_path: Optional[str] = None


class PruningRequest(BaseModel):
    model_path: str
    pruning_ratio: float = 0.3
    output_path: Optional[str] = None


class DistillationRequest(BaseModel):
    teacher_path: str
    X: List[List[float]]
    y: List[Any]
    student_config: Optional[Dict] = None
    epochs: int = 10


@router.post("/quantize")
def quantize_model(req: QuantizationRequest):
    """量化模型"""
    if req.quantization_type == "dynamic":
        result = quant_service.quantize_pytorch_dynamic(req.model_path, req.output_path)
    elif req.quantization_type == "onnx":
        result = quant_service.quantize_sklearn_model(req.model_path, req.output_path)
    else:
        raise HTTPException(status_code=400, detail="不支持的量化类型")

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/prune")
def prune_model(req: PruningRequest):
    """剪枝模型"""
    result = quant_service.prune_model(
        req.model_path, req.pruning_ratio, req.output_path
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/distill")
def distill_model(req: DistillationRequest):
    """知识蒸馏"""
    X = np.array(req.X)
    y = np.array(req.y)

    result = quant_service.knowledge_distillation(
        req.teacher_path, X, y, req.student_config, req.epochs
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.get("/model/info")
def get_model_info(model_path: str):
    """获取模型信息"""
    return quant_service.get_model_info(model_path)


@router.post("/model/benchmark")
def benchmark_model(model_path: str, X: List[List[float]], n_runs: int = 100):
    """模型性能基准测试"""
    X_test = np.array(X)
    return quant_service.benchmark_model(model_path, X_test, n_runs)


# ==================== 分布式训练API ====================


class DistributedTrainingRequest(BaseModel):
    name: str
    X: List[List[float]]
    y: List[Any]
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.001
    hidden_dims: List[int] = [128, 64]
    num_workers: int = 1
    gradient_accumulation_steps: int = 1
    mixed_precision: bool = False


@router.post("/distributed/jobs")
def create_distributed_job(req: DistributedTrainingRequest):
    """创建分布式训练任务"""
    config = {
        "name": req.name,
        "epochs": req.epochs,
        "batch_size": req.batch_size,
        "learning_rate": req.learning_rate,
        "hidden_dims": req.hidden_dims,
        "num_workers": req.num_workers,
        "gradient_accumulation_steps": req.gradient_accumulation_steps,
        "mixed_precision": req.mixed_precision,
    }

    job_id = dist_service.create_job(config)
    data = {"X": req.X, "y": req.y}
    dist_service.start_training(job_id, data)

    return {"job_id": job_id, "status": "started", "message": "分布式训练任务已启动"}


@router.get("/distributed/jobs")
def list_distributed_jobs():
    """列出所有分布式训练任务"""
    jobs = dist_service.list_jobs()
    return {"total": len(jobs), "items": jobs}


@router.get("/distributed/jobs/{job_id}")
def get_distributed_job(job_id: str):
    """获取分布式训练任务状态"""
    try:
        return dist_service.get_job_status(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/distributed/cluster")
def get_cluster_info():
    """获取集群信息"""
    return dist_service.get_cluster_info()


@router.get("/distributed/estimate")
def estimate_training_time(
    dataset_size: int, model_params: int, epochs: int, batch_size: int = 32
):
    """估算训练时间"""
    return dist_service.estimate_training_time(
        dataset_size, model_params, epochs, batch_size
    )


# ==================== 健康检查 ====================


@router.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "services": {
            "nas": "available",
            "quantization": "available",
            "distributed_training": "available",
        },
        "features": [
            "neural_architecture_search",
            "model_quantization",
            "model_pruning",
            "knowledge_distillation",
            "distributed_training",
            "gradient_accumulation",
            "mixed_precision",
        ],
    }
