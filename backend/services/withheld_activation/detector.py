"""Activation detector + re-adjudication engine.

The detector makes the WITHHELD -> decided transition *caused by data* instead
of by memory.  Each pass compares, per subject, the digest of the current
reference row against the digest the stored verdict was decided against:

======================  ==============================  =====================
reference digest moved  meaning                         trigger recorded
======================  ==============================  =====================
None -> d               reference truth arrived         ``activation``
d1   -> d2              reference truth corrected       ``reference_update``
d    -> None            reference truth withdrawn       ``withdrawal``
no change               nothing to do (idempotent)      *no record*
======================  ==============================  =====================

Re-adjudication is a pure function of (computed value, reference value,
tolerance)::

    error_pct = |computed - reference| / |reference| * 100
    verdict   = PASS if error_pct <= tolerance_pct else FAIL

and ``WITHHELD`` whenever the reference row is missing or unusable.  Nothing
here knows what the quantity means: the numbers arrive through the reference
table and the ledger, so the mechanism stays domain-neutral.

Audit record fields (one line per transition, append-only)
---------------------------------------------------------
``seq``, ``ts``, ``actor``(=``auto``), ``trigger``, ``subject_id``,
``old_verdict``, ``new_verdict``, ``old_digest``, ``new_digest``,
``sha256`` (digest of the reference row that drove the transition),
``computed_value``, ``reference_value``, ``tolerance_pct``, ``error_pct``,
``reason``, ``idempotency_key``.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .ledger import (
    TRIGGER_ACTIVATION,
    TRIGGER_REFERENCE_UPDATE,
    TRIGGER_WITHDRAWAL,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_WITHHELD,
    CriterionLedger,
    ReferenceStore,
    canonical_json,
    reference_row_digest,
    sha256_hex,
    utc_now,
)


@dataclass(frozen=True)
class Adjudication:
    """Outcome of one re-judgement."""

    verdict: str
    error_pct: Optional[float]
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "error_pct": self.error_pct, "reason": self.reason}


def relative_error_pct(computed_value: float, reference_value: float) -> float:
    """Relative error in percent.  Reference must be finite and non-zero."""
    if reference_value == 0.0:
        raise ZeroDivisionError("relative error against a zero reference is undefined")
    return abs(float(computed_value) - float(reference_value)) / abs(float(reference_value)) * 100.0


def adjudicate(computed_value: float, reference_row: Optional[Mapping[str, Any]],
               tolerance_pct: float) -> Adjudication:
    """Decide one criterion against the reference truth currently on file."""
    if not reference_row:
        return Adjudication(VERDICT_WITHHELD, None, "no_reference_truth")
    try:
        reference_value = float(reference_row["reference_value"])
    except (KeyError, TypeError, ValueError):
        return Adjudication(VERDICT_WITHHELD, None, "reference_value_unusable")
    if not math.isfinite(reference_value) or reference_value == 0.0:
        return Adjudication(VERDICT_WITHHELD, None, "reference_value_unusable")
    error = relative_error_pct(computed_value, reference_value)
    verdict = VERDICT_PASS if error <= float(tolerance_pct) else VERDICT_FAIL
    return Adjudication(verdict, error, "reference_present")


def _transition(previous_digest: Optional[str], current_digest: Optional[str]) -> Optional[str]:
    if current_digest == previous_digest:
        return None
    if previous_digest is None:
        return TRIGGER_ACTIVATION
    if current_digest is None:
        return TRIGGER_WITHDRAWAL
    return TRIGGER_REFERENCE_UPDATE


def scan(ledger: CriterionLedger, store: ReferenceStore, *,
         actor: str = "auto", dry_run: bool = False) -> Dict[str, Any]:
    """One detector pass.  Returns a machine-readable report.

    The pass is serialised (advisory lock), refuses to run on a tampered counter
    block (write-time recompute gate) and appends at most one audit record per
    subject per digest change.
    """
    started = time.time()
    with ledger.transaction():
        # Re-read under the lock: another detector process may have appended the
        # record for this digest change while we were waiting for the lock.
        ledger.reload()
        ledger.enforce_gate()
        events: list[Dict[str, Any]] = []
        noop: list[str] = []
        known_keys = ledger.audit_idempotency_keys()
        # Position in the append-only log: part of the idempotency key so that a
        # replay at the same log position is dropped, while a genuinely later
        # re-activation (same pair of digests, new position) is recorded.
        position = len(ledger.audit_records())
        for subject_id in sorted(ledger.criteria):
            criterion = ledger.criteria[subject_id]
            current_digest = store.digest(subject_id)
            previous_digest = criterion.get("reference_digest")
            trigger = _transition(previous_digest, current_digest)
            if trigger is None:
                noop.append(subject_id)
                continue
            row = store.row(subject_id)
            outcome = adjudicate(criterion["computed_value"], row, criterion["tolerance_pct"])
            old_verdict = criterion.get("verdict", VERDICT_WITHHELD)
            idempotency_key = sha256_hex(canonical_json({
                "subject_id": subject_id,
                "audit_position": position + 1,
                "previous_digest": previous_digest,
                "current_digest": current_digest,
                "old_verdict": old_verdict,
                "new_verdict": outcome.verdict,
            }))
            if idempotency_key in known_keys and not dry_run:
                # Defence in depth: the digest comparison above already prevents
                # replays, this catches a restored-from-backup ledger.
                noop.append(subject_id)
                continue
            record = {
                "ts": utc_now(),
                "actor": actor,
                "trigger": trigger,
                "subject_id": subject_id,
                "old_verdict": old_verdict,
                "new_verdict": outcome.verdict,
                "old_digest": previous_digest,
                "new_digest": current_digest,
                "sha256": current_digest or previous_digest,
                "computed_value": criterion["computed_value"],
                "reference_value": None if row is None else row.get("reference_value"),
                "tolerance_pct": criterion["tolerance_pct"],
                "error_pct": outcome.error_pct,
                "reason": outcome.reason,
                "idempotency_key": idempotency_key,
            }
            if not dry_run:
                record = ledger.append_audit(record)
                known_keys.add(idempotency_key)
                position = record["seq"]
                criterion.update({
                    "verdict": outcome.verdict,
                    "error_pct": outcome.error_pct,
                    "reason": outcome.reason,
                    "reference_digest": current_digest,
                    "decided_at": record["ts"],
                    "audit_seq": record["seq"],
                })
            events.append({k: record.get(k) for k in (
                "seq", "ts", "actor", "trigger", "subject_id", "old_verdict",
                "new_verdict", "sha256", "error_pct", "reason")})
        counters = ledger.recomputed_counters() if dry_run else ledger.save()
        verdicts = {sid: ledger.criteria[sid]["verdict"] for sid in sorted(ledger.criteria)}
    return {
        "dry_run": dry_run,
        "audit_appended": 0 if dry_run else len(events),
        "events": events,
        "noop_subjects": noop,
        "verdicts": verdicts,
        "counters": counters,
        "table_digest": store.table_digest(),
        "elapsed_s": round(time.time() - started, 6),
    }


def reactivate(ledger: CriterionLedger, store: ReferenceStore, subject_id: str, *,
               quantity: str, reference_value: float, source: str = "external_feed",
               uncertainty_pct: Optional[float] = None,
               actor: str = "auto") -> Dict[str, Any]:
    """Convenience: write one reference row, then run one detector pass."""
    store.upsert(subject_id, quantity=quantity, reference_value=reference_value,
                 source=source, uncertainty_pct=uncertainty_pct)
    report = scan(ledger, store, actor=actor)
    report["injected"] = {"subject_id": subject_id, "reference_value": float(reference_value),
                          "row_digest": store.digest(subject_id)}
    return report


def withdraw(ledger: CriterionLedger, store: ReferenceStore, subject_id: str, *,
             actor: str = "auto") -> Dict[str, Any]:
    """Delete one reference row and re-run the detector (verdict falls back)."""
    removed = store.delete(subject_id)
    report = scan(ledger, store, actor=actor)
    report["withdrawn"] = {"subject_id": subject_id, "removed": removed}
    return report


def digest_of_row(row: Mapping[str, Any]) -> Optional[str]:
    return reference_row_digest(row)

def watch(ledger: CriterionLedger, store: ReferenceStore, *, interval_s: float = 60.0,
          timeout_s: Optional[float] = None, max_passes: Optional[int] = None,
          stop_on_event: bool = False, actor: str = "auto") -> Dict[str, Any]:
    """Polling detector: the self-healing half of the mechanism.

    Repeatedly runs :func:`scan` until one of the stopping conditions is hit, so a
    reference row that lands in the table *while nobody is looking* still flips
    the verdict.  The platform's default interval (60 s) is well inside the
    5-minute activation budget of the maturity order.
    """
    started = time.time()
    passes = 0
    events: list[Dict[str, Any]] = []
    while True:
        # The reference feed and the ledger may be written by other processes:
        # pick up whatever is on disk before judging.
        ledger.reload()
        store = store.reload()
        report = scan(ledger, store, actor=actor)
        passes += 1
        events.extend(report["events"])
        if stop_on_event and report["events"]:
            break
        if max_passes is not None and passes >= max_passes:
            break
        elapsed = time.time() - started
        if timeout_s is not None and elapsed + interval_s > timeout_s:
            break
        time.sleep(interval_s)
    return {
        "passes": passes,
        "events": events,
        "elapsed_s": round(time.time() - started, 6),
        "interval_s": interval_s,
        "verdicts": {sid: ledger.criteria[sid]["verdict"] for sid in sorted(ledger.criteria)},
    }
