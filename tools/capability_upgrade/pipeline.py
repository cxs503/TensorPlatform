"""Evidence-ingest pipeline with event-driven re-adjudication (P218-N01).

``ingest()`` is atomic from the caller's point of view:

1. the evidence record is persisted (content-addressed, immutable);
2. an ``evidence_ingested`` event is appended with its emission timestamp;
3. the re-adjudication engine runs **synchronously inside the same call**, so a
   successful ``ingest()`` return implies the capability level already reflects
   the new evidence — this is what makes the "≤1 s" guarantee structural rather
   than a polling promise;
4. the measured latency is written back onto the event row, so an auditor can
   verify the deadline from the database alone;
5. registered subscribers (e.g. an external audit log) are notified after the
   adjudication, with per-subscriber exceptions isolated and recorded.

Deadline breach is *reported*, never hidden: the returned record carries
``deadline_ok``/``deadline_ms`` and the event row keeps the measurement.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from .engine import RejudgeEngine
from .model import EvidenceItem, digest, now_iso
from .store import Store

DEFAULT_DEADLINE_SECONDS = 1.0


class EvidencePipeline:
    """Ingest evidence → emit event → re-adjudicate → record history."""

    def __init__(
        self,
        store: Store,
        engine: Optional[RejudgeEngine] = None,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ):
        self.store = store
        self.engine = engine or RejudgeEngine(store)
        self.deadline_seconds = float(deadline_seconds)
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    # ── subscription ──────────────────────────────────────────────────────
    def subscribe(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Register a post-adjudication observer (called synchronously)."""
        self._subscribers.append(fn)

    # ── ingest ────────────────────────────────────────────────────────────
    def ingest(
        self,
        capability_id: str,
        kind: str,
        metrics: Optional[Dict[str, Any]] = None,
        observed_level: Optional[str] = None,
        source: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        evidence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if self.store.get_slot(capability_id) is None:
            raise KeyError(f"unknown capability slot: {capability_id}")

        body = {
            "capability_id": capability_id,
            "kind": kind,
            "metrics": metrics or {},
            "observed_level": observed_level,
            "source": source or {},
            "payload": payload or {},
        }
        evidence_id = evidence_id or f"ev-{digest(body)[:16]}"
        item = EvidenceItem(
            evidence_id=evidence_id,
            capability_id=capability_id,
            kind=kind,
            metrics=dict(metrics or {}),
            observed_level=observed_level,
            source=dict(source or {}),
            payload=dict(payload or {}),
        )

        emitted_at = now_iso()
        inserted = self.store.add_evidence(item)
        event_id = self.store.append_event(
            evidence_id=evidence_id,
            capability_id=capability_id,
            event_type="evidence_ingested",
            emitted_at=emitted_at,
            duplicate=not inserted,
        )

        if not inserted:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.store.complete_event(
                event_id, now_iso(), run_id="", latency_ms=round(latency_ms, 4)
            )
            return {
                "evidence": item.as_dict(),
                "duplicate": True,
                "rejudged": False,
                "run": None,
                "latency_ms": round(latency_ms, 4),
                "deadline_ms": self.deadline_seconds * 1000.0,
                "deadline_ok": True,
                "level": self.store.get_slot(capability_id).current_level,  # type: ignore[union-attr]
                "event_id": event_id,
                "evidence_id": evidence_id,
            }

        # Adjudicate over the *whole* current evidence set (not just the new item)
        # so an auto run and a later manual run are guaranteed to see the same
        # inputs and therefore produce the same verdict (C2 consistency).
        run = self.engine.judge(
            capability_id,
            trigger="evidence_ingested",
            evidence_ids=None,
            run_id=f"rj-{uuid.uuid4().hex[:16]}",
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        completed_at = now_iso()
        self.store.complete_event(event_id, completed_at, run["run_id"], round(latency_ms, 4))

        result: Dict[str, Any] = {
            "evidence": item.as_dict(),
            "evidence_id": evidence_id,
            "duplicate": False,
            "rejudged": True,
            "event_id": event_id,
            "run": run,
            "latency_ms": round(latency_ms, 4),
            "deadline_ms": self.deadline_seconds * 1000.0,
            "deadline_ok": latency_ms <= self.deadline_seconds * 1000.0,
            "level": run["result_level"],
            "outcome": run["outcome"],
            "upgraded": run["outcome"] == "upgraded",
        }

        for fn in self._subscribers:
            try:
                fn(dict(result))
            except Exception as exc:  # pragma: no cover - defensive
                result.setdefault("subscriber_errors", []).append(
                    {"subscriber": getattr(fn, "__name__", repr(fn)), "error": str(exc)}
                )
        return result

    def ingest_many(self, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convenience batch ingest; each item is a kwargs dict for :meth:`ingest`."""
        return [self.ingest(**item) for item in items]

    # ── read helpers (used by the API and reports) ────────────────────────
    def chain(self, capability_id: str) -> Dict[str, Any]:
        """Full evidence chain: slot → evidence → criteria → runs → history."""
        slot = self.store.get_slot(capability_id)
        if slot is None:
            raise KeyError(capability_id)
        evidence = [ev.as_dict() for ev in self.store.list_evidence(capability_id)]
        criteria = [c.as_dict() for c in self.store.list_criteria(capability_id)]
        runs = self.store.list_runs(capability_id)
        history = self.store.list_history(capability_id)
        used = {eid for run in runs for eid in run.get("evidence_ids", [])}
        return {
            "capability": slot.as_dict(),
            "evidence": evidence,
            "criteria": criteria,
            "runs": runs,
            "history": history,
            "traceability": {
                "evidence_with_source": sum(
                    1 for ev in evidence if any((ev.get("source") or {}).values())
                ),
                "evidence_total": len(evidence),
                "evidence_used_in_runs": len(used),
            },
        }

    def coverage(self) -> Dict[str, Any]:
        """C1 coverage: which WITHHELD slots lack any linked evidence?"""
        slots = self.store.list_slots()
        unbacked: List[Dict[str, Any]] = []
        backed = 0
        withheld = 0
        for slot in slots:
            ids = self.store.evidence_ids(slot.capability_id)
            if slot.current_level == "WITHHELD":
                withheld += 1
                if ids:
                    backed += 1
                else:
                    unbacked.append({"capability_id": slot.capability_id})
        return {
            "slots": len(slots),
            "withheld_slots": withheld,
            "withheld_with_evidence": backed,
            "withheld_without_evidence": unbacked,
            "coverage_ok": not unbacked,
        }