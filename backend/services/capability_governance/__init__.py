"""Capability governance — auto ledger upgrade, cross-solver attribution, sample corpora.

Three mechanisms that share one property: each turns a claim into arithmetic.

``probes`` + ``grading`` + ``ledger``
    A frozen endpoint is probed, graded against declared criteria, sealed into a
    content-addressed evidence chain and written to an append-only ledger.  ``REAL``
    is written only when every criterion passes and the chain validates; a failing
    or tampered chain can never produce an upgrade.  ``backtest`` measures the
    mechanism itself on a frozen labelled set (accuracy, false-positive rate) and
    against a single-gate ablation.

``attribution``
    A cross-solver disagreement is decomposed into format / output-definition /
    input-definition / discretisation by a telescoping correction sequence, so the
    unexplained residual is an explicit number rather than an implicit assumption.

``samples``
    A declarative spec is expanded into ``ship_samples`` rows with a content-addressed
    dedupe key and a provenance block; loading is idempotent and the audit reads its
    numbers back from the database, not from the generator.

Domain vocabulary is injected through ``config/capability_governance/*.json`` and the
corpus axis-label sidecar; no module here names a discipline, a geometry class or a
solver.
"""
from __future__ import annotations

from .attribution import (DIMENSIONS, AttributionError, attribute_arm, attribute_case,
                          attribute_cases)
from .backtest import freeze_digest, run_frozen_set
from .grading import build_chain, digest_of, grade_probe, validate_chain
from .ledger import CapabilityLedger
from .probes import run_endpoint

__all__ = [
    "AttributionError",
    "CapabilityLedger",
    "DIMENSIONS",
    "attribute_arm",
    "attribute_case",
    "attribute_cases",
    "build_chain",
    "digest_of",
    "freeze_digest",
    "grade_probe",
    "run_endpoint",
    "run_frozen_set",
    "validate_chain",
]
__version__ = "1.0.0"