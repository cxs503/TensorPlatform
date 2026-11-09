"""提案门服务层：封装 ProposalGate 核心判定逻辑，供 API 与异步任务调用。

职责
----
* 配置装载与缓存（进程级单例，配置变更时重建）
* 账本存储管理（按 DB engine 缓存 LedgerStore）
* 索引构建（战役节点 + 历史提案 + 已采纳提案）
* 判定执行（evaluate / adopt / coverage / stats）
* 事件与隔离区落盘（由 gate 模块处理，本层只负责组装参数）

纪律：本模块零领域语义——所有阈值/白名单/词表来自 config/proposal_gate.json；
测试用合成域名（foo/bar/widget）夹具走 tests/proposal_gate_support.py。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from tools.proposal_gate.config import GateConfig, load_config
from tools.proposal_gate.gate import GateResult, ProposalGate, payload_sha256
from tools.proposal_gate.index import IndexEntry, ProposalIndex
from tools.proposal_gate.ledger import LedgerRow, LedgerStore, sql_trace
from tools.proposal_gate.sources import load_campaign_nodes, populate_index

logger = logging.getLogger(__name__)

# ── 进程级缓存 ──────────────────────────────────────────────────────────
_CONFIG_CACHE: GateConfig | None = None
_STORE_CACHE: dict[int, LedgerStore] = {}
_INDEX_CACHE: ProposalIndex | None = None


def _get_config() -> GateConfig:
    """获取配置（进程级缓存，首次装载后复用）。"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = load_config()
    return _CONFIG_CACHE


def _get_store(db: Session, cfg: GateConfig) -> LedgerStore:
    """按 DB engine 获取/创建 LedgerStore（进程级缓存）。"""
    engine = db.get_bind()
    key = id(engine)
    store = _STORE_CACHE.get(key)
    if store is None:
        store = LedgerStore.for_engine(engine, cfg)
        _STORE_CACHE[key] = store
    return store


def _build_index(
    db: Session,
    cfg: GateConfig,
    *,
    with_nodes: bool = True,
    node_limit: int = 5000,
) -> ProposalIndex:
    """构建索引（战役节点 + 已采纳提案）。"""
    index = ProposalIndex(cfg)
    if with_nodes:
        try:
            nodes = load_campaign_nodes(db, cfg, limit=node_limit)
            populate_index(index, nodes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("战役节点索引装载失败（降级为账本口径）: %s", exc)
    # 把已采纳提案放回索引
    store = _get_store(db, cfg)
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
    return index


def _make_gate(
    db: Session,
    cfg: GateConfig,
    *,
    with_nodes: bool = True,
    node_limit: int = 5000,
) -> ProposalGate:
    """组装 ProposalGate 实例（配置 + 账本 + 索引）。"""
    store = _get_store(db, cfg)
    index = _build_index(db, cfg, with_nodes=with_nodes, node_limit=node_limit)
    return ProposalGate(cfg, store, index)


# ── 公开服务接口 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProposalGateService:
    """提案门服务（无状态，每次调用从缓存取配置/账本/索引）。"""

    @staticmethod
    def evaluate(
        db: Session,
        proposal: Mapping[str, Any],
        *,
        mission_id: int,
        plan_id: int | None = None,
        source_path: str | None = None,
        schema_version: str | None = None,
        with_nodes: bool = True,
    ) -> GateResult:
        """送一份提案过三道门（契约 → 幂等 → 去重 → 孪生 → 落账）。

        返回 GateResult，包含 verdict/state/score/reasons 等字段。
        """
        cfg = _get_config()
        gate = _make_gate(db, cfg, with_nodes=with_nodes)
        return gate.evaluate(
            proposal,
            mission_id=mission_id,
            plan_id=plan_id,
            source_path=source_path,
            version=schema_version,
        )

    @staticmethod
    def adopt(
        db: Session,
        mission_id: int,
        sha256: str,
        plan_id: int | None = None,
    ) -> LedgerRow:
        """标记某提案已被战役播种采纳（状态上行留痕）。"""
        cfg = _get_config()
        store = _get_store(db, cfg)
        if store.get(mission_id, sha256) is None:
            raise ValueError(f"ledger_row_not_found: mission_id={mission_id}, sha256={sha256}")
        gate = ProposalGate(cfg, store)
        return gate.adopt(mission_id=mission_id, sha256=sha256, plan_id=plan_id)

    @staticmethod
    def stats(db: Session) -> dict[str, Any]:
        """四态计数 + 重复率 + KPI。"""
        cfg = _get_config()
        store = _get_store(db, cfg)
        stats = store.stats()
        return {
            "stats": stats.as_dict(),
            "sql_trace": sql_trace(store),
            "table": cfg.table_name,
            "config_path": str(cfg.path) if cfg.path else None,
        }

    @staticmethod
    def ledger(
        db: Session,
        *,
        state: str | None = None,
        mission_id: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """账本行查询（可按 state / mission_id 过滤）。"""
        cfg = _get_config()
        store = _get_store(db, cfg)
        rows = store.rows(state=state, mission_id=mission_id, limit=min(limit, 2000))
        return {
            "items": [r.as_dict() for r in rows],
            "count": len(rows),
            "sql_trace": sql_trace(store, state),
        }

    @staticmethod
    def coverage(db: Session) -> dict[str, Any]:
        """播种覆盖率账本（采纳提案 ↔ 战役节点名）。"""
        cfg = _get_config()
        gate = _make_gate(db, cfg, with_nodes=True)
        cov = gate.coverage()
        return {"coverage": cov, "sql_trace": sql_trace(gate.ledger)}

    @staticmethod
    def config_info() -> dict[str, Any]:
        """当前生效阈值/白名单（全部来自配置文件）。"""
        cfg = _get_config()
        return {
            "config_path": str(cfg.path) if cfg.path else None,
            "thresholds": {
                "dup_threshold": cfg.get("dedup.dup_threshold"),
                "review_threshold": cfg.get("dedup.review_threshold"),
                "twin_dependency_overlap": cfg.get("dedup.twin_dependency_overlap"),
                "coverage_match_threshold": cfg.get("dedup.coverage_match_threshold"),
                "shingle_size": cfg.get("dedup.shingle_size"),
                "min_criteria": cfg.get("contract.min_criteria"),
                "containment_enabled": cfg.get("dedup.containment_enabled"),
                "min_length_ratio": cfg.get("dedup.min_length_ratio"),
            },
            "repo_whitelist_size": len(cfg.repo_whitelist),
            "table": cfg.table_name,
            "states": cfg.state_map,
            "kpi_keys": {
                "dup_rejected": cfg.get("kpi.dup_rejected_key"),
                "seed_coverage": cfg.get("kpi.seed_coverage_key"),
            },
        }

    @staticmethod
    def compute_sha256(payload: Any) -> str:
        """计算提案载荷的 SHA256（用于幂等校验）。"""
        return payload_sha256(payload)


# ── 便捷函数（面向 API 层直接调用）─────────────────────────────────────

def evaluate_proposal(
    db: Session,
    proposal: Mapping[str, Any],
    *,
    mission_id: int,
    plan_id: int | None = None,
    source_path: str | None = None,
    schema_version: str | None = None,
    with_nodes: bool = True,
) -> GateResult:
    """便捷函数：送提案过门。"""
    return ProposalGateService.evaluate(
        db,
        proposal,
        mission_id=mission_id,
        plan_id=plan_id,
        source_path=source_path,
        schema_version=schema_version,
        with_nodes=with_nodes,
    )


def adopt_proposal(
    db: Session,
    mission_id: int,
    sha256: str,
    plan_id: int | None = None,
) -> LedgerRow:
    """便捷函数：标记采纳。"""
    return ProposalGateService.adopt(db, mission_id, sha256, plan_id=plan_id)


def get_gate_stats(db: Session) -> dict[str, Any]:
    """便捷函数：获取统计。"""
    return ProposalGateService.stats(db)


def get_gate_ledger(
    db: Session,
    *,
    state: str | None = None,
    mission_id: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """便捷函数：查询账本。"""
    return ProposalGateService.ledger(db, state=state, mission_id=mission_id, limit=limit)


def get_gate_coverage(db: Session) -> dict[str, Any]:
    """便捷函数：获取覆盖率。"""
    return ProposalGateService.coverage(db)


def get_gate_config() -> dict[str, Any]:
    """便捷函数：获取配置信息。"""
    return ProposalGateService.config_info()
