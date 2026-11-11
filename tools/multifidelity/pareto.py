"""Cost–accuracy Pareto front over fidelity strategies.

The front is built on the two quantities that matter to an engineer under a budget:
**total cost** (work units, minimised) and **prediction error** (minimised).  A point is
on the front when no other point is at least as good in both and strictly better in one.

Two checks are provided because the campaign acceptance criterion is machine-checkable:

``pareto_front``      returns the non-dominated subset (generic 2-objective problem).
``check_monotone_front`` verifies the ladder-specific statement "at least N fidelity
                      levels, cost increasing, error strictly decreasing, so every
                      level is non-dominated" and returns the violations verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

__all__ = ["Point", "dominates", "pareto_front", "check_monotone_front"]


@dataclass(frozen=True)
class Point:
    """A strategy in the cost/error plane."""

    name: str
    cost: float
    error: float
    extra: Mapping[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", dict(self.extra or {}))


def dominates(a: Point, b: Point) -> bool:
    """``a`` dominates ``b`` (both objectives minimised)."""
    return (a.cost <= b.cost and a.error <= b.error) and (a.cost < b.cost or a.error < b.error)


def pareto_front(points: Sequence[Point]) -> List[Point]:
    """Non-dominated subset, sorted by increasing cost."""
    pts = list(points)
    front = [p for p in pts if not any(dominates(q, p) for q in pts if q is not p)]
    return sorted(front, key=lambda p: (p.cost, p.error))


def check_monotone_front(
    items: Sequence[Mapping[str, object]], min_levels: int = 3, tol: float = 0.0
) -> Dict[str, object]:
    """Machine check of the "3+ level monotone non-dominated front" criterion.

    ``items`` are mappings with at least ``name``, ``cost``, ``error``.  The front is
    recomputed here (never trusted from the caller) and the three required properties
    are reported separately so a failure can be attributed:

    * ``enough_levels``: ``len(items) >= min_levels``;
    * ``cost_increasing``: costs strictly increase along the ladder order given;
    * ``error_decreasing``: errors strictly decrease along the same order (by ``tol``);
    * all items are non-dominated (``all_on_front``).
    """
    pts = [Point(str(it["name"]), float(it["cost"]), float(it["error"])) for it in items]
    ordered = sorted(pts, key=lambda p: p.cost)
    front = pareto_front(pts)
    front_names = {p.name for p in front}
    cost_ok = all(b.cost > a.cost for a, b in zip(ordered, ordered[1:]))
    err_ok = all(b.error < a.error - tol for a, b in zip(ordered, ordered[1:]))
    enough = len(pts) >= int(min_levels)
    on_front = all(p.name in front_names for p in pts)
    return {
        "pass": bool(enough and cost_ok and err_ok and on_front),
        "n_points": len(pts),
        "min_levels": int(min_levels),
        "enough_levels": bool(enough),
        "cost_increasing": bool(cost_ok),
        "error_decreasing": bool(err_ok),
        "all_on_front": bool(on_front),
        "front": [{"name": p.name, "cost": p.cost, "error": p.error} for p in front],
        "dominated": [p.name for p in pts if p.name not in front_names],
    }