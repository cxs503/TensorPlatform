"""Lost-write probe: diff "what the writer submitted" against "what a reader sees".

The probe answers one question — *which submitted ids can no longer be read?* —
with two design rules learned from the failure it is meant to catch:

1. **The submitted set never comes from the report store.**  It comes from the
   write-side ledger (:mod:`.ledger`).  Diffing the store against itself always
   yields the empty set, which is how a silent loss stays invisible.
2. **A grace window is mandatory.**  A row that was committed a millisecond ago
   may legitimately be invisible to a lagging reader; reporting it as lost
   would drown the signal in false positives, so fresh submissions are skipped
   and counted separately (``deferred``), never silently dropped.

Recall is measured against an *injected* ground truth: :meth:`LostReportProbe.scan`
accepts ``ground_truth_missing`` so that the probe's detection rate is a number
the test can assert instead of a claim in prose.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterable, Mapping

from .ledger import SubmissionLedger, SubmissionState


def _as_lookup(source: Callable[[int], Any] | Collection[int] | Mapping[int, Any]):
    """Normalise ``searchable`` into ``id -> observed record | None``."""
    if callable(source):
        def _fetch(rid: int) -> Any:
            try:
                return source(rid)
            except KeyError:
                return None
    else:
        present = set(source)
        def _fetch(rid: int) -> Any:
            return {"id": rid} if rid in present else None
    return _fetch


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one scan."""

    checked: int
    missing: tuple[int, ...] = ()
    present: tuple[int, ...] = ()
    deferred: tuple[int, ...] = ()
    errors: tuple[str, ...] = ()
    ground_truth: tuple[int, ...] = ()
    scan_ts: float = 0.0

    @property
    def missed_count(self) -> int:
        return len(self.missing)

    @property
    def detection_rate(self) -> float | None:
        """Share of the injected/real losses the probe found (recall)."""
        if not self.ground_truth:
            return None
        found = set(self.missing) & set(self.ground_truth)
        return len(found) / len(self.ground_truth)

    @property
    def false_positives(self) -> tuple[int, ...]:
        """Ids flagged missing that were *not* part of the ground truth.

        Only meaningful when a ground truth is known; without one every hit is
        a legitimate detection.
        """
        if not self.ground_truth:
            return ()
        return tuple(rid for rid in self.missing if rid not in set(self.ground_truth))

    @property
    def false_positive_rate(self) -> float | None:
        if not self.ground_truth:
            return None
        total = max(len(self.present) + len(self.missing), 1)
        return len(self.false_positives) / total

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "missing": list(self.missing),
            "missing_count": self.missed_count,
            "present_count": len(self.present),
            "deferred": list(self.deferred),
            "errors": list(self.errors),
            "ground_truth": list(self.ground_truth),
            "detection_rate": self.detection_rate,
            "false_positives": list(self.false_positives),
            "false_positive_rate": self.false_positive_rate,
            "scan_ts": self.scan_ts,
        }


class LostReportProbe:
    """Periodic scan: submitted ids ∖ readable ids."""

    def __init__(self, searchable: Callable[[int], Any] | Collection[int] | Mapping[int, Any],
                 *, ledger: SubmissionLedger | None = None, grace_s: float = 0.0,
                 clock: Callable[[], float] = time.time):
        self._fetch = _as_lookup(searchable)
        self.ledger = ledger
        self.grace_s = float(grace_s)
        self._clock = clock

    # ── submitted set ──────────────────────────────────────────────────
    def _submitted(self, submitted_ids: Iterable[int] | None,
                   states: list[SubmissionState] | None) -> list[int]:
        if submitted_ids is not None:
            out: dict[int, None] = {}
            for rid in submitted_ids:
                out.setdefault(int(rid), None)
            return list(out)
        if states is not None:
            out = {}
            for state in states:
                if state.report_id is not None:
                    out.setdefault(int(state.report_id), None)
            return list(out)
        if self.ledger is not None:
            return self.ledger.submitted_ids()
        raise ValueError("probe needs submitted_ids, states, or a ledger")

    def _fresh_ids(self, now: float) -> set[int]:
        if self.ledger is None or self.grace_s <= 0:
            return set()
        fresh: set[int] = set()
        for state in self.ledger.states():
            if state.report_id is not None and (now - state.submitted_ts) < self.grace_s:
                fresh.add(int(state.report_id))
        return fresh

    # ── scan ───────────────────────────────────────────────────────────
    def scan(self, submitted_ids: Iterable[int] | None = None,
             *, states: list[SubmissionState] | None = None,
             ground_truth_missing: Iterable[int] = ()) -> ProbeResult:
        now = float(self._clock())
        candidates = self._submitted(submitted_ids, states)
        fresh = self._fresh_ids(now)

        missing: list[int] = []
        present: list[int] = []
        deferred: list[int] = []
        errors: list[str] = []

        for rid in candidates:
            if rid in fresh:
                deferred.append(rid)
                continue
            try:
                observed = self._fetch(rid)
            except Exception as exc:  # a read error is *not* a pass
                errors.append(f"{rid}:{type(exc).__name__}")
                missing.append(rid)
                continue
            if observed is None:
                missing.append(rid)
            else:
                present.append(rid)

        return ProbeResult(
            checked=len(candidates), missing=tuple(missing), present=tuple(present),
            deferred=tuple(deferred), errors=tuple(errors),
            ground_truth=tuple(int(x) for x in ground_truth_missing), scan_ts=now)