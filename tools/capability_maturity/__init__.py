"""Capability maturity upgrade framework (P250-N02).

把「能力审计 → 升级路径 → 验收判据 → 证据链 → 负控」做成一条可机器裁决的流水线。
本包**零领域语义**：能力 id、等级、基准数值全部来自台账/JSON 工件（注入，不硬编码），
代码只处理等级、判据、误差与证据摘要。

公开面::

    from tools.capability_maturity import (
        load_audit_table, plan_paths, AcceptanceCriteria, PREDECLARED,
        ProbeRegistry, run_trial, MaturityUpgradeEngine, EvidenceChain)

    table = load_audit_table()              # 21 项能力（与 audit_capabilities 同源渲染）
    paths = plan_paths(table.rows, PREDECLARED)
    eng = MaturityUpgradeEngine(table, PREDECLARED, registry)
    trial = eng.execute(spec)               # 实现 vs 独立基准 逐例误差
    decision = eng.promote(trial, chain)    # 判据+证据链齐 → REAL；否则显式拒绝
"""
from __future__ import annotations

from .chain import Artifact, EvidenceChain
from .criteria import PREDECLARED, AcceptanceCriteria
from .engine import MaturityUpgradeEngine, PromotionDecision
from .executor import CaseResult, TrialResult, rel_error_pct, run_trial
from .model import LEVELS, LEVEL_RANK, UPGRADABLE_LEVELS, AuditRow, AuditTable
from .paths import STAGE_SPECS, UpgradePath, coverage, plan_path, plan_paths
from .register import capture_audit_text, load_audit_table, parse_audit_text
from .validators import Probe, ProbeRegistry

__all__ = [
    "LEVELS", "LEVEL_RANK", "UPGRADABLE_LEVELS", "AuditRow", "AuditTable",
    "AcceptanceCriteria", "PREDECLARED",
    "STAGE_SPECS", "UpgradePath", "coverage", "plan_path", "plan_paths",
    "capture_audit_text", "load_audit_table", "parse_audit_text",
    "Probe", "ProbeRegistry", "run_trial", "CaseResult", "TrialResult",
    "rel_error_pct", "MaturityUpgradeEngine", "PromotionDecision",
    "Artifact", "EvidenceChain",
]
