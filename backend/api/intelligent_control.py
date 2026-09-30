"""
智能控制 API
"""
from fastapi import APIRouter, HTTPException
from services.intelligent_control_service import (
    ControlType,
    get_intelligent_control_service,
)

router = APIRouter(prefix="/control", tags=["智能控制"])


# ========== 控制系统管理 ==========


@router.get("/systems")
def list_control_systems(control_type: str = None):
    """列出控制系统"""
    service = get_intelligent_control_service()
    return service.list_control_systems(control_type)


@router.post("/systems")
def create_control_system(req: dict):
    """创建控制系统"""
    service = get_intelligent_control_service()
    control = service.create_control_system(
        name=req.get("name"),
        control_type=req.get("control_type"),
        target_system=req.get("target_system"),
        parameters=req.get("parameters"),
    )
    return {
        "id": control.id,
        "name": control.name,
        "control_type": control.control_type,
        "status": control.status,
    }


@router.get("/systems/{control_id}")
def get_control_system(control_id: str):
    """获取控制系统"""
    service = get_intelligent_control_service()
    control = service.get_control_system(control_id)
    if not control:
        raise HTTPException(status_code=404, detail="控制系统不存在")
    return {
        "id": control.id,
        "name": control.name,
        "control_type": control.control_type,
        "target_system": control.target_system,
        "status": control.status,
        "parameters": control.parameters,
    }


@router.post("/systems/{control_id}/start")
def start_control(control_id: str, req: dict):
    """启动控制"""
    service = get_intelligent_control_service()
    if not service.start_control(control_id, req.get("setpoints", {})):
        raise HTTPException(status_code=404, detail="控制系统不存在")
    return {"message": "控制已启动"}


@router.post("/systems/{control_id}/stop")
def stop_control(control_id: str):
    """停止控制"""
    service = get_intelligent_control_service()
    if not service.stop_control(control_id):
        raise HTTPException(status_code=404, detail="控制系统不存在")
    return {"message": "控制已停止"}


# ========== 控制执行 ==========


@router.post("/systems/{control_id}/execute")
def execute_control(control_id: str, req: dict):
    """执行控制"""
    service = get_intelligent_control_service()
    return service.execute_control(control_id, req.get("current_state", {}))


# ========== 控制历史 ==========


@router.get("/systems/{control_id}/history")
def get_control_history(control_id: str, limit: int = 100):
    """获取控制历史"""
    service = get_intelligent_control_service()
    return service.get_control_history(control_id, limit)


# ========== 优化建议 ==========


@router.get("/systems/{control_id}/optimization")
def get_optimization_suggestions(control_id: str):
    """获取优化建议"""
    service = get_intelligent_control_service()
    return service.get_optimization_suggestions(control_id)
