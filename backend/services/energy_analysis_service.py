"""
能耗分析服务 — 数据库持久化版本
"""
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from models.core import EnergyConsumption as ConsumptionModel


class EnergyType(str, Enum):
    ELECTRICITY = "electricity"
    GAS = "gas"
    COAL = "coal"
    WATER = "water"
    STEAM = "steam"


class TimeGranularity(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class EnergyAnalysisService:
    """能耗分析服务（数据库持久化）"""

    def __init__(self, db: Session):
        self.db = db

    def record_consumption(
        self,
        entity_id: str,
        entity_type: str = None,
        energy_type: str = "electricity",
        consumption: float = 0,
        unit: str = "kWh",
        cost_yuan: float = None,
    ) -> dict:
        record = ConsumptionModel(
            entity_id=entity_id,
            entity_type=entity_type,
            energy_type=energy_type,
            consumption=consumption,
            unit=unit,
            cost_yuan=cost_yuan,
        )
        self.db.add(record)
        self.db.commit()
        return record.to_dict()

    def get_consumption(
        self,
        entity_id: str = None,
        entity_type: str = None,
        energy_type: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[dict]:
        q = self.db.query(ConsumptionModel)
        if entity_id:
            q = q.filter(ConsumptionModel.entity_id == entity_id)
        if entity_type:
            q = q.filter(ConsumptionModel.entity_type == entity_type)
        if energy_type:
            q = q.filter(ConsumptionModel.energy_type == energy_type)
        if start_time:
            q = q.filter(ConsumptionModel.recorded_at >= start_time)
        if end_time:
            q = q.filter(ConsumptionModel.recorded_at <= end_time)
        records = q.order_by(ConsumptionModel.recorded_at.desc()).limit(limit).all()
        return [r.to_dict() for r in records]

    def get_total_consumption(
        self,
        entity_id: str,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> dict:
        q = self.db.query(
            func.sum(ConsumptionModel.consumption).label("total"),
            func.avg(ConsumptionModel.consumption).label("avg"),
            func.max(ConsumptionModel.consumption).label("max"),
            func.min(ConsumptionModel.consumption).label("min"),
            func.count(ConsumptionModel.id).label("count"),
        ).filter(ConsumptionModel.entity_id == entity_id)

        if start_time:
            q = q.filter(ConsumptionModel.recorded_at >= start_time)
        if end_time:
            q = q.filter(ConsumptionModel.recorded_at <= end_time)

        result = q.first()
        return {
            "entity_id": entity_id,
            "total_consumption": round(result.total or 0, 2),
            "avg_consumption": round(result.avg or 0, 2),
            "max_consumption": round(result.max or 0, 2),
            "min_consumption": round(result.min or 0, 2),
            "record_count": result.count or 0,
            "unit": "kWh",
        }

    def get_daily_breakdown(self, entity_id: str, days: int = 30) -> List[dict]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = (
            self.db.query(
                func.date(ConsumptionModel.recorded_at).label("date"),
                func.sum(ConsumptionModel.consumption).label("total"),
                func.avg(ConsumptionModel.consumption).label("avg"),
            )
            .filter(ConsumptionModel.entity_id == entity_id)
            .filter(ConsumptionModel.recorded_at >= cutoff)
            .group_by("date")
            .order_by("date")
            .all()
        )
        return [
            {"date": str(r.date), "total_kwh": round(r.total or 0, 2),
             "avg_kw": round(r.avg or 0, 2)}
            for r in results
        ]

    def get_entity_summary(self) -> List[dict]:
        results = (
            self.db.query(
                ConsumptionModel.entity_id,
                ConsumptionModel.entity_type,
                func.sum(ConsumptionModel.consumption).label("total"),
                func.count(ConsumptionModel.id).label("count"),
            )
            .group_by(ConsumptionModel.entity_id, ConsumptionModel.entity_type)
            .order_by(func.sum(ConsumptionModel.consumption).desc())
            .limit(50)
            .all()
        )
        return [
            {"entity_id": r.entity_id, "entity_type": r.entity_type,
             "total_consumption": round(r.total or 0, 2), "record_count": r.count}
            for r in results
        ]


_ea_service: Optional[EnergyAnalysisService] = None


def get_energy_analysis_service(db: Session = None) -> EnergyAnalysisService:
    global _ea_service
    if db:
        return EnergyAnalysisService(db)
    if _ea_service is None:
        from database.config import SessionLocal
        _ea_service = EnergyAnalysisService(SessionLocal())
    return _ea_service
