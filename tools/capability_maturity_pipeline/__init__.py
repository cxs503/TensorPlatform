"""能力成熟度自治管线（p156w2-n07）。

五段闭环，全部机器可判：

1. ``pipeline.EvidenceScanner``  —— 证据扫描（artifacts/ runs/ tensor_reports/ 等配置根）
2. ``contradiction``             —— 矛盾/推翻检测（后续证据推翻先前判定）
3. ``..capability_ledger_maturity.adjudication_engine`` —— 自动判定（预声明阈值→等级）
4. ``ledger.MaturityLedger``     —— 台账更新 + 自动 ROLLBACK + 变更链
5. ``chain.AuditChain``          —— 哈希链审计日志（每事件 sha256 + prev 链接可校验）

与既有模块的关系（不重复实现，只补齐缺失的闭环段）：

* 复用 ``tools.capability_ledger_maturity``：证据采集器、裁定引擎、阈值契约；
* 该模块的 ``upgrade_workflow`` 是「只升不降 + 快照式回滚」；
  本模块补的是**证据驱动的自动回滚**（矛盾检测→ROLLBACK 事件→变更链）
  与**带前驱链接的防篡改审计链**（快照回滚无法证明历史未被改写）。
"""
from __future__ import annotations

from .chain import (
    GENESIS, EVENT_TYPES, AuditChain, ChainEvent, ChainVerification,
)
from .contradiction import (
    Conflict, ContradictionPolicy, Supersession, detect_conflicts,
    detect_supersessions, relative_deviation_pct,
)
from .ledger import CapabilityState, Decision, MaturityLedger
from .pipeline import (
    ARTIFACT_SCHEMA, DEFAULT_ROOTS, EvidenceScanner, MaturityPipeline,
    PipelineConfig, PipelineRun,
)

__all__ = [
    "GENESIS", "EVENT_TYPES", "AuditChain", "ChainEvent", "ChainVerification",
    "Conflict", "ContradictionPolicy", "Supersession", "detect_conflicts",
    "detect_supersessions", "relative_deviation_pct",
    "CapabilityState", "Decision", "MaturityLedger",
    "ARTIFACT_SCHEMA", "DEFAULT_ROOTS", "EvidenceScanner", "MaturityPipeline",
    "PipelineConfig", "PipelineRun",
]

__version__ = "1.0.0"