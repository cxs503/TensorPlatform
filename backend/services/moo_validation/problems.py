"""Analytic multi-objective benchmark problems with closed-form Pareto fronts.

Zero domain semantics: this module knows nothing about any application field; a
domain layer injects its own objectives through the same protocol
(``evaluate(x) -> (n, n_obj)`` + optional ``analytic_front()``).

Pinned problems (Zitzler, Deb & Thiele 2000, "Comparison of Multiobjective
Evolutionary Algorithms: Empirical Results", Evol. Comput. 8(2)):

===========  ==========================================  =====================
problem      objectives (minimised)                      analytic front
===========  ==========================================  =====================
ZDT1         f1 = x1 ; f2 = g * (1 - sqrt(f1/g))          f2 = 1 - sqrt(f1)
ZDT2         f1 = x1 ; f2 = g * (1 - (f1/g)^2)            f2 = 1 - f1^2
ZDT3         f1 = x1 ; f2 = g * (1 - sqrt(f1/g)           f2 = 1 - sqrt(f1)
                          - (f1/g) * sin(10*pi*f1))       - f1*sin(10*pi*f1)
                                                          (disconnected, 5 arcs)
===========  ==========================================  =====================

with ``g = 1 + 9/(n-1) * sum_{i>=2} x_i`` and ``x_i in [0, 1]``.  The Pareto
set is ``x_2..x_n = 0``; the front is 1-D in objective space, which makes both
convergence (GD) and spread (spacing) machine-checkable against a closed form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

import numpy as np

__all__ = [
    "ZDT1",
    "ZDT2",
    "ZDT3",
    "BenchmarkProblem",
    "PROBLEM_REGISTRY",
    "get_problem",
    "non_dominated_mask",
]


def non_dominated_mask(F: np.ndarray, tol: float = 0.0) -> np.ndarray:
    """Boolean mask of non-dominated rows of ``F`` (all objectives minimised).

    ``tol`` allows an epsilon-dominance margin to absorb floating point noise in
    a densely sampled analytic curve.
    """
    F = np.asarray(F, dtype=float)
    n = F.shape[0]
    keep = np.ones(n, dtype=bool)
    le = np.all(F[:, None, :] <= F[None, :, :] + tol, axis=2)
    lt = np.any(F[:, None, :] < F[None, :, :] - tol, axis=2)
    dominates = le & lt
    return ~dominates.any(axis=0)


@dataclass
class BenchmarkProblem:
    """Minimise ``n_obj`` objectives over the unit hypercube.

    Attributes
    ----------
    name : str
        Registry key.
    n_var : int
        Decision-space dimension (ZDT convention: ``n_var >= 2``).
    n_obj : int
        Number of objectives (2 for all ZDT problems).
    reference : str
        Literature anchor for the closed form (audit chain).
    """

    name: str
    n_var: int = 30
    n_obj: int = 2
    reference: str = ""
    lower: Optional[np.ndarray] = None
    upper: Optional[np.ndarray] = None
    _evaluate_fn: Optional[Callable[[np.ndarray], np.ndarray]] = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        if self.lower is None:
            self.lower = np.zeros(self.n_var)
        if self.upper is None:
            self.upper = np.ones(self.n_var)
        self.lower = np.asarray(self.lower, dtype=float)
        self.upper = np.asarray(self.upper, dtype=float)

    # -- evaluation ------------------------------------------------------
    def evaluate(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        if X.shape[1] != self.n_var:
            raise ValueError(f"expected {self.n_var} variables, got {X.shape[1]}")
        return np.clip(self._evaluate_fn(X), None, None)

    # -- analytic front --------------------------------------------------
    def analytic_front(self, n_points: int = 1000) -> np.ndarray:
        raise NotImplementedError

    # -- serialisation ---------------------------------------------------
    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "n_var": self.n_var,
            "n_obj": self.n_obj,
            "bounds": [self.lower.min(), self.upper.max()],
            "reference": self.reference,
        }


class ZDT1(BenchmarkProblem):
    reference = "ZDT 2000, test problem 1 (convex front)"

    def __init__(self, n_var: int = 30) -> None:
        super().__init__(name="ZDT1", n_var=n_var, n_obj=2,
                         reference=ZDT1.reference)
        self._evaluate_fn = self._evaluate

    def _evaluate(self, X: np.ndarray) -> np.ndarray:
        f1 = X[:, 0]
        g = 1.0 + 9.0 * np.sum(X[:, 1:], axis=1) / (self.n_var - 1)
        h = 1.0 - np.sqrt(f1 / g)
        return np.column_stack([f1, g * h])

    def analytic_front(self, n_points: int = 1000) -> np.ndarray:
        f1 = np.linspace(0.0, 1.0, n_points)
        return np.column_stack([f1, 1.0 - np.sqrt(f1)])


class ZDT2(BenchmarkProblem):
    reference = "ZDT 2000, test problem 2 (concave front)"

    def __init__(self, n_var: int = 30) -> None:
        super().__init__(name="ZDT2", n_var=n_var, n_obj=2,
                         reference=ZDT2.reference)
        self._evaluate_fn = self._evaluate

    def _evaluate(self, X: np.ndarray) -> np.ndarray:
        f1 = X[:, 0]
        g = 1.0 + 9.0 * np.sum(X[:, 1:], axis=1) / (self.n_var - 1)
        h = 1.0 - (f1 / g) ** 2
        return np.column_stack([f1, g * h])

    def analytic_front(self, n_points: int = 1000) -> np.ndarray:
        f1 = np.linspace(0.0, 1.0, n_points)
        return np.column_stack([f1, 1.0 - f1 ** 2])


class ZDT3(BenchmarkProblem):
    reference = "ZDT 2000, test problem 3 (disconnected front, 5 Pareto arcs)"

    def __init__(self, n_var: int = 30) -> None:
        super().__init__(name="ZDT3", n_var=n_var, n_obj=2,
                         reference=ZDT3.reference)
        self._evaluate_fn = self._evaluate

    def _evaluate(self, X: np.ndarray) -> np.ndarray:
        f1 = X[:, 0]
        g = 1.0 + 9.0 * np.sum(X[:, 1:], axis=1) / (self.n_var - 1)
        h = 1.0 - np.sqrt(f1 / g) - (f1 / g) * np.sin(10.0 * np.pi * f1)
        return np.column_stack([f1, g * h])

    def analytic_front(self, n_points: int = 4000) -> np.ndarray:
        """Sample ``f2 = 1 - sqrt(f1) - f1*sin(10*pi*f1)`` and keep the
        non-dominated subset (the 5 disjoint Pareto arcs)."""
        f1 = np.linspace(0.0, 1.0, n_points)
        f2 = 1.0 - np.sqrt(f1) - f1 * np.sin(10.0 * np.pi * f1)
        curve = np.column_stack([f1, f2])
        return curve[non_dominated_mask(curve, tol=1e-12)]


PROBLEM_REGISTRY: Dict[str, Callable[..., BenchmarkProblem]] = {
    "ZDT1": ZDT1,
    "ZDT2": ZDT2,
    "ZDT3": ZDT3,
}


def get_problem(name: str, n_var: int = 30) -> BenchmarkProblem:
    key = name.upper()
    if key not in PROBLEM_REGISTRY:
        raise KeyError(f"unknown benchmark problem {name!r}; "
                       f"known: {sorted(PROBLEM_REGISTRY)}")
    return PROBLEM_REGISTRY[key](n_var=n_var)