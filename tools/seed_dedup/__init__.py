"""Seed deduplication: BM25 + char 3-gram Jaccard dual-channel.

Public surface::

    from tools.seed_dedup import (
        SeedConfig, Candidate, Node, LedgerEntry,
        dual_similarity, adjudicate, ledger_seal,
        build_benchmark, evaluate_benchmark,
    )

Zero domain semantics: all inputs are opaque strings.
"""
from __future__ import annotations

from .core import (
    CALIBRATED_THETA,
    SeedConfig,
    dual_similarity,
    DualSim,
    char_3gram_jaccard,
    char_3grams,
)
from .ledger import (
    adjudicate,
    ledger_seal,
    load_ledger,
    Candidate,
    Node,
    LedgerEntry,
    ALLOW,
    BLOCK,
    CODES,
    DELTA_CHANNELS,
    SCHEMA,
    VERSION,
)
from .bench import (
    build_benchmark,
    evaluate_benchmark,
    DEFAULT_PAIRS_PATH,
)

__all__ = [
    "SeedConfig",
    "CALIBRATED_THETA",
    "Candidate",
    "Node",
    "LedgerEntry",
    "dual_similarity",
    "DualSim",
    "char_3gram_jaccard",
    "char_3grams",
    "adjudicate",
    "ledger_seal",
    "load_ledger",
    "ALLOW",
    "BLOCK",
    "CODES",
    "DELTA_CHANNELS",
    "SCHEMA",
    "VERSION",
    "build_benchmark",
    "evaluate_benchmark",
    "DEFAULT_PAIRS_PATH",
]
__version__ = "p33w3-n12-v1"
