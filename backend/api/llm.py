"""
大模型训练和模型服务化API
"""
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.llm_training_service import get_llm_training_service
from services.model_serving_service import get_model_serving_service

router = APIRouter(prefix="/llm", tags=["大模型"])

llm_service = get_llm_training_service()
serving_service = get_model_serving_service()


# ==================== 大模型训练 ====================


class LLMTrainingConfig(BaseModel):
    name: str
    base_model: str = "distilgpt2"
    task_type: str = "causal_lm"
    epochs: int = 3
    batch_size: int = 8
    learning_rate: float = 5e-5
    max_length: int = 512
    use_lora: bool = True
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    quantization: Optional[str] = None


class LLMTrainingData(BaseModel):
    texts: List[str]
    labels: Optional[List[Any]] = None


class GenerateRequest(BaseModel):
    prompt: str
    max_new_tokens: int = 100
    temperature: float = 0.7
    top_p: float = 0.9


@router.get("/models")
def list_supported_models():
    """列出支持的大模型"""
    models = llm_service.list_supported_models()
    return {"total": len(models), "models": models}


@router.post("/training/jobs")
def create_llm_job(config: LLMTrainingConfig):
    """创建大模型训练任务"""
    job_id = llm_service.create_job(config.dict())

    return {"job_id": job_id, "status": "created", "message": "任务创建成功"}


@router.post("/training/jobs/{job_id}/start")
def start_llm_training(job_id: str, data: LLMTrainingData):
    """启动大模型训练"""
    try:
        result = llm_service.start_training(job_id, data.dict())
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/training/jobs")
def list_llm_jobs(status: str = None):
    """列出所有大模型训练任务"""
    jobs = llm_service.list_jobs(status)
    return {"total": len(jobs), "items": jobs}


@router.get("/training/jobs/{job_id}")
def get_llm_job(job_id: str):
    """获取大模型训练任务状态"""
    try:
        return llm_service.get_job_status(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/training/jobs/{job_id}/generate")
def generate_text(job_id: str, req: GenerateRequest):
    """使用训练好的模型生成文本"""
    try:
        result = llm_service.generate_text(
            job_id, req.prompt, req.max_new_tokens, req.temperature, req.top_p
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/training/estimate")
def estimate_training(model_id: str, dataset_size: int, epochs: int = 3):
    """估算训练时间"""
    result = llm_service.estimate_training_time(model_id, dataset_size, epochs)
    return result


# ==================== 模型服务化 ====================


class EndpointConfig(BaseModel):
    name: str
    model_path: str
    model_type: str = "sklearn"
    max_batch_size: int = 32
    max_concurrent: int = 10
    timeout_ms: int = 5000
    enable_cache: bool = True
    cache_ttl_seconds: int = 3600


class PredictRequest(BaseModel):
    X: List[List[float]]
    use_cache: bool = True


class BatchPredictRequest(BaseModel):
    batch_X: List[List[List[float]]]


@router.post("/serving/endpoints")
def create_endpoint(config: EndpointConfig):
    """创建服务端点"""
    endpoint_id = serving_service.create_endpoint(config.dict())

    return {
        "endpoint_id": endpoint_id,
        "status": "created",
        "message": "端点创建成功，请调用load接口加载模型",
    }


@router.post("/serving/endpoints/{endpoint_id}/load")
def load_model(endpoint_id: str):
    """加载模型"""
    try:
        result = serving_service.load_model(endpoint_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/serving/endpoints/{endpoint_id}/predict")
def predict(endpoint_id: str, req: PredictRequest):
    """推理预测"""
    try:
        result = serving_service.predict(endpoint_id, req.X, req.use_cache)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/serving/endpoints/{endpoint_id}/batch-predict")
def batch_predict(endpoint_id: str, req: BatchPredictRequest):
    """批量预测"""
    try:
        result = serving_service.batch_predict(endpoint_id, req.batch_X)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/serving/endpoints/{endpoint_id}/stop")
def stop_endpoint(endpoint_id: str):
    """停止服务端点"""
    try:
        result = serving_service.stop_endpoint(endpoint_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/serving/endpoints")
def list_endpoints(status: str = None):
    """列出所有服务端点"""
    endpoints = serving_service.list_endpoints(status)
    return {"total": len(endpoints), "items": endpoints}


@router.get("/serving/endpoints/{endpoint_id}")
def get_endpoint(endpoint_id: str):
    """获取端点详情"""
    try:
        return serving_service.get_endpoint(endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/serving/stats")
def get_serving_stats(endpoint_id: str = None):
    """获取服务统计"""
    try:
        return serving_service.get_stats(endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/serving/cache/clear")
def clear_cache(endpoint_id: str = None):
    """清除缓存"""
    from services.model_serving_service import ModelCache

    ModelCache.clear(endpoint_id)
    return {"message": "缓存已清除"}


# ==================== 健康检查 ====================


@router.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "services": {"llm_training": "available", "model_serving": "available"},
        "features": [
            "small_llm_training",
            "lora_finetuning",
            "text_generation",
            "model_inference",
            "batch_prediction",
            "model_caching",
        ],
    }
