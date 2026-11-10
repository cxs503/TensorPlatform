"""能力自动发现与注册（capability auto-discovery & registration）。

问题：平台的 ``capability_ledger`` 靠人工登记——求解器写了真实算法、跑过验证，
但没人记得去台账里加一行，能力台账就长期落后于代码现实。

本包把「求解器自己声明、平台自动登记」做成一条可审计闭环：

    测试里的 capability marker  ──静态扫描──▶  DiscoveryRecord
                                 ──聚合────▶  CapabilityFeature
    pytest 运行结局（插件）      ──定级────▶  Grade（策略即数据，保守口径）
                                 ──登记────▶  capability_ledger（哈希链 + 回滚）
                                 ──导出────▶  capability_upgrade 自动升级管线

零领域语义：本包不出现任何能力名、物理量或领域词条；能力身份完全来自被扫描
测试源码的 marker 元数据。测试夹具使用自造词汇（synth/quadrature）并做了
「换一套词汇结果同构」的对照（见 ``tests/test_capability_discovery_pipeline.py``）。

模块
----
- :mod:`~tools.capability_discovery.model`   公共类型（等级 / 记录 / 特征 / 定级）
- :mod:`~tools.capability_discovery.markers` AST 静态扫描（6 种 marker 格式）
- :mod:`~tools.capability_discovery.extract` 特征聚合（区间合并 / 结局索引）
- :mod:`~tools.capability_discovery.grading` 定级（策略 JSON，保守默认）
- :mod:`~tools.capability_discovery.store`   台账（哈希链 / 幂等 / 回滚）
- :mod:`~tools.capability_discovery.pipeline` 编排 + 差异报告
- :mod:`~tools.capability_discovery.plugin`  pytest 插件（真实结局采集）
- :mod:`~tools.capability_discovery.export`  导出到 capability_upgrade 管线
- :mod:`~tools.capability_discovery.cli`     CLI
"""
from __future__ import annotations

from .model import (
    LEVELS,
    LEVEL_RANK,
    MARKER_FORMATS,
    OUTCOME_STATES,
    CapabilityFeature,
    DiscoveryRecord,
    Grade,
    canonical_json,
    digest,
    level_rank,
    norm_level,
    now_iso,
    sha256_file,
    sha256_text,
)
from .markers import Diagnostics, MarkerError, MarkerScanner, marker_name, scan
from .extract import OutcomeIndex, aggregate_outcome, extract_features
from .grading import DEFAULT_POLICY, POLICY_SCHEMA, grade_feature, grade_features, load_policy
from .store import LEDGER_SCHEMA, Ledger, LedgerError
from .pipeline import REPORT_SCHEMA, RunResult, report_markdown, run

__all__ = [
    "LEVELS",
    "LEVEL_RANK",
    "MARKER_FORMATS",
    "OUTCOME_STATES",
    "CapabilityFeature",
    "DiscoveryRecord",
    "Grade",
    "canonical_json",
    "digest",
    "level_rank",
    "norm_level",
    "now_iso",
    "sha256_file",
    "sha256_text",
    "Diagnostics",
    "MarkerError",
    "MarkerScanner",
    "marker_name",
    "scan",
    "OutcomeIndex",
    "aggregate_outcome",
    "extract_features",
    "DEFAULT_POLICY",
    "POLICY_SCHEMA",
    "grade_feature",
    "grade_features",
    "load_policy",
    "LEDGER_SCHEMA",
    "Ledger",
    "LedgerError",
    "REPORT_SCHEMA",
    "RunResult",
    "report_markdown",
    "run",
]

__version__ = "1.0.0"