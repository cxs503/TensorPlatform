"""
能耗分析 API — 数据库持久化版本
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.config import get_db
from services.energy_analysis_service import (
    EnergyType,
    TimeGranularity,
    EnergyAnalysisService,
)

router = APIRouter(prefix="/energy", tags=["能耗分析"])


def get_service(db: Session = Depends(get_db)) -> EnergyAnalysisService:
    return EnergyAnalysisService(db)


# ========== 能耗记录 ==========

@router.post("/consumption")
def record_consumption(req: dict, service: EnergyAnalysisService = Depends(get_service)):
    record = service.record_consumption(
        entity_id=req.get("entity_id"),
        entity_type=req.get("entity_type"),
        energy_type=req.get("energy_type", "electricity"),
        consumption=req.get("consumption", 0),
        unit=req.get("unit", "kWh"),
        cost_yuan=req.get("cost_yuan"),
    )
    return record


@router.get("/consumption")
def get_consumption(
    entity_id: str = None,
    entity_type: str = None,
    energy_type: str = None,
    start_time: str = None,
    end_time: str = None,
    limit: int = 100,
    service: EnergyAnalysisService = Depends(get_service),
):
    start = datetime.fromisoformat(start_time) if start_time else None
    end = datetime.fromisoformat(end_time) if end_time else None
    return service.get_consumption(entity_id, entity_type, energy_type, start, end, limit)


@router.get("/consumption/total/{entity_id}")
def get_total_consumption(
    entity_id: str,
    start_time: str = None,
    end_time: str = None,
    service: EnergyAnalysisService = Depends(get_service),
):
    start = datetime.fromisoformat(start_time) if start_time else None
    end = datetime.fromisoformat(end_time) if end_time else None
    return service.get_total_consumption(entity_id, start, end)


@router.get("/consumption/daily/{entity_id}")
def get_daily_breakdown(
    entity_id: str,
    days: int = 30,
    service: EnergyAnalysisService = Depends(get_service),
):
    return service.get_daily_breakdown(entity_id, days)


@router.get("/consumption/summary")
def get_entity_summary(service: EnergyAnalysisService = Depends(get_service)):
    return service.get_entity_summary()
