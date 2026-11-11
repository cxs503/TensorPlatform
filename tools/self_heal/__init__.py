"""Stalled-mission self-healing pipeline (detect → classify → recover → ledger).

Why this package exists
-----------------------
Autonomy level L3 asks the platform to keep itself alive without a human in the
loop.  The missing piece was *mission-level* self-healing: a mission whose
executor died keeps the status ``running`` forever, a mission stuck in the
queue keeps ``pending`` forever, and nobody notices because the process that
would have noticed is the one that died.

This package is storage-agnostic on purpose:

* :mod:`tools.self_heal.detector`    — which rows are stalled (pure functions);
* :mod:`tools.self_heal.classifier`  — six root causes from observable signals;
* :mod:`tools.self_heal.recovery`    — bounded recovery policy per cause;
* :mod:`tools.self_heal.executor`    — effect adapter (dry-run / SQL backed);
* :mod:`tools.self_heal.ledger`      — tamper-evident, idempotent audit chain;
* :mod:`tools.self_heal.pipeline`    — one deterministic healing window.

The package contains **no application-domain vocabulary** (machine-checked by
``tools.benchmark_runner.domain_guard``); missions are opaque rows.

Public entry points::

    from tools.self_heal import run_pipeline, SelfHealLedger, DEFAULT_THRESHOLDS

    ledger = SelfHealLedger()
    result = run_pipeline(rows, now=time.time(), window_id="W1", ledger=ledger)
    result.detected, result.entries, result.ledger_digest
"""
from __future__ import annotations

from .classifier import Classification, HealContext, classify
from .detector import Detection, detect_stalled
from .executor import ActionOutcome, DryRunExecutor, Executor, SqlMissionExecutor
from .ledger import LedgerEntry, SelfHealLedger
from .model import (
    ACTIVE_STATUSES,
    CAUSES,
    DEFAULT_THRESHOLDS,
    QUEUED_STATUSES,
    RECOVERABLE_CAUSES,
    TERMINAL_STATUSES,
    MissionRow,
    Thresholds,
)
from .pipeline import PipelineResult, run_pipeline
from .recovery import RecoveryPlan, plan_recovery

__all__ = [
    "ACTIVE_STATUSES",
    "CAUSES",
    "DEFAULT_THRESHOLDS",
    "QUEUED_STATUSES",
    "RECOVERABLE_CAUSES",
    "TERMINAL_STATUSES",
    "ActionOutcome",
    "Classification",
    "Detection",
    "DryRunExecutor",
    "Executor",
    "HealContext",
    "LedgerEntry",
    "MissionRow",
    "PipelineResult",
    "RecoveryPlan",
    "SelfHealLedger",
    "SqlMissionExecutor",
    "Thresholds",
    "classify",
    "detect_stalled",
    "plan_recovery",
    "run_pipeline",
]

SCHEMA = "self_heal/v1"