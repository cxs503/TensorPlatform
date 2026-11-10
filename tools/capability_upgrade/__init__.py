"""Capability evidence auto-upgrade pipeline (P218-N01).

Public surface::

    from tools.capability_upgrade import Store, EvidencePipeline, RejudgeEngine

    store = Store(":memory:")
    store.upsert_slot("synth.alpha", initial_level="WITHHELD")
    pipe = EvidencePipeline(store)
    result = pipe.ingest("synth.alpha", kind="numeric_verification",
                         metrics={"error_pct": 1.1},
                         source={"path": "case.json", "sha256": "<64 hex>"})

See ``tools/capability_upgrade/README.md`` for the acceptance criteria mapping.
"""
from __future__ import annotations

from .engine import RejudgeEngine
from .model import (
    LEVELS,
    LEVEL_RANK,
    CapabilitySlot,
    Criterion,
    CriterionVerdict,
    EvidenceItem,
    level_rank,
)
from .pipeline import EvidencePipeline
from .registry import apply_registry, load_registry
from .store import Store

__all__ = [
    "LEVELS",
    "LEVEL_RANK",
    "CapabilitySlot",
    "Criterion",
    "CriterionVerdict",
    "EvidenceItem",
    "EvidencePipeline",
    "RejudgeEngine",
    "Store",
    "apply_registry",
    "level_rank",
    "load_registry",
]