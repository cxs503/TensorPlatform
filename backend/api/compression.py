"""
模型量化/压缩API
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.compression_service import get_compression_service

router = APIRouter(prefix="/compression", tags=["模型压缩"])


class QuantizeReq(BaseModel):
    model_id: str
    model_path: str
    method: str = "dynamic"


class PruneReq(BaseModel):
    model_id: str
    model_path: str
    amount: float = 0.5


class BenchmarkReq(BaseModel):
    model_path: str
    input_shape: Optional[List[int]] = [1, 3, 224, 224]


@router.post("/quantize")
def quantize_model(req: QuantizeReq):
    """量化模型"""
    service = get_compression_service()
    job_id = service.quantize(req.model_id, req.model_path, req.method)
    return {"job_id": job_id, "message": "Quantization started"}


@router.post("/prune")
def prune_model(req: PruneReq):
    """剪枝模型"""
    service = get_compression_service()
    job_id = service.prune(req.model_id, req.model_path, req.amount)
    return {"job_id": job_id, "message": "Pruning started"}


@router.post("/benchmark")
def benchmark_model(req: BenchmarkReq):
    """基准测试"""
    service = get_compression_service()
    result = service.benchmark(req.model_path, req.input_shape)
    return result


@router.get("/job/{job_id}")
def get_job(job_id: str):
    """获取压缩任务状态"""
    service = get_compression_service()
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/methods")
def get_compression_methods():
    """获取可用的压缩方法"""
    return {
        "methods": [
            {
                "id": "dynamic",
                "name": "动态量化",
                "description": "训练后动态量化，无需重新训练",
                "compression_ratio": "2-4x",
            },
            {
                "id": "static",
                "name": "静态量化",
                "description": "训练后静态量化，需要校准数据",
                "compression_ratio": "3-4x",
            },
            {
                "id": "qat",
                "name": "量化感知训练",
                "description": "训练时量化，精度更高",
                "compression_ratio": "4x",
            },
        ],
        "pruning": [
            {"id": "magnitude", "name": "幅度剪枝", "description": "剪枝权重较小的连接"},
            {"id": " Lottery", "name": "彩票假说剪枝", "description": "寻找最优子网络"},
        ],
    }
