"""Acceptance Gate Maturity — pre-existing-failure baseline differencing.

This package upgrades the acceptance gate from absolute all-green to relative
baseline differencing.  It can distinguish:

* **new failures** (regressions introduced by the current change)
* **pre-existing failures** (known issues already present in the baseline)
* **missing artifacts** (fail-closed: verdict = MISSING_ARTIFACT)
* **baseline tampering** (3 types: content / structural / chain-of-trust)
* **cross-architecture drift** (per-arch baselines, drift reported separately)

Domain-neutral by construction: all domain semantics arrive via configuration
and artifacts, never via code in this package.

Schema version: acceptance-gate-maturity/1.0
"""
from .model import (
    ArchKey,
    BaselineRecord,
    BaselineSnapshot,
    CriterionOutcome,
    DiffResult,
    GateVerdict,
    MissingArtifactVerdict,
    TamperVerdict,
    TamperType,
)
from .baseline_diff import diff_against_baseline, classify_failures
from .tamper_detect import verify_baseline_integrity, TamperReport
from .arch_separation import separate_by_arch, ArchDriftReport
from .runner import run_acceptance_gate, AcceptanceGateResult

__all__ = [
    "ArchKey",
    "BaselineRecord",
    "BaselineSnapshot",
    "CriterionOutcome",
    "DiffResult",
    "GateVerdict",
    "MissingArtifactVerdict",
    "TamperVerdict",
    "TamperType",
    "TamperReport",
    "ArchDriftReport",
    "AcceptanceGateResult",
    "diff_against_baseline",
    "classify_failures",
    "verify_baseline_integrity",
    "separate_by_arch",
    "run_acceptance_gate",
]
