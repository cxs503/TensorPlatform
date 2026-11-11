"""Platform governance gates (p338w3-n07).

Three fail-closed guards, domain-neutral by construction:

``evidence`` + ``ledger_guard``
    Completeness-scored capability ledger guard: an evidence set covering N
    requirements yields a score; >= ``promote`` promotes to REAL, < ``demote``
    demotes, contradictions and malformed input always demote.
``report_fingerprint``
    Report-body / metadata four-way binding with sha256 content fingerprints;
    duplicate and mismatched reports become ledger defects (gate closed).
``status_drift``
    Campaign-node status vs mission final-state drift detection and automatic
    convergence, including the invisible "blocked but already merged" case.

Capability ids, thresholds, file roots and mission states are injected data;
nothing in this package interprets what a capability means.
"""
from .evidence import (
    CRITICAL_REQUIREMENTS,
    REQUIREMENTS,
    CapabilityEvidence,
    CompletenessReport,
    Contradiction,
    EvidenceItem,
    evaluate,
    evaluate_all,
    scan_evidence_roots,
    sha256_file,
)
from .ledger_guard import (
    LedgerGuard,
    LedgerGuardResult,
    Thresholds,
    decide,
)
from .report_fingerprint import (
    ReportEnvelope,
    ReportFingerprintGate,
    body_sha256,
    gate,
    metadata_sha256,
)
from .status_drift import (
    DriftDetector,
    DriftFinding,
    MissionState,
    NodeState,
    expected_node_status,
)

__all__ = [
    "CRITICAL_REQUIREMENTS",
    "REQUIREMENTS",
    "CapabilityEvidence",
    "CompletenessReport",
    "Contradiction",
    "EvidenceItem",
    "evaluate",
    "evaluate_all",
    "scan_evidence_roots",
    "sha256_file",
    "LedgerGuard",
    "LedgerGuardResult",
    "Thresholds",
    "decide",
    "ReportEnvelope",
    "ReportFingerprintGate",
    "body_sha256",
    "gate",
    "metadata_sha256",
    "DriftDetector",
    "DriftFinding",
    "MissionState",
    "NodeState",
    "expected_node_status",
]