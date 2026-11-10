"""Progress metering for long-running jobs: turn "is it still making progress?" into a number.

Why a meter and not a log line
------------------------------
A long run can be *alive* and still be *stuck*: the process keeps burning budget
(iterations, GPU-seconds, tokens) while producing no artifacts.  Wall-clock
liveness probes cannot see that; a budget-normalised credit rate can.

The meter attaches a **credit** to every observable unit of work:

==============  =========================================================  ===============
kind            counted when                                               default weight
==============  =========================================================  ===============
``artifact``    a durable output file (or record) exists on disk           1.0 per file
``criterion``   a declared acceptance criterion flips to ``passed``        2.0 per criterion
``info_gain``   an observation reduces the Shannon entropy of the run's    1.0 per bit
                measurement histogram (measured, not asserted)
==============  =========================================================  ===============

and defines

.. code-block:: text

    progress_rate = credits / budget_used        [credit per unit of budget]

i.e. *credit per unit of the scarce resource*, not credit per hour.  Budget then
maps one-to-one onto the campaign's budgeted quantity (iterations, node-seconds,
tokens), which is what a stall policy has to reason about.

Two properties are deliberate:

* **recording is idempotent** -- an artifact/criterion is keyed by name, so a
  resumed run that re-emits an already-counted artifact does not inflate the rate;
* **the meter is auditable** -- every event is appended to a JSONL ledger, and
  :func:`manual_recount` recomputes the rate from raw ground truth (files on disk
  + ledger lines + an externally measured step counter) without touching the
  in-memory accumulators.  The acceptance test compares the two; that is the
  difference between a metric and a claim.

The module is domain-free on purpose (TensorPlatform iron rule): it knows about
"artifacts", "criteria" and "budget", never about what they mean.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "progress_meter/1"

#: Event kinds, in the fixed order used by the ledger's canonical form.
KINDS = ("artifact", "criterion", "info_gain")


@dataclass(frozen=True)
class Weights:
    """Credit per unit of each event kind (pre-declared, never tuned post hoc)."""

    artifact: float = 1.0
    criterion: float = 2.0
    info_gain: float = 1.0

    def as_dict(self) -> Dict[str, float]:
        return {"artifact": self.artifact, "criterion": self.criterion, "info_gain": self.info_gain}

    def for_kind(self, kind: str) -> float:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}; expected one of {KINDS}")
        return float(getattr(self, kind))


@dataclass
class Event:
    seq: int
    budget_used: float
    kind: str
    name: str
    credit: float
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "budget_used": self.budget_used,
            "kind": self.kind,
            "name": self.name,
            "credit": self.credit,
            "detail": self.detail,
        }


class ProgressMeter:
    """Accumulates credit against a fixed budget and reports the credit rate."""

    def __init__(
        self,
        budget_total: float,
        weights: Weights = Weights(),
        ledger_path: Optional[os.PathLike | str] = None,
        artifact_dir: Optional[os.PathLike | str] = None,
        clock: Optional[Any] = None,
    ) -> None:
        if float(budget_total) <= 0:
            raise ValueError("budget_total must be positive")
        self.budget_total = float(budget_total)
        self.weights = weights
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None
        self.ledger_path = Path(ledger_path) if ledger_path is not None else None
        self.clock = clock or time.time
        self._events: List[Event] = []
        self._seen: set[tuple[str, str]] = set()
        self.budget_used = 0.0
        self.counts = {"artifact": 0, "criterion": 0, "criterion_total": 0, "info_gain_bits": 0.0}
        self.criteria: Dict[str, bool] = {}
        if self.ledger_path is not None:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.ledger_path.write_text("", encoding="utf-8")  # truncate on new run

    # -- recording --------------------------------------------------------- #
    def _emit(self, kind: str, name: str, credit: float, detail: Optional[Dict[str, Any]] = None) -> bool:
        key = (kind, str(name))
        if key in self._seen:
            return False
        self._seen.add(key)
        ev = Event(
            seq=len(self._events),
            budget_used=self.budget_used,
            kind=kind,
            name=str(name),
            credit=float(credit),
            detail=dict(detail or {}),
        )
        self._events.append(ev)
        if kind == "artifact":
            self.counts["artifact"] += 1
        elif kind == "criterion":
            self.counts["criterion_total"] += 1
            self.criteria[str(name)] = True
            if credit > 0:
                self.counts["criterion"] += 1
        else:
            self.counts["info_gain_bits"] += max(0.0, float(credit))
        if self.ledger_path is not None:
            with self.ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": self.clock(), **ev.as_dict()}, sort_keys=True) + "\n")
        return True

    def record_artifact(self, name: str, digest: Optional[str] = None, weight: Optional[float] = None) -> bool:
        credit = self.weights.artifact if weight is None else float(weight)
        return self._emit("artifact", name, credit, {"digest": digest})

    def record_criterion(self, name: str, passed: bool, weight: Optional[float] = None) -> bool:
        """Record a criterion *evaluation*. A failed evaluation yields 0 credit but is logged."""
        full = self.weights.criterion if weight is None else float(weight)
        credit = full if passed else 0.0
        return self._emit("criterion", name, credit, {"passed": bool(passed)})

    def record_info_gain(self, name: str, bits: float) -> bool:
        bits = float(bits)
        if not math.isfinite(bits):
            raise ValueError("information gain must be finite")
        return self._emit("info_gain", name, max(0.0, bits) * self.weights.info_gain, {"bits": bits})

    # -- budget ------------------------------------------------------------ #
    def advance(self, budget_used: Optional[float] = None, delta: Optional[float] = None) -> float:
        """Set (or increment) the consumed budget. Only ever increases."""
        if budget_used is None:
            budget_used = self.budget_used + float(delta if delta is not None else 0.0)
        budget_used = float(budget_used)
        if budget_used < self.budget_used:
            raise ValueError("budget_used must be monotonically non-decreasing")
        self.budget_used = budget_used
        return self.budget_used

    # -- reporting --------------------------------------------------------- #
    @property
    def credits(self) -> float:
        return float(sum(ev.credit for ev in self._events))

    @property
    def progress_rate(self) -> float:
        """Credits per unit of consumed budget (0.0 before any budget is spent)."""
        if self.budget_used <= 0:
            return 0.0
        return self.credits / self.budget_used

    def credits_since(self, since_budget: float) -> float:
        return float(sum(ev.credit for ev in self._events if ev.budget_used > since_budget))

    def fraction(self, target_credits: Optional[float] = None) -> Optional[float]:
        if target_credits is None:
            return None
        if target_credits <= 0:
            raise ValueError("target_credits must be positive")
        return self.credits / float(target_credits)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "budget_total": self.budget_total,
            "budget_used": self.budget_used,
            "credits": self.credits,
            "progress_rate": self.progress_rate,
            "artifacts": self.counts["artifact"],
            "criteria_passed": self.counts["criterion"],
            "criteria_evaluated": self.counts["criterion_total"],
            "info_gain_bits": self.counts["info_gain_bits"],
            "weights": self.weights.as_dict(),
            "events": len(self._events),
        }

    def timeseries(self, buckets: int = 20) -> List[Dict[str, Any]]:
        """Credit accrual sampled on a uniform budget grid (for figures/timelines)."""
        buckets = max(1, int(buckets))
        width = self.budget_total / buckets
        out: List[Dict[str, Any]] = []
        for i in range(1, buckets + 1):
            upto = i * width
            credits = float(sum(ev.credit for ev in self._events if ev.budget_used <= upto + 1e-12))
            out.append(
                {
                    "budget_used": upto,
                    "credits": credits,
                    "rate": credits / upto if upto > 0 else 0.0,
                }
            )
        return out

    def ledger(self) -> List[Dict[str, Any]]:
        return [ev.as_dict() for ev in self._events]

    # -- audit ------------------------------------------------------------- #
    def manual_recount(
        self,
        budget_used: Optional[float] = None,
        artifact_dir: Optional[os.PathLike | str] = None,
    ) -> Dict[str, Any]:
        """Recompute the rate from ground truth, bypassing the in-memory counters.

        Ground truth:
          * artifacts -- files actually present under ``artifact_dir``;
          * criteria  -- distinct ``criterion`` ledger lines with ``passed`` true;
          * budget    -- an *externally* measured counter (caller-supplied).
        """
        adir = Path(artifact_dir) if artifact_dir is not None else self.artifact_dir
        if adir is None:
            raise ValueError("artifact_dir required for a manual recount")
        files = sorted(p for p in adir.rglob("*") if p.is_file())
        n_art = len(files)
        crit_passed = 0
        bits = 0.0
        if self.ledger_path is not None and self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") == "criterion" and rec.get("detail", {}).get("passed"):
                    crit_passed += 1
                elif rec.get("kind") == "info_gain":
                    bits += max(0.0, float(rec["detail"]["bits"]))
        else:  # no ledger on disk: fall back to the recorded events
            crit_passed = self.counts["criterion"]
            bits = self.counts["info_gain_bits"]
        budget = float(self.budget_used if budget_used is None else budget_used)
        credits = (
            n_art * self.weights.artifact
            + crit_passed * self.weights.criterion
            + bits * self.weights.info_gain
        )
        return {
            "artifacts": n_art,
            "criteria_passed": crit_passed,
            "info_gain_bits": bits,
            "budget_used": budget,
            "credits": credits,
            "progress_rate": credits / budget if budget > 0 else 0.0,
            "artifact_files": [str(p.relative_to(adir)) for p in files[:50]],
        }

    @staticmethod
    def relative_error(measured: float, reference: float) -> float:
        """|measured - reference| / reference, with a 0/0 convention of 0.0."""
        if reference == 0:
            return 0.0 if measured == 0 else float("inf")
        return abs(float(measured) - float(reference)) / abs(float(reference))