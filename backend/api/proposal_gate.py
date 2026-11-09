"""提案→战役供给链机器门 API（三道门：契约 / 去重 / 播种覆盖率账本）。

端点
----
* ``GET  /proposals/gate/stats``    —— 四态计数 + 重复率 + KPI（``guide.proposal_dup_rejected``…）
* ``GET  /proposals/gate/ledger``   —— 账本行（可按 state / mission_id 过滤，含 SQL 溯源）
* ``POST /proposals/gate/evaluate`` —— 送一份提案过门（fail-closed；隔离区落盘 + 事件）
* ``POST /proposals/gate/adopt``    —— 标记某提案已被战役播种采纳（状态上行留痕）
* ``GET  /proposals/gate/coverage`` —— 播种覆盖率账本（采纳提案 ↔ 真库战役节点名）
* ``GET  /proposals/gate/config``   —— 当前生效阈值/白名单（全部来自配置文件）

纪律：本路由零领域语义——白名单/阈值/词表都从 ``config/proposal_gate.json`` 读；
DB 读写走 ``proposal_gate_ledger``（幂等唯一键 ``(mission_id, sha256)``）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from database.config import get_db

from tools.proposal_gate.config import GateConfig, load_config
from tools.proposal_gate.gate import ProposalGate
from tools.proposal_gate.index import IndexEntry, ProposalIndex
from tools.proposal_gate.ledger import LedgerStore, sql_trace
from tools.proposal_gate.sources import load_campaign_nodes, populate_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proposals", tags=["提案门"])

_STORE_CACHE: dict[int, LedgerStore] = {}


def _config() -> GateConfig:
    try:
        return load_config()
    except Exception as exc:  # noqa: BLE001 - 配置缺失 = fail-closed，不给默认阈值
        logger.error("提案门配置装载失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"proposal_gate_config_unavailable: {exc}") from exc


def _store(db: Session, cfg: GateConfig) -> LedgerStore:
    engine = db.get_bind()
    key = id(engine)
    store = _STORE_CACHE.get(key)
    if store is None:
        store = LedgerStore.for_engine(engine, cfg)
        _STORE_CACHE[key] = store
    return store


def _gate(db: Session, cfg: GateConfig, *, with_nodes: bool = False, node_limit: int = 5000) -> ProposalGate:
    store = _store(db, cfg)
    index = ProposalIndex(cfg)
    if with_nodes:
        try:
            populate_index(index, load_campaign_nodes(db, cfg, limit=node_limit))
        except Exception as exc:  # noqa: BLE001 - 索引降级：只用账本口径，明确暴露
            logger.warning("战役节点索引装载失败（继续以账本/提案池口径工作）: %s", exc)
    gate = ProposalGate(cfg, store, index)
    # 把已采纳提案放回索引（kind=proposal），使新提案能与之逐条对拍
    for row in store.rows(state="adopted", limit=100_000):
        if row.blob:
            index.add(
                IndexEntry(
                    kind="proposal",
                    key=f"ledger:{row.sha256}",
                    text=row.blob,
                    sha256=row.sha256,
                )
            )
    return gate


class EvaluateRequest(BaseModel):
    mission_id: int = Field(..., ge=0)
    plan_id: int | None = None
    proposal: dict[str, Any]
    source_path: str | None = None
    schema_version: str | None = None
    with_nodes: bool = True


class AdoptRequest(BaseModel):
    mission_id: int = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64)
    plan_id: int | None = None


@router.get("/gate/stats", dependencies=[Depends(require_auth)])
def gate_stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = _config()
    store = _store(db, cfg)
    stats = store.stats()
    return {
        "stats": stats.as_dict(),
        "sql_trace": sql_trace(store),
        "table": cfg.table_name,
        "config_path": str(cfg.path) if cfg.path else None,
    }


@router.get("/gate/ledger", dependencies=[Depends(require_auth)])
def gate_ledger(
    state: str | None = None,
    mission_id: int | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cfg = _config()
    store = _store(db, cfg)
    rows = store.rows(state=state, mission_id=mission_id, limit=min(int(limit), 2000))
    return {
        "items": [r.as_dict() for r in rows],
        "count": len(rows),
        "sql_trace": sql_trace(store, state),
    }


@router.post("/gate/evaluate", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def gate_evaluate(req: EvaluateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = _config()
    gate = _gate(db, cfg, with_nodes=req.with_nodes)
    result = gate.evaluate(
        req.proposal,
        mission_id=req.mission_id,
        plan_id=req.plan_id,
        source_path=req.source_path,
        version=req.schema_version,
    )
    return {"result": result.as_dict(), "thresholds": _thresholds(cfg)}


@router.post("/gate/adopt", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def gate_adopt(req: AdoptRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = _config()
    store = _store(db, cfg)
    if store.get(req.mission_id, req.sha256) is None:
        raise HTTPException(status_code=404, detail="ledger_row_not_found")
    gate = ProposalGate(cfg, store)
    row = gate.adopt(mission_id=req.mission_id, sha256=req.sha256, plan_id=req.plan_id)
    return {"row": row.as_dict()}


@router.get("/gate/coverage", dependencies=[Depends(require_auth)])
def gate_coverage(db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = _config()
    gate = _gate(db, cfg, with_nodes=True)
    cov = gate.coverage()
    return {"coverage": cov, "sql_trace": sql_trace(gate.ledger)}


@router.get("/gate/config", dependencies=[Depends(require_auth)])
def gate_config() -> dict[str, Any]:
    cfg = _config()
    return {
        "config_path": str(cfg.path) if cfg.path else None,
        "thresholds": _thresholds(cfg),
        "repo_whitelist_size": len(cfg.repo_whitelist),
        "table": cfg.table_name,
        "states": cfg.state_map,
        "kpi_keys": {
            "dup_rejected": cfg.get("kpi.dup_rejected_key"),
            "seed_coverage": cfg.get("kpi.seed_coverage_key"),
        },
    }


def _thresholds(cfg: GateConfig) -> dict[str, Any]:
    return {
        "dup_threshold": cfg.get("dedup.dup_threshold"),
        "review_threshold": cfg.get("dedup.review_threshold"),
        "twin_dependency_overlap": cfg.get("dedup.twin_dependency_overlap"),
        "coverage_match_threshold": cfg.get("dedup.coverage_match_threshold"),
        "shingle_size": cfg.get("dedup.shingle_size"),
        "min_criteria": cfg.get("contract.min_criteria"),
        "containment_enabled": cfg.get("dedup.containment_enabled"),
        "min_length_ratio": cfg.get("dedup.min_length_ratio"),
    }