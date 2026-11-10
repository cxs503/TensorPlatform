"""
能力台账自动化证据链 API

端点:
- POST /tensor/capabilities/evidence/collect    采集证据
- POST /tensor/capabilities/evidence/adjudicate 裁定等级
- POST /tensor/capabilities/evidence/upgrade    应用升级
- POST /tensor/capabilities/evidence/reevaluate 全量重评估
- GET  /tensor/capabilities/evidence/audit-log   查询审计日志
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import require_auth
from database.config import get_db
from services.capability_evidence_chain import (
    AdjudicationEngine,
    EvidenceBundle,
    EvidenceCollector,
    OverclaimGate,
    UpgradeWorkflow,
    reevaluate_all_capabilities,
)

router = APIRouter(prefix="/tensor/capabilities/evidence", tags=["能力证据链"])
logger = logging.getLogger(__name__)

# ── 请求/响应模型 ─────────────────────────────────────────────────────


class CollectRequest(BaseModel):
    source_type: str = Field(..., description="证据来源类型: pytest/benchmark/report/directory")
    source_path: str = Field(..., description="证据文件/目录路径")
    thresholds: dict[str, Any] = Field(default_factory=dict, description="自定义阈值")


class CollectResponse(BaseModel):
    status: str
    bundles: list[dict[str, Any]]
    total_capabilities: int
    total_items: int


class AdjudicateRequest(BaseModel):
    capability_id: str = Field(..., description="能力 ID")
    evidence: dict[str, Any] | None = Field(None, description="证据数据")
    implementation_info: dict[str, Any] | None = Field(None, description="实现信息")
    thresholds: dict[str, Any] = Field(default_factory=dict, description="自定义阈值")


class AdjudicateResponse(BaseModel):
    status: str
    result: dict[str, Any]


class UpgradeRequest(BaseModel):
    capability_id: str = Field(..., description="能力 ID")
    evidence_grade: str = Field(..., description="证据等级")
    evidence: dict[str, Any] | None = Field(None, description="证据数据")
    run_id: str | None = Field(None, description="运行 ID")
    ledger_path: str | None = Field(None, description="台账路径")


class UpgradeResponse(BaseModel):
    status: str
    result: dict[str, Any]


class ReevaluateRequest(BaseModel):
    ledger_path: str | None = Field(None, description="台账路径")
    evidence_dir: str | None = Field(None, description="证据目录")
    thresholds: dict[str, Any] = Field(default_factory=dict, description="自定义阈值")
    run_id: str | None = Field(None, description="运行 ID")


class ReevaluateResponse(BaseModel):
    status: str
    result: dict[str, Any]


class AuditLogRequest(BaseModel):
    limit: int = Field(100, description="返回条数限制")
    offset: int = Field(0, description="偏移量")
    capability_id: str | None = Field(None, description="按能力 ID 过滤")


class AuditLogResponse(BaseModel):
    status: str
    entries: list[dict[str, Any]]
    total: int


# ── 端点实现 ──────────────────────────────────────────────────────────


@router.post("/collect", response_model=CollectResponse)
def collect_evidence(
    request: CollectRequest,
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/evidence/collect

    从指定来源采集证据。
    """
    collector = EvidenceCollector(thresholds=request.thresholds)

    try:
        if request.source_type == "pytest":
            bundles = collector.collect_from_pytest(request.source_path)
        elif request.source_type == "benchmark":
            bundles = collector.collect_from_benchmark(request.source_path)
        elif request.source_type == "report":
            bundles = collector.collect_from_report(request.source_path)
        elif request.source_type == "directory":
            bundles = collector.collect_from_directory(request.source_path)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source type: {request.source_type}")
    except Exception as e:
        logger.error(f"Error collecting evidence: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    total_items = sum(len(b.items) for b in bundles)

    return CollectResponse(
        status="ok",
        bundles=[b.to_dict() for b in bundles],
        total_capabilities=len(bundles),
        total_items=total_items,
    )


@router.post("/adjudicate", response_model=AdjudicateResponse)
def adjudicate_capability(
    request: AdjudicateRequest,
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/evidence/adjudicate

    对指定能力进行证据等级裁定。
    """
    engine = AdjudicationEngine(thresholds=request.thresholds)

    # 转换证据数据
    evidence = None
    if request.evidence:
        if "items" in request.evidence:
            # EvidenceBundle 格式
            evidence = EvidenceBundle(
                capability_id=request.capability_id,
                items=[],  # 简化处理，实际应从 items 重建
            )
        else:
            # 简单证据列表
            evidence = request.evidence

    try:
        result = engine.adjudicate(
            request.capability_id,
            evidence,
            request.implementation_info,
        )
    except Exception as e:
        logger.error(f"Error adjudicating capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return AdjudicateResponse(
        status="ok",
        result=result.to_dict(),
    )


@router.post("/upgrade", response_model=UpgradeResponse)
def upgrade_capability(
    request: UpgradeRequest,
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/evidence/upgrade

    应用升级：根据证据等级更新台账。
    """
    ledger_path = request.ledger_path or "artifacts/capability_ledger/capability_ledger.json"
    workflow = UpgradeWorkflow(ledger_path=ledger_path)

    try:
        result = workflow.apply_upgrade(
            capability_id=request.capability_id,
            evidence_grade=request.evidence_grade,
            evidence=request.evidence,
            run_id=request.run_id,
        )
    except Exception as e:
        logger.error(f"Error upgrading capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return UpgradeResponse(
        status="ok",
        result=result,
    )


@router.post("/reevaluate", response_model=ReevaluateResponse)
def reevaluate_capabilities(
    request: ReevaluateRequest,
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/evidence/reevaluate

    全量重评估：对台账中所有能力进行自动化重评估。
    """
    ledger_path = request.ledger_path or "artifacts/capability_ledger/capability_ledger.json"

    try:
        result = reevaluate_all_capabilities(
            ledger_path=ledger_path,
            evidence_dir=request.evidence_dir,
            thresholds=request.thresholds,
            run_id=request.run_id,
        )
    except Exception as e:
        logger.error(f"Error reevaluating capabilities: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return ReevaluateResponse(
        status="ok",
        result=result,
    )


@router.post("/audit-log", response_model=AuditLogResponse)
def get_audit_log(
    request: AuditLogRequest,
    user: dict = Depends(require_auth),
):
    """
    POST /tensor/capabilities/evidence/audit-log

    查询审计日志。
    """
    audit_log_path = Path("artifacts/capability_ledger/audit_log.jsonl")

    if not audit_log_path.exists():
        return AuditLogResponse(
            status="ok",
            entries=[],
            total=0,
        )

    try:
        lines = audit_log_path.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in lines:
            if line.strip():
                entry = json.loads(line)
                if request.capability_id is None or entry.get("capability_id") == request.capability_id:
                    entries.append(entry)

        total = len(entries)
        entries = entries[request.offset : request.offset + request.limit]

        return AuditLogResponse(
            status="ok",
            entries=entries,
            total=total,
        )
    except Exception as e:
        logger.error(f"Error reading audit log: {e}")
        raise HTTPException(status_code=500, detail=str(e))
