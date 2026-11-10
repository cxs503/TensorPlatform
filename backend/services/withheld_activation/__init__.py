"""Content-addressed verdict ledger for reference-gated judgements.

Public surface
--------------
``ledger``    reference truth table, criterion ledger, audit log and the
              write-time recompute gate.
``detector``  activation detector, re-adjudication engine and polling watcher.
``fixtures``  synthetic subjects (``synthetic_a`` / ``synthetic_b``) used by the
              acceptance run and the test-suite; no domain vocabulary.
``acceptance``machine-checkable criteria A..F of the maturity order.
"""
from __future__ import annotations

from .ledger import (  # noqa: F401
    COUNTER_FIELDS,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_WITHHELD,
    CounterMismatch,
    CriterionLedger,
    ReferenceStore,
    ReferenceValueError,
    recompute_counters,
)
from .detector import (  # noqa: F401
    Adjudication,
    adjudicate,
    reactivate,
    relative_error_pct,
    scan,
    watch,
    withdraw,
)

__all__ = [
    "COUNTER_FIELDS",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_WITHHELD",
    "Adjudication",
    "CounterMismatch",
    "CriterionLedger",
    "ReferenceStore",
    "ReferenceValueError",
    "adjudicate",
    "reactivate",
    "recompute_counters",
    "relative_error_pct",
    "scan",
    "watch",
    "withdraw",
]