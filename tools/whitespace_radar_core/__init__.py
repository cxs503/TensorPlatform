"""Whitespace radar: pool coverage index + reproducible blank verdicts.

Public surface (see ``docs/whitespace_radar/README.md``)::

    from tools.whitespace_radar_core import (
        Thresholds, analyze, load_pool, pool_digest, Bm25Index, replay,
    )

The package is stdlib-only and holds **no subject-matter vocabulary**: the pool
is described by a JSON manifest, the topics come from the caller, and the only
built-in lists are natural-language particles (see ``tokenize``).
"""
from __future__ import annotations

from .adjudicate import (
    ADJACENT,
    BLANK,
    COVERED,
    COVERING,
    GENERIC_ONLY,
    LABELS,
    PARTIAL,
    Thresholds,
    analyze,
    corroboration,
    generic_ledger,
    triage,
)
from .bridge import GATE_SCHEMA, dedup_gate
from .bench import (
    BENCH_SCHEMA,
    FALSE_COVERED_SCHEMA,
    calibration_curve,
    load_bench,
    load_false_covered,
    passes_acceptance,
    replay,
    replay_false_covered,
)
from .evidence import SCHEMA, check_compatible, dumps, seal, verify
from .index import Bm25Index
from .sources import (
    PIN_SCHEMA,
    POOL_SCHEMA,
    Doc,
    apply_pin,
    build_pin,
    doc_digest,
    load_campaign_export,
    load_module_manifest,
    load_pin,
    load_pool,
    pin_report,
    pool_digest,
)
from .tokenize import terms, tokenize

__all__ = [
    "BLANK",
    "COVERED",
    "GENERIC_ONLY",
    "ADJACENT",
    "COVERING",
    "PARTIAL",
    "LABELS",
    "Thresholds",
    "analyze",
    "triage",
    "Bm25Index",
    "load_pool",
    "pool_digest",
    "load_pin",
    "build_pin",
    "apply_pin",
    "pin_report",
    "doc_digest",
    "PIN_SCHEMA",
    "FALSE_COVERED_SCHEMA",
    "load_false_covered",
    "replay_false_covered",
    "corroboration",
    "generic_ledger",
    "Doc",
    "load_module_manifest",
    "load_campaign_export",
    "POOL_SCHEMA",
    "BENCH_SCHEMA",
    "load_bench",
    "replay",
    "passes_acceptance",
    "calibration_curve",
    "SCHEMA",
    "GATE_SCHEMA",
    "dedup_gate",
    "seal",
    "verify",
    "check_compatible",
    "dumps",
    "tokenize",
    "terms",
]
__version__ = "1.0.0"