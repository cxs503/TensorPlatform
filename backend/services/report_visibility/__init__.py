"""Report write-read visibility contract + lost-write detection & self-healing.

Public surface
--------------
``assert_readback``          write-read contract assertion (pure function)
``SubmissionLedger``         append-only, hash-chained write-side ground truth
``PayloadStore``             content-addressed payload snapshots for re-sending
``LostReportProbe``          submitted ∖ readable diff with grace window
``LostReportHealer``         bounded re-send + verify + structured alerts
``ReportVisibilityService``  glue: after-write assertion, scan, heal

Why it exists: a POST can answer 200 while a later GET cannot see the row. That
loss is silent and only shows up as a coverage metric quietly drifting down.
This package makes it a *detected event* (assertion + probe) and gives it a
bounded, auditable repair path (healer).
"""
from __future__ import annotations

from .contract import (
    CONTRACT_FIELDS,
    FieldCheck,
    ReadbackVerdict,
    assert_readback,
    canonical,
    digest,
    figures_projection,
    first_divergence,
    normalize_markdown,
)
from .heal import (
    ALERT_HEAL_EXHAUSTED,
    ALERT_HEAL_OK,
    ALERT_LOST_DETECTED,
    ALERT_UNHEALABLE,
    HealOutcome,
    HealPolicy,
    HealReport,
    LostReportHealer,
)
from .ledger import (
    BROKEN_KINDS,
    DEAD_KINDS,
    KIND_HEALED,
    KIND_HEAL_FAILED,
    KIND_LOST,
    KIND_SUBMITTED,
    KIND_UNHEALABLE,
    KIND_VERIFIED,
    LIVE_KINDS,
    LedgerEntry,
    PayloadStore,
    SubmissionLedger,
    SubmissionState,
    ledger_dir_from_env,
    open_ledger,
    payload_digest,
)
from .probe import LostReportProbe, ProbeResult
from .service import (
    ReportVisibilityService,
    WriteVerification,
    default_ledger,
    ledger_from_env,
)

__all__ = [
    "CONTRACT_FIELDS", "FieldCheck", "ReadbackVerdict", "assert_readback",
    "canonical", "digest", "figures_projection", "first_divergence",
    "normalize_markdown",
    "ALERT_HEAL_EXHAUSTED", "ALERT_HEAL_OK", "ALERT_LOST_DETECTED",
    "ALERT_UNHEALABLE", "HealOutcome", "HealPolicy", "HealReport",
    "LostReportHealer",
    "BROKEN_KINDS", "DEAD_KINDS", "KIND_HEALED", "KIND_HEAL_FAILED", "KIND_LOST",
    "KIND_SUBMITTED", "KIND_UNHEALABLE", "KIND_VERIFIED", "LIVE_KINDS",
    "LedgerEntry", "PayloadStore", "SubmissionLedger", "SubmissionState",
    "ledger_dir_from_env", "open_ledger", "payload_digest",
    "LostReportProbe", "ProbeResult",
    "ReportVisibilityService", "WriteVerification", "default_ledger",
    "ledger_from_env",
]
