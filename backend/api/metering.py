"""
API调用计量 — 算法API调用计数/配额/计费统计
"""
from fastapi import APIRouter, Depends
from database.config import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from models.core import ApiUsageLog

router = APIRouter(prefix="/metering", tags=["API计量"])


@router.post("/log")
def log_call(req: dict, db: Session = Depends(get_db)):
    """记录一次API调用"""
    log = ApiUsageLog(
        api_endpoint=req.get("endpoint", ""),
        caller_id=req.get("caller_id", ""),
        organization_id=req.get("org_id"),
        response_time_ms=req.get("latency_ms", 0),
        status_code=req.get("status", 200),
    )
    db.add(log)
    db.commit()
    return {"ok": True}


@router.get("/stats")
def usage_stats(db: Session = Depends(get_db)):
    """API调用统计"""
    total = db.query(func.count(ApiUsageLog.id)).scalar()
    today = db.query(func.count(ApiUsageLog.id)).filter(
        func.date(ApiUsageLog.created_at) == date.today()
    ).scalar()
    avg_latency = db.query(func.avg(ApiUsageLog.response_time_ms)).scalar() or 0

    top_apis = db.query(
        ApiUsageLog.api_endpoint,
        func.count(ApiUsageLog.id).label("cnt"),
        func.avg(ApiUsageLog.response_time_ms).label("avg_ms"),
    ).group_by(ApiUsageLog.api_endpoint).order_by(func.count(ApiUsageLog.id).desc()).limit(10).all()

    return {
        "total_calls": total, "today_calls": today,
        "avg_latency_ms": round(avg_latency, 1),
        "top_apis": [{"endpoint": r[0], "calls": r[1], "avg_ms": round(r[2] or 0, 1)} for r in top_apis],
    }


@router.get("/org/{org_id}")
def org_usage(org_id: int, db: Session = Depends(get_db)):
    """某组织的API使用量"""
    total = db.query(func.count(ApiUsageLog.id)).filter(
        ApiUsageLog.organization_id == org_id
    ).scalar()
    today = db.query(func.count(ApiUsageLog.id)).filter(
        ApiUsageLog.organization_id == org_id,
        func.date(ApiUsageLog.created_at) == date.today(),
    ).scalar()
    return {"org_id": org_id, "total_calls": total, "today_calls": today}
