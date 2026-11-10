#!/usr/bin/env python3
"""Verification-harness package: domain-neutral case registry / runner / evidence.

Public surface
--------------
``model``     declarative case model, tolerances, error convention
``registry``  configuration-injected case registry (``*.json``) + callable resolution
``runner``    execution + error table (deterministic bytes)
``evidence``  sha256 / environment fingerprint / dependency set / chain + verifier

Nothing in these four modules carries domain semantics: a case arrives as
configuration (``tools/verification_harness/cases/*.json``) and the callables
are resolved from dotted paths at run time.  ``anchors`` is an *application* of
the harness to synthetic mathematics and is deliberately not imported here, so
the mechanism keeps zero physical knowledge (see
``tests/test_verification_harness_domain_neutral.py``, which proves the import
graph direction with an AST scan).
"""
from __future__ import annotations

from .evidence import (
    BundleVerdict,
    DEFAULT_DEPENDENCY_PROBE,
    dependency_versions,
    environment_digest,
    environment_fingerprint,
    sha256_bytes,
    sha256_file,
    sha256_text,
    verify_bundle,
    verification_markdown,
    write_bundle,
)
from .model import (
    SCHEMA_VERSION,
    CaseResult,
    CaseSpec,
    Metric,
    ReferenceKind,
    Tolerance,
    Verdict,
    absolute_error,
    observed_order,
    relative_error,
)
from .registry import (
    CallableResolutionError,
    CaseRegistry,
    RegistryError,
    case_spec_from_mapping,
    resolve_callable,
)
from .runner import (
    RunReport,
    error_table_markdown,
    run_case,
    run_registry,
    write_run_report,
)

__all__ = [
    "SCHEMA_VERSION",
    "BundleVerdict",
    "CallableResolutionError",
    "CaseRegistry",
    "CaseResult",
    "CaseSpec",
    "DEFAULT_DEPENDENCY_PROBE",
    "Metric",
    "ReferenceKind",
    "RegistryError",
    "RunReport",
    "Tolerance",
    "Verdict",
    "absolute_error",
    "case_spec_from_mapping",
    "dependency_versions",
    "environment_digest",
    "environment_fingerprint",
    "error_table_markdown",
    "observed_order",
    "relative_error",
    "resolve_callable",
    "run_case",
    "run_registry",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "verification_markdown",
    "verify_bundle",
    "write_bundle",
    "write_run_report",
]