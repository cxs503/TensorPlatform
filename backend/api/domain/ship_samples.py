"""
Ship Samples API Domain — 船舶计算样本的批量存储、分页查询、批量扫描、基准验证、计时基线。

端点：
- POST /ship/batch_sweep        参数矩阵批量扫描
- POST /ship/benchmark_validate 基准验证
- POST /ship/samples/bulk       批量存储样本
- GET  /ship/samples            分页查询样本
- GET  /ship/timing_baseline    计时基线 (p50/p90/p99)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.config import get_db
from models.ship_sample import ShipSample
from services.ship_proxy import (
    ShipProxy,
    ShipProxyError,
    benchmark_validate,
    get_timing_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["船舶计算代理"])

# ── Pydantic Schemas ──────────────────────────────────


class BatchSweepRequest(BaseModel):
    """批量扫描请求。"""
    param_matrix: Dict[str, List[Any]] = Field(
        ...,
        description="参数矩阵，如 {'speed': [10, 12, 14], 'cb': [0.5, 0.6]}",
    )
    calc_type: str = Field(default="resistance", description="计算类型：resistance / stability")
    concurrent: bool = Field(default=True, description="是否并发调用")
    save_samples: bool = Field(default=False, description="是否保存样本到数据库")


class BatchSweepResponse(BaseModel):
    """批量扫描响应。"""
    calc_type: str
    total_cases: int
    results: List[Dict[str, Any]]
    timing: Dict[str, float]


class BenchmarkValidateRequest(BaseModel):
    """基准验证请求。"""
    calc_type: str = Field(default="resistance", description="计算类型")
    case_name: str = Field(..., description="基准案例名称")


class BenchmarkValidateResponse(BaseModel):
    """基准验证响应。"""
    case_name: str
    calc_type: str
    computed_value: Optional[float] = None
    reference_value: Optional[float] = None
    error_pct: Optional[float] = None
    tolerance_pct: Optional[float] = None
    passed: Optional[bool] = None
    note: str = ""
    error: Optional[str] = None


class SampleBulkRequest(BaseModel):
    """批量样本提交请求。"""
    samples: List[Dict[str, Any]] = Field(
        ...,
        min_items=1,
        max_items=1000,
        description="样本列表",
    )


class SampleBulkResponse(BaseModel):
    """批量样本提交响应。"""
    inserted: int
    ids: List[int]


class SampleListResponse(BaseModel):
    """样本列表响应。"""
    total: int
    page: int
    page_size: int
    items: List[Dict[str, Any]]


class TimingBaselineResponse(BaseModel):
    """计时基线响应。"""
    p50: float
    p90: float
    p99: float
    count: int
    min: float
    max: float
    mean: float


# ── 端点实现 ──────────────────────────────────────────


@router.post("/ship/batch_sweep", response_model=BatchSweepResponse)
async def batch_sweep(req: BatchSweepRequest, db: Session = Depends(get_db)):
    """
    参数矩阵批量扫描。

    从参数矩阵生成笛卡尔积，串行/并发调用船舶计算服务，返回结果数组。
    """
    proxy = ShipProxy()

    try:
        results = await proxy.batch_sweep(
            param_matrix=req.param_matrix,
            calc_type=req.calc_type,
            concurrent=req.concurrent,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ship API proxy error: {e}")

    # 可选：保存样本
    if req.save_samples:
        for item in results:
            sample = ShipSample(
                calc_type=req.calc_type,
                params=item.get("params"),
                result=item.get("result"),
                error=item.get("error"),
                source="batch_sweep",
            )
            db.add(sample)
        db.commit()

    timing = get_timing_stats()

    return BatchSweepResponse(
        calc_type=req.calc_type,
        total_cases=len(results),
        results=results,
        timing=timing,
    )


@router.post("/ship/benchmark_validate", response_model=BenchmarkValidateResponse)
async def benchmark_validate_endpoint(req: BenchmarkValidateRequest):
    """
    基准验证。

    调用代理获取计算值，与内置参考数据比较，返回 error_pct + pass。
    """
    proxy = ShipProxy()
    result = await benchmark_validate(
        calc_type=req.calc_type,
        case_name=req.case_name,
        proxy=proxy,
    )

    if "error" in result and result.get("error"):
        return BenchmarkValidateResponse(
            case_name=req.case_name,
            calc_type=req.calc_type,
            error=result["error"],
            passed=False,
        )

    return BenchmarkValidateResponse(
        case_name=result["case_name"],
        calc_type=result["calc_type"],
        computed_value=result.get("computed_value"),
        reference_value=result.get("reference_value"),
        error_pct=result.get("error_pct"),
        tolerance_pct=result.get("tolerance_pct"),
        passed=result.get("passed"),
        note=result.get("note", ""),
    )


@router.post("/ship/samples/bulk", response_model=SampleBulkResponse)
async def bulk_insert_samples(req: SampleBulkRequest, db: Session = Depends(get_db)):
    """
    批量存储样本。

    接收样本列表，批量插入数据库。
    """
    ids = []
    for sample_data in req.samples:
        sample = ShipSample(
            calc_type=sample_data.get("calc_type", "unknown"),
            params=sample_data.get("params"),
            result=sample_data.get("result"),
            error=sample_data.get("error"),
            error_pct=sample_data.get("error_pct"),
            passed=sample_data.get("passed"),
            source=sample_data.get("source", "manual"),
            tags=sample_data.get("tags"),
        )
        db.add(sample)
        db.flush()
        ids.append(sample.id)

    db.commit()

    return SampleBulkResponse(inserted=len(ids), ids=ids)


@router.get("/ship/samples", response_model=SampleListResponse)
async def list_samples(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    calc_type: Optional[str] = Query(default=None, description="过滤计算类型"),
    source: Optional[str] = Query(default=None, description="过滤来源"),
    db: Session = Depends(get_db),
):
    """
    分页查询样本。
    """
    query = db.query(ShipSample)

    if calc_type:
        query = query.filter(ShipSample.calc_type == calc_type)
    if source:
        query = query.filter(ShipSample.source == source)

    total = query.count()
    items = (
        query.order_by(ShipSample.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return SampleListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[item.to_dict() for item in items],
    )


@router.get("/ship/timing_baseline", response_model=TimingBaselineResponse)
async def timing_baseline():
    """
    计时基线。

    返回代理层调用的 p50/p90/p99 延迟统计。
    """
    stats = get_timing_stats()
    return TimingBaselineResponse(
        p50=stats["p50"],
        p90=stats["p90"],
        p99=stats["p99"],
        count=stats["count"],
        min=stats.get("min", 0),
        max=stats.get("max", 0),
        mean=stats.get("mean", 0),
    )
