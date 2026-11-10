"""Honest FAIL: stop, keep the evidence, and say why in machine-readable form.

A failed long run is worth more than a silent one.  The rule enforced here is that a
FAIL record can never be written *without* its artifacts: :class:`HonestFail`
enumerates the files that survived, hashes them, and refuses to emit a record whose
retention ratio is below the declared floor (default 50 %).

Definitions used by the acceptance criteria (kept explicit because they are the
whole point):

``produced``
    artifacts the run actually emitted before stopping.
``retained``
    produced artifacts still on disk at FAIL time (a run must not delete evidence
    on the way out; the ratio is therefore expected to be exactly 1.0).
``planned``
    artifacts the run's own declared plan would have emitted given the full budget.
``retention_vs_produced`` / ``retention_vs_planned``
    the two ratios below.  A6 requires ``retention_vs_produced >= 0.5`` **and** a
    non-empty reason; ``retention_vs_planned`` is reported (not gated) because a
    near-budget failure can legitimately have few artifacts relative to the plan.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .forensics_bundle import sha256_file

SCHEMA = "survival.honest_fail/1"
RETENTION_FLOOR = 0.5


@dataclass
class HonestFail:
    """Collects artifacts + the failure narrative, then writes an auditable FAIL record."""

    artifact_dir: Any
    planned_total: int = 0
    reason: str = ""
    status: str = "FAIL"
    produced: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifact_dir = Path(self.artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    # -- bookkeeping ------------------------------------------------------- #
    def register(self, name: str) -> None:
        """Record that ``name`` was produced (it must exist under ``artifact_dir``)."""
        if name not in self.produced:
            self.produced.append(name)

    def note(self, kind: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self.actions.append({"at_unix": time.time(), "kind": kind, "detail": dict(detail or {})})

    # -- measurement ------------------------------------------------------- #
    def retained(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for name in self.produced:
            p = self.artifact_dir / name
            if p.exists() and p.is_file():
                out.append({"name": name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
        return out

    def ratios(self) -> Dict[str, Any]:
        retained = self.retained()
        produced_n = len(self.produced)
        planned_n = max(int(self.planned_total), produced_n)
        return {
            "produced": produced_n,
            "retained": len(retained),
            "planned": planned_n,
            "retention_vs_produced": (len(retained) / produced_n) if produced_n else 0.0,
            "retention_vs_planned": (len(retained) / planned_n) if planned_n else 0.0,
            "floor": RETENTION_FLOOR,
        }

    # -- emission ---------------------------------------------------------- #
    def write(self, reason: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
              resume_point: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        reason = (reason or self.reason or "").strip()
        if not reason:
            raise ValueError("an honest FAIL must state a reason")
        self.reason = reason
        ratios = self.ratios()
        if ratios["retention_vs_produced"] < RETENTION_FLOOR:
            raise RuntimeError(
                f"artifact retention {ratios['retention_vs_produced']:.3f} below floor "
                f"{RETENTION_FLOOR}: evidence was deleted, refusing to write a FAIL claim"
            )
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": reason,
            "written_unix": time.time(),
            "artifact_dir": str(self.artifact_dir),
            "ratios": ratios,
            "partial_artifacts": self.retained(),
            "actions": self.actions,
            "resume_point": dict(resume_point or {}),
        }
        if extra:
            payload["extra"] = extra
        fdir = self.artifact_dir.parent
        (fdir / "FAIL.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        (fdir / "FAIL.md").write_text(self._markdown(payload), encoding="utf-8")
        return payload

    def _markdown(self, payload: Dict[str, Any]) -> str:
        r = payload["ratios"]
        lines = [
            "# HONEST FAIL",
            "",
            f"- status: **{payload['status']}**",
            f"- reason: {payload['reason']}",
            f"- planned artifacts: {r['planned']}, produced: {r['produced']}, retained: {r['retained']}",
            f"- retention vs produced: {r['retention_vs_produced']:.3f} (floor {r['floor']})",
            f"- retention vs planned: {r['retention_vs_planned']:.3f}",
            "",
            "## retained artifacts",
            "",
        ]
        for rec in payload["partial_artifacts"]:
            lines.append(f"- `{rec['name']}` ({rec['bytes']} B) sha256 `{rec['sha256'][:16]}…`")
        lines += ["", "## recovery actions", ""]
        for act in payload["actions"]:
            lines.append(f"- {act['kind']}: {json.dumps(act['detail'], sort_keys=True)}")
        if payload.get("resume_point"):
            lines += ["", "## resume point", "", f"```\n{json.dumps(payload['resume_point'], indent=2, sort_keys=True)}\n```"]
        lines += ["", "This run did not meet its acceptance criteria. The numbers above are the",
                  "evidence that the partial work is preserved and traceable.", ""]
        return "\n".join(lines)