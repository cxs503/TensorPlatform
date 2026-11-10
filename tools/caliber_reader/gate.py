"""Downstream error-decision gate (fail-closed on missing caliber).

Every comparison of a computed number against a benchmark number is an *error
judgement*, and an error judgement is only defined inside one caliber.  This gate
makes that precondition executable:

* ``caliber`` must be present, carry ``scale`` in {reduced, full}, an evidence
  ``source`` (the id of the verdict that established it) and a ``verdict_id``;
* the two values must live on the *same* scale;
* otherwise the gate returns ``decision="REJECT"`` with a machine reason, writes an
  audit event, and the caller must withhold (never silently compare).

The gate knows nothing about which quantity is compared; tolerances and scales are
parameters.  Audit events are appended as one JSON object per line so a whole run
can be replayed and hash-chained.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence

VALID_SCALES = ("reduced", "full")
REQUIRED_CALIBER_FIELDS = ("scale", "source", "verdict_id")

ACCEPT_PASS = "PASS"
ACCEPT_FAIL = "FAIL"
REJECT_MISSING = "REJECT_CALIBER_MISSING"
REJECT_INVALID = "REJECT_CALIBER_INVALID"
REJECT_SCALE_MISMATCH = "REJECT_SCALE_MISMATCH"
REJECT_NOT_ALLOWED = "REJECT_VERDICT_NOT_ALLOWED"


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def event_sha256(event: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()


@dataclass
class AuditLog:
    """Append-only JSONL audit sink (also keeps events in memory for tests)."""

    path: Optional[str] = None
    events: list = field(default_factory=list)

    def append(self, event: Mapping[str, object]) -> Dict[str, object]:
        record = dict(event)
        record["sha256"] = event_sha256(event)
        record["prev_sha256"] = self.events[-1]["sha256"] if self.events else ""
        self.events.append(record)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        return record


def judge_error(observed: Optional[float], reference: Optional[float], *,
                caliber: Optional[Mapping[str, object]],
                tolerance_pct: float = 15.0,
                case_id: str = "",
                observed_scale: Optional[str] = None,
                audit: Optional[AuditLog] = None,
                extra: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
    """Compare ``observed`` with ``reference`` inside one caliber.

    Returns a dict with ``decision``, ``reason``, ``error_pct`` and the audit
    ``event`` (whose ``sha256`` chained into ``prev_sha256`` gives a tamper-evident
    trail).  Rejection is total: no ``error_pct`` is produced without a caliber.
    """
    audit = audit if audit is not None else AuditLog()
    issues = []
    if caliber is None:
        issues.append("caliber field absent")
        decision = REJECT_MISSING
    else:
        missing = [f for f in REQUIRED_CALIBER_FIELDS if caliber.get(f) in (None, "")]
        if missing:
            issues.append(f"caliber missing fields {missing}")
        scale = str(caliber.get("scale", "")).lower()
        if scale not in VALID_SCALES:
            issues.append(f"caliber scale {scale!r} not in {VALID_SCALES}")
        decision = REJECT_MISSING if not issues else REJECT_INVALID
    scale = str((caliber or {}).get("scale", "")).lower()
    if not issues and observed_scale is not None and str(observed_scale).lower() != scale:
        issues.append(f"observed scale {observed_scale!r} != caliber scale {scale!r}")
        decision = REJECT_SCALE_MISMATCH
    if not issues and caliber is not None and caliber.get("error_decision_allowed") is False:
        issues.append("the caliber verdict does not permit an error judgement")
        decision = REJECT_NOT_ALLOWED
    if not issues:
        if observed is None or reference is None:
            issues.append("missing observed/reference value")
            decision = REJECT_INVALID
        else:
            observed = float(observed)
            reference = float(reference)
            if reference == 0.0:
                issues.append("reference value is zero: relative error undefined")
                decision = REJECT_INVALID
    event: Dict[str, object] = {
        "kind": "error_judgement",
        "case_id": case_id,
        "timestamp_ns": time.time_ns(),
        "caliber": dict(caliber) if caliber else None,
        "observed": observed,
        "reference": reference,
        "observed_scale": observed_scale,
        "tolerance_pct": float(tolerance_pct),
    }
    if extra:
        event["extra"] = dict(extra)
    if issues:
        event.update({"decision": decision, "reason": "; ".join(issues),
                      "error_pct": None, "within_tolerance": None})
        record = audit.append(event)
        return {"decision": decision, "reason": "; ".join(issues), "error_pct": None,
                "within_tolerance": None, "audit_event": record}
    error_pct = (observed - reference) / reference * 100.0
    within = abs(error_pct) <= float(tolerance_pct)
    event.update({"decision": ACCEPT_PASS if within else ACCEPT_FAIL,
                  "reason": "caliber present and consistent",
                  "error_pct": float(error_pct), "within_tolerance": bool(within)})
    record = audit.append(event)
    return {"decision": ACCEPT_PASS if within else ACCEPT_FAIL,
            "reason": "caliber present and consistent",
            "error_pct": float(error_pct), "within_tolerance": bool(within),
            "audit_event": record}


def rejection_rate(cases: Sequence[Mapping[str, object]], *,
                   tolerance_pct: float = 15.0,
                   audit: Optional[AuditLog] = None) -> Dict[str, object]:
    """Run a batch of gate calls and report the rejection statistics.

    Each case is ``{observed, reference, caliber, observed_scale, case_id}``.
    ``caliber`` may be ``None`` (the negative control: the field was dropped).
    """
    audit = audit if audit is not None else AuditLog()
    decisions: Dict[str, int] = {}
    rejected_missing = 0
    accepted = 0
    for case in cases:
        res = judge_error(case.get("observed"), case.get("reference"),
                          caliber=case.get("caliber"),
                          tolerance_pct=float(case.get("tolerance_pct", tolerance_pct)),
                          case_id=str(case.get("case_id", "")),
                          observed_scale=case.get("observed_scale"),
                          audit=audit)
        decisions[res["decision"]] = decisions.get(res["decision"], 0) + 1
        if res["decision"] == REJECT_MISSING:
            rejected_missing += 1
        if res["decision"] in (ACCEPT_PASS, ACCEPT_FAIL):
            accepted += 1
    n = len(cases)
    return {
        "n_cases": n,
        "decisions": decisions,
        "rejected_missing": rejected_missing,
        "rejected_missing_rate": rejected_missing / n if n else 0.0,
        "accepted": accepted,
        "accept_rate": accepted / n if n else 0.0,
        "audit_events": len(audit.events),
        "audit_chain_head_sha256": audit.events[-1]["sha256"] if audit.events else "",
    }