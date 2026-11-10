"""能力台账自动升账引擎（capability ledger auto-upgrade engine）。

零领域语义：扫描器不依赖任何域词条，仅通过能力定义注册表的模式匹配。

模块
----
- registry       : 能力定义注册表加载与校验
- scanner        : 证据扫描器（代码/测试/报告三维度）
- evidence_chain : 证据链构建（theory/code/config/data/report 五元）
- grader         : 等级判定（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）
- anchors        : 验证锚模型与单锚判决（PASS/FAIL/UNDECIDABLE，含不确定度带）
- provenance     : 等级变更哈希链（append-only，可复算防篡改）
- state_machine  : 五态 + 不可判态状态机（fail-closed 升级/降级/禁止人工覆盖）
- cli            : CLI 入口

用法
----
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root .
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root . --write

    # v2 状态机（五态 + UNDECIDABLE）
    from capability_ledger_auto import (
        EvidenceLevelLedger, Observation, VerificationAnchor, EvidencePolicy,
    )
    ledger = EvidenceLevelLedger(EvidencePolicy(tolerance=0.15))
    ledger.register(Observation(capability_id="synth.node", has_implementation=True,
                                anchors=(VerificationAnchor(anchor_id="a1", kind="analytic",
                                                            rel_error=0.02),)))
"""
from __future__ import annotations

from .registry import CapabilityRegistry, CapabilityDef
from .scanner import EvidenceScanner, ScanResult
from .evidence_chain import EvidenceChain, FiveTuple
from .grader import GradeDeterminer, GradeResult
from .anchors import (
    AnchorVerdict,
    VerificationAnchor,
    judge_anchor,
    judge_anchors,
)
from .provenance import ProvenanceChain, ProvenanceEntry
from .state_machine import (
    ADMISSIBLE_RANK,
    EXTENDED_STATES,
    RULES,
    STATE_SEMANTICS,
    UNDECIDABLE,
    Counterexample,
    DowngradeWithoutTrigger,
    EvidenceLevelLedger,
    EvidencePolicy,
    EvidenceStateMachine,
    ManualOverrideRejected,
    Observation,
    StateDecision,
    admissible_rank,
    gate_decision,
)

__all__ = [
    # v1（三态/五态扫描判定）
    "CapabilityRegistry",
    "CapabilityDef",
    "EvidenceScanner",
    "ScanResult",
    "EvidenceChain",
    "FiveTuple",
    "GradeDeterminer",
    "GradeResult",
    # v2（五态 + 不可判态状态机）
    "AnchorVerdict",
    "VerificationAnchor",
    "judge_anchor",
    "judge_anchors",
    "ProvenanceChain",
    "ProvenanceEntry",
    "ADMISSIBLE_RANK",
    "EXTENDED_STATES",
    "RULES",
    "STATE_SEMANTICS",
    "UNDECIDABLE",
    "Counterexample",
    "DowngradeWithoutTrigger",
    "EvidenceLevelLedger",
    "EvidencePolicy",
    "EvidenceStateMachine",
    "ManualOverrideRejected",
    "Observation",
    "StateDecision",
    "admissible_rank",
    "gate_decision",
]