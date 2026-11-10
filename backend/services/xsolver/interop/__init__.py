"""Cross-solver interoperation gate (campaign integration gate mechanics).

Public surface::

    from services.xsolver.interop import evaluate, load_manifest, summarize

Domain content (which artifact carries which quantity, tolerances, contracts)
lives in ``data/campaigns/<campaign>/`` and ``config/xsolver/`` -- never here.

Modules
-------
``units``   unit spellings / scale factors / dimension vectors (injected table)
``checks``  declarative check kinds (series, band, trend, contract, orders, ...)
``gate``    manifest validation, artifact resolution (sha256 pinned), verdicts
"""
from __future__ import annotations

from .checks import CHECKS, FAIL, PASS, WITHHELD, Ctx, run_check
from .gate import (
    GateError,
    canonical_json,
    digest_of,
    evaluate,
    load_manifest,
    resolve_artifacts,
    sha256_file,
    summarize,
    validate_manifest,
    write_verdict,
)
from .units import UnitTable, canonical_dim, dim_str, load_default_table

__all__ = [
    "CHECKS",
    "Ctx",
    "FAIL",
    "GateError",
    "PASS",
    "UnitTable",
    "WITHHELD",
    "canonical_dim",
    "canonical_json",
    "digest_of",
    "dim_str",
    "evaluate",
    "load_default_table",
    "load_manifest",
    "resolve_artifacts",
    "run_check",
    "sha256_file",
    "summarize",
    "validate_manifest",
    "write_verdict",
]
__version__ = "1.0.0"