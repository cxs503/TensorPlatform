"""Reproduction Closure Module — 复算漂移清偿闭环机制。

成熟化节点 P180W2-N10 产物：
- 根因分类（环境/种子/依赖/数据 四类）
- 修复方案（锁定依赖/固定种子/容器化/数据版本控制）
- 复测验证（repro_rate ≥ 0.95）
- KPI 分母口径敏感性表（≥5 种定义）
- 10 节点产物集成验证
- 知识图谱构建
- BP §8.2 对齐报告
"""
from __future__ import annotations

from .core import (
    ReproductionClosure,
    RootCauseCategory,
    FixStrategy,
    DenominatorDefinition,
    SensitivityEntry,
    IntegrationNode,
    KnowledgeGraphEdge,
    BPClause,
)
from .analyzer import RootCauseAnalyzer
from .fixer import FixEngine
from .sensitivity import DenominatorSensitivityTable
from .integrator import ArtifactIntegrator
from .knowledge_graph import KnowledgeGraphBuilder
from .bp_aligner import BPAligner

__all__ = [
    "ReproductionClosure",
    "RootCauseCategory",
    "FixStrategy",
    "DenominatorDefinition",
    "SensitivityEntry",
    "IntegrationNode",
    "KnowledgeGraphEdge",
    "BPClause",
    "RootCauseAnalyzer",
    "FixEngine",
    "DenominatorSensitivityTable",
    "ArtifactIntegrator",
    "KnowledgeGraphBuilder",
    "BPAligner",
]
