"""
光伏管理 API — 数据库持久化版本
"""
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.config import get_db
from services.photovoltaic_service import PhotovoltaicService

router = APIRouter(prefix="/photovoltaic", tags=["光伏管理"])


def get_service(db: Session = Depends(get_db)) -> PhotovoltaicService:
    return PhotovoltaicService(db)


# ========== 系统管理 ==========

@router.get("/systems")
def list_systems(status: str = None, service: PhotovoltaicService = Depends(get_service)):
    return service.list_systems(status)


@router.post("/systems")
def create_system(req: dict, service: PhotovoltaicService = Depends(get_service)):
    return service.create_system(
        name=req.get("name"),
        capacity_kwp=req.get("capacity_kwp"),
        location=req.get("location"),
    )


@router.get("/systems/{system_id}")
def get_system(system_id: int, service: PhotovoltaicService = Depends(get_service)):
    system = service.get_system(system_id)
    if not system:
        raise HTTPException(status_code=404, detail="光伏系统不存在")
    return system


@router.put("/systems/{system_id}")
def update_system(system_id: int, req: dict, service: PhotovoltaicService = Depends(get_service)):
    result = service.update_system(system_id, **req)
    if not result:
        raise HTTPException(status_code=404, detail="光伏系统不存在")
    return result


@router.delete("/systems/{system_id}")
def delete_system(system_id: int, service: PhotovoltaicService = Depends(get_service)):
    if service.delete_system(system_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="光伏系统不存在")


# ========== 实时数据 ==========

@router.post("/systems/{system_id}/data")
def record_data(system_id: int, req: dict, service: PhotovoltaicService = Depends(get_service)):
    return service.record_data(
        system_id=system_id,
        power_kw=req.get("power_kw", 0),
        energy_kwh=req.get("energy_kwh", 0),
        irradiance_wm2=req.get("irradiance_wm2"),
        temperature_c=req.get("temperature_c"),
    )


@router.get("/systems/{system_id}/realtime")
def get_realtime_data(system_id: int, service: PhotovoltaicService = Depends(get_service)):
    return service.get_realtime_data(system_id)


@router.get("/systems/{system_id}/history")
def get_history(
    system_id: int,
    start_time: str = None,
    end_time: str = None,
    limit: int = 100,
    service: PhotovoltaicService = Depends(get_service),
):
    start = datetime.fromisoformat(start_time) if start_time else None
    end = datetime.fromisoformat(end_time) if end_time else None
    return service.get_history(system_id, start, end, limit)


@router.get("/systems/{system_id}/daily-summary")
def get_daily_summary(
    system_id: int, days: int = 7,
    service: PhotovoltaicService = Depends(get_service),
):
    return service.get_daily_summary(system_id, days)


# ========== 统计 ==========

@router.get("/statistics")
def get_statistics(system_id: int = None, service: PhotovoltaicService = Depends(get_service)):
    return service.get_statistics(system_id)


# ========== 预测（保留模拟，标记 is_synthetic） ==========

@router.get("/systems/{system_id}/prediction")
def get_prediction(system_id: int, service: PhotovoltaicService = Depends(get_service)):
    """光伏发电预测 — 暂用模拟数据，待接入气象预报 API 与训练模型后替换"""
    # 尝试从 DB 获取近期发电量作为基准
    recent = service.get_daily_summary(system_id, days=7)
    avg_daily = 0
    if recent and recent.get("daily_data"):
        avg_daily = sum(d.get("total_kwh", 0) for d in recent["daily_data"]) / max(len(recent["daily_data"]), 1)
    # 若 DB 有数据则基于历史均值预测，否则回退随机
    if avg_daily > 0:
        return {
            "system_id": system_id,
            "today_predicted_kwh": round(avg_daily * random.uniform(0.9, 1.1), 1),
            "tomorrow_predicted_kwh": round(avg_daily * random.uniform(0.85, 1.15), 1),
            "confidence_pct": 92.0,
            "based_on_days": min(len(recent.get("daily_data", [])), 7),
            "is_synthetic": True,
        }
    return {
        "system_id": system_id,
        "today_predicted_kwh": round(random.uniform(1800, 2200), 1),
        "tomorrow_predicted_kwh": round(random.uniform(1700, 2300), 1),
        "confidence_pct": round(random.uniform(85, 96), 1),
        "is_synthetic": True,
        "hint": "数据库无光伏历史数据，无法基于历史预测",
    }
