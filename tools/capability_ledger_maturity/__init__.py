"""能力台账自动化证据链成熟化机制（p273w3-n05）。

四组件：
1. evidence_collector —— 证据采集器（扫描 pytest/benchmark/对标报告提取关键量与误差）
2. adjudication_engine —— 裁定引擎（按预声明阈值自动判定 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）
3. upgrade_workflow —— 升级工作流（证据达标→自动升账+审计日志+回滚点）
4. overclaim_gate —— 防 OVERCLAIM 门（自述等级>证据等级时自动降级+告警）

设计原则：
- 零领域语义：所有域名/能力名通过配置注入，不硬编码
- 纯函数裁定：grade = f(evidence, thresholds)，确定性、可复现
- 审计链完整：每次裁定/升级/降级都有 sha256 签名的审计日志
- 可回滚：每次状态变更保存快照，支持一致性校验回滚
"""
from __future__ import annotations

from .evidence_collector import EvidenceCollector, EvidenceItem
from .adjudication_engine import AdjudicationEngine, AdjudicationResult
from .upgrade_workflow import UpgradeWorkflow, UpgradeRecord
from .overclaim_gate import OverclaimGate, OverclaimAlert

__all__ = [
    "EvidenceCollector", "EvidenceItem",
    "AdjudicationEngine", "AdjudicationResult",
    "UpgradeWorkflow", "UpgradeRecord",
    "OverclaimGate", "OverclaimAlert",
]

__version__ = "1.0.0"
