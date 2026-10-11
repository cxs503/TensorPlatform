"""
模型漂移检测API
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.drift_detection_service import get_drift_detection_service

router = APIRouter(prefix="/drift", tags=["模型漂移检测"])


class RegisterModelReq(BaseModel):
    model_id: str
    features: List[Dict]  # [{"name": "feature1", "type": "ks", "threshold": 0.1}]


class SetBaselineReq(BaseModel):
    model_id: str
    feature_name: str
    baseline_data: List[float]


class RecordPredictionReq(BaseModel):
    model_id: str
    prediction: float
    features: Optional[Dict[str, float]] = {}


@router.post("/register")
def register_model(req: RegisterModelReq):
    """注册模型进行漂移检测"""
    service = get_drift_detection_service()

    for feature in req.features:
        service.register_model(
            req.model_id,
            feature["name"],
            feature.get("type", "ks"),
            feature.get("threshold", 0.1),
        )

    return {
        "message": f"Model {req.model_id} registered",
        "features": len(req.features),
    }


@router.post("/baseline")
def set_baseline(req: SetBaselineReq):
    """设置基准数据"""
    service = get_drift_detection_service()
    success = service.set_baseline(req.model_id, req.feature_name, req.baseline_data)

    if success:
        return {"message": "Baseline set", "data_points": len(req.baseline_data)}
    raise HTTPException(status_code=404, detail="Detector not found")


@router.post("/predictions")
def record_prediction(req: RecordPredictionReq):
    """记录预测结果"""
    service = get_drift_detection_service()
    service.record_prediction(req.model_id, req.prediction, req.features)
    return {"message": "Prediction recorded"}


@router.get("/detect/{model_id}")
def detect_drift(model_id: str, window_size: int = 30):
    """检测模型漂移

    无足够数据时返回明确的"数据不足"状态(不再伪造随机漂移分数/告警)。
    service.detect_drift 已确保所有 numpy 值转为原生类型,可安全 JSON 序列化。

    window_size 默认 30:训练自动注册 baseline + 评估自动灌入 ~30+ 条带特征的预测后,
    即可执行 KS/PSI 检验;不再要求 1000 条(演示场景无法达到)。
    """
    service = get_drift_detection_service()
    result = service.detect_drift(model_id, window_size)

    if "error" in result:
        # 模型未注册 / 无预测历史 / 窗口数据不足:返回 200 + 明确状态,
        # 不再写假的随机漂移结果和告警。
        return {
            "model_id": model_id,
            "drift_detected": False,
            "timestamp": _now_iso(),
            "window_size": window_size,
            "total_predictions": result.get("total_predictions", 0),
            "sufficient_data": False,
            "message": f"无足够数据进行漂移检测: {result['error']}",
            "hint": "请先通过 /drift/predictions 记录预测数据(至少需 window_size 条),"
                    "并通过 /drift/baseline 设置基准分布。"
                    "训练完成的模型会自动注册 baseline,评估时自动灌入预测。",
        }

    result["sufficient_data"] = True
    return result


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


@router.get("/stats/{model_id}")
def get_prediction_stats(model_id: str, window_size: int = 100):
    """获取预测统计"""
    service = get_drift_detection_service()
    stats = service.get_prediction_stats(model_id, window_size)

    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])

    return stats


@router.get("/alerts")
def get_alerts(model_id: Optional[str] = None, limit: int = 100):
    """获取漂移告警"""
    service = get_drift_detection_service()
    alerts = service.get_alerts(model_id, limit)
    return {"total": len(alerts), "items": alerts}


@router.get("/methods")
def get_detection_methods():
    """获取可用的漂移检测方法"""
    return {
        "methods": [
            {
                "id": "ks",
                "name": "Kolmogorov-Smirnov Test",
                "description": "检测两个分布是否相同",
            },
            {
                "id": "psi",
                "name": "Population Stability Index",
                "description": "检测分布稳定性变化",
            },
            {
                "id": "ddm",
                "name": "Drift Detection Method",
                "description": "基于错误率变化的漂移检测",
            },
        ]
    }
