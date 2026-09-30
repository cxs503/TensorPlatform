"""
数字孪生 API
"""
from fastapi import APIRouter, HTTPException
from services.digital_twin_service import TwinLevel, get_digital_twin_service

router = APIRouter(prefix="/digital-twin", tags=["数字孪生"])


# ========== 数字孪生管理 ==========


@router.get("")
def list_twins(twin_level: str = None):
    """列出数字孪生"""
    service = get_digital_twin_service()
    return service.list_twins(twin_level)


@router.post("")
def create_twin(req: dict):
    """创建数字孪生"""
    service = get_digital_twin_service()
    twin = service.create_twin(
        name=req.get("name"),
        twin_level=req.get("twin_level"),
        target_system=req.get("target_system"),
        model_data=req.get("model_data"),
    )
    return {
        "id": twin.id,
        "name": twin.name,
        "twin_level": twin.twin_level,
        "status": twin.status,
    }


@router.get("/{twin_id}")
def get_twin(twin_id: str):
    """获取数字孪生"""
    service = get_digital_twin_service()
    twin = service.get_twin(twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="数字孪生不存在")
    return {
        "id": twin.id,
        "name": twin.name,
        "twin_level": twin.twin_level,
        "target_system": twin.target_system,
        "status": twin.status,
    }


# ========== 实时数据 ==========


@router.post("/{twin_id}/realtime")
def update_realtime_data(twin_id: str, req: dict):
    """更新实时数据"""
    service = get_digital_twin_service()
    if not service.update_realtime_data(twin_id, req.get("data", {})):
        raise HTTPException(status_code=404, detail="数字孪生不存在")
    return {"message": "更新成功"}


@router.get("/{twin_id}/realtime")
def get_realtime_data(twin_id: str):
    """获取实时数据"""
    service = get_digital_twin_service()
    return service.get_realtime_data(twin_id)


# ========== 3D模型 ==========


@router.get("/{twin_id}/3d-model")
def get_3d_model(twin_id: str):
    """获取3D模型数据"""
    service = get_digital_twin_service()
    return service.get_3d_model(twin_id)


# ========== 仿真分析 ==========


@router.post("/{twin_id}/simulate")
def run_simulation(twin_id: str, req: dict):
    """运行仿真"""
    service = get_digital_twin_service()
    sim = service.run_simulation(
        twin_id=twin_id,
        simulation_type=req.get("simulation_type"),
        parameters=req.get("parameters"),
    )
    return {
        "id": sim.id,
        "simulation_type": sim.simulation_type,
        "results": sim.results,
    }


@router.get("/simulate/{sim_id}")
def get_simulation(sim_id: str):
    """获取仿真结果"""
    service = get_digital_twin_service()
    sim = service.get_simulation(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="仿真不存在")
    return {
        "id": sim.id,
        "simulation_type": sim.simulation_type,
        "results": sim.results,
    }


# ========== 预测分析 ==========


@router.get("/{twin_id}/predict")
def predict_behavior(twin_id: str, horizon: int = 24):
    """预测行为"""
    service = get_digital_twin_service()
    return service.predict_behavior(twin_id, horizon)


# ========== 统计 ==========


@router.get("/{twin_id}/statistics")
def get_twin_statistics(twin_id: str):
    """获取孪生统计"""
    service = get_digital_twin_service()
    return service.get_twin_statistics(twin_id)
