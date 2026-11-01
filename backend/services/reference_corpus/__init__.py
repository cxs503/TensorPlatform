"""Reference-value corpus and digitisation service.

Public surface
--------------
``ReferenceCorpus``
    Load / schema-validate / sha256-anchor / serve reference entries.
``digitise``
    CSV artefact reading and the mandatory dual-pass (two independent
    transcriptions) gate.
``interpolate``
    Monotone PCHIP and linear interpolation + interpolation consistency.
``adjudicate``
    Wave-8 acceptance ledger and corpus-driven re-adjudication.
``api``
    FastAPI router: ``GET /api/v1/reference/{id}`` and friends.
"""
from .adjudicate import build_ledger, readjudicate
from .corpus import DEFAULT_ROOT, ReferenceCorpus, ReferenceEntry, default_root
from .errors import (
    DualPassViolation,
    EntryNotFound,
    IntegrityError,
    OutOfRangeError,
    ReferenceCorpusError,
    SchemaViolation,
)
from .interpolate import interpolation_consistency, linear, pchip
from .digitise import dual_pass_report, read_table, require_dual_pass, sha256_file

__all__ = [
    "ReferenceCorpus",
    "ReferenceEntry",
    "DEFAULT_ROOT",
    "default_root",
    "ReferenceCorpusError",
    "SchemaViolation",
    "IntegrityError",
    "OutOfRangeError",
    "EntryNotFound",
    "DualPassViolation",
    "build_ledger",
    "readjudicate",
    "pchip",
    "linear",
    "interpolation_consistency",
    "read_table",
    "sha256_file",
    "dual_pass_report",
    "require_dual_pass",
]