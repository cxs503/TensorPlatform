"""Proposal Gate API — 提案→战役播种供给链机器门。

Endpoints:
  POST /proposals/gate/validate   — Schema 校验（fail-closed）
  POST /proposals/gate/submit     — 完整三道门处理
  GET  /proposals/gate/stats      — 播种覆盖率账本统计
  GET  /proposals/gate/ledger     — 账本查询
  POST /proposals/gate/dedup-check — 单独去重检查

零领域语义：所有域词由配置文件注入，代码不含任何领域词条。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.proposal_gate_service import (
    SchemaValidationError,
    compute_proposal_signature,
    ensure_ledger_table,
    find_duplicate_proposal,
    get_ledger_stats,
    process_proposal,
    validate_proposal_schema,
    _load_config,
)

router = APIRouter(prefix="/proposals/gate", tags=["提案供给链机器门"])


# ───────────────────────── Request/Response Models ─────────────────────────

class ProposalSubmit(BaseModel):
    """提案提交请求体。"""
    proposal: dict = Field(..., description="提案内容（JSON）")
    schema_version: str | None = Field(default=None, description="Schema 版本（v1/v2）")
    mission_id: int | None = Field(default=None, description="关联任务 ID")
    plan_id: int | None = Field(default=None, description="关联战役计划 ID")


class ProposalValidate(BaseModel):
    """Schema 校验请求体。"""
    proposal: dict = Field(..., description="提案内容（JSON）")
    schema_version: str | None = Field(default=None, description="Schema 版本（v1/v2）")


class DedupCheckRequest(BaseModel):
    """去重检查请求体。"""
    proposal: dict = Field(..., description="提案内容（JSON）")


# ───────────────────────── Startup: Ensure Table ─────────────────────────

@router.on_event("startup")
async def _ensure_table():
    """Ensure ledger table exists on startup."""
    try:
        from database.config import SessionLocal
        db = SessionLocal()
        try:
            ensure_ledger_table(db)
        finally:
            db.close()
    except Exception:
        pass  # Table creation is best-effort at startup


# ───────────────────────── Gate 1: Schema Validation ─────────────────────────

@router.post("/validate", dependencies=[Depends(require_auth)])
def validate_schema(
    body: ProposalValidate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Schema 校验（fail-closed）。
    
    校验规则：
    - JSON Schema 结构校验（v1/v2）
    - title 非空
    - repo ∈ 白名单
    - goals ≥ 3 条且每条含可量化阈值
    - is_maturity 为 bool（v2）
    
    失败 → 返回错误详情，不入库。
    """
    try:
        normalized = validate_proposal_schema(body.proposal, body.schema_version)
        return {
            "valid": True,
            "schema_version": "v2",  # After normalization
            "normalized_proposal": normalized,
        }
    except SchemaValidationError as e:
        return {
            "valid": False,
            "schema_version": e.schema_version,
            "errors": e.errors,
        }


# ───────────────────────── Gate 2+3: Full Processing ─────────────────────────

@router.post("/submit", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def submit_proposal(
    body: ProposalSubmit,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    完整三道门处理：Schema 校验 → 去重 → 入库。
    
    返回 verdict: accepted / quarantined / duplicate
    """
    org_id = resolved_org(None, user, db)
    
    # Ensure table exists
    ensure_ledger_table(db)
    
    # Process through all gates
    result = process_proposal(
        db=db,
        organization_id=org_id,
        mission_id=body.mission_id,
        plan_id=body.plan_id,
        proposal=body.proposal,
        schema_version=body.schema_version,
    )
    
    return result


# ───────────────────────── Dedup Check ─────────────────────────

@router.post("/dedup-check", dependencies=[Depends(require_auth)])
def check_duplicate(
    body: DedupCheckRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    单独去重检查（不入库）。
    
    返回是否重复及相似度分数。
    """
    org_id = resolved_org(None, user, db)
    
    # First validate schema
    try:
        normalized = validate_proposal_schema(body.proposal)
    except SchemaValidationError as e:
        raise HTTPException(400, f"Schema validation failed: {'; '.join(e.errors)}")
    
    # Check for duplicates
    duplicate = find_duplicate_proposal(normalized, db, org_id)
    
    if duplicate:
        return {
            "is_duplicate": True,
            "dup_of": duplicate["sha256"],
            "similarity_score": duplicate.get("similarity_score"),
            "existing_mission_id": duplicate.get("mission_id"),
        }
    else:
        return {
            "is_duplicate": False,
            "signature": compute_proposal_signature(normalized),
        }


# ───────────────────────── Ledger Stats ─────────────────────────

@router.get("/stats", dependencies=[Depends(require_auth)])
def ledger_stats(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    播种覆盖率账本统计。
    
    返回四态计数（accepted/rejected/quarantined/duplicate）与重复率。
    """
    org_id = resolved_org(None, user, db)
    
    # Ensure table exists
    ensure_ledger_table(db)
    
    stats = get_ledger_stats(db, org_id)
    return stats


# ───────────────────────── Ledger Query ─────────────────────────

@router.get("/ledger", dependencies=[Depends(require_auth)])
def query_ledger(
    verdict: str | None = Query(default=None, description="筛选 verdict"),
    repo: str | None = Query(default=None, description="筛选 repo"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    账本查询。
    
    支持按 verdict/repo 筛选，分页返回。
    """
    org_id = resolved_org(None, user, db)
    
    # Ensure table exists
    ensure_ledger_table(db)
    
    # Build query
    conditions = ["organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit, "offset": offset}
    
    if verdict:
        conditions.append("verdict = :verdict")
        params["verdict"] = verdict
    if repo:
        conditions.append("repo = :repo")
        params["repo"] = repo
    
    where_clause = " AND ".join(conditions)
    
    rows = db.execute(
        text(f"""
            SELECT id, mission_id, plan_id, sha256, schema_version, repo, title,
                   verdict, dup_of, score, at
            FROM proposal_gate_ledger
            WHERE {where_clause}
            ORDER BY at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()
    
    # Count total
    count_result = db.execute(
        text(f"SELECT COUNT(*) as cnt FROM proposal_gate_ledger WHERE {where_clause}"),
        params,
    ).mappings().first()
    
    items = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("at"), datetime):
            item["at"] = item["at"].isoformat()
        items.append(item)
    
    return {
        "items": items,
        "total": count_result["cnt"],
        "limit": limit,
        "offset": offset,
    }


# ───────────────────────── Config Introspection ─────────────────────────

@router.get("/config", dependencies=[Depends(require_role("ADMIN"))])
def get_config(
    user: dict = Depends(require_auth),
):
    """
    查看机器门配置（管理员专用）。
    
    返回阈值、白名单等配置信息。
    """
    config = _load_config()
    return config
