"""Explicit subcycle clock contract for partitioned (multi-rate) coupling.

Zero domain semantics: a "parent" clock and a "child" clock are just two
time steps. The contract is fail-closed:

* ``dt_parent / dt_child`` must be a positive integer (the child must take
  an integer number of sub-steps per parent step);
* exchanges are only legal at sync points (child step count divisible by
  the ratio); stepping across a sync boundary raises.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ClockContract", "ClockContractError", "SubcycleScheduler"]


class ClockContractError(ValueError):
    """Clock contract violation (fail-closed)."""


@dataclass(frozen=True)
class ClockContract:
    """Explicit subcycle declaration: ``ratio = dt_parent / dt_child``.

    Raises :class:`ClockContractError` at construction time if the ratio
    is not a positive integer (subcycle steps must divide the parent step).
    """

    dt_parent: float
    dt_child: float
    name: str = "clock"

    def __post_init__(self) -> None:
        if not (self.dt_parent > 0.0) or not (self.dt_child > 0.0):
            raise ClockContractError("dt_parent and dt_child must be > 0")
        r = self.dt_parent / self.dt_child
        if r < 1.0:
            raise ClockContractError(
                f"child dt must not exceed parent dt (ratio={r:.6g} < 1)"
            )
        if abs(r - round(r)) > 1e-9 * max(1.0, r):
            raise ClockContractError(
                f"dt ratio {r:.9g} is not an integer; subcycle steps must "
                f"divide the parent step exactly"
            )
        object.__setattr__(self, "_ratio", int(round(r)))

    @property
    def ratio(self) -> int:
        return self._ratio

    def total_child_steps(self, parent_steps: int) -> int:
        if parent_steps < 0:
            raise ClockContractError("parent_steps must be >= 0")
        return parent_steps * self.ratio


@dataclass
class SubcycleScheduler:
    """Tracks parent/child step counters under a :class:`ClockContract`.

    ``advance_parent()`` runs exactly ``ratio`` child ticks; manual
    ``advance_child(n)`` is allowed only while it stays inside the current
    parent window (exchange at non-sync points is rejected).
    """

    contract: ClockContract
    parent_step: int = 0
    child_step: int = 0
    _child_in_parent: int = 0

    def __post_init__(self) -> None:
        if self._child_in_parent != 0 or self.parent_step != 0 or self.child_step != 0:
            raise ClockContractError("scheduler must start at the origin (0, 0)")

    @property
    def at_sync(self) -> bool:
        return self._child_in_parent == 0

    def _tick(self) -> None:
        self.child_step += 1
        self._child_in_parent += 1

    def advance_parent(self) -> None:
        if not self.at_sync:
            raise ClockContractError(
                "advance_parent() called off-sync; finish the child window first"
            )
        for _ in range(self.contract.ratio):
            self._tick()
        self._child_in_parent = 0
        self.parent_step += 1

    def advance_child(self, n: int = 1) -> None:
        if n < 0:
            raise ClockContractError("n must be >= 0")
        if self._child_in_parent + n > self.contract.ratio:
            raise ClockContractError(
                f"child step would cross a sync boundary "
                f"({self._child_in_parent} + {n} > {self.contract.ratio}); "
                f"exchange only at sync points (fail-closed)"
            )
        for _ in range(n):
            self._tick()

    def status(self) -> dict:
        return {
            "name": self.contract.name,
            "parent_step": self.parent_step,
            "child_step": self.child_step,
            "child_in_parent": self._child_in_parent,
            "at_sync": self.at_sync,
            "ratio": self.contract.ratio,
        }
