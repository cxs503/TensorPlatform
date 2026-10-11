"""
碳排放核算 API — 数据库持久化版本
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.config import get_db
from services.carbon_service import CarbonService

router = APIRouter(prefix="/carbon", tags=["碳排放核算"])


def get_service(db: Session = Depends(get_db)) -> CarbonService:
    return CarbonService(db)


# ========== 排放因子 ==========

@router.get("/factors")
def list_emission_factors(service: CarbonService = Depends(get_service)):
    return service.list_emission_factors()


@router.post("/factors")
def add_emission_factor(req: dict, service: CarbonService = Depends(get_service)):
    return service.add_emission_factor(
        name=req.get("name"),
        factor_value=req.get("factor_value"),
        unit=req.get("unit"),
        source=req.get("source", "custom"),
    )


# ========== 碳排放计算与记录 ==========

@router.post("/calculate")
def calculate_emission(req: dict, service: CarbonService = Depends(get_service)):
    return service.calculate_emission(
        entity_id=req.get("entity_id"),
        scope=req.get("scope"),
        source=req.get("source"),
        activity_data=req.get("activity_data"),
        factor_name=req.get("factor_name"),
        entity_type=req.get("entity_type"),
    )


@router.post("/emissions")
def record_emission(req: dict, service: CarbonService = Depends(get_service)):
    return service.record_emission(
        entity_id=req.get("entity_id"),
        scope=req.get("scope"),
        source=req.get("source"),
        emission_amount=req.get("emission_amount"),
        entity_type=req.get("entity_type"),
        unit=req.get("unit", "tCO2e"),
    )


@router.get("/emissions")
def get_emissions(
    entity_id: str = None,
    scope: str = None,
    start_time: str = None,
    end_time: str = None,
    limit: int = 100,
    service: CarbonService = Depends(get_service),
):
    start = datetime.fromisoformat(start_time) if start_time else None
    end = datetime.fromisoformat(end_time) if end_time else None
    return service.get_emissions(entity_id, scope, start, end, limit)


@router.get("/total")
def get_total_emission(
    entity_id: str = None,
    scope: str = None,
    start_time: str = None,
    end_time: str = None,
    service: CarbonService = Depends(get_service),
):
    start = datetime.fromisoformat(start_time) if start_time else None
    end = datetime.fromisoformat(end_time) if end_time else None
    return service.get_total_emission(entity_id, scope, start, end)


@router.get("/by-scope")
def get_emissions_by_scope(
    entity_id: str = None,
    service: CarbonService = Depends(get_service),
):
    return service.get_emissions_by_scope(entity_id)


@router.delete("/emissions/{emission_id}")
def delete_emission(
    emission_id: int,
    service: CarbonService = Depends(get_service),
):
    if service.delete_emission(emission_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="排放记录不存在")


# ========== 初始化 ==========

@router.post("/init-factors")
def init_default_factors(service: CarbonService = Depends(get_service)):
    """初始化默认排放因子（幂等操作）"""
    service.init_default_factors()
    return {"status": "ok", "count": len(service.list_emission_factors())}


# ========== 趋势分析 ==========

@router.get("/trend")
def get_emission_trend(
    entity_id: str = None,
    granularity: str = "day",
    days: int = 30,
    service: CarbonService = Depends(get_service),
):
    return service.get_emission_trend(entity_id, granularity, days)


# ========== 碳配额 ==========

@router.post("/credits")
def create_carbon_credit(req: dict, service: CarbonService = Depends(get_service)):
    return service.create_carbon_credit(
        entity_id=req.get("entity_id"),
        credit_type=req.get("credit_type"),
        amount=req.get("amount"),
        expiration_date=req.get("expiration_date"),
    )


@router.get("/balance")
def get_carbon_balance(
    entity_id: str,
    service: CarbonService = Depends(get_service),
):
    return service.get_carbon_balance(entity_id)


# ========== 减排建议 ==========

@router.get("/reduction-suggestions")
def get_reduction_suggestions(
    entity_id: str,
    service: CarbonService = Depends(get_service),
):
    return service.get_reduction_suggestions(entity_id)
