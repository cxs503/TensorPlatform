"""
设备故障预测 API
"""
from fastapi import APIRouter, HTTPException
from services.fault_prediction_service import get_fault_prediction_service

router = APIRouter(prefix="/fault-prediction", tags=["故障预测"])


# ========== 设备管理 ==========


@router.get("/equipment")
def list_equipment(equipment_type: str = None):
    """列出设备"""
    service = get_fault_prediction_service()
    return service.list_equipment(equipment_type)


@router.post("/equipment")
def register_equipment(req: dict):
    """注册设备"""
    service = get_fault_prediction_service()
    equip = service.register_equipment(
        name=req.get("name"),
        equipment_type=req.get("equipment_type"),
        location=req.get("location"),
    )
    return {"id": equip.id, "name": equip.name, "equipment_type": equip.equipment_type}


@router.get("/equipment/{equipment_id}")
def get_equipment(equipment_id: str):
    """获取设备"""
    service = get_fault_prediction_service()
    equip = service.get_equipment(equipment_id)
    if not equip:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {
        "id": equip.id,
        "name": equip.name,
        "equipment_type": equip.equipment_type,
        "health_status": equip.health_status,
        "health_score": equip.health_score,
    }


# ========== 健康评估 ==========


@router.get("/equipment/{equipment_id}/health")
def assess_health(equipment_id: str):
    """健康评估"""
    service = get_fault_prediction_service()
    return service.assess_health(equipment_id)


@router.get("/health/all")
def get_all_health_status():
    """获取所有设备健康状态"""
    service = get_fault_prediction_service()
    return service.get_all_health_status()


# ========== 故障预测 ==========


@router.post("/equipment/{equipment_id}/predict")
def predict_fault(equipment_id: str, prediction_type: str = "fault"):
    """故障预测"""
    service = get_fault_prediction_service()
    prediction = service.predict_fault(equipment_id, prediction_type)
    return {
        "id": prediction.id,
        "prediction_type": prediction.prediction_type,
        "risk_level": prediction.risk_level,
        "predictions": prediction.predictions,
        "estimated_failure_date": prediction.estimated_failure_date,
    }


@router.get("/predictions/{prediction_id}")
def get_prediction(prediction_id: str):
    """获取预测结果"""
    service = get_fault_prediction_service()
    prediction = service.get_prediction(prediction_id)
    if not prediction:
        raise HTTPException(status_code=404, detail="预测不存在")
    return {
        "id": prediction.id,
        "risk_level": prediction.risk_level,
        "predictions": prediction.predictions,
    }


# ========== 异常检测 ==========


@router.post("/equipment/{equipment_id}/detect")
def detect_anomaly(equipment_id: str, req: dict):
    """检测异常"""
    service = get_fault_prediction_service()
    anomaly = service.detect_anomaly(equipment_id, req.get("sensor_data", {}))
    if not anomaly:
        return {"message": "未检测到异常", "anomaly": None}
    return {
        "id": anomaly.id,
        "anomaly_type": anomaly.anomaly_type,
        "severity": anomaly.severity,
        "description": anomaly.description,
    }


@router.get("/anomalies")
def list_anomalies(equipment_id: str = None, status: str = None):
    """列出异常"""
    service = get_fault_prediction_service()
    return service.list_anomalies(equipment_id, status)


@router.post("/anomalies/{anomaly_id}/resolve")
def resolve_anomaly(anomaly_id: str):
    """解决异常"""
    service = get_fault_prediction_service()
    if not service.resolve_anomaly(anomaly_id):
        raise HTTPException(status_code=404, detail="异常不存在")
    return {"message": "已解决"}


# ========== 维护建议 ==========


@router.get("/equipment/{equipment_id}/maintenance")
def get_maintenance_suggestions(equipment_id: str):
    """获取维护建议"""
    service = get_fault_prediction_service()
    return service.get_maintenance_suggestions(equipment_id)
