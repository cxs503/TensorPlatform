"""否定性知识台账 API。

端点（prefix ``/tensor/negative-knowledge``）：

- ``GET  /``                       台账条目列表（按状态/类型/mission 过滤）
- ``GET  /stats``                  统计（按失败类型/状态分布）
- ``GET  /search``                 检索相似否定性知识（语义检索）
- ``GET  /{entry_id}``             单条详情
- ``POST /register``               登记一条否定性知识
- ``POST /gate``                   派单前检索门（返回判决+命中列表）
- ``POST /harvest``                从 mission 的 FAIL 节点自动收割
- ``PATCH /{entry_id}/status``     更新状态（superseded/retracted）
- ``GET  /health``                 健康检查

纪律：本模块零领域语义——schema 通用，域数据经配置/工件注入。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from database.config import get_db

from tools.negative_knowledge import (
    NegativeKnowledgeConfig,
    NegativeKnowledgeSearcher,
    NegativeKnowledgeStore,
    load_config,
)
from tools.negative_knowledge.store import FAILURE_KINDS, STATUSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/negative-knowledge", tags=["否定性知识台账"])

# 缓存 store 实例（按 engine id）
_STORE_CACHE: dict[int, NegativeKnowledgeStore] = {}
_CONFIG_CACHE: NegativeKnowledgeConfig | None = None


def _config() -> NegativeKnowledgeConfig:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = load_config()
    return _CONFIG_CACHE


def _store(db: Session) -> NegativeKnowledgeStore:
    cfg = _config()
    engine = db.get_bind()
    key = id(engine)
    store = _STORE_CACHE.get(key)
    if store is None:
        store = NegativeKnowledgeStore.for_engine(engine, cfg)
        _STORE_CACHE[key] = store
    return store


def _searcher(db: Session) -> NegativeKnowledgeSearcher:
    return NegativeKnowledgeSearcher(_store(db), _config())


# ─────────────────────────────── 请求体 ───────────────────────────────


class RegisterRequest(BaseModel):
    """登记否定性知识条目。"""

    title: str = Field(min_length=3, max_length=500, description="假设/失败标题")
    hypothesis: str = Field(min_length=10, description="被证伪的假设描述")
    failure_kind: str = Field(description=f"失败类型：{', '.join(FAILURE_KINDS)}")
    mission_id: int | None = Field(default=None, description="来源 mission")
    plan_id: int | None = Field(default=None, description="来源战役计划")
    node_id: str | None = Field(default=None, max_length=128, description="来源 DAG 节点")
    falsification_evidence: str | None = Field(default=None, description="证伪证据")
    failure_reason: str | None = Field(default=None, description="失败根因")
    applicable_conditions: dict[str, Any] | None = Field(default=None, description="适用条件边界")
    keywords: list[str] | None = Field(default=None, description="关键词")
    domain_tags: list[str] | None = Field(default=None, description="域标签")
    differentiation_required: list[str] | None = Field(default=None, description="后续提案需声明的差异点")
    source_artifacts: list[str] | None = Field(default=None, description="来源工件路径")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="证伪置信度")
    registered_by: str | None = Field(default=None, max_length=128, description="登记者标识")


class GateRequest(BaseModel):
    """派单前检索门请求。"""

    proposal_title: str = Field(min_length=3, max_length=500, description="提案标题")
    proposal_description: str = Field(default="", max_length=5000, description="提案描述")
    proposal_keywords: list[str] | None = Field(default=None, description="提案关键词")
    mission_id: int | None = Field(default=None, description="提案所属 mission（用于引用登记）")


class HarvestRequest(BaseModel):
    """从 mission 自动收割 FAIL 节点。"""

    mission_id: int = Field(ge=1, description="mission ID")
    plan_id: int | None = None
    node_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=3, max_length=500)
    hypothesis: str = Field(min_length=10)
    failure_kind: str = Field(description=f"失败类型：{', '.join(FAILURE_KINDS)}")
    falsification_evidence: str | None = None
    failure_reason: str | None = None
    applicable_conditions: dict[str, Any] | None = None
    keywords: list[str] | None = None
    domain_tags: list[str] | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class StatusUpdateRequest(BaseModel):
    """更新条目状态。"""

    status: str = Field(description=f"目标状态：{', '.join(STATUSES)}")
    reason: str | None = Field(default=None, max_length=500, description="状态变更原因")


# ─────────────────────────────── 读面 ───────────────────────────────


@router.get("", dependencies=[Depends(require_auth)])
def list_entries(
    status: str | None = Query(default=None, description=f"状态过滤：{', '.join(STATUSES)}"),
    failure_kind: str | None = Query(default=None, description=f"失败类型过滤：{', '.join(FAILURE_KINDS)}"),
    mission_id: int | None = Query(default=None, description="按 mission 过滤"),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """台账条目列表。"""
    if status and status not in STATUSES:
        raise HTTPException(422, f"status 非法：{status}，允许值：{STATUSES}")
    if failure_kind and failure_kind not in FAILURE_KINDS:
        raise HTTPException(422, f"failure_kind 非法：{failure_kind}，允许值：{FAILURE_KINDS}")

    store = _store(db)
    entries = store.list_entries(status=status, failure_kind=failure_kind, mission_id=mission_id, limit=limit)
    return {
        "total": len(entries),
        "items": [e.as_dict() for e in entries],
        "failure_kinds": list(FAILURE_KINDS),
        "statuses": list(STATUSES),
    }


@router.get("/stats", dependencies=[Depends(require_auth)])
def stats(db: Session = Depends(get_db)):
    """统计信息。"""
    store = _store(db)
    return store.stats()


@router.get("/search", dependencies=[Depends(require_auth)])
def search(
    q: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(default=5, ge=1, le=50),
    min_score: float = Query(default=0.2, ge=0.0, le=1.0),
    failure_kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """检索相似否定性知识。"""
    searcher = _searcher(db)
    hits = searcher.search(q, top_k=top_k, min_score=min_score, failure_kind=failure_kind)
    return {
        "query": q,
        "hits": [h.as_dict() for h in hits],
        "count": len(hits),
    }


@router.get("/{entry_id}", dependencies=[Depends(require_auth)])
def get_entry(entry_id: str, db: Session = Depends(get_db)):
    """单条详情。"""
    store = _store(db)
    entry = store.find_by_entry_id(entry_id)
    if entry is None:
        raise HTTPException(404, f"entry_id 不存在：{entry_id}")
    return entry.as_dict()


# ─────────────────────────────── 写面 ───────────────────────────────


@router.post("/register", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """登记一条否定性知识。"""
    if body.failure_kind not in FAILURE_KINDS:
        raise HTTPException(422, f"failure_kind 非法：{body.failure_kind}，允许值：{FAILURE_KINDS}")

    store = _store(db)
    entry, created = store.register(
        title=body.title,
        hypothesis=body.hypothesis,
        failure_kind=body.failure_kind,
        mission_id=body.mission_id,
        plan_id=body.plan_id,
        node_id=body.node_id,
        falsification_evidence=body.falsification_evidence,
        failure_reason=body.failure_reason,
        applicable_conditions=body.applicable_conditions,
        keywords=body.keywords,
        domain_tags=body.domain_tags,
        differentiation_required=body.differentiation_required,
        source_artifacts=body.source_artifacts,
        confidence=body.confidence,
        registered_by=body.registered_by,
    )
    return {
        "entry": entry.as_dict(),
        "created": created,
        "message": "登记成功" if created else "已存在（幂等）",
    }


@router.post("/gate", dependencies=[Depends(require_auth)])
def gate_check(body: GateRequest, db: Session = Depends(get_db)):
    """派单前检索门。

    对提案做语义检索，返回判决：
    - action=pass: 无相似否定知识，可入池
    - action=review: 中度相似，建议人工审查
    - action=reject: 高度相似，必须声明差异化
    """
    searcher = _searcher(db)
    verdict = searcher.gate_check(
        proposal_title=body.proposal_title,
        proposal_description=body.proposal_description,
        proposal_keywords=body.proposal_keywords,
    )
    cfg = _config()
    return {
        "verdict": verdict.as_dict(),
        "thresholds": {
            "reject_threshold": cfg.gate_reject_threshold,
            "review_threshold": cfg.gate_review_threshold,
        },
    }


@router.post("/harvest", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def harvest(body: HarvestRequest, db: Session = Depends(get_db)):
    """从 mission 的 FAIL 节点收割否定性知识。"""
    if body.failure_kind not in FAILURE_KINDS:
        raise HTTPException(422, f"failure_kind 非法：{body.failure_kind}")

    store = _store(db)
    entry, created = store.register(
        title=body.title,
        hypothesis=body.hypothesis,
        failure_kind=body.failure_kind,
        mission_id=body.mission_id,
        plan_id=body.plan_id,
        node_id=body.node_id,
        falsification_evidence=body.falsification_evidence,
        failure_reason=body.failure_reason,
        applicable_conditions=body.applicable_conditions,
        keywords=body.keywords,
        domain_tags=body.domain_tags,
        registered_by="harvester:negative-knowledge@1.0",
    )
    return {
        "entry": entry.as_dict(),
        "created": created,
        "message": "收割成功" if created else "已存在（幂等）",
    }


@router.patch("/{entry_id}/status", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def update_status(entry_id: str, body: StatusUpdateRequest, db: Session = Depends(get_db)):
    """更新条目状态。"""
    if body.status not in STATUSES:
        raise HTTPException(422, f"status 非法：{body.status}，允许值：{STATUSES}")

    store = _store(db)
    try:
        entry = store.update_status(entry_id, body.status)
    except KeyError:
        raise HTTPException(404, f"entry_id 不存在：{entry_id}")

    return {
        "entry": entry.as_dict(),
        "message": f"状态已更新为 {body.status}",
    }


@router.get("/health", dependencies=[Depends(require_auth)])
def health(db: Session = Depends(get_db)):
    """健康检查。"""
    cfg = _config()
    store = _store(db)
    try:
        stats = store.stats()
        status = "up"
    except Exception as exc:
        logger.exception("否定性知识台账健康检查失败")
        return {"status": "DEGRADED", "error": str(exc)}

    return {
        "status": status,
        "table": cfg.table_name,
        "schema_version": cfg.schema_version,
        "stats": stats,
        "thresholds": {
            "gate_reject": cfg.gate_reject_threshold,
            "gate_review": cfg.gate_review_threshold,
            "search_min_score": cfg.search_min_score,
        },
    }
