"""Capability evidence auto-upgrade pipeline (P218-N01).

Public surface::

    from tools.capability_upgrade import Store, EvidencePipeline, RejudgeEngine

    store = Store(":memory:")
    store.upsert_slot("synth.alpha", initial_level="WITHHELD")
    pipe = EvidencePipeline(store)
    result = pipe.ingest("synth.alpha", kind="numeric_verification",
                         metrics={"error_pct": 1.1},
                         source={"path": "case.json", "sha256": "<64 hex>"})

The rung-by-rung upgrade state machine (P348-W3-N01) adds the audited half::

    from tools.capability_upgrade import (
        CapabilityStateMachine, EvidenceBundle, UpgradeAudit, UpgradeRuleTable,
    )
    machine = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit("trail.jsonl"))
    machine.register("synth.alpha", level="MOCK")
    machine.apply("synth.alpha", EvidenceBundle.from_evidence("synth.alpha", items,
                                                             reviews=[...]))
    machine.inject_defect("synth.alpha", {"kind": "provenance_broken", "reason": "..."})

See ``tools/capability_upgrade/README.md`` for the acceptance criteria mapping.
"""
from __future__ import annotations

from .completeness import (
    Assessment,
    EvidenceBundle,
    assess,
    chain_report,
    completeness_report,
)
from .engine import RejudgeEngine
from .model import (
    LEVELS,
    LEVEL_RANK,
    CapabilitySlot,
    Criterion,
    CriterionVerdict,
    EvidenceItem,
    level_rank,
)
from .pipeline import EvidencePipeline
from .registry import apply_registry, load_registry
from .state_machine import CapabilityStateMachine
from .store import Store
from .upgrade_audit import UpgradeAudit
from .upgrade_rules import UpgradeRule, UpgradeRuleTable

__all__ = [
    "Assessment",
    "CapabilityStateMachine",
    "EvidenceBundle",
    "UpgradeAudit",
    "UpgradeRule",
    "UpgradeRuleTable",
    "assess",
    "chain_report",
    "completeness_report",
    "LEVELS",
    "LEVEL_RANK",
    "CapabilitySlot",
    "Criterion",
    "CriterionVerdict",
    "EvidenceItem",
    "EvidencePipeline",
    "RejudgeEngine",
    "Store",
    "apply_registry",
    "level_rank",
    "load_registry",
]