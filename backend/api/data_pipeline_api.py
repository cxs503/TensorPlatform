"""
数据管道 API — 端到端数据→训练→推理编排
提供给前端一键触发 demo 案例全流程
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.config import get_db
from services.data_pipeline import DataPipeline, get_data_pipeline

router = APIRouter(prefix="/pipeline", tags=["数据管道"])


class UploadRequest(BaseModel):
    data: List[Dict[str, Any]]
    project_id: int
    name: Optional[str] = None
    description: Optional[str] = ""


class TrainRequest(BaseModel):
    dataset_id: int
    train_config: Dict[str, Any] = {}


class QuickTrainRequest(BaseModel):
    scenario: str  # sinter_temperature, sinter_feo, dust_collector
    project_id: int
    train_config: Optional[Dict[str, Any]] = None
    n_samples: Optional[int] = 1000


class InferenceRequest(BaseModel):
    model_id: int
    features: List[List[float]]


@router.post("/upload-and-govern")
def upload_and_govern(req: UploadRequest, db: Session = Depends(get_db)):
    """上传数据并自动治理（质量检查+标准化）"""
    pipeline = DataPipeline(db)
    result = pipeline.upload_and_govern(
        data=req.data,
        project_id=req.project_id,
        name=req.name,
        description=req.description or "",
    )
    return result


@router.post("/train")
def train_from_dataset(req: TrainRequest, db: Session = Depends(get_db)):
    """从已有数据集训练模型"""
    pipeline = DataPipeline(db)
    result = pipeline.train_from_dataset(
        dataset_id=req.dataset_id,
        model_config=req.train_config or {},
    )
    return result


@router.post("/quick-train")
def quick_train(req: QuickTrainRequest, db: Session = Depends(get_db)):
    """
    一键完成 demo 案例全流程：生成数据 → 治理 → 训练 → 返回模型ID
    支持场景: sinter_temperature, sinter_feo, dust_collector
    """
    pipeline = DataPipeline(db)
    result = pipeline.quick_train_from_scenario(
        scenario=req.scenario,
        project_id=req.project_id,
        model_config=req.train_config,
        n_samples=req.n_samples or 1000,
    )
    return result


@router.post("/inference")
def pipeline_inference(req: InferenceRequest, db: Session = Depends(get_db)):
    """通过模型ID推理"""
    pipeline = DataPipeline(db)
    result = pipeline.inference_from_model(
        model_id=req.model_id,
        features=req.features,
    )
    return result


@router.get("/scenarios")
def list_scenarios():
    """列出所有可用场景"""
    return {
        "scenarios": [
            {
                "id": "sinter_temperature",
                "name": "生产过程终点温度预测",
                "description": "基于8个传感器特征预测生产过程终点温度",
                "model_type": "regression",
                "features": ["machine_speed", "bed_thickness", "permeability",
                             "pressure1", "pressure2", "temp15", "temp16", "temp19"],
                "target": "end_temp",
            },
            {
                "id": "sinter_feo",
                "name": "生产过程工艺参数优化FeO预测",
                "description": "通过工艺参数优化比例和环冷斜率预测FeO含量",
                "model_type": "regression",
                "features": ["carbon_ratio", "cooling_slope"],
                "target": "feo_content",
            },
            {
                "id": "dust_collector",
                "name": "除尘能效优化",
                "description": "根据工况参数预测能耗并优化风机转速",
                "model_type": "regression",
                "features": ["production_load", "dust_concentration",
                             "filter_pressure_diff", "temperature", "fan_speed"],
                "target": "energy_consumption_kwh",
            },
        ],
    }
