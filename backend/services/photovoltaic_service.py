"""
光伏管理服务 — 数据库持久化版本
"""
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from models.core import PhotovoltaicSystem as PVSystemModel
from models.core import PhotovoltaicData as PVDataModel


class PhotovoltaicService:
    """光伏管理服务（数据库持久化）"""

    def __init__(self, db: Session):
        self.db = db

    # ========== 系统管理 ==========

    def create_system(self, name: str, capacity_kwp: float, location: str = None) -> dict:
        system = PVSystemModel(name=name, capacity_kwp=capacity_kwp, location=location)
        self.db.add(system)
        self.db.commit()
        return system.to_dict()

    def get_system(self, system_id: int) -> Optional[dict]:
        system = self.db.query(PVSystemModel).filter(PVSystemModel.id == system_id).first()
        return system.to_dict() if system else None

    def list_systems(self, status: str = None) -> List[dict]:
        q = self.db.query(PVSystemModel)
        if status:
            q = q.filter(PVSystemModel.status == status)
        return [s.to_dict() for s in q.all()]

    def update_system(self, system_id: int, **kwargs) -> Optional[dict]:
        system = self.db.query(PVSystemModel).filter(PVSystemModel.id == system_id).first()
        if not system:
            return None
        for k, v in kwargs.items():
            if hasattr(system, k):
                setattr(system, k, v)
        self.db.commit()
        return system.to_dict()

    def delete_system(self, system_id: int) -> bool:
        system = self.db.query(PVSystemModel).filter(PVSystemModel.id == system_id).first()
        if system:
            self.db.delete(system)
            self.db.commit()
            return True
        return False

    # ========== 实时数据 ==========

    def record_data(
        self,
        system_id: int,
        power_kw: float,
        energy_kwh: float = 0,
        irradiance_wm2: float = None,
        temperature_c: float = None,
    ) -> dict:
        data = PVDataModel(
            system_id=system_id,
            power_kw=power_kw,
            energy_kwh=energy_kwh,
            irradiance_wm2=irradiance_wm2,
            temperature_c=temperature_c,
        )
        self.db.add(data)
        self.db.commit()
        return data.to_dict()

    def get_power_output(self, system_id: int) -> Optional[dict]:
        """获取最新功率输出（别名）"""
        return self.get_realtime_data(system_id)

    def get_daily_energy(self, system_id: int, days: int = 7) -> List[dict]:
        """获取每日发电量（别名）"""
        return self.get_daily_summary(system_id, days)

    def get_realtime_data(self, system_id: int) -> Optional[dict]:
        """获取最新一条实时数据"""
        data = (
            self.db.query(PVDataModel)
            .filter(PVDataModel.system_id == system_id)
            .order_by(PVDataModel.recorded_at.desc())
            .first()
        )
        return data.to_dict() if data else None

    def get_history(
        self,
        system_id: int,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[dict]:
        q = self.db.query(PVDataModel).filter(PVDataModel.system_id == system_id)
        if start_time:
            q = q.filter(PVDataModel.recorded_at >= start_time)
        if end_time:
            q = q.filter(PVDataModel.recorded_at <= end_time)
        data = q.order_by(PVDataModel.recorded_at.desc()).limit(limit).all()
        return [d.to_dict() for d in data]

    # ========== 统计 ==========

    def get_statistics(self, system_id: int = None) -> dict:
        """获取发电统计"""
        q = self.db.query(
            func.sum(PVDataModel.energy_kwh).label("total_energy"),
            func.avg(PVDataModel.power_kw).label("avg_power"),
            func.max(PVDataModel.power_kw).label("max_power"),
            func.count(PVDataModel.id).label("records"),
        )
        if system_id:
            q = q.filter(PVDataModel.system_id == system_id)

        result = q.first()
        return {
            "total_energy_kwh": round(result.total_energy or 0, 2),
            "avg_power_kw": round(result.avg_power or 0, 2),
            "max_power_kw": round(result.max_power or 0, 2),
            "record_count": result.records or 0,
            "estimated_co2_saved_kg": round((result.total_energy or 0) * 0.581, 2),
        }

    def get_daily_summary(self, system_id: int, days: int = 7) -> List[dict]:
        """获取每日发电汇总"""
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = (
            self.db.query(
                func.date(PVDataModel.recorded_at).label("date"),
                func.sum(PVDataModel.energy_kwh).label("energy"),
                func.max(PVDataModel.power_kw).label("peak_power"),
            )
            .filter(PVDataModel.system_id == system_id)
            .filter(PVDataModel.recorded_at >= cutoff)
            .group_by("date")
            .order_by("date")
            .all()
        )
        return [
            {"date": str(r.date), "energy_kwh": round(r.energy or 0, 2),
             "peak_power_kw": round(r.peak_power or 0, 2)}
            for r in results
        ]


_pv_service: Optional[PhotovoltaicService] = None


def get_photovoltaic_service(db: Session = None) -> PhotovoltaicService:
    global _pv_service
    if db:
        return PhotovoltaicService(db)
    if _pv_service is None:
        from database.config import SessionLocal
        _pv_service = PhotovoltaicService(SessionLocal())
    return _pv_service
