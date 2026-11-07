"""审查预算账本 API（SPEC §6.5 测量先行：四类审查记账 + 周聚合查询）。

端点只做两件事：把**真实发生**的审查/豁免/生产活动逐笔写入只增不改账本；
按 ISO 周把治理活动占生产活动的比例算出来（不限额、不降级、不拦截）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.review_budget_ledger import (
    CONCLUSIONS,
    DURATION_SOURCES,
    REVIEW_TYPE_SPEC,
    REVIEW_TYPES,
    LedgerImmutabilityError,
    LedgerValidationError,
    ReviewBudgetLedger,
    _parse_ts,
    build_weekly_report,
    week_window,
)

router = APIRouter(prefix="/governance/review-budget", tags=["审查预算账本"])


def _ledger() -> ReviewBudgetLedger:
    return ReviewBudgetLedger()


class ReviewEntryIn(BaseModel):
    review_type: str = Field(..., description="execution|technical|outcome|innovation")
    subject: str = Field(..., description="审查对象（工件/分支/报告/提案的通用标识）")
    duration_s: float = Field(0.0, ge=0.0, description="本次审查耗时（秒）")
    conclusion: str = Field("approved", description="审查结论")
    reviewer: str = Field(..., description="审查者（Agent 名/门禁名/人名）")
    duration_source: str = Field("reported", description="observed|reported|not_observable")
    evidence: Optional[Dict[str, Any]] = Field(None, description="证据引用（run_id/sha256/链接）")
    recorded_at: Optional[str] = Field(None, description="发生时刻（ISO8601，缺省=写入时刻）")


class ExemptionIn(BaseModel):
    subject: str
    reason: str = Field(..., description="豁免原因（§6.5：不得静默免审）")
    reviewer: str
    review_type: str = "outcome"
    duration_s: float = 0.0
    recorded_at: Optional[str] = None


class ProductionIn(BaseModel):
    subject: str
    duration_s: float = Field(..., ge=0.0)
    actor: str
    duration_source: str = "observed"
    evidence: Optional[Dict[str, Any]] = None
    recorded_at: Optional[str] = None


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LedgerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LedgerImmutabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/types")
def review_types() -> Dict[str, Any]:
    """四类审查定义与合法取值（记账方与审计方共用同一份口径）。"""
    return {
        "review_types": [
            {"id": review_type, "spec": REVIEW_TYPE_SPEC[review_type]}
            for review_type in REVIEW_TYPES
        ],
        "conclusions": list(CONCLUSIONS),
        "duration_sources": list(DURATION_SOURCES),
        "policy": "measurement_only",
    }


@router.post("/reviews", status_code=201)
def record_review(payload: ReviewEntryIn) -> Dict[str, Any]:
    ledger = _ledger()
    entry = _wrap(
        ledger.record_review,
        payload.review_type,
        payload.subject,
        payload.duration_s,
        payload.conclusion,
        payload.reviewer,
        duration_source=payload.duration_source,
        evidence=payload.evidence,
        recorded_at=payload.recorded_at,
    )
    return {"entry": entry, "chain": ledger.verify_chain()}


@router.post("/exemptions", status_code=201)
def record_exemption(payload: ExemptionIn) -> Dict[str, Any]:
    ledger = _ledger()
    entry = _wrap(
        ledger.record_exemption,
        payload.subject,
        payload.reason,
        payload.reviewer,
        review_type=payload.review_type,
        duration_s=payload.duration_s,
        recorded_at=payload.recorded_at,
    )
    return {"entry": entry, "chain": ledger.verify_chain()}


@router.post("/production", status_code=201)
def record_production(payload: ProductionIn) -> Dict[str, Any]:
    ledger = _ledger()
    entry = _wrap(
        ledger.record_production,
        payload.subject,
        payload.duration_s,
        actor=payload.actor,
        duration_source=payload.duration_source,
        evidence=payload.evidence,
        recorded_at=payload.recorded_at,
    )
    return {"entry": entry, "chain": ledger.verify_chain()}


@router.get("/entries")
def list_entries(
    week_start: Optional[str] = Query(None, description="任意时刻，落到其 ISO 周"),
    review_type: Optional[str] = Query(None),
    kind: Optional[str] = Query(None, description="review|exemption|production"),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    ledger = _ledger()
    rows = ledger.entries()
    if week_start:
        try:
            lo, hi = week_window(week_start)
        except LedgerValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rows = [
            row
            for row in rows
            if (parsed := _parse_ts(row.get("recorded_at"))) is not None and lo <= parsed < hi
        ]
    if review_type:
        rows = [row for row in rows if row.get("review_type") == review_type]
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]
    return {"total": len(rows), "items": rows[-limit:], "chain": ledger.verify_chain()}


@router.get("/verify")
def verify() -> Dict[str, Any]:
    ledger = _ledger()
    return {"path": str(ledger.path), "chain": ledger.verify_chain()}


@router.get("/weekly")
def weekly(
    week_start: Optional[str] = Query(None, description="缺省=最近一个已结束 ISO 周"),
) -> Dict[str, Any]:
    try:
        return build_weekly_report(_ledger(), week_start)
    except LedgerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LedgerImmutabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc