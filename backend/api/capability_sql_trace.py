"""
能力台账自动化证据链 — SQL 溯源 API

端点:
- POST /tensor/capabilities/sql/register        注册能力
- POST /tensor/capabilities/sql/register-batch  批量注册
- GET  /tensor/capabilities/sql/list            列出所有能力
- GET  /tensor/capabilities/sql/trace/{id}      获取能力溯源
- GET  /tensor/capabilities/sql/evidence/{id}   获取证据明细
- GET  /tensor/capabilities/sql/history/{id}    获取变更历史
- GET  /tensor/capabilities/sql/verify-hash/{id} 验证哈希链
- GET  /tensor/capabilities/sql/distribution    获取等级分布
- GET  /tensor/capabilities/sql/accuracy/{run}  获取准确率报告
- GET  /tensor/capabilities/sql/export/{id}     导出 SQL 溯源
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import require_auth
from services.capability_sql_tracer import CapabilitySQLTracer

router = APIRouter(prefix="/tensor/capabilities/sql", tags=["能力证据链 SQL 溯源"])
logger = logging.getLogger(__name__)

# 默认数据库路径
DEFAULT_DB_PATH = "data/capability_evidence.db"


def get_tracer() -> CapabilitySQLTracer:
    """获取 SQL 溯源器实例。"""
    tracer = CapabilitySQLTracer(db_path=DEFAULT_DB_PATH)
    tracer.init_schema()
    return tracer


# ── 请求/响应模型 ─────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    capability_id: str = Field(..., description="能力唯一标识")
    description: str = Field("", description="能力描述")
    domain: str = Field("", description="所属领域（通用标签）")
    claimed_level: str = Field("WITHHELD", description="自述等级")
    expected_level: str = Field("WITHHELD", description="预期等级")
    metadata: dict[str, Any] | None = Field(None, description="附加元数据")


class RegisterResponse(BaseModel):
    status: str
    capability_id: str
    registered_at: str | None = None
    updated_at: str | None = None


class RegisterBatchRequest(BaseModel):
    capabilities: list[dict[str, Any]] = Field(..., description="能力列表")


class RegisterBatchResponse(BaseModel):
    status: str
    results: list[dict[str, Any]]
    total: int
    created: int
    updated: int
    errors: int


class TraceResponse(BaseModel):
    status: str
    trace: dict[str, Any] | None


class EvidenceResponse(BaseModel):
    status: str
    capability_id: str
    evidence_count: int
    details: list[dict[str, Any]]


class HistoryResponse(BaseModel):
    status: str
    capability_id: str
    count: int
    entries: list[dict[str, Any]]
    hash_chain_valid: bool


class HashVerifyResponse(BaseModel):
    status: str
    capability_id: str
    valid: bool
    total: int
    broken_at: int | None


class DistributionResponse(BaseModel):
    status: str
    distribution: dict[str, int]


class AccuracyResponse(BaseModel):
    status: str
    run_id: str
    total: int
    correct: int
    accuracy: float
    passed: bool
    details: list[dict[str, Any]]


class ExportResponse(BaseModel):
    status: str
    capability_id: str
    sql_trace: str


# ── 端点实现 ──────────────────────────────────────────────────────────


@router.post("/register", response_model=RegisterResponse)
def register_capability(
    request: RegisterRequest,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/sql/register

    注册新能力到台账。
    """
    try:
        result = tracer.register_capability(
            capability_id=request.capability_id,
            description=request.description,
            domain=request.domain,
            claimed_level=request.claimed_level,
            expected_level=request.expected_level,
            metadata=request.metadata,
        )
        return RegisterResponse(
            status=result["status"],
            capability_id=result["capability_id"],
            registered_at=result.get("registered_at"),
            updated_at=result.get("updated_at"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error registering capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/register-batch", response_model=RegisterBatchResponse)
def register_batch(
    request: RegisterBatchRequest,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/sql/register-batch

    批量注册能力。
    """
    results = []
    created = 0
    updated = 0
    errors = 0

    for cap in request.capabilities:
        try:
            result = tracer.register_capability(
                capability_id=cap.get("capability_id", cap.get("id", "")),
                description=cap.get("description", ""),
                domain=cap.get("domain", ""),
                claimed_level=cap.get("claimed_level", "WITHHELD"),
                expected_level=cap.get("expected_level", "WITHHELD"),
                metadata=cap.get("metadata"),
            )
            results.append(result)
            if result["status"] == "created":
                created += 1
            else:
                updated += 1
        except Exception as e:
            results.append({"status": "error", "error": str(e), "input": cap})
            errors += 1

    return RegisterBatchResponse(
        status="ok",
        results=results,
        total=len(request.capabilities),
        created=created,
        updated=updated,
        errors=errors,
    )


@router.get("/list")
def list_capabilities(
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/list

    列出所有已注册能力。
    """
    try:
        caps = tracer.list_capabilities()
        return {"status": "ok", "capabilities": caps, "total": len(caps)}
    except Exception as e:
        logger.error(f"Error listing capabilities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trace/{capability_id}", response_model=TraceResponse)
def get_trace(
    capability_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/trace/{capability_id}

    获取能力最新溯源信息。
    """
    trace = tracer.get_capability_trace(capability_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Capability not found: {capability_id}")

    return TraceResponse(status="ok", trace=trace)


@router.get("/evidence/{capability_id}", response_model=EvidenceResponse)
def get_evidence(
    capability_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/evidence/{capability_id}

    获取能力所有证据明细。
    """
    details = tracer.get_evidence_details(capability_id)
    return EvidenceResponse(
        status="ok",
        capability_id=capability_id,
        evidence_count=len(details),
        details=details,
    )


@router.get("/history/{capability_id}", response_model=HistoryResponse)
def get_history(
    capability_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/history/{capability_id}

    获取能力台账变更历史。
    """
    history = tracer.get_change_history(capability_id)
    hash_check = tracer.verify_hash_chain(capability_id)

    return HistoryResponse(
        status="ok",
        capability_id=capability_id,
        count=len(history),
        entries=history,
        hash_chain_valid=hash_check["valid"],
    )


@router.get("/verify-hash/{capability_id}", response_model=HashVerifyResponse)
def verify_hash(
    capability_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/verify-hash/{capability_id}

    验证台账变更哈希链完整性。
    """
    result = tracer.verify_hash_chain(capability_id)
    return HashVerifyResponse(
        status="ok",
        capability_id=capability_id,
        valid=result["valid"],
        total=result["total"],
        broken_at=result["broken_at"],
    )


@router.get("/distribution", response_model=DistributionResponse)
def get_distribution(
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/distribution

    获取各等级能力数量分布。
    """
    dist = tracer.get_grade_distribution()
    return DistributionResponse(status="ok", distribution=dist)


@router.get("/accuracy/{run_id}", response_model=AccuracyResponse)
def get_accuracy(
    run_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/accuracy/{run_id}

    获取指定 run 的裁定准确率报告。
    """
    report = tracer.get_accuracy_report(run_id)
    return AccuracyResponse(
        status="ok",
        run_id=run_id,
        total=report["total"],
        correct=report["correct"],
        accuracy=report["accuracy"],
        passed=report["accuracy"] >= 0.95,
        details=report["details"],
    )


@router.get("/export/{capability_id}", response_model=ExportResponse)
def export_trace(
    capability_id: str,
    tracer: CapabilitySQLTracer = Depends(get_tracer),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/sql/export/{capability_id}

    导出能力的完整 SQL 溯源语句。
    """
    trace = tracer.get_capability_trace(capability_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Capability not found: {capability_id}")

    sql_trace = tracer.export_sql_trace(capability_id)
    return ExportResponse(
        status="ok",
        capability_id=capability_id,
        sql_trace=sql_trace,
    )
