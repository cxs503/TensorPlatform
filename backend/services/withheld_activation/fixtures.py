"""Synthetic fixtures for the reference-activation mechanism.

The mechanism is platform infrastructure, so its fixtures carry **no domain
vocabulary** (TensorPlatform iron rule: domain data arrives through artifacts and
configuration, never through platform code).  Subjects and quantities are named
``synthetic_a`` / ``synthetic_b`` / ``synthetic_quantity_*``; the numbers are
arbitrary bookkeeping values chosen so that the two fixtures exercise opposite
verdict branches:

===========  ==============  ================  ==============  ==========
subject      computed        reference         error           verdict
===========  ==============  ================  ==============  ==========
synthetic_a  100.0           101.0             0.990 % (< 3 %)  PASS
synthetic_b   40.0            50.0             20.0 % (> 5 %)  FAIL
===========  ==============  ================  ==============  ==========
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .ledger import CriterionLedger, ReferenceStore

DEMO_CRITERIA = (
    {"subject_id": "synthetic_a", "computed_value": 100.0, "tolerance_pct": 3.0},
    {"subject_id": "synthetic_b", "computed_value": 40.0, "tolerance_pct": 5.0},
)

DEMO_REFERENCES = (
    {"subject_id": "synthetic_a", "quantity": "synthetic_quantity_alpha",
     "reference_value": 101.0, "uncertainty_pct": 1.0, "source": "synthetic_source_a"},
    {"subject_id": "synthetic_b", "quantity": "synthetic_quantity_beta",
     "reference_value": 50.0, "uncertainty_pct": 2.0, "source": "synthetic_source_b"},
)


def seed(state_dir: Path, *, with_references: bool = False,
         criteria: Optional[tuple] = None,
         references: Optional[tuple] = None) -> Dict[str, Any]:
    """Create (or extend) a ledger in ``state_dir``; references default to absent.

    Starting without references is the normal case: the subjects are registered,
    filed ``WITHHELD`` and wait for the reference table to fill in.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger = CriterionLedger(state_dir / "criteria.json")
    store = ReferenceStore(state_dir / "reference_truth.json")
    created = []
    for spec in (criteria or DEMO_CRITERIA):
        if spec["subject_id"] not in ledger.criteria:
            ledger.add_criterion(spec["subject_id"], computed_value=spec["computed_value"],
                                 tolerance_pct=spec["tolerance_pct"], persist=False)
            created.append(spec["subject_id"])
    if with_references:
        for row in (references or DEMO_REFERENCES):
            store.upsert(row["subject_id"], quantity=row["quantity"],
                         reference_value=row["reference_value"],
                         source=row.get("source", "synthetic_source"),
                         uncertainty_pct=row.get("uncertainty_pct"), persist=False)
        store.save()
    ledger.save()
    return {"state_dir": str(state_dir), "created": created,
            "criteria": sorted(ledger.criteria),
            "references": sorted(store.rows())}


def open_pair(state_dir: Path) -> tuple[CriterionLedger, ReferenceStore]:
    state_dir = Path(state_dir)
    return (CriterionLedger(state_dir / "criteria.json"),
            ReferenceStore(state_dir / "reference_truth.json"))