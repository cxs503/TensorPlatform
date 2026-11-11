"""跨仓能力依赖图谱 + 级联失效传播 + 自动预警（p285w2b-n03）。

零领域语义：本包只处理「能力 id → 等级 → 依赖边 → 置信度」的图结构，不认知任何
学科词条；能力 id / 依赖边 / 权重 / 等级词表 / 判据阈值全部经
``config/capability_graph.json`` 注入。

对外入口
--------
``builder.build_snapshot(cfg)``          台账 + 配置边 + 证据推断 → 图快照
``cascade.simulate(snapshot, event, cfg)`` 单次等级变化的级联传播
``alerts.AlertEngine``                    变化 → 预警（端到端延迟可计量）
``checks``                                Jaccard / 人工分析误差 / 零领域语义扫描
"""

from __future__ import annotations

from .alerts import Alert, AlertEngine
from .builder import GraphConfig, build_snapshot, load_declared_edges, load_levels
from .cascade import CascadeEvent, CascadeResult, simulate
from .checks import (
    compare_cascade_with_manual,
    compare_edge_sets,
    jaccard,
    mutation_sensitivity,
    scan_forbidden_tokens,
)
from .model import Edge, Node, Snapshot, level_from_confidence

__all__ = [
    "Alert",
    "AlertEngine",
    "CascadeEvent",
    "CascadeResult",
    "Edge",
    "GraphConfig",
    "Node",
    "Snapshot",
    "build_snapshot",
    "compare_cascade_with_manual",
    "compare_edge_sets",
    "jaccard",
    "level_from_confidence",
    "load_declared_edges",
    "load_levels",
    "mutation_sensitivity",
    "scan_forbidden_tokens",
    "simulate",
]

__version__ = "1.0.0"