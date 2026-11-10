"""Declared strategy queue + blacklist: the *destination* of a stall reroute.

A reroute is only auditable if the alternatives were enumerated *before* the stall,
so this module is a plain ordered queue rather than a search.  Policy:

* strategies are tried in declared order;
* a strategy that produced no credit for a full stall window is blacklisted
  (append-only, with the budget at which it was blacklisted);
* :meth:`StrategyPlan.next` never returns a blacklisted strategy, and returns
  ``None`` when the queue is exhausted -- which is the trigger for the next,
  more drastic rung of the stall ladder (scope shrink -> honest FAIL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Strategy:
    """One way of attacking the task, with a declared expected yield."""

    name: str
    #: Artifacts this strategy is expected to emit per unit budget (0.0 = emits none).
    yield_per_budget: float = 0.1
    description: str = ""
    #: ``True`` for the minimal provable unit variant (used by the shrink rung).
    minimal: bool = False
    #: If set, the strategy stops yielding after this much budget (a "dies mid-run"
    #: strategy, used to exercise the stall ladder with genuine partial work in hand).
    active_until_budget: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "yield_per_budget": self.yield_per_budget,
            "description": self.description,
            "minimal": self.minimal,
            "active_until_budget": self.active_until_budget,
        }


class StrategyPlan:
    """Ordered strategy queue with an append-only blacklist."""

    def __init__(self, strategies: List[Strategy]) -> None:
        if not strategies:
            raise ValueError("a strategy plan needs at least one strategy")
        self.strategies: List[Strategy] = list(strategies)
        self.blacklist: List[Dict[str, Any]] = []
        self._cursor = 0
        self.switches: List[Dict[str, Any]] = []

    # -- state ------------------------------------------------------------- #
    @property
    def current(self) -> Strategy:
        return self.strategies[self._cursor]

    def _blacklisted_names(self) -> set[str]:
        return {rec["strategy"] for rec in self.blacklist}

    def blacklist_current(self, budget_used: float, reason: str) -> Strategy:
        """Blacklist the current strategy; the cursor stays put until :meth:`next`."""
        strat = self.current
        if strat.name not in self._blacklisted_names():
            self.blacklist.append(
                {"strategy": strat.name, "at_budget": float(budget_used), "reason": reason}
            )
        return strat

    def next(self, budget_used: float, reason: str = "stall_reroute") -> Optional[Strategy]:
        """Advance to the first non-blacklisted strategy after the cursor."""
        bl = self._blacklisted_names()
        for idx in range(self._cursor + 1, len(self.strategies)):
            if self.strategies[idx].name not in bl:
                self.switches.append(
                    {
                        "from": self.current.name,
                        "to": self.strategies[idx].name,
                        "at_budget": float(budget_used),
                        "reason": reason,
                    }
                )
                self._cursor = idx
                return self.current
        return None

    def remaining(self) -> List[Strategy]:
        bl = self._blacklisted_names()
        return [s for s in self.strategies[self._cursor + 1 :] if s.name not in bl]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "current": self.current.as_dict(),
            "order": [s.as_dict() for s in self.strategies],
            "blacklist": list(self.blacklist),
            "switches": list(self.switches),
        }


#: The default four-rung recovery plan used by the generic workload.  Rung 2 and 3
#: trade ambition for certifiability; the last entry is deliberately minimal.
DEFAULT_PLAN = (
    Strategy("baseline", 0.20, "full-scope declared pipeline"),
    Strategy("fallback_streaming", 0.15, "streaming variant with per-shard checkpoints"),
    Strategy("minimal_unit", 0.08, "smallest subgoal with its own criterion", minimal=True),
    Strategy("probe_only", 0.02, "measurement-only probe; yields evidence, not answers", minimal=True),
)