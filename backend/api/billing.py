"""
计费 API — 定价配置、计费记录、统计
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.config import get_db
from models.core import BillingRecord, PricingConfig, PricingModel

router = APIRouter(prefix="/billing", tags=["计费"])


# ── 计费扣款 ──

@router.post("/charge")
def charge(req: dict, db: Session = Depends(get_db)):
    """创建一条计费记录，自动计算 total_amount = unit_price × quantity"""
    component_id = req.get("component_id")
    org_id = req.get("org_id")
    unit_price = float(req.get("unit_price", 0))
    quantity = float(req.get("quantity", 1))
    pricing_model_raw = req.get("pricing_model", "free")

    # 如果提供了 component_id，尝试从定价配置中获取单价
    if component_id is not None and unit_price == 0:
        cfg = db.query(PricingConfig).filter(
            PricingConfig.component_id == component_id
        ).first()
        if cfg:
            unit_price = cfg.price_per_call

    total_amount = round(unit_price * quantity, 4)

    record = BillingRecord(
        org_id=org_id,
        component_id=component_id,
        pricing_model=PricingModel(pricing_model_raw),
        unit_price=unit_price,
        quantity=quantity,
        total_amount=total_amount,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record.to_dict()


# ── 计费记录查询 ──

@router.get("/records")
def list_records(
    org_id: Optional[int] = Query(None),
    component_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """查询计费记录，支持按组织 / 组件筛选"""
    q = db.query(BillingRecord)
    if org_id is not None:
        q = q.filter(BillingRecord.org_id == org_id)
    if component_id is not None:
        q = q.filter(BillingRecord.component_id == component_id)
    total = q.count()
    rows = q.order_by(BillingRecord.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [r.to_dict() for r in rows],
    }


# ── 计费统计 ──

@router.get("/stats")
def billing_stats(
    org_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """计费统计：按组织 / 定价模型 / 组件聚合"""
    base = db.query(BillingRecord)
    if org_id is not None:
        base = base.filter(BillingRecord.org_id == org_id)

    total_amount = base.with_entities(
        func.coalesce(func.sum(BillingRecord.total_amount), 0)
    ).scalar() or 0
    total_count = base.with_entities(
        func.count(BillingRecord.id)
    ).scalar() or 0
    today_count = base.filter(
        func.date(BillingRecord.created_at) == date.today()
    ).count()

    # 按定价模型分组
    by_model = db.query(
        BillingRecord.pricing_model,
        func.count(BillingRecord.id).label("cnt"),
        func.coalesce(func.sum(BillingRecord.total_amount), 0).label("sum_amount"),
    )
    if org_id is not None:
        by_model = by_model.filter(BillingRecord.org_id == org_id)
    by_model = by_model.group_by(BillingRecord.pricing_model).all()

    # 按组件分组 Top 10
    by_component = db.query(
        BillingRecord.component_id,
        func.count(BillingRecord.id).label("cnt"),
        func.coalesce(func.sum(BillingRecord.total_amount), 0).label("sum_amount"),
    )
    if org_id is not None:
        by_component = by_component.filter(BillingRecord.org_id == org_id)
    by_component = by_component.group_by(BillingRecord.component_id).order_by(
        func.sum(BillingRecord.total_amount).desc()
    ).limit(10).all()

    return {
        "total_amount": round(total_amount, 4),
        "total_count": total_count,
        "today_count": today_count,
        "by_model": [
            {
                "model": r.pricing_model.value if r.pricing_model else None,
                "count": r.cnt,
                "amount": round(r.sum_amount, 4),
            }
            for r in by_model
        ],
        "by_component": [
            {
                "component_id": r.component_id,
                "count": r.cnt,
                "amount": round(r.sum_amount, 4),
            }
            for r in by_component
        ],
    }
