"""
M406 · 执行谱系追溯 API
──────────────────────
GET  /lineage/{execution_id}        → 返回完整谱系链
POST /lineage/auto_record           → execution 完成后自动记录谱系
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db
from models.lineage import LineageRecord, _compute_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lineage", tags=["执行谱系追溯"])


# ─────────────────────── Pydantic Schemas ───────────────────────


class AutoRecordRequest(BaseModel):
    """POST /lineage/auto_record 请求体"""
    execution_id: int = Field(..., ge=1, description="tensor_executions.id")
    config: Optional[dict] = Field(default=None, description="运行配置")
    env: Optional[dict] = Field(default=None, description="环境信息")
    data: Optional[dict] = Field(default=None, description="数据指纹")
    params: Optional[dict] = Field(default=None, description="参数集")
    extra_metadata: Optional[dict] = Field(default=None, description="扩展元数据")


class LineageChainItem(BaseModel):
    """谱系链中单条记录"""
    id: str
    execution_id: int
    config_hash: str
    env_hash: str
    data_hash: str
    param_hash: str
    scenario_key: str
    stage: str
    status: str
    extra_metadata: dict
    created_at: Optional[str] = None


# ─────────────────────── Helper ───────────────────────


def _load_execution_row(db: Session, execution_id: int) -> dict:
    """从 tensor_executions 表加载执行记录。"""
    row = db.execute(
        text("""
            SELECT id, execution_key, scenario_key, stage, status,
                   budget, result_summary, created_at, finished_at
            FROM tensor_executions WHERE id = :eid
        """),
        {"eid": execution_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"execution {execution_id} not found")
    return dict(row)


# ─────────────────────── GET /lineage/{execution_id} ───────────────────────


@router.get("/{execution_id}", summary="获取执行谱系链")
def get_lineage_chain(
    execution_id: int,
    db: Session = Depends(get_db),
):
    """
    返回指定 execution 的完整谱系链。
    包含：本条谱系记录 + 同 scenario_key 的历史谱系（追溯链）。
    """
    # 1. 查找本条记录
    record = db.query(LineageRecord).filter(
        LineageRecord.execution_id == execution_id
    ).first()

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"lineage record for execution {execution_id} not found",
        )

    # 2. 查找同 scenario_key 的历史谱系（按时间正序，构成追溯链）
    chain_rows = db.query(LineageRecord).filter(
        LineageRecord.scenario_key == record.scenario_key,
        LineageRecord.scenario_key != "",
    ).order_by(LineageRecord.created_at.asc()).all()

    chain = [r.to_dict() for r in chain_rows]

    return {
        "execution_id": execution_id,
        "current": record.to_dict(),
        "chain": chain,
        "chain_length": len(chain),
    }


# ─────────────────────── POST /lineage/auto_record ───────────────────────


@router.post("/auto_record", summary="自动记录执行谱系")
def auto_record_lineage(
    body: AutoRecordRequest,
    db: Session = Depends(get_db),
):
    """
    execution 完成后调用此端点，自动计算各维度哈希并持久化谱系记录。
    幂等：同一 execution_id 重复调用会更新而非重复插入。
    """
    # 1. 验证 execution 存在
    exec_row = _load_execution_row(db, body.execution_id)

    # 2. 计算哈希
    config_hash = _compute_hash(body.config)
    env_hash = _compute_hash(body.env)
    data_hash = _compute_hash(body.data)
    param_hash = _compute_hash(body.params)

    # 3. 幂等 upsert
    existing = db.query(LineageRecord).filter(
        LineageRecord.execution_id == body.execution_id
    ).first()

    if existing:
        existing.config_hash = config_hash
        existing.env_hash = env_hash
        existing.data_hash = data_hash
        existing.param_hash = param_hash
        existing.scenario_key = exec_row.get("scenario_key", "")
        existing.stage = exec_row.get("stage", "")
        existing.status = exec_row.get("status", "")
        existing.extra_metadata = body.extra_metadata or {}
        db.commit()
        db.refresh(existing)
        return {
            "status": "updated",
            "record": existing.to_dict(),
        }
    else:
        new_record = LineageRecord(
            execution_id=body.execution_id,
            config_hash=config_hash,
            env_hash=env_hash,
            data_hash=data_hash,
            param_hash=param_hash,
            scenario_key=exec_row.get("scenario_key", ""),
            stage=exec_row.get("stage", ""),
            status=exec_row.get("status", ""),
            extra_metadata=body.extra_metadata or {},
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        return {
            "status": "created",
            "record": new_record.to_dict(),
        }
