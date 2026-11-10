"""Automatic capability-ledger upgrade / downgrade engine (p262w1-N07).

Ties the pieces together:

    verified/ artifacts --(load)-- evidence_chain.EvidenceChainValidator
                                   |
                                   v  ChainVerdict
                          level_machine.decide (+ current level from state file)
                                   |
                                   v  Transition
                          ledger state file (atomic replace)
                                   |
                                   v
                          append_only_log.AppendOnlyAuditLog  (sha256 chained)

The engine is *pull* based: :meth:`AutoAuditEngine.watch_once` rescans the
verified tree (file names, sizes and mtimes), and audits every capability whose
evidence signature changed.  :meth:`AutoAuditEngine.run` turns that into a
polling watcher thread with a bounded interval, so a new artifact promotes a
capability and a deleted artifact demotes it within one poll period.

Zero domain semantics: paths, spec and capability ids are injected; the engine
never interprets what a capability means.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .append_only_log import AppendOnlyAuditLog, AuditEntry
from .evidence_chain import (
    ChainSpec,
    ChainVerdict,
    DiscoveredEvidence,
    EvidenceChainValidator,
    canonical_json,
    load_evidence,
    sha256_json,
)
from .level_machine import DOWNGRADE, HOLD, UPGRADE, Transition, decide

SCHEMA_VERSION = "1.0"
OBJECT_ACTION = "AUTO_AUDIT"


@dataclass
class AuditOutcome:
    """Result of one audit operation (also the audit-log payload)."""

    capability_id: str
    from_level: str
    to_level: str
    action: str
    reason_code: str
    complete: bool
    failure_kind: Optional[str] = None
    missing: List[str] = field(default_factory=list)
    chain_digest: str = ""
    spec_digest: str = ""
    latency_s: float = 0.0
    log_seq: int = 0
    checks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.from_level != self.to_level

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "action": self.action,
            "changed": self.changed,
            "reason_code": self.reason_code,
            "complete": self.complete,
            "failure_kind": self.failure_kind,
            "missing": list(self.missing),
            "chain_digest": self.chain_digest,
            "spec_digest": self.spec_digest,
            "latency_s": round(self.latency_s, 6),
            "log_seq": self.log_seq,
            "checks": self.checks,
        }


class AutoAuditEngine:
    """Evidence-driven level promotion/demotion with an immutable audit trail."""

    def __init__(
        self,
        verified_dir: "str | Path",
        state_path: "str | Path",
        log_path: "str | Path",
        *,
        spec: Optional[ChainSpec] = None,
        validator: Optional[EvidenceChainValidator] = None,
        initial_levels: Optional[Dict[str, str]] = None,
        actor: str = "capability-auto-audit",
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.verified_dir = Path(verified_dir)
        self.state_path = Path(state_path)
        self.spec = spec or ChainSpec()
        self.validator = validator or EvidenceChainValidator(self.spec)
        self.actor = actor
        self._clock = clock
        self.log = AppendOnlyAuditLog(log_path)
        self._state: Dict[str, Any] = self._load_state()
        if initial_levels:
            for cap_id, level in initial_levels.items():
                self._state.setdefault("capabilities", {}).setdefault(
                    cap_id, {"level": level, "reason_code": "SEEDED", "chain_digest": "",
                             "updated_seq": 0})
            self._persist_state()
        self._signatures: Dict[str, str] = {}
        self.outcomes: List[AuditOutcome] = []

    # -- state ---------------------------------------------------------------
    def _empty_state(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "spec_digest": self.spec.digest(),
            "capabilities": {},
        }

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            state = __import__("json").loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty_state()
        state.setdefault("capabilities", {})
        state.setdefault("spec_digest", self.spec.digest())
        state.setdefault("schema_version", SCHEMA_VERSION)
        return state

    def _persist_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        body = dict(self._state)
        body["digest"] = sha256_json({k: v for k, v in body.items() if k != "digest"})
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(canonical_json(body) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def level_of(self, capability_id: str) -> str:
        rec = self._state["capabilities"].get(capability_id)
        if not rec:
            return self.spec.default_level
        return rec.get("level", self.spec.default_level)

    def levels(self) -> Dict[str, str]:
        return {cap: rec.get("level", self.spec.default_level)
                for cap, rec in self._state["capabilities"].items()}

    def known_capabilities(self) -> List[str]:
        found = set(self._state["capabilities"])
        if self.verified_dir.is_dir():
            found.update(p.name for p in self.verified_dir.iterdir() if p.is_dir())
        return sorted(found)

    # -- single audit --------------------------------------------------------
    def audit(self, capability_id: str, *, ts: Optional[str] = None) -> AuditOutcome:
        start = self._clock()
        current = self.level_of(capability_id)
        ev: DiscoveredEvidence = load_evidence(self.verified_dir, capability_id, self.spec)
        verdict: ChainVerdict = self.validator.validate(ev)
        transition: Transition = decide(current, verdict)
        latency = self._clock() - start

        if transition.changed:
            self._state["capabilities"][capability_id] = {
                "level": transition.to_level,
                "reason_code": transition.reason_code,
                "chain_digest": verdict.chain_digest,
                "updated_seq": len(self.log) + 1,
            }
            self._persist_state()

        entry: AuditEntry = self.log.append(
            action=OBJECT_ACTION,
            capability_id=capability_id,
            from_level=current,
            to_level=transition.to_level,
            reason_code=transition.reason_code,
            evidence_digest=verdict.chain_digest,
            payload={
                "outcome": transition.action,
                "complete": verdict.complete,
                "missing": list(verdict.missing),
                "failure_kind": verdict.failure_kind,
                "latency_s": round(latency, 6),
                "spec_digest": verdict.spec_digest,
                "actor": self.actor,
            },
            ts=ts,
        )
        outcome = AuditOutcome(
            capability_id=capability_id,
            from_level=current,
            to_level=transition.to_level,
            action=transition.action,
            reason_code=transition.reason_code,
            complete=verdict.complete,
            failure_kind=verdict.failure_kind,
            missing=list(verdict.missing),
            chain_digest=verdict.chain_digest,
            spec_digest=verdict.spec_digest,
            latency_s=latency,
            log_seq=entry.seq,
            checks=[c.as_dict() for c in verdict.checks],
        )
        self.outcomes.append(outcome)
        return outcome

    def audit_many(self, capability_ids: Iterable[str]) -> List[AuditOutcome]:
        return [self.audit(cap) for cap in capability_ids]

    # -- change detection ----------------------------------------------------
    def signature(self, capability_id: str) -> str:
        base = self.verified_dir / capability_id
        if not base.is_dir():
            return ""
        parts = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                stat = path.stat()
                parts.append([path.relative_to(base).as_posix(), stat.st_size, stat.st_mtime_ns])
        return sha256_json(parts)

    def snapshot_signatures(self) -> Dict[str, str]:
        caps = set(self.known_capabilities())
        if self.verified_dir.is_dir():
            caps.update(p.name for p in self.verified_dir.iterdir() if p.is_dir())
        return {cap: self.signature(cap) for cap in caps}

    def pending_changes(self) -> List[str]:
        sigs = self.snapshot_signatures()
        return sorted(cap for cap, sig in sigs.items()
                      if self._signatures.get(cap) != sig)

    def watch_once(self, *, rescan_all_if_uninitialised: bool = True) -> List[AuditOutcome]:
        """Audit every capability whose evidence signature changed since last poll."""
        sigs = self.snapshot_signatures()
        if not self._signatures and rescan_all_if_uninitialised:
            changed = sorted(sigs)
        else:
            changed = sorted(cap for cap, sig in sigs.items()
                             if self._signatures.get(cap) != sig)
        self._signatures = sigs
        return [self.audit(cap) for cap in changed]

    def run(
        self,
        *,
        interval: float = 0.2,
        stop_event: Optional[threading.Event] = None,
        max_polls: Optional[int] = None,
        on_outcome: Optional[Callable[[AuditOutcome], None]] = None,
    ) -> List[AuditOutcome]:
        """Polling watcher loop. Returns every outcome produced."""
        produced: List[AuditOutcome] = []
        polls = 0
        while True:
            for outcome in self.watch_once():
                produced.append(outcome)
                if on_outcome is not None:
                    on_outcome(outcome)
            polls += 1
            if max_polls is not None and polls >= max_polls:
                return produced
            if stop_event is not None and stop_event.is_set():
                return produced
            time.sleep(interval)

    def start_background(self, *, interval: float = 0.2) -> tuple[threading.Thread, threading.Event]:
        """Start the watcher in a daemon thread; returns (thread, stop_event)."""
        stop = threading.Event()
        thread = threading.Thread(target=self.run, kwargs={"interval": interval,
                                                           "stop_event": stop},
                                  daemon=True, name="capability-auto-audit")
        thread.start()
        return thread, stop

    # -- reporting -----------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        levels = self.levels()
        dist: Dict[str, int] = {}
        for level in levels.values():
            dist[level] = dist.get(level, 0) + 1
        return {
            "capabilities": len(levels),
            "levels": levels,
            "distribution": dist,
            "audit_entries": len(self.log),
            "audit_integrity": self.log.verify(),
            "spec_digest": self.spec.digest(),
        }