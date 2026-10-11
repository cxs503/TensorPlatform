"""
碳排放核算服务 — 数据库持久化版本
提供范围1/2/3排放计算、碳排放因子、核算报告
"""
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from models.core import EmissionFactor as EmissionFactorModel
from models.core import CarbonEmission as CarbonEmissionModel
from models.core import EmissionScope
from services.base import BaseService

# 默认排放因子，用于数据库初始化
DEFAULT_FACTORS = [
    ("电网排放因子", 0.581, "kgCO2/kWh", "生态环境部"),
    ("天然气因子", 2.09, "kgCO2/m³", "IPCC"),
    ("汽油因子", 2.31, "kgCO2/L", "IPCC"),
    ("柴油因子", 2.68, "kgCO2/L", "IPCC"),
    ("煤碳因子", 2.49, "kgCO2/kg", "IPCC"),
    ("电力因子(华北)", 0.7593, "kgCO2/kWh", "区域电网"),
    ("电力因子(华东)", 0.7035, "kgCO2/kWh", "区域电网"),
    ("电力因子(南方)", 0.5271, "kgCO2/kWh", "区域电网"),
]


class CarbonService(BaseService):
    """碳排放核算服务（数据库持久化）"""

    def __init__(self, db: Session):
        super().__init__(db)

    # ========== 排放因子管理 ==========

    def add_emission_factor(self, name: str, factor_value: float, unit: str, source: str = "custom") -> dict:
        """添加排放因子"""
        existing = self.db.query(EmissionFactorModel).filter(
            EmissionFactorModel.name == name
        ).first()
        if existing:
            existing.factor_value = factor_value
            existing.unit = unit
            existing.source = source
            self.db.commit()
            return existing.to_dict()

        factor = EmissionFactorModel(
            name=name, factor_value=factor_value, unit=unit, source=source
        )
        self.db.add(factor)
        self.db.commit()
        return factor.to_dict()

    def get_emission_factor(self, name: str) -> Optional[dict]:
        """获取排放因子"""
        factor = self.db.query(EmissionFactorModel).filter(
            EmissionFactorModel.name == name
        ).first()
        return factor.to_dict() if factor else None

    def list_emission_factors(self) -> List[dict]:
        """列出所有排放因子"""
        factors = self.db.query(EmissionFactorModel).all()
        return [f.to_dict() for f in factors]

    def init_default_factors(self):
        """初始化默认排放因子（幂等）"""
        for name, value, unit, source in DEFAULT_FACTORS:
            existing = self.db.query(EmissionFactorModel).filter(
                EmissionFactorModel.name == name
            ).first()
            if not existing:
                self.db.add(EmissionFactorModel(
                    name=name, factor_value=value, unit=unit, source=source
                ))
        self.db.commit()

    # ========== 碳排放计算 ==========

    def calculate_emission(
        self,
        entity_id: str,
        scope: str,
        source: str,
        activity_data: float,
        factor_name: str = None,
        entity_type: str = None,
    ) -> dict:
        """计算并记录碳排放"""
        factor_value = 1.0
        factor_id = None

        if factor_name:
            factor = self.db.query(EmissionFactorModel).filter(
                EmissionFactorModel.name == factor_name
            ).first()
            if factor:
                factor_value = factor.factor_value
                factor_id = factor.id

        emission_amount = activity_data * factor_value

        emission = CarbonEmissionModel(
            entity_id=entity_id,
            entity_type=entity_type,
            scope=scope,
            source=source,
            emission_amount=emission_amount,
            unit="tCO2e" if emission_amount < 1000 else "kgCO2",
            factor_id=factor_id,
        )
        self.db.add(emission)
        self.db.commit()
        return emission.to_dict()

    def record_emission(
        self,
        entity_id: str,
        scope: str,
        source: str,
        emission_amount: float,
        entity_type: str = None,
        unit: str = "tCO2e",
    ) -> dict:
        """直接记录碳排放（已知排放量）"""
        emission = CarbonEmissionModel(
            entity_id=entity_id,
            entity_type=entity_type,
            scope=scope,
            source=source,
            emission_amount=emission_amount,
            unit=unit,
        )
        self.db.add(emission)
        self.db.commit()
        return emission.to_dict()

    # ========== 查询 ==========

    def get_emissions(
        self,
        entity_id: str = None,
        scope: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[dict]:
        """查询碳排放记录"""
        q = self.db.query(CarbonEmissionModel)

        if entity_id:
            q = q.filter(CarbonEmissionModel.entity_id == entity_id)
        if scope:
            q = q.filter(CarbonEmissionModel.scope == scope)
        if start_time:
            q = q.filter(CarbonEmissionModel.recorded_at >= start_time)
        if end_time:
            q = q.filter(CarbonEmissionModel.recorded_at <= end_time)

        emissions = q.order_by(CarbonEmissionModel.recorded_at.desc()).limit(limit).all()
        return [e.to_dict() for e in emissions]

    def get_total_emission(
        self,
        entity_id: str = None,
        scope: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> dict:
        """获取总排放量"""
        q = self.db.query(func.sum(CarbonEmissionModel.emission_amount))

        if entity_id:
            q = q.filter(CarbonEmissionModel.entity_id == entity_id)
        if scope:
            q = q.filter(CarbonEmissionModel.scope == scope)
        if start_time:
            q = q.filter(CarbonEmissionModel.recorded_at >= start_time)
        if end_time:
            q = q.filter(CarbonEmissionModel.recorded_at <= end_time)

        total = q.scalar() or 0.0
        return {
            "entity_id": entity_id,
            "scope": scope,
            "total_emission": round(total, 4),
            "unit": "tCO2e",
        }

    def get_total_emissions(
        self,
        entity_id: str = None,
        scope: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> dict:
        """获取总排放量（别名，兼容旧调用）"""
        return self.get_total_emission(entity_id, scope, start_time, end_time)

    def get_by_entity(
        self,
        entity_id: str,
        scope: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[dict]:
        """按实体查询碳排放记录"""
        return self.get_emissions(entity_id=entity_id, scope=scope,
                                  start_time=start_time, end_time=end_time, limit=limit)

    def get_emissions_by_scope(self, entity_id: str = None) -> List[dict]:
        """按范围统计排放"""
        q = self.db.query(
            CarbonEmissionModel.scope,
            func.sum(CarbonEmissionModel.emission_amount).label("total"),
            func.count(CarbonEmissionModel.id).label("count"),
        )
        if entity_id:
            q = q.filter(CarbonEmissionModel.entity_id == entity_id)

        results = q.group_by(CarbonEmissionModel.scope).all()
        return [
            {"scope": r[0].value if isinstance(r[0], EmissionScope) else r[0],
             "total_emission": round(r[1], 4), "record_count": r[2]}
            for r in results
        ]

    def delete_emission(self, emission_id: int) -> bool:
        """删除排放记录"""
        emission = self.db.query(CarbonEmissionModel).filter(
            CarbonEmissionModel.id == emission_id
        ).first()
        if emission:
            self.db.delete(emission)
            self.db.commit()
            return True
        return False

    # ========== 趋势分析 ==========

    def get_emission_trend(self, entity_id: str, granularity: str = "day", days: int = 30) -> List[dict]:
        """获取排放趋势"""
        start_time = datetime.now() - timedelta(days=days)
        emissions = self.get_emissions(entity_id=entity_id, start_time=start_time)
        # 按粒度聚合
        trend = {}
        if granularity == "day":
            fmt = "%Y-%m-%d"
        elif granularity == "month":
            fmt = "%Y-%m"
        else:
            fmt = "%Y-%m-%d"
        for e in emissions:
            key = e.get("recorded_at", "")
            if isinstance(key, datetime):
                key = key.strftime(fmt)
            elif isinstance(key, str) and len(key) >= 10:
                key = key[:10] if granularity == "day" else key[:7]
            trend[key] = trend.get(key, 0) + e.get("emission_amount", 0)
        return [{"date": k, "emission": round(v, 4)} for k, v in sorted(trend.items())]

    # ========== 碳配额 ==========

    def create_carbon_credit(self, entity_id: str, credit_type: str, amount: float,
                             expiration_date: str = None) -> dict:
        """创建碳配额"""
        return {
            "id": str(uuid.uuid4())[:8],
            "entity_id": entity_id,
            "credit_type": credit_type,
            "amount": amount,
            "expiration_date": expiration_date,
        }

    def get_carbon_balance(self, entity_id: str) -> dict:
        """获取碳配额余额"""
        total_emission = self.get_total_emission(entity_id=entity_id)
        return {
            "entity_id": entity_id,
            "available": 1000.0,
            "used": total_emission.get("total_emission", 0),
            "total": 1000.0,
        }

    # ========== 减排建议 ==========

    def get_reduction_suggestions(self, entity_id: str) -> List[dict]:
        """获取减排建议"""
        return [
            {"id": "s1", "title": "Switch to LED lighting", "potential_saving": 50.0},
            {"id": "s2", "title": "Optimize HVAC scheduling", "potential_saving": 120.0},
        ]


# ========== 服务工厂 ==========

_carbon_service: Optional[CarbonService] = None


def get_carbon_service(db: Session = None) -> CarbonService:
    """获取碳排放服务实例"""
    global _carbon_service
    if db:
        return CarbonService(db)
    if _carbon_service is None:
        from database.config import SessionLocal
        _carbon_service = CarbonService(SessionLocal())
    return _carbon_service
