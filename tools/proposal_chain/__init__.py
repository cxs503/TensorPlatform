"""提案→战役供给链成熟化（零领域语义平台机制）。

模块分工（每层都可脱库/脱网单测）：

* :mod:`tools.proposal_chain.text`      —— 归一化 / 词项（拉丁词 + CJK 二元组可选单字）/ TF-IDF
* :mod:`tools.proposal_chain.semantic`  —— TF-IDF → 随机化 SVD（LSA）→ 语义余弦（无 numpy 自动降级）
* :mod:`tools.proposal_chain.dedup`     —— 跨战役语义去重（阈值分档 + 并查集合并建议）
* :mod:`tools.proposal_chain.node_schema` —— 战役节点 metadata 契约（必填四字段 + 恒等降级 + 漏报对账）
* :mod:`tools.proposal_chain.ledger`    —— 缺口账本结构解析（注入式 markdown → 条目）
* :mod:`tools.proposal_chain.coverage`  —— 播种覆盖率账本（域矩阵/主题矩阵 + 账本逐项比对）
* :mod:`tools.proposal_chain.labels`    —— 标注候选采样 / 阈值扫描 / tune-holdout 评估
* :mod:`tools.proposal_chain.pipeline`  —— 端到端分阶段管线（工件落盘、幂等可复算）
* :mod:`tools.proposal_chain.figures`   —— 论文图（matplotlib，数据只读工件）
* :mod:`tools.proposal_chain.report`    —— 报告文字（数字程序化注入，零手抄）
"""

from __future__ import annotations

from .config import ChainConfig, ConfigError, load_config
from .coverage import coverage_matrix, ledger_coverage, topic_vocabulary
from .dedup import DedupResult, Doc, MergeSuggestion, Pair, analyze
from .ledger import LedgerItem, parse_gap_ledger
from .node_schema import BatchReport, NodeValidation, validate_batch, validate_node
from .semantic import SemanticSpace
from .text import TfidfSpace, build_space, normalize, tokens

__all__ = [
    "ChainConfig",
    "ConfigError",
    "load_config",
    "coverage_matrix",
    "ledger_coverage",
    "topic_vocabulary",
    "DedupResult",
    "Doc",
    "MergeSuggestion",
    "Pair",
    "analyze",
    "LedgerItem",
    "parse_gap_ledger",
    "BatchReport",
    "NodeValidation",
    "validate_batch",
    "validate_node",
    "SemanticSpace",
    "TfidfSpace",
    "build_space",
    "normalize",
    "tokens",
]
