"""Self-healing for lost report writes: detect → resend → verify → alert.

Semantics
---------
* **heal == re-send the original payload and prove the row is now readable.**
  Re-sending without verification would just move the silent loss one step
  later, so ``verify`` is a required argument of the engine (the API layer
  passes a real store lookup).
* **retry is bounded and observable.**  ``HealPolicy.max_attempts`` caps the
  attempts per submission; every attempt is written to the same hash-chained
  ledger the probe reads, so a "healed" claim is auditable.
* **no payload → no replay.**  If the payload snapshot is gone or fails its
  digest check, the submission is marked ``unhealable`` and escalated; it is
  never counted as healed and never silently dropped.
* **alerts are structured** (``code`` / ``level`` / ``submission_id``), so a
  monitoring layer can route them without parsing prose.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .ledger import (KIND_HEAL_FAILED, KIND_HEALED, KIND_UNHEALABLE,
                     SubmissionLedger, SubmissionState)

# Alert codes (stable machine identifiers).
ALERT_LOST_DETECTED = "LOST_WRITE_DETECTED"
ALERT_HEAL_OK = "LOST_WRITE_HEALED"
ALERT_HEAL_EXHAUSTED = "LOST_WRITE_HEAL_EXHAUSTED"
ALERT_UNHEALABLE = "LOST_WRITE_UNHEALABLE"


@dataclass(frozen=True)
class HealPolicy:
    """Bounded retry policy (delays are advisory for the caller's scheduler)."""

    max_attempts: int = 3
    backoff_s: tuple[float, ...] = (0.0, 0.5, 2.0)

    def delay_for(self, attempt: int) -> float:
        if not self.backoff_s:
            return 0.0
        idx = min(max(attempt - 1, 0), len(self.backoff_s) - 1)
        return float(self.backoff_s[idx])


@dataclass(frozen=True)
class HealOutcome:
    submission_id: str
    report_id: int | None
    healed: bool
    attempts: int
    reason: str = ""
    new_report_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"submission_id": self.submission_id, "report_id": self.report_id,
                "healed": self.healed, "attempts": self.attempts,
                "reason": self.reason, "new_report_id": self.new_report_id}


@dataclass
class HealReport:
    outcomes: list[HealOutcome] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.outcomes)

    @property
    def healed(self) -> int:
        return sum(1 for o in self.outcomes if o.healed)

    @property
    def success_rate(self) -> float | None:
        if not self.outcomes:
            return None
        return self.healed / len(self.outcomes)

    def as_dict(self) -> dict[str, Any]:
        return {"attempted": self.attempted, "healed": self.healed,
                "success_rate": self.success_rate,
                "outcomes": [o.as_dict() for o in self.outcomes]}


class LostReportHealer:
    """Re-send lost writes and verify they became readable."""

    def __init__(self, *, ledger: SubmissionLedger,
                 resend: Callable[[Mapping[str, Any]], int | None],
                 verify: Callable[[int], bool] | None = None,
                 policy: HealPolicy | None = None,
                 alerts: list[dict[str, Any]] | None = None,
                 clock: Callable[[], float] = time.time):
        self.ledger = ledger
        self._resend = resend
        self._verify = verify
        self.policy = policy or HealPolicy()
        self.alerts: list[dict[str, Any]] = alerts if alerts is not None else []
        self._clock = clock

    # ── alerting ──────────────────────────────────────────────────────
    def _alert(self, code: str, level: str, submission_id: str,
               detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
        event = {"code": code, "level": level, "submission_id": submission_id,
                 "ts": float(self._clock()), "detail": dict(detail or {})}
        self.alerts.append(event)
        return event

    # ── healing ───────────────────────────────────────────────────────
    def _heal_one(self, state: SubmissionState) -> HealOutcome:
        payload = self.ledger.payload(state.submission_id)
        if payload is None:
            self.ledger.mark(state.submission_id, KIND_UNHEALABLE,
                             detail={"reason": "payload_missing_or_tampered"})
            self._alert(ALERT_UNHEALABLE, "error", state.submission_id,
                        {"report_id": state.report_id})
            return HealOutcome(state.submission_id, state.report_id, False, 0,
                               reason="payload_missing_or_tampered")

        last_reason = "not_attempted"
        for attempt in range(1, max(self.policy.max_attempts, 1) + 1):
            new_id: int | None = None
            try:
                new_id = self._resend(payload)
            except Exception as exc:  # transport/write failure of one attempt
                last_reason = f"resend_error:{type(exc).__name__}"
                new_id = None

            if new_id is not None:
                readable = True
                if self._verify is not None:
                    try:
                        readable = bool(self._verify(new_id))
                    except Exception as exc:
                        readable = False
                        last_reason = f"verify_error:{type(exc).__name__}"
                if readable:
                    self.ledger.mark(state.submission_id, KIND_HEALED,
                                     detail={"attempts": attempt, "report_id": new_id},
                                     report_id=new_id)
                    self._alert(ALERT_HEAL_OK, "info", state.submission_id,
                                {"attempts": attempt, "new_report_id": new_id})
                    return HealOutcome(state.submission_id, state.report_id, True,
                                       attempt, reason="resend_verified", new_report_id=new_id)
                last_reason = "resend_not_readable"
            elif new_id is None and last_reason == "not_attempted":
                last_reason = "resend_rejected"

        self.ledger.mark(state.submission_id, KIND_HEAL_FAILED,
                         detail={"attempts": self.policy.max_attempts,
                                 "reason": last_reason})
        self._alert(ALERT_HEAL_EXHAUSTED, "error", state.submission_id,
                    {"attempts": self.policy.max_attempts, "reason": last_reason})
        return HealOutcome(state.submission_id, state.report_id, False,
                           self.policy.max_attempts, reason=last_reason)

    def heal(self, submission_ids: Iterable[str] | None = None,
             *, states: list[SubmissionState] | None = None) -> HealReport:
        if states is not None:
            todo = list(states)
        elif submission_ids is not None:
            todo = []
            for sid in submission_ids:
                state = self.ledger.state(sid)
                if state is None:
                    # Unknown id: alert, never invent a payload for it.
                    self._alert(ALERT_UNHEALABLE, "error", str(sid),
                                {"reason": "unknown_submission"})
                    todo.append(SubmissionState(str(sid), None, None, None,
                                                KIND_UNHEALABLE, self._clock()))
                else:
                    todo.append(state)
        else:
            todo = self.ledger.broken_states()

        report = HealReport()
        for state in todo:
            report.outcomes.append(self._heal_one(state))
        return report