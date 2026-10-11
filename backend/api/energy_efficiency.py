"""
能效优化 API — 数据库持久化版本
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.config import get_db
from services.energy_efficiency_service import (
    OptimizationType,
    EnergyEfficiencyService,
)

router = APIRouter(prefix="/energy-efficiency", tags=["能效优化"])


def get_service(db: Session = Depends(get_db)) -> EnergyEfficiencyService:
    return EnergyEfficiencyService(db)


# ========== 系统管理 ==========

@router.get("/systems")
def list_systems(
    optimization_type: str = None,
    service: EnergyEfficiencyService = Depends(get_service),
):
    return service.list_systems(optimization_type)


@router.post("/systems")
def register_system(req: dict, service: EnergyEfficiencyService = Depends(get_service)):
    return service.register_system(
        name=req.get("name"),
        optimization_type=req.get("optimization_type"),
        location=req.get("location"),
    )


@router.get("/systems/{system_id}")
def get_system(system_id: int, service: EnergyEfficiencyService = Depends(get_service)):
    system = service.get_system(system_id)
    if not system:
        raise HTTPException(status_code=404, detail="系统不存在")
    return system


@router.put("/systems/{system_id}")
def update_system(system_id: int, req: dict, service: EnergyEfficiencyService = Depends(get_service)):
    result = service.update_system(system_id, **req)
    if not result:
        raise HTTPException(status_code=404, detail="系统不存在")
    return result


# ========== 参数优化 ==========

@router.post("/systems/{system_id}/optimize")
def optimize_parameters(
    system_id: int,
    optimization_type: str = "dust_collector",
    service: EnergyEfficiencyService = Depends(get_service),
):
    return service.optimize_parameters(system_id, optimization_type)


# ========== 统计 ==========

@router.get("/stats")
def get_efficiency_stats(
    optimization_type: str = None,
    service: EnergyEfficiencyService = Depends(get_service),
):
    return service.get_efficiency_stats(optimization_type)
