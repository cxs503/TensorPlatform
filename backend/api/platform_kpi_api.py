"""
平台 KPI 面板 API — GET /tensor/kpi/platform_dashboard

四维度 KPI 自动采集面板：
- repro_rate ≥ 0.8
- kb_samples ≥ 100
- unattended ≥ 0.9
- benchmark ≥ 5
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db
from services.platform_kpi import compute_all_kpis

router = APIRouter(prefix="/tensor/kpi", tags=["平台 KPI 面板"])


@router.get("/platform_dashboard", dependencies=[Depends(require_auth)])
def platform_kpi_dashboard(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    四维度 KPI 面板。

    返回每个维度的当前值、目标值、是否达标，以及总体是否全部达标。
    """
    kpis = compute_all_kpis(db)
    return {"status": "ok", "data": kpis}
