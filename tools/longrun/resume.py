"""Idempotent resume: "same input + same checkpoint -> same output", with an audit trail.

The controller deliberately exposes *one* key derivation function so that the
idempotence claim is checkable rather than rhetorical::

    key = sha256(canonical({"input": input_digest, "ckpt": checkpoint_digest,
                            "config": config_digest, "from_step": step}))

Two resumes that produce the same key must produce bit-identical final state; that
is exactly what ``tests/test_longrun_checkpoint_resume.py`` asserts (three repeats,
plus equality against the uninterrupted run).  Every resume appends a JSONL record,
so a long run's recovery history is auditable after the fact.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from . import checkpoint as ckpt_mod


def idempotency_key(
    input_digest: str, checkpoint_digest: str, config_digest: str, from_step: int
) -> str:
    payload = ckpt_mod.canonical_json(
        {
            "input": input_digest,
            "ckpt": checkpoint_digest,
            "config": config_digest,
            "from_step": int(from_step),
        }
    )
    return ckpt_mod.sha256_bytes(payload.encode())


class ResumeAudit:
    """Append-only JSONL audit trail of resume decisions."""

    def __init__(self, path: os.PathLike | str, run_id: str = "") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.records: list[Dict[str, Any]] = []

    def log(self, event: str, **fields: Any) -> Dict[str, Any]:
        record = {"ts_unix": round(time.time(), 6), "event": event, "run_id": self.run_id}
        record.update(fields)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.records.append(record)
        return record

    def events(self, name: Optional[str] = None) -> list[Dict[str, Any]]:
        if name is None:
            return list(self.records)
        return [r for r in self.records if r.get("event") == name]


@dataclass
class ResumePlan:
    from_step: int
    checkpoint_digest: Optional[str]
    checkpoint_path: Optional[str]
    idempotency_key: str
    verified: bool
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "from_step": self.from_step,
            "checkpoint_digest": self.checkpoint_digest,
            "checkpoint_path": self.checkpoint_path,
            "idempotency_key": self.idempotency_key,
            "verified": self.verified,
            "reason": self.reason,
        }


class ResumeController:
    """Decides where to restart, verifies the state, and audits the decision."""

    def __init__(
        self,
        store: ckpt_mod.CheckpointStore,
        audit: ResumeAudit,
        *,
        input_digest: str,
        config_digest: str,
        require_match: bool = True,
    ) -> None:
        self.store = store
        self.audit = audit
        self.input_digest = input_digest
        self.config_digest = config_digest
        self.require_match = require_match

    def plan(self, force_from_step: Optional[int] = None) -> ResumePlan:
        step = force_from_step if force_from_step is not None else self.store.latest_step()
        if step is None:
            plan = ResumePlan(
                from_step=0,
                checkpoint_digest=None,
                checkpoint_path=None,
                idempotency_key=idempotency_key(self.input_digest, "none", self.config_digest, 0),
                verified=False,
                reason="no_checkpoint_found",
            )
            self.audit.log("resume_plan", **plan.as_dict())
            return plan
        try:
            ck = self.store.load(step)
        except ckpt_mod.CheckpointError as exc:
            self.audit.log(
                "resume_rejected",
                from_step=step,
                error=type(exc).__name__,
                detail=str(exc)[:600],
            )
            raise
        reasons = []
        if ck.manifest.get("input_digest") not in (None, self.input_digest):
            reasons.append(
                f"input_digest mismatch ckpt={ck.manifest.get('input_digest')} now={self.input_digest}"
            )
        if ck.manifest.get("config_digest") not in (None, self.config_digest):
            reasons.append(
                f"config_digest mismatch ckpt={ck.manifest.get('config_digest')} now={self.config_digest}"
            )
        if reasons and self.require_match:
            self.audit.log("resume_rejected", from_step=step, reasons=reasons)
            raise ckpt_mod.CheckpointCorruption(
                "checkpoint does not belong to this input/config: " + "; ".join(reasons)
            )
        plan = ResumePlan(
            from_step=ck.step,
            checkpoint_digest=ck.digest,
            checkpoint_path=str(self.store.root),
            idempotency_key=idempotency_key(
                self.input_digest, ck.digest, self.config_digest, ck.step
            ),
            verified=True,
            reason="checkpoint_verified",
        )
        self.audit.log(
            "resume_plan",
            **plan.as_dict(),
            state_digest=ck.manifest.get("state_digest"),
            warnings=reasons,
        )
        return plan


def restore(store: ckpt_mod.CheckpointStore, plan: ResumePlan) -> Optional[ckpt_mod.Checkpoint]:
    if plan.checkpoint_digest is None:
        return None
    return store.load(plan.from_step)