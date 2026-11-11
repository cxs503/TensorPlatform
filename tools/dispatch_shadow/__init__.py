"""Decision-log versioning, deterministic replay and shadow-policy gate.

Public surface::

    from tools.dispatch_shadow import (
        LogConfig, DecisionLog, Policy, PolicyRegistry,
        evaluate_shadow_gate, GateConfig,
    )

The module is domain-free: candidate ids, feature names and outcome fields all
come from an injected :class:`LogConfig`.  See ``docs/DISPATCH_SHADOW_REPLAY.md``.
"""

from __future__ import annotations

from .canonical import canonical_json, content_digest, sha256_hex
from .leakage import LeakageDetector, inject_outcome_feature, scan_structural
from .offpolicy import OffPolicyEstimate, bootstrap_ci, estimate_from_records
from .policy import Policy, PolicyRegistry, UnpinnedPolicyError
from .replay import ReplayReport, perturbation_probe, replay_log, replay_record
from .schema import (
    SCHEMA_VERSION,
    DecisionRecord,
    LeakageError,
    LogConfig,
    SchemaError,
    make_outcome,
    make_record,
)
from .shadow import GateConfig, GateCriterion, evaluate_shadow_gate
from .store import ChainReport, DecisionLog

__all__ = [
    "SCHEMA_VERSION",
    "ChainReport",
    "DecisionLog",
    "DecisionRecord",
    "GateConfig",
    "GateCriterion",
    "LeakageDetector",
    "LeakageError",
    "LogConfig",
    "OffPolicyEstimate",
    "Policy",
    "PolicyRegistry",
    "ReplayReport",
    "SchemaError",
    "UnpinnedPolicyError",
    "bootstrap_ci",
    "canonical_json",
    "content_digest",
    "estimate_from_records",
    "evaluate_shadow_gate",
    "inject_outcome_feature",
    "make_outcome",
    "make_record",
    "perturbation_probe",
    "replay_log",
    "replay_record",
    "scan_structural",
    "sha256_hex",
]