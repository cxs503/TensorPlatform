"""Unified evidence-governance pipeline (P210W2-N01).

Merges the two previously separate mechanisms into one platform capability:

* the WITHHELD -> REAL upgrade pipeline (``scan -> upgrade gate -> closure KPI``)
* the evidence-debt compiler (``classify -> structured acquisition proposal``)

Public surface::

    from tools.evidence_governance import run_pipeline, verify_idempotent
    payloads, summary = run_pipeline(config)
    # summary: withheld total, proposals, upgraded, closure_rate, accuracy,
    #          combined_sha256 of the artifact manifest

Module map
----------
``scanner``    whole-library WITHHELD scan over configured sources
``classifier`` weighted baseline-type classification (rules from config)
``proposal``   missing magnitude + priority + verification spec per entry
``upgrade``    upgrade gate + closure-rate KPI (anti-fabrication checks)
``artifacts``  canonical serialisation and sha256 manifest (idempotency)
``runner``     CLI and one-shot orchestration

Discipline: zero subject vocabulary in code (rules, thresholds and row maps are
configuration / injected data); deterministic artifacts (no wall clock, sorted
keys); honest KPI (a rejected pass claim leaves the entry at WITHHELD).
"""
from __future__ import annotations

from .artifacts import combined_digest, write_artifact_set
from .classifier import GovernanceClassifier
from .model import (
    GovernanceLedger,
    LibraryItem,
    ScanResult,
    UpgradeRecord,
    VerificationProposal,
)
from .proposal import ProposalCompiler, summarise_proposals
from .scanner import LibraryScanner
from .upgrade import UpgradeEngine

def run_pipeline(*args, **kwargs):
    """Lazy wrapper — keeps ``python -m tools.evidence_governance.runner`` clean."""
    from .runner import run_pipeline as _impl

    return _impl(*args, **kwargs)


def verify_idempotent(*args, **kwargs):
    from .runner import verify_idempotent as _impl

    return _impl(*args, **kwargs)


def evaluate_accuracy(*args, **kwargs):
    from .runner import evaluate_accuracy as _impl

    return _impl(*args, **kwargs)


__all__ = [
    "GovernanceClassifier",
    "GovernanceLedger",
    "LibraryItem",
    "LibraryScanner",
    "ProposalCompiler",
    "ScanResult",
    "UpgradeEngine",
    "UpgradeRecord",
    "VerificationProposal",
    "combined_digest",
    "evaluate_accuracy",
    "run_pipeline",
    "summarise_proposals",
    "verify_idempotent",
    "write_artifact_set",
]