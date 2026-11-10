"""Stall detection with a four-rung recovery ladder (detect -> reroute -> shrink -> FAIL).

Signal
------
Stall is *not* "the process stopped".  It is:

.. code-block:: text

    stalls when  budget_used - budget_at_last_credit  >=  stall_frac * budget_total

i.e. a full ``stall_frac`` share of the budget has been spent without a single new
credit.  Because the meter counts only durable artifacts / passed criteria /
measured information gain, a busy-but-barren loop is caught, which is exactly the
failure mode that burns a long run's budget.

Recovery ladder (each further barren window escalates one rung, monotonically)
------------------------------------------------------------------------------
===  ==============  ==========================================================
rung action          what actually happens
===  ==============  ==========================================================
1    ``restate``     re-emit the task contract (criteria + budget + current
                     strategy) into the run directory -- cheap, no scope change;
                     the classic first response to "the agent forgot the goal".
2    ``reroute``     blacklist the barren strategy and switch to the next
                     *pre-declared* strategy (see :mod:`strategies`).
3    ``shrink``      cut down to the minimal provable unit: the smallest subgoal
                     that still has its own acceptance criterion.
4    ``honest_fail`` stop, keep every intermediate artifact, and write a FAIL
                     record naming the reason (see :mod:`tools.survival.honest`).
===  ==============  ==========================================================

De-escalation: any new credit resets the rung counter and records a ``recovered``
event with the budget at which progress resumed -- that is the evidence that a
reroute worked (acceptance criterion A5).

Nothing here is domain-specific: the detector only sees budgets, credits and a
strategy queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .meter import ProgressMeter
from .strategies import Strategy, StrategyPlan

SCHEMA = "progress_meter.stall/1"

LEVELS = ("restate", "reroute", "shrink", "honest_fail")

#: Default barren window as a fraction of total budget.  Chosen so that a stall is
#: detected inside 30 % of the budget (acceptance A4) while leaving headroom for
#: legitimately bursty producers (acceptance A8 negative control uses 20 % gaps).
DEFAULT_STALL_FRAC = 0.25


@dataclass
class StallEvent:
    index: int
    level: str
    action: str
    budget_used: float
    budget_total: float
    since_credit_budget: float
    credits_at_detection: float
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def budget_fraction_at_detection(self) -> float:
        return self.budget_used / self.budget_total if self.budget_total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "level": self.level,
            "action": self.action,
            "budget_used": self.budget_used,
            "budget_total": self.budget_total,
            "budget_fraction": self.budget_fraction_at_detection,
            "barren_budget": round(self.since_credit_budget, 12),
            "credits_at_detection": self.credits_at_detection,
            "detail": self.detail,
        }


class StallDetector:
    """Watches a :class:`ProgressMeter` and drives the four-rung ladder."""

    def __init__(
        self,
        meter: ProgressMeter,
        plan: Optional[StrategyPlan] = None,
        stall_frac: float = DEFAULT_STALL_FRAC,
        max_events: Optional[int] = None,
    ) -> None:
        if not (0.0 < float(stall_frac) <= 1.0):
            raise ValueError("stall_frac must be in (0, 1]")
        self.meter = meter
        self.plan = plan
        self.stall_frac = float(stall_frac)
        self.max_events = max_events
        self.events: List[StallEvent] = []
        self.recoveries: List[Dict[str, Any]] = []
        self.last_credit = meter.credits
        self.last_credit_budget = meter.budget_used
        self.rung = 0  # 0 = no stall seen, 1..4 = last rung applied
        self.honest_fail = False

    # -- thresholds -------------------------------------------------------- #
    @property
    def stall_window(self) -> float:
        return self.stall_frac * self.meter.budget_total

    def barren_budget(self) -> float:
        return max(0.0, self.meter.budget_used - self.last_credit_budget)

    # -- observation ------------------------------------------------------- #
    def observe(self) -> Optional[StallEvent]:
        """Return the recovery action for this tick, or ``None`` if progress is fine."""
        meter = self.meter
        if meter.credits > self.last_credit:
            self._note_recovery()
            return None
        if self.barren_budget() + 1e-12 < self.stall_window:
            return None
        if self.honest_fail:
            return None
        if self.max_events is not None and len(self.events) >= self.max_events:
            return None
        return self._escalate()

    def _note_recovery(self) -> None:
        if self.rung > 0:
            self.recoveries.append(
                {
                    "at_budget": self.meter.budget_used,
                    "after_rung": LEVELS[self.rung - 1],
                    "credits": self.meter.credits,
                    "barren_budget": round(self.barren_budget(), 12),
                }
            )
            self.rung = 0
        self.last_credit = self.meter.credits
        self.last_credit_budget = self.meter.budget_used

    def _escalate(self) -> StallEvent:
        idx = min(len(self.events), len(LEVELS) - 1)
        level = LEVELS[idx]
        detail: Dict[str, Any] = {}
        action = level
        if level == "reroute" and self.plan is not None:
            stalled = self.plan.blacklist_current(self.meter.budget_used, "stall_barren_window")
            nxt = self.plan.next(self.meter.budget_used, reason="stall_reroute")
            detail = {
                "blacklisted": stalled.name,
                "switched_to": nxt.name if nxt else None,
                "queue_exhausted": nxt is None,
            }
            if nxt is None:
                action = "reroute_exhausted"
        elif level == "shrink":
            detail = {"scope": "minimal_provable_unit"}
            if self.plan is not None:
                minimal = next(
                    (s for s in self.plan.strategies if s.minimal and s.name not in {b["strategy"] for b in self.plan.blacklist}),
                    None,
                )
                detail["minimal_strategy"] = minimal.name if minimal else None
        elif level == "honest_fail":
            detail = {"scope": "final", "retain_artifacts": True}
            self.honest_fail = True
        ev = StallEvent(
            index=len(self.events),
            level=level,
            action=action,
            budget_used=self.meter.budget_used,
            budget_total=self.meter.budget_total,
            since_credit_budget=self.barren_budget(),
            credits_at_detection=self.meter.credits,
            detail=detail,
        )
        self.events.append(ev)
        self.rung = idx + 1
        # measure the next barren window from *now*, so escalation has a fixed cadence
        self.last_credit_budget = self.meter.budget_used
        self.last_credit = self.meter.credits
        return ev

    # -- reporting --------------------------------------------------------- #
    def detected(self) -> bool:
        return bool(self.events)

    def first_detection_fraction(self) -> Optional[float]:
        return self.events[0].budget_fraction_at_detection if self.events else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "stall_frac": self.stall_frac,
            "stall_window_budget": self.stall_window,
            "events": [e.as_dict() for e in self.events],
            "recoveries": list(self.recoveries),
            "detected": self.detected(),
            "first_detection_fraction": self.first_detection_fraction(),
            "honest_fail": self.honest_fail,
            "levels_seen": [e.level for e in self.events],
            "plan": self.plan.as_dict() if self.plan is not None else None,
        }

    def write(self, path) -> Dict[str, Any]:
        payload = self.as_dict()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        return payload