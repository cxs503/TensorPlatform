"""Vectorised reference implementation of NSGA-II (Deb et al. 2002).

Kept generic on purpose (any ``n_obj``/``n_var``, box bounds, maximise->negate
at the call site).  Every operator is reproducible from ``seed``:

* fast non-dominated sorting (vectorised domination matrix + front peeling),
* crowding distance (Deb 2002 §III-B),
* binary tournament on (rank, -crowding),
* simulated binary crossover, SBX, ``eta_c = 20`` (Deb & Agrawal 1995),
* polynomial mutation, ``eta_m = 20`` (Deb & Goyal 1996),
* ``(mu + lambda)`` elitist survival: 2N pool -> rank-wise truncation.

The history recorded per generation is what the validation faces consume:
GD/IGD/spacing/coverage against the analytic front plus the number of
non-dominated members of the current population.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from .metrics import evaluate_front

__all__ = [
    "NSGA2Config",
    "run_nsga2",
    "NSGA2Result",
    "fast_non_dominated_sort",
    "crowding_distance",
    "non_dominated_rank",
    "nsga2",
    "sbx_crossover",
    "polynomial_mutation",
]


# ---------------------------------------------------------------------------
# Config / result containers
# ---------------------------------------------------------------------------
@dataclass
class NSGA2Config:
    pop_size: int = 100
    n_generations: int = 300
    crossover_prob: float = 0.9
    crossover_eta: float = 20.0
    mutation_eta: float = 20.0
    mutation_prob: Optional[float] = None  # None -> 1 / n_var (Deb 2002)
    seed: int = 0
    track_front_metrics: bool = True
    analytic_front: Optional[np.ndarray] = field(default=None, repr=False)

    def resolved_mutation_prob(self, n_var: int) -> float:
        return 1.0 / n_var if self.mutation_prob is None else self.mutation_prob


@dataclass
class NSGA2Result:
    population: np.ndarray
    objectives: np.ndarray
    ranks: np.ndarray
    history: List[Dict[str, float]]
    n_evaluations: int
    wall_time_s: float
    seed: int

    def pareto_set(self) -> np.ndarray:
        return self.population[self.ranks == self.ranks.min()]

    def pareto_front(self) -> np.ndarray:
        return self.objectives[self.ranks == self.ranks.min()]

    def final_metrics(self) -> Dict[str, float]:
        return self.history[-1] if self.history else {}


# ---------------------------------------------------------------------------
# Non-dominated sorting
# ---------------------------------------------------------------------------
def _domination_matrix(F: np.ndarray) -> np.ndarray:
    """``D[i, j] = 1`` iff solution ``i`` Pareto-dominates ``j`` (all minimised)."""
    le = np.all(F[:, None, :] <= F[None, :, :], axis=2)
    lt = np.any(F[:, None, :] < F[None, :, :], axis=2)
    return le & lt


def non_dominated_rank(F: np.ndarray) -> np.ndarray:
    """Return the NSGA-II rank (0 = first front) of every row of ``F``."""
    F = np.atleast_2d(np.asarray(F, dtype=float))
    n = F.shape[0]
    ranks = np.full(n, -1, dtype=int)
    if n == 0:
        return ranks
    D = _domination_matrix(F)
    alive = np.ones(n, dtype=bool)
    front = 0
    while alive.any():
        idx = np.flatnonzero(alive)
        counts = D[np.ix_(idx, idx)].sum(axis=0)
        current = idx[counts == 0]
        ranks[current] = front
        alive[current] = False
        front += 1
    return ranks


def fast_non_dominated_sort(F: np.ndarray) -> List[List[int]]:
    """Classic API-compatible front decomposition: list of index lists."""
    ranks = non_dominated_rank(F)
    return [list(np.flatnonzero(ranks == r)) for r in range(ranks.max() + 1)]


def crowding_distance(F: np.ndarray, front: Sequence[int]) -> np.ndarray:
    """Crowding distance of the members of one front (Deb 2002 §III-B)."""
    idx = np.asarray(list(front), dtype=int)
    n = idx.size
    cd = np.zeros(n, dtype=float)
    if n == 0:
        return cd
    if n <= 2:
        cd[:] = np.inf
        return cd
    sub = F[idx]
    for m in range(sub.shape[1]):
        order = np.argsort(sub[:, m], kind="mergesort")
        cd[order[0]] = np.inf
        cd[order[-1]] = np.inf
        span = sub[order[-1], m] - sub[order[0], m]
        if span > 0:
            cd[order[1:-1]] += (sub[order[2:], m] - sub[order[:-2], m]) / span
    return cd


# ---------------------------------------------------------------------------
# Variation operators
# ---------------------------------------------------------------------------
def sbx_crossover(
    p1: np.ndarray,
    p2: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    rng: np.random.Generator,
    eta: float = 20.0,
    prob: float = 0.9,
) -> tuple:
    """Simulated binary crossover for a pair of parents -> two children."""
    n = p1.size
    child1 = p1.copy()
    child2 = p2.copy()
    if rng.random() > prob:
        return child1, child2
    u = rng.random(n)
    beta = np.where(
        u <= 0.5,
        (2.0 * u) ** (1.0 / (eta + 1.0)),
        (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0)),
    )
    c1 = 0.5 * ((1.0 + beta) * p1 + (1.0 - beta) * p2)
    c2 = 0.5 * ((1.0 - beta) * p1 + (1.0 + beta) * p2)
    return np.clip(c1, lower, upper), np.clip(c2, lower, upper)


def polynomial_mutation(
    x: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    rng: np.random.Generator,
    eta: float = 20.0,
    prob: Optional[float] = None,
) -> np.ndarray:
    """Polynomial mutation (Deb & Goyal 1996), bounded."""
    y = x.copy()
    n = y.size
    if prob is None:
        prob = 1.0 / n
    mask = rng.random(n) < prob
    if not mask.any():
        return y
    span = upper - lower
    u = rng.random(n)
    delta = np.where(
        u < 0.5,
        (2.0 * u) ** (1.0 / (eta + 1.0)) - 1.0,
        1.0 - (2.0 * (1.0 - u)) ** (1.0 / (eta + 1.0)),
    )
    y[mask] = y[mask] + delta[mask] * span[mask]
    return np.clip(y, lower, upper)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _tournament_select(
    ranks: np.ndarray, crowding: np.ndarray, rng: np.random.Generator
) -> int:
    a, b = rng.integers(0, ranks.size, size=2)
    if ranks[a] < ranks[b]:
        return int(a)
    if ranks[b] < ranks[a]:
        return int(b)
    return int(a if crowding[a] >= crowding[b] else b)


def _survival_sort(objectives: np.ndarray) -> tuple:
    """Return (ranks, crowding) for the combined pool."""
    ranks = non_dominated_rank(objectives)
    crowding = np.full(objectives.shape[0], -np.inf, dtype=float)
    for r in range(ranks.max() + 1):
        members = np.flatnonzero(ranks == r)
        crowding[members] = crowding_distance(objectives, members)
    return ranks, crowding


def nsga2(
    problem,
    config: NSGA2Config,
    analytic_front: Optional[np.ndarray] = None,
) -> NSGA2Result:
    """Run NSGA-II on ``problem`` (anything with ``evaluate``/``lower``/``upper``)."""
    t0 = time.perf_counter()
    if analytic_front is None:
        analytic_front = config.analytic_front
    rng = np.random.default_rng(config.seed)
    lower = np.asarray(problem.lower, dtype=float)
    upper = np.asarray(problem.upper, dtype=float)
    n_var = lower.size
    pop_size = config.pop_size
    pm = config.resolved_mutation_prob(n_var)
    reference_point = np.ones(problem.n_obj) * 1.5

    pop = rng.uniform(lower, upper, size=(pop_size, n_var))
    objs = np.asarray(problem.evaluate(pop), dtype=float)
    n_eval = pop_size
    history: List[Dict[str, float]] = []

    def snapshot(gen: int, objectives: np.ndarray) -> None:
        if not config.track_front_metrics or analytic_front is None:
            history.append({"generation": gen})
            return
        ranks = non_dominated_rank(objectives)
        front = objectives[ranks == 0]
        m = evaluate_front(front, analytic_front, reference_point)
        m["generation"] = gen
        m["front_share"] = float(front.shape[0]) / float(objectives.shape[0])
        history.append(m)

    snapshot(0, objs)

    ranks, crowding = _survival_sort(objs)
    for gen in range(1, config.n_generations + 1):
        children = np.empty_like(pop)
        i = 0
        while i < pop_size:
            a = _tournament_select(ranks, crowding, rng)
            b = _tournament_select(ranks, crowding, rng)
            c1, c2 = sbx_crossover(pop[a], pop[b], lower, upper, rng,
                                   eta=config.crossover_eta,
                                   prob=config.crossover_prob)
            children[i] = polynomial_mutation(c1, lower, upper, rng,
                                              eta=config.mutation_eta, prob=pm)
            if i + 1 < pop_size:
                children[i + 1] = polynomial_mutation(c2, lower, upper, rng,
                                                      eta=config.mutation_eta,
                                                      prob=pm)
            i += 2

        child_objs = np.asarray(problem.evaluate(children), dtype=float)
        n_eval += pop_size

        pool_pop = np.vstack([pop, children])
        pool_obj = np.vstack([objs, child_objs])
        pool_ranks, pool_crowding = _survival_sort(pool_obj)

        # Elitist truncation: rank ascending, crowding descending.
        order = np.lexsort((-pool_crowding, pool_ranks))[:pop_size]
        pop = pool_pop[order]
        objs = pool_obj[order]
        ranks = non_dominated_rank(objs)
        crowding = np.full(pop_size, -np.inf)
        for r in range(ranks.max() + 1):
            members = np.flatnonzero(ranks == r)
            crowding[members] = crowding_distance(objs, members)

        snapshot(gen, objs)

    wall = time.perf_counter() - t0
    return NSGA2Result(
        population=pop,
        objectives=objs,
        ranks=non_dominated_rank(objs),
        history=history,
        n_evaluations=n_eval,
        wall_time_s=wall,
        seed=config.seed,
    )


run_nsga2 = nsga2  # public alias (keeps the prefix explicit)


def converges_at(
    history: Sequence[Dict[str, float]],
    metric: str = "gd",
    threshold: float = 0.01,
    patience: int = 3,
) -> Optional[int]:
    """First generation from which ``metric`` stays <= ``threshold``.

    "Stays" means the next ``patience`` recorded generations also satisfy the
    threshold — guards against a transient dip of a noisy metric.
    """
    values = [h.get(metric, np.nan) for h in history]
    gens = [int(h.get("generation", i)) for i, h in enumerate(history)]
    for idx, val in enumerate(values):
        if not np.isfinite(val) or val > threshold:
            continue
        window = values[idx: idx + patience]
        if len(window) < patience:
            window = values[idx:]
        if all(np.isfinite(v) and v <= threshold for v in window):
            return gens[idx]
    return None