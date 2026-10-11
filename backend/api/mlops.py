"""
MLOps闭环API
实现数据-训练-部署-监控的完整闭环
"""
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.mlops_service import (
    DataVersionControl,
    ModelDeployment,
    ModelRegistry,
    MonitoringService,
    get_mlops_service,
)

router = APIRouter(prefix="/mlops", tags=["MLOps闭环"])

mlops_service = get_mlops_service()


# ==================== 流水线管理 ====================


class PipelineConfig(BaseModel):
    name: str
    model_type: str = "lightgbm"
    task_type: str = "classification"
    auto_deploy: bool = False
    dataset_id: Optional[str] = None
    features: int = 10
    samples: int = 1000


class RunPipelineRequest(BaseModel):
    dataset_id: Optional[str] = None
    features: int = 10
    samples: int = 1000


@router.post("/pipelines")
def create_pipeline(config: PipelineConfig):
    """创建MLOps流水线"""
    pipeline_id = mlops_service.create_pipeline(config.dict())

    return {"pipeline_id": pipeline_id, "status": "created", "message": "流水线创建成功"}


@router.post("/pipelines/{pipeline_id}/run")
def run_pipeline(pipeline_id: str, req: RunPipelineRequest = None):
    """运行流水线"""
    data_config = req.dict() if req else {}

    try:
        result = mlops_service.run_pipeline(pipeline_id, data_config)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pipelines")
def list_pipelines(status: str = None):
    """列出所有流水线"""
    pipelines = mlops_service.list_pipelines(status)
    return {"total": len(pipelines), "items": pipelines}


@router.get("/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: str):
    """获取流水线详情"""
    pipeline = mlops_service.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return pipeline


# ==================== 模型注册 ====================


class ModelRegistrationRequest(BaseModel):
    model_path: str
    model_name: str
    version: Optional[str] = None
    metrics: Optional[Dict] = None
    data_version_id: Optional[str] = None
    training_config: Optional[Dict] = None


@router.post("/models/register")
def register_model(req: ModelRegistrationRequest):
    """注册模型"""
    registration = ModelRegistry.register_model(
        model_path=req.model_path,
        model_name=req.model_name,
        version=req.version,
        metrics=req.metrics,
        data_version_id=req.data_version_id,
        training_config=req.training_config,
    )

    return registration


@router.get("/models")
def list_models(name: str = None, status: str = None):
    """列出所有模型"""
    models = ModelRegistry.list_models(name, status)
    return {"total": len(models), "items": models}


@router.get("/models/{model_id}")
def get_model(model_id: str):
    """获取模型详情"""
    model = ModelRegistry.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.post("/models/{model_id}/tags")
def add_model_tag(model_id: str, tag: str):
    """添加模型标签"""
    model = ModelRegistry.add_model_tag(model_id, tag)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.get("/models/{model_id}/lineage")
def get_model_lineage(model_id: str):
    """获取模型血缘"""
    lineage = mlops_service.get_lineage(model_id)
    if "error" in lineage:
        raise HTTPException(status_code=404, detail=lineage["error"])
    return lineage


# ==================== 模型部署 ====================


class DeploymentRequest(BaseModel):
    model_id: str
    deployment_name: str
    deployment_config: Optional[Dict] = None


class PredictRequest(BaseModel):
    X: List[List[float]]


@router.post("/deployments")
def create_deployment(req: DeploymentRequest):
    """创建部署"""
    try:
        deployment = ModelDeployment.deploy_model(
            model_id=req.model_id,
            deployment_name=req.deployment_name,
            deployment_config=req.deployment_config,
        )
        return deployment
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/deployments")
def list_deployments(status: str = None):
    """列出所有部署"""
    deployments = ModelDeployment.list_deployments(status)
    return {"total": len(deployments), "items": deployments}


@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: str):
    """获取部署详情"""
    deployment = ModelDeployment.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="部署不存在")
    return deployment


@router.post("/deployments/{deployment_id}/predict")
def predict(deployment_id: str, req: PredictRequest):
    """推理预测"""
    try:
        result = ModelDeployment.predict(deployment_id, req.X)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 监控 ====================


@router.get("/deployments/{deployment_id}/metrics")
def get_deployment_metrics(deployment_id: str, hours: int = 24):
    """获取部署监控指标"""
    try:
        metrics = MonitoringService.get_metrics(deployment_id, hours)
        return metrics
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class DriftDetectionRequest(BaseModel):
    baseline_metrics: Dict
    current_metrics: Dict


@router.post("/deployments/{deployment_id}/drift")
def detect_drift(deployment_id: str, req: DriftDetectionRequest):
    """检测模型漂移"""
    result = MonitoringService.detect_drift(
        deployment_id, req.baseline_metrics, req.current_metrics
    )
    return result


# ==================== 数据版本 ====================


class DataVersionRequest(BaseModel):
    dataset_id: str
    data_path: str
    metadata: Optional[Dict] = None


@router.post("/data/versions")
def create_data_version(req: DataVersionRequest):
    """创建数据版本"""
    version = DataVersionControl.create_data_version(
        dataset_id=req.dataset_id, data_path=req.data_path, metadata=req.metadata
    )
    return version


@router.get("/data/versions")
def list_data_versions(dataset_id: str = None):
    """列出数据版本"""
    versions = DataVersionControl.list_data_versions(dataset_id)
    return {"total": len(versions), "items": versions}


# ==================== 数据-训练关联 ====================


class DataToTrainingRequest(BaseModel):
    dataset_id: str
    target_column: str
    feature_columns: List[str]
    model_type: str = "lightgbm"
    task_type: str = "classification"


@router.post("/data-to-training")
def link_data_to_training(req: DataToTrainingRequest):
    """将数据关联到训练任务"""
    # 创建数据版本
    import os

    dataset_path = f"/app/uploads/datasets/{req.dataset_id}.csv"

    if os.path.exists(dataset_path):
        data_version = DataVersionControl.create_data_version(
            dataset_id=req.dataset_id,
            data_path=dataset_path,
            metadata={
                "target_column": req.target_column,
                "feature_columns": req.feature_columns,
            },
        )
    else:
        data_version = {"version_id": "simulated", "dataset_id": req.dataset_id}

    # 创建训练流水线
    pipeline_config = {
        "name": f"训练-{req.dataset_id}",
        "model_type": req.model_type,
        "task_type": req.task_type,
        "dataset_id": req.dataset_id,
        "data_version_id": data_version.get("version_id"),
        "features": len(req.feature_columns),
        "auto_deploy": False,
    }

    pipeline_id = mlops_service.create_pipeline(pipeline_config)

    return {
        "pipeline_id": pipeline_id,
        "data_version": data_version,
        "config": pipeline_config,
        "message": "数据已关联到训练流水线",
    }


# ==================== 健康检查 ====================


@router.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "mlops",
        "features": [
            "pipeline_management",
            "model_registry",
            "model_deployment",
            "model_monitoring",
            "data_versioning",
            "data_to_training_linkage",
            "model_lineage",
        ],
        "stages": [
            "data_preparation",
            "feature_engineering",
            "model_training",
            "model_evaluation",
            "model_registration",
            "model_deployment",
            "monitoring",
        ],
    }
