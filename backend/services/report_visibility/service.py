"""Glue: wire the contract, the probe and the healer into one service.

The service is storage-agnostic on purpose: it receives a ``fetch`` callable
(``report_id -> observed record | None``) and a ``resend`` callable
(``payload -> new report_id | None``).  The API layer binds them to SQLAlchemy;
tests bind them to in-memory fakes; a CLI/cron job can bind them to the SDK.
That keeps the mechanism testable without a database and free of any
domain-specific knowledge.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contract import ReadbackVerdict, assert_readback
from .heal import HealPolicy, HealReport, LostReportHealer
from .ledger import (KIND_LOST, KIND_VERIFIED, SubmissionLedger, ledger_dir_from_env)
from .probe import LostReportProbe, ProbeResult

logger = logging.getLogger(__name__)


@dataclass
class WriteVerification:
    """Result of "write then read back" for one POST."""

    submission_id: str
    report_id: int | None
    verdict: ReadbackVerdict
    ledger_ok: bool = True
    ledger_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict.ok and self.ledger_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "report_id": self.report_id,
            "ok": self.ok,
            "readback": self.verdict.as_dict(),
            "ledger_ok": self.ledger_ok,
            "ledger_error": self.ledger_error,
        }


@dataclass
class ReportVisibilityService:
    """Write-read contract enforcement + lost-write detection + self-healing."""

    ledger: SubmissionLedger
    fetch: Callable[[int], Mapping[str, Any] | None] | None = None
    resend: Callable[[Mapping[str, Any]], int | None] | None = None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    grace_s: float = 0.0
    clock: Callable[[], float] = time.time

    # ── write-read contract ───────────────────────────────────────────
    def after_write(self, payload: Mapping[str, Any], report_id: int | None, *,
                    observed: Mapping[str, Any] | None = None,
                    fetch: Callable[[int], Mapping[str, Any] | None] | None = None
                    ) -> WriteVerification:
        """Assert the just-written row is readable; ledger the verdict.

        ``observed`` allows the caller to pass a fresh read it already made;
        otherwise ``fetch(report_id)`` is used.  A ledger failure is reported
        (``ledger_ok=False``) instead of being swallowed, but it never turns a
        readable write into a failed POST.
        """
        getter = fetch or self.fetch
        if observed is None and report_id is not None and getter is not None:
            observed = getter(report_id)

        verdict = assert_readback(payload, observed)
        submission_id = ""
        ledger_ok, ledger_error = True, None
        try:
            entry = self.ledger.submit(payload, report_id=report_id,
                                       mission_id=payload.get("mission_id"))
            submission_id = entry.submission_id
            self.ledger.mark(submission_id,
                             KIND_VERIFIED if verdict.ok else KIND_LOST,
                             detail={"codes": list(verdict.codes),
                                     "report_id": report_id})
        except Exception as exc:  # ledger is an audit aid, not a gate
            ledger_ok = False
            ledger_error = f"{type(exc).__name__}: {exc}"
            logger.warning("report visibility ledger unavailable: %s", ledger_error)

        if not verdict.ok:
            self.alerts.append({
                "code": "WRITE_READ_VISIBILITY_VIOLATION",
                "level": "error",
                "submission_id": submission_id,
                "ts": float(self.clock()),
                "detail": {"report_id": report_id, "codes": list(verdict.codes)},
            })

        return WriteVerification(submission_id=submission_id, report_id=report_id,
                                 verdict=verdict, ledger_ok=ledger_ok,
                                 ledger_error=ledger_error)

    # ── lost-write detection ──────────────────────────────────────────
    def probe(self, searchable: Callable[[int], Any] | Iterable[int] | None = None,
              *, grace_s: float | None = None) -> LostReportProbe:
        source = searchable if searchable is not None else self.fetch
        if source is None:
            raise ValueError("probe needs a searchable source or a fetch callable")
        return LostReportProbe(source, ledger=self.ledger,
                               grace_s=self.grace_s if grace_s is None else grace_s,
                               clock=self.clock)

    def scan(self, searchable: Callable[[int], Any] | Iterable[int] | None = None,
             *, submitted_ids: Iterable[int] | None = None,
             ground_truth_missing: Iterable[int] = (),
             grace_s: float | None = None) -> ProbeResult:
        probe = self.probe(searchable, grace_s=grace_s)
        result = probe.scan(submitted_ids=submitted_ids,
                            ground_truth_missing=ground_truth_missing)
        # Persist the detection: a scan that finds losses is itself evidence,
        # and it is what moves a submission into the healer's work queue.
        missing = set(result.missing)
        for state in self.ledger.states():
            if state.report_id in missing and not state.needs_healing:
                try:
                    self.ledger.mark(state.submission_id, KIND_LOST,
                                     detail={"detected_by": "probe"})
                except Exception:  # pragma: no cover - ledger is an audit aid
                    logger.debug("ledger mark failed for %s", state.submission_id)
        return result

    # ── self-healing ─────────────────────────────────────────────────
    def heal(self, *, policy: HealPolicy | None = None,
             states: list | None = None,
             submission_ids: Iterable[str] | None = None) -> HealReport:
        if self.resend is None:
            raise ValueError("healer needs a resend callable")
        verify = None
        if self.fetch is not None:
            def verify(report_id: int) -> bool:
                return self.fetch(report_id) is not None  # type: ignore[misc]
        healer = LostReportHealer(ledger=self.ledger, resend=self.resend,
                                  verify=verify, policy=policy, alerts=self.alerts,
                                  clock=self.clock)
        return healer.heal(submission_ids, states=states)


def default_ledger(path: Path | str | None = None) -> SubmissionLedger:
    """Open the ledger file at ``path``, else under ``TENSOR_REPORT_LEDGER_DIR``.

    The environment variable names a *directory*: the log lives at
    ``<dir>/ledger.jsonl``, payload snapshots at ``<dir>/payloads/`` and the
    chain-head anchor at ``<dir>/head.json``.
    """
    if path is not None:
        return SubmissionLedger(Path(path))
    return ledger_from_env()


def ledger_from_env() -> SubmissionLedger:
    return SubmissionLedger(ledger_dir_from_env() / "ledger.jsonl")