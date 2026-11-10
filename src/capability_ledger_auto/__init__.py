"""能力台账自动升账引擎（capability ledger auto-upgrade engine）。

零领域语义：扫描器不依赖任何域词条，仅通过能力定义注册表的模式匹配。

模块
----
- registry    : 能力定义注册表加载与校验
- scanner     : 证据扫描器（代码/测试/报告三维度）
- evidence_chain : 证据链构建（theory/code/config/data/report 五元）
- grader      : 等级判定（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）
- cli         : CLI 入口

用法
----
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root .
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root . --write
"""
from __future__ import annotations

from .registry import CapabilityRegistry, CapabilityDef
from .scanner import EvidenceScanner, ScanResult
from .evidence_chain import EvidenceChain, FiveTuple
from .grader import GradeDeterminer, GradeResult

__all__ = [
    "CapabilityRegistry",
    "CapabilityDef",
    "EvidenceScanner",
    "ScanResult",
    "EvidenceChain",
    "FiveTuple",
    "GradeDeterminer",
    "GradeResult",
]
