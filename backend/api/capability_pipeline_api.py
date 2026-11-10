"""
能力台账自动化证据链 — 全链路管道 API

端点:
- POST /tensor/capabilities/pipeline/run       运行全链路管道
- GET  /tensor/capabilities/pipeline/acceptance/{run_id}  获取验收报告
- POST /tensor/capabilities/pipeline/verify/c2  验证 C2 准确率
- POST /tensor/capabilities/pipeline/verify/c3  验证 C3 SQL 溯源
- POST /tensor/capabilities/pipeline/verify/c4  验证 C4 负控
- POST /tensor/capabilities/pipeline/verify/c5  验证 C5 变更历史
- POST /tensor/capabilities/pipeline/verify/c6  验证 C6 性能
- POST /tensor/capabilities/pipeline/verify/c7  验证 C7 一致性
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import require_auth
from services.capability_pipeline import CapabilityPipeline

router = APIRouter(prefix="/tensor/capabilities/pipeline", tags=["能力证据链全链路管道"])
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/capability_evidence.db"
DEFAULT_LEDGER_PATH = "artifacts/capability_ledger/capability_ledger.json"


def get_pipeline() -> CapabilityPipeline:
    """获取管道实例。"""
    p = CapabilityPipeline(db_path=DEFAULT_DB_PATH, ledger_path=DEFAULT_LEDGER_PATH)
    p.init()
    return p


# ── 请求/响应模型 ─────────────────────────────────────────────────────


class PipelineRunRequest(BaseModel):
    capabilities: list[dict[str, Any]] = Field(..., description="能力列表")
    evidence_sources: list[dict[str, Any]] | None = Field(None, description="证据来源列表")
    run_id: str | None = Field(None, description="运行批次 ID")


class PipelineRunResponse(BaseModel):
    status: str
    run_id: str
    capabilities_processed: int
    evidence_collected: int
    adjudications: list[dict[str, Any]]
    ledger_changes: list[dict[str, Any]]
    summary: dict[str, Any]
    c1_automated: bool
    c6_performance_seconds: float


class AcceptanceResponse(BaseModel):
    status: str
    run_id: str
    c1_automated: bool
    c2_accuracy: dict[str, Any]
    c3_sql_trace: dict[str, Any]
    c4_negative_control: dict[str, Any]
    c5_change_history: dict[str, Any]
    c6_performance: dict[str, Any]
    c7_consistency: dict[str, Any]
    all_passed: bool


class VerifyRequest(BaseModel):
    capability_id: str = Field(..., description="能力 ID")
    run_id: str | None = Field(None, description="运行批次 ID（C2 用）")
    runs: int = Field(3, description="重复运行次数（C7 用）")


class VerifyResponse(BaseModel):
    status: str
    check: str
    passed: bool
    result: dict[str, Any]


# ── 端点实现 ──────────────────────────────────────────────────────────


@router.post("/run", response_model=PipelineRunResponse)
def run_pipeline(
    request: PipelineRunRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/run

    运行全链路管道：注册→采集→裁定→升级→溯源。
    """
    try:
        result = pipeline.run_full_pipeline(
            capabilities=request.capabilities,
            evidence_sources=request.evidence_sources,
            run_id=request.run_id,
        )
        return PipelineRunResponse(
            status="ok",
            run_id=result["run_id"],
            capabilities_processed=result["capabilities_processed"],
            evidence_collected=result["evidence_collected"],
            adjudications=result["adjudications"],
            ledger_changes=result["ledger_changes"],
            summary=result["summary"],
            c1_automated=result["c1_automated"],
            c6_performance_seconds=result["c6_performance_seconds"],
        )
    except Exception as e:
        logger.error(f"Error running pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/acceptance/{run_id}", response_model=AcceptanceResponse)
def get_acceptance(
    run_id: str,
    capability_ids: str = "",  # 逗号分隔的能力 ID 列表
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/pipeline/acceptance/{run_id}

    获取综合验收报告（C1-C7）。
    """
    cap_ids = [c.strip() for c in capability_ids.split(",") if c.strip()] if capability_ids else []

    try:
        report = pipeline.generate_acceptance_report(run_id, cap_ids)
        return AcceptanceResponse(
            status="ok",
            run_id=run_id,
            c1_automated=report["c1_automated"],
            c2_accuracy=report["c2_accuracy"],
            c3_sql_trace=report["c3_sql_trace"],
            c4_negative_control=report["c4_negative_control"],
            c5_change_history=report["c5_change_history"],
            c6_performance=report["c6_performance"],
            c7_consistency=report["c7_consistency"],
            all_passed=report["all_passed"],
        )
    except Exception as e:
        logger.error(f"Error generating acceptance report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify/c2", response_model=VerifyResponse)
def verify_c2(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c2

    验证 C2: 等级判定准确率 ≥ 95%。
    """
    if not request.run_id:
        raise HTTPException(status_code=400, detail="run_id is required for C2 verification")

    result = pipeline.verify_accuracy(request.run_id)
    return VerifyResponse(
        status="ok",
        check="C2_accuracy",
        passed=result["passed"],
        result=result,
    )


@router.post("/verify/c3", response_model=VerifyResponse)
def verify_c3(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c3

    验证 C3: SQL 溯源。
    """
    result = pipeline.verify_sql_trace(request.capability_id)
    return VerifyResponse(
        status="ok",
        check="C3_sql_trace",
        passed=result["traceable"],
        result=result,
    )


@router.post("/verify/c4", response_model=VerifyResponse)
def verify_c4(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c4

    验证 C4: 负控（无证据→WITHHELD）。
    """
    result = pipeline.verify_negative_control(request.capability_id)
    return VerifyResponse(
        status="ok",
        check="C4_negative_control",
        passed=result["passed"],
        result=result,
    )


@router.post("/verify/c5", response_model=VerifyResponse)
def verify_c5(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c5

    验证 C5: 变更历史。
    """
    result = pipeline.verify_change_history(request.capability_id)
    return VerifyResponse(
        status="ok",
        check="C5_change_history",
        passed=result["has_history"],
        result=result,
    )


@router.post("/verify/c6", response_model=VerifyResponse)
def verify_c6(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c6

    验证 C6: 性能（<5 分钟）。
    """
    result = pipeline.verify_performance(request.capability_id)
    return VerifyResponse(
        status="ok",
        check="C6_performance",
        passed=result["passed"],
        result=result,
    )


@router.post("/verify/c7", response_model=VerifyResponse)
def verify_c7(
    request: VerifyRequest,
    pipeline: CapabilityPipeline = Depends(get_pipeline),
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/pipeline/verify/c7

    验证 C7: 一致性。
    """
    result = pipeline.verify_consistency(request.capability_id, runs=request.runs)
    return VerifyResponse(
        status="ok",
        check="C7_consistency",
        passed=result["consistent"],
        result=result,
    )
