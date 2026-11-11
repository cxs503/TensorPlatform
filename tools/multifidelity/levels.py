"""Fidelity ladder primitive: levels, costs, and cost accounting.

A *level* is a black-box evaluator of the same scalar quantity, tagged with a rank
(0 = coarsest) and a cost in abstract work units.  Costs are supplied by the caller
(measured wall-clock, core-seconds, licence credits, …) — the framework never guesses
them, and never looks inside the evaluator.

Domain neutrality: the only names this module knows are ``name``/``rank``/``cost``/
``evaluate``.  What a level physically *is* belongs to the caller's configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

import numpy as np

__all__ = ["Level", "Ladder", "EvaluationLedger"]


@dataclass(frozen=True)
class Level:
    """One model in the ladder.

    Parameters
    ----------
    name:
        Opaque label (echoed back in reports).
    rank:
        0 = coarsest/cheapest, increasing with fidelity.  Must be unique and
        contiguous from 0 inside a :class:`Ladder`.
    cost:
        Abstract cost per evaluation (> 0).  Only cost *ratios* matter for allocation.
    evaluate:
        ``f(X) -> y`` with ``X`` shaped ``(n, d)`` and ``y`` shaped ``(n,)``.
    """

    name: str
    rank: int
    cost: float
    evaluate: Callable[[np.ndarray], np.ndarray]

    def __post_init__(self) -> None:
        if self.rank < 0:
            raise ValueError(f"rank must be >= 0, got {self.rank}")
        if not np.isfinite(self.cost) or self.cost <= 0:
            raise ValueError(f"cost must be finite and > 0, got {self.cost}")
        if not callable(self.evaluate):
            raise TypeError("evaluate must be callable")


@dataclass
class EvaluationLedger:
    """Running cost/count accounting for one ladder campaign."""

    counts: Dict[int, int] = field(default_factory=dict)
    spent: float = 0.0

    def record(self, rank: int, n: int, cost: float) -> None:
        self.counts[rank] = self.counts.get(rank, 0) + int(n)
        self.spent += float(n) * float(cost)

    def as_dict(self) -> Dict[str, object]:
        return {
            "counts": {int(k): int(v) for k, v in sorted(self.counts.items())},
            "spent_cost": float(self.spent),
        }


class Ladder:
    """An ordered collection of :class:`Level` objects with cost accounting."""

    def __init__(self, levels: Sequence[Level], ledger: EvaluationLedger | None = None):
        levels = list(levels)
        if len(levels) < 1:
            raise ValueError("a ladder needs at least one level")
        ranks = [lv.rank for lv in levels]
        if len(set(ranks)) != len(ranks):
            raise ValueError(f"duplicate ranks in ladder: {ranks}")
        if sorted(ranks) != list(range(len(levels))):
            raise ValueError(f"ranks must be 0..{len(levels) - 1}, got {sorted(ranks)}")
        costs = [lv.cost for lv in levels]
        if any(b < a for a, b in zip(costs, costs[1:])):
            raise ValueError(f"cost must be non-decreasing with rank, got {costs}")
        self._levels: List[Level] = sorted(levels, key=lambda lv: lv.rank)
        self.ledger: EvaluationLedger = ledger or EvaluationLedger()

    # ------------------------------------------------------------------ basics
    def __len__(self) -> int:
        return len(self._levels)

    def __iter__(self) -> Iterable[Level]:
        return iter(self._levels)

    def __getitem__(self, rank: int) -> Level:
        return self._levels[rank]

    @property
    def ranks(self) -> List[int]:
        return [lv.rank for lv in self._levels]

    @property
    def costs(self) -> np.ndarray:
        return np.array([lv.cost for lv in self._levels], dtype=float)

    @property
    def names(self) -> List[str]:
        return [lv.name for lv in self._levels]

    @property
    def top_rank(self) -> int:
        return len(self._levels) - 1

    # ------------------------------------------------------------- evaluation
    def evaluate(self, rank: int, X: np.ndarray) -> np.ndarray:
        """Evaluate level ``rank`` on ``X`` and charge the ledger."""
        level = self._levels[rank]
        X = np.atleast_2d(np.asarray(X, dtype=float))
        y = np.asarray(level.evaluate(X), dtype=float).reshape(-1)
        if y.shape[0] != X.shape[0]:
            raise ValueError(
                f"level {level.name!r} returned {y.shape[0]} values for {X.shape[0]} points"
            )
        self.ledger.record(rank, X.shape[0], level.cost)
        return y

    def cost_of(self, counts: Dict[int, int]) -> float:
        """Total cost of an allocation ``{rank: n}``."""
        return float(sum(self._levels[r].cost * int(n) for r, n in counts.items()))

    def as_dict(self) -> Dict[str, object]:
        return {
            "levels": [
                {"name": lv.name, "rank": lv.rank, "cost": float(lv.cost)}
                for lv in self._levels
            ],
            "ledger": self.ledger.as_dict(),
        }

    # ------------------------------------------------------------- helpers
    def cost_ratios(self) -> List[float]:
        """Cost of each level relative to the cheapest one (rank 0)."""
        c0 = float(self._levels[0].cost)
        return [float(lv.cost / c0) for lv in self._levels]


def normalize_ranks(levels: Sequence[Level]) -> Tuple[Level, ...]:
    """Return levels sorted by rank (validation already done by :class:`Ladder`)."""
    return tuple(sorted(levels, key=lambda lv: lv.rank))