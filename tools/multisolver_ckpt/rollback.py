"""Restart supervision: verify, quarantine, roll back, or fail loudly.

The failure this module exists for is mundane and expensive: a long coupled run
writes checkpoints for hours, the last one is torn (or one bit flipped on the
way to disk), the job is restarted -- and, without a supervisor, it either
crashes on a corrupt file or, far worse, loads something plausible and silently
continues a different trajectory.

:class:`RestartSupervisor` implements the contract that prevents both:

1. candidates are tried newest-first;
2. every candidate must pass the store's full verification (payload sha256,
   chunk sha256, per-array sha256, group state digest, sync-instant policy,
   energy-ledger agreement) before it is accepted;
3. a failing candidate is **quarantined** (moved aside, with a reason file) so
   it can never be picked again by an unsupervised restart, and an
   append-only JSONL audit line is written for each attempt;
4. if no candidate survives, :class:`RestartFailed` is raised -- never a silent
   fresh start, because a silent restart from step 0 would look like progress.

"Rollback with no data loss" here means: the restored state is bit-identical to
the state that was checkpointed (digest equality), and because the dynamics are
deterministic the continuation reproduces the uninterrupted run exactly.  Both
are asserted in the tests and in the report, not assumed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .errors import CheckpointCorruption, CheckpointError, CheckpointUnreadable, NonSyncPointError
from .protocol import StateBundle
from .store import CheckpointStore, LoadedCheckpoint

AUDIT_NAME = "restart_audit.jsonl"
_RECOVERABLE = (CheckpointCorruption, CheckpointUnreadable, NonSyncPointError)


class RestartFailed(CheckpointError):
    """No checkpoint survived verification: the restart cannot be trusted."""


@dataclass
class RollbackEvent:
    attempted_step: int
    fallback_step: Optional[int]
    error_type: str
    reason: str
    quarantined_path: Optional[str] = None
    at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": "rollback",
            "attempted_step": int(self.attempted_step),
            "fallback_step": None if self.fallback_step is None else int(self.fallback_step),
            "error_type": self.error_type,
            "reason": self.reason,
            "quarantined_path": self.quarantined_path,
            "at": self.at,
        }


@dataclass
class RestartOutcome:
    ok: bool
    step: int
    digest: str
    loaded: LoadedCheckpoint
    rollbacks: List[RollbackEvent] = field(default_factory=list)
    attempts: int = 0
    reason: str = ""

    @property
    def bundle(self) -> StateBundle:
        return self.loaded.bundle

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "step": int(self.step),
            "digest": self.digest,
            "attempts": int(self.attempts),
            "rollbacks": [r.to_dict() for r in self.rollbacks],
            "reason": self.reason,
        }


class RestartSupervisor:
    """Newest-first verified restart with quarantine + audit trail."""

    def __init__(self, store: CheckpointStore, *, max_attempts: int = 4,
                 quarantine_corrupt: bool = True, audit_name: str = AUDIT_NAME) -> None:
        self.store = store
        self.max_attempts = max(1, int(max_attempts))
        self.quarantine_corrupt = bool(quarantine_corrupt)
        self.audit_path: Path = store.root / audit_name

    # -- public API -------------------------------------------------------- #
    def restart(self, *, prefer: Optional[int] = None, at_or_before: Optional[int] = None
                ) -> RestartOutcome:
        steps = [s for s in self.store.steps()
                 if (at_or_before is None or s <= int(at_or_before))]
        if prefer is not None:
            if int(prefer) not in steps:
                raise RestartFailed(f"requested checkpoint {prefer} is not in the store")
            steps = [int(prefer)]
        if not steps:
            self._audit({"event": "restart_failed", "reason": "no checkpoints in store",
                         "root": str(self.store.root)})
            raise RestartFailed(f"no checkpoints available under {self.store.root}")
        candidates = sorted(steps, reverse=True)[: self.max_attempts]

        rollbacks: List[RollbackEvent] = []
        attempts = 0
        for idx, step in enumerate(candidates):
            attempts += 1
            try:
                loaded = self.store.load(step)
            except CheckpointError as exc:
                remaining = candidates[idx + 1:]
                fallback = remaining[0] if remaining else None
                quarantined = None
                if self.quarantine_corrupt and isinstance(exc, _RECOVERABLE):
                    try:
                        quarantined = str(self.store.quarantine(step, reason=str(exc)))
                    except OSError as qexc:  # pragma: no cover - filesystem trouble
                        quarantined = f"quarantine failed: {qexc}"
                event = RollbackEvent(
                    attempted_step=step, fallback_step=fallback,
                    error_type=type(exc).__name__, reason=str(exc),
                    quarantined_path=quarantined,
                )
                rollbacks.append(event)
                self._audit(event.to_dict())
                continue
            outcome = RestartOutcome(
                ok=True, step=loaded.step, digest=loaded.digest, loaded=loaded,
                rollbacks=rollbacks, attempts=attempts,
                reason=f"restored step {loaded.step} after {len(rollbacks)} rollback(s)",
            )
            self._audit({
                "event": "restart_ok", "step": loaded.step, "digest": loaded.digest,
                "attempts": attempts, "rollbacks": len(rollbacks),
                "sync_point": bool((loaded.manifest.get("sync") or {}).get("sync_point", True)),
            })
            return outcome

        self._audit({"event": "restart_failed", "attempts": attempts,
                     "reason": "every candidate checkpoint failed verification",
                     "tried": candidates})
        raise RestartFailed(
            f"no usable checkpoint among {candidates}: "
            + "; ".join(f"{r.attempted_step}:{r.error_type}" for r in rollbacks)
        )

    def resume_into(self, harness: Any, *, prefer: Optional[int] = None,
                    at_or_before: Optional[int] = None) -> RestartOutcome:
        """Restore the newest verified checkpoint into a fresh harness."""

        outcome = self.restart(prefer=prefer, at_or_before=at_or_before)
        harness.restore(outcome.bundle)
        return outcome

    # -- audit ------------------------------------------------------------- #
    def _audit(self, payload: Mapping[str, Any]) -> None:
        line = json.dumps({**dict(payload), "ts": time.time()}, sort_keys=True)
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def read_audit(self) -> List[Dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        out = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def audit_events(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.read_audit() if e.get("event") == kind]