"""Budget-constrained fidelity allocation.

Two strategies, both domain neutral:

``optimal_static_allocation``
    Closed-form error law per level (``err_k(n) = a_k n^-b_k``, fitted from
    cross-validation data by :func:`fit_error_law`) plus the first-order propagation
    model of the co-kriging chain.  A discrete grid search returns the allocation that
    minimises the predicted error for a given budget.  Cheap, deterministic, and easy to
    audit — used to *plan* a campaign.

``adaptive_allocation``
    Sequential, data-driven: fit the co-kriging chain, then spend the next unit of
    budget on the (level, point) pair with the largest *expected variance reduction per
    unit cost*.  The acquisition is the standard kriging identity
    ``Delta var(x_p) = cov(x_p, x)^2 / var(x)``, propagated up the autoregressive chain
    by ``prod_{j>k} rho_j^2``.  Used to *execute* a campaign; it is what "adaptive
    fidelity selection under a budget" means operationally.

Predicted error (static model)
------------------------------
``e(n) = sqrt( w_K^2 a_K^2 n_K^{-2 b_K} + sum_{k<K} w_k^2 a_k^2 n_k^{-2 b_k} )``
with ``w_k = prod_{j>k} rho_j`` (user supplied, default 1).  The rationale: the top
level's own interpolation error cannot be removed by lower levels, while lower-level
modelling error propagates through the ``rho`` chain (and is *reduced*, not removed, by
the residual processes).  Forgetting the reduction (using ``w_k`` unchanged) makes the
model conservative — it never claims a lower error than the chain can deliver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .cokriging import MultiFidelitySurrogate
from .levels import Ladder

__all__ = [
    "Allocation",
    "fit_error_law",
    "predict_error",
    "optimal_static_allocation",
    "adaptive_allocation",
    "expected_error_reduction",
]


# --------------------------------------------------------------------- error laws
@dataclass
class ErrorLaw:
    """Power-law error model ``a * n**(-b)`` with a floor."""

    a: float
    b: float
    floor: float = 0.0

    def __call__(self, n: float) -> float:
        n = max(float(n), 1.0)
        return max(self.a * n ** (-self.b), self.floor)

    def as_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "floor": self.floor}


def fit_error_law(ns: Sequence[float], errs: Sequence[float], floor: float = 0.0) -> ErrorLaw:
    """Least-squares fit of ``log(err - floor) = log a - b log n``."""
    ns = np.asarray([max(float(v), 1.0) for v in ns], dtype=float)
    errs = np.asarray([float(v) for v in errs], dtype=float)
    if ns.size < 2:
        raise ValueError("need at least two (n, error) pairs")
    e = np.maximum(errs - floor, 1e-300)
    A = np.vstack([np.ones_like(ns), -np.log(ns)]).T
    coef, *_ = np.linalg.lstsq(A, np.log(e), rcond=None)
    a = float(np.exp(coef[0]))
    b = float(max(coef[1], 0.0))
    return ErrorLaw(a=a, b=b, floor=floor)


def predict_error(
    counts: Mapping[int, int],
    laws: Mapping[int, ErrorLaw],
    rho_chain: Sequence[float] | None = None,
    top_rank: Optional[int] = None,
) -> float:
    """First-order predicted error of the fused surrogate for an allocation."""
    ranks = sorted(counts)
    if top_rank is not None:
        ranks = [r for r in ranks if r <= top_rank]
    if not ranks:
        return float("inf")
    top = ranks[-1]
    rho_chain = list(rho_chain or [1.0] * len(ranks))
    acc = 0.0
    for idx, r in enumerate(ranks):
        if r not in laws:
            continue
        w = 1.0
        for j in range(idx + 1, len(ranks)):
            rho = rho_chain[j] if j < len(rho_chain) else 1.0
            w *= float(rho)
        acc += (w * laws[r](counts[r])) ** 2
    return float(np.sqrt(acc))


@dataclass
class Allocation:
    """An allocation of evaluations over the ladder."""

    counts: Dict[int, int]
    cost: float
    predicted_error: float
    budget: float
    strategy: str = "static"
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return self.cost <= self.budget + 1e-9

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "counts": {int(k): int(v) for k, v in sorted(self.counts.items())},
            "cost": float(self.cost),
            "budget": float(self.budget),
            "feasible": bool(self.feasible),
            "predicted_error": float(self.predicted_error),
            "detail": self.detail,
        }


# ---------------------------------------------------------------- static allocation
def optimal_static_allocation(
    ladder: Ladder,
    budget: float,
    laws: Mapping[int, ErrorLaw],
    rho_chain: Sequence[float] | None = None,
    caps: Optional[Mapping[int, int]] = None,
    min_counts: Optional[Mapping[int, int]] = None,
) -> Allocation:
    """Exhaustive/greedy-search allocation minimising the predicted error.

    Search space: every level count from its minimum to its cap (``caps`` default 60 for
    lower levels and 40 for the top level).  The search is a full product over levels, so
    it is exact within the caps — no heuristic needed at these sizes.
    """
    from itertools import product

    ranks = list(ladder.ranks)
    caps = dict(caps or {})
    min_counts = dict(min_counts or {})
    grids: List[range] = []
    for r in ranks:
        lo = int(min_counts.get(r, 3))
        hi = int(caps.get(r, 40 if r == ranks[-1] else 60))
        grids.append(range(max(lo, 1) if r == ranks[-1] else lo, hi + 1))

    best: Optional[Allocation] = None
    for combo in product(*grids):
        counts = {r: int(c) for r, c in zip(ranks, combo)}
        cost = ladder.cost_of(counts)
        if cost > budget + 1e-9:
            continue
        err = predict_error(counts, laws, rho_chain=rho_chain)
        if best is None or err < best.predicted_error - 1e-15:
            best = Allocation(counts=counts, cost=cost, predicted_error=err,
                              budget=budget, strategy="static")
    if best is None:  # nothing fits: fall back to the minimum design that fits
        counts = {r: int(min_counts.get(r, 1)) for r in ranks}
        while ladder.cost_of(counts) > budget and max(counts.values()) > 1:
            r = max(counts, key=lambda k: counts[k])
            counts[r] -= 1
        best = Allocation(counts=counts, cost=ladder.cost_of(counts),
                          predicted_error=predict_error(counts, laws, rho_chain=rho_chain),
                          budget=budget, strategy="static-fallback")
    return best


# ------------------------------------------------------------- adaptive allocation
def expected_error_reduction(
    surrogate: MultiFidelitySurrogate, rank: int, x: np.ndarray, pool: np.ndarray
) -> float:
    """Expected variance reduction at ``pool`` from one evaluation at ``(rank, x)``."""
    gp = surrogate.level_residual_gp(rank)
    if gp is None:
        return 0.0
    return float(gp.variance_reduction(np.asarray(x, dtype=float), pool)
                 * surrogate.propagate_factor(rank))


def _space_filling(n: int, d: int, rng: np.random.Generator, lo, hi) -> np.ndarray:
    """Latin-hypercube sample in the box [lo, hi]."""
    u = (np.argsort(rng.random((n, d)), axis=0) + rng.random((n, d))) / n
    return lo + u * (hi - lo)


def adaptive_allocation(
    ladder: Ladder,
    budget: float,
    pool: np.ndarray,
    box_lo: np.ndarray,
    box_hi: np.ndarray,
    *,
    init_counts: Optional[Mapping[int, int]] = None,
    caps: Optional[Mapping[int, int]] = None,
    batch: int = 1,
    max_rounds: int = 400,
    seed: int = 0,
    min_gain_per_cost: float = 1e-14,
    refit_every: int = 1,
    warm_start_hyper: bool = True,
    refit_hyper_every: int = 40,
) -> Dict[str, object]:
    """Sequentially spend ``budget`` on the ladder by variance-reduction per unit cost.

    Returns a dict with the fitted surrogate, the data actually evaluated, the ledger and
    the acquisition history (so the allocation decision is auditable after the fact).
    """
    rng = np.random.default_rng(seed)
    pool = np.atleast_2d(np.asarray(pool, dtype=float))
    box_lo = np.asarray(box_lo, dtype=float).reshape(1, -1)
    box_hi = np.asarray(box_hi, dtype=float).reshape(1, -1)
    ranks = list(ladder.ranks)
    top = ranks[-1]
    caps = dict(caps or {r: (40 if r == top else 120) for r in ranks})
    init_counts = dict(init_counts or {r: (3 if r == top else 8) for r in ranks})

    data: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    history: List[dict] = []
    for r in ranks:
        n = int(init_counts.get(r, 3))
        X = _space_filling(n, pool.shape[1], rng, box_lo, box_hi)
        y = ladder.evaluate(r, X)
        data[r] = (X, y)
        history.append({"rank": r, "kind": "init", "n_added": n,
                        "cost": n * ladder[r].cost})

    surrogate: MultiFidelitySurrogate | None = None
    rounds = 0
    batch = max(int(batch), 1)
    while rounds < int(max_rounds):
        rounds += 1
        if rounds % max(int(refit_every), 1) == 1 or surrogate is None:
            reuse = bool(warm_start_hyper and surrogate is not None
                         and rounds % max(int(refit_hyper_every), 1) != 1)
            surrogate = MultiFidelitySurrogate(
                nugget=1e-4, restarts=1, seed=seed, rho_mode="ls",
                reuse_hyper=reuse,
                hyper_cache=(surrogate._hyper_cache if surrogate is not None else None),
            ).fit(data)

        # acquisition uses a sub-pool (cheap, deterministic) over a random candidate set
        gain_pool = pool[rng.choice(pool.shape[0], size=min(64, pool.shape[0]),
                                    replace=False)]
        n_cand = min(48, pool.shape[0])
        cand = pool[rng.choice(pool.shape[0], size=n_cand, replace=False)]

        scored: List[Tuple[float, int, np.ndarray]] = []
        for r in ranks:
            gp = surrogate.level_residual_gp(r)
            if gp is None:
                continue
            if data.get(r) is not None and data[r][0].shape[0] >= caps.get(r, 10**9):
                continue
            if ladder.ledger.spent + ladder[r].cost > budget + 1e-9:
                continue
            cf = surrogate.propagate_factor(r)
            gains = gp.variance_reduction_many(cand, gain_pool) * cf / ladder[r].cost
            for g, x in zip(gains, cand):
                scored.append((float(g), int(r), np.asarray(x, dtype=float)))
        if not scored:
            break
        scored.sort(key=lambda t: -t[0])
        picked: List[Tuple[int, np.ndarray]] = []
        used_rows = set()
        seen_x = []
        for score, r, x in scored:
            if score < min_gain_per_cost:
                break
            if len(picked) >= batch:
                break
            if any(np.allclose(x, sx) for sx in seen_x):
                continue
            if ladder.ledger.spent + ladder[r].cost > budget + 1e-9:
                continue
            picked.append((r, x))
            seen_x.append(x)
        if not picked:
            break
        for r, x in picked:
            newX = np.atleast_2d(x)
            # nested design: every coarse level is also evaluated at the new point, which
            # is cheap and lets the co-kriging estimate its scaling from paired samples
            for rr in ranks:
                if rr > r:
                    continue
                newy = ladder.evaluate(rr, newX) if rr == r else ladder.evaluate(rr, newX)
                X_old, y_old = data[rr]
                data[rr] = (np.vstack([X_old, newX]), np.concatenate([y_old, newy]))
            history.append({"rank": int(r), "kind": "adaptive", "n_added": 1,
                            "cost": float(ladder[r].cost),
                            "spent_after": float(ladder.ledger.spent)})
        # keep the warm-start cache: the next refit reuses the length scales

    if surrogate is None:
        surrogate = MultiFidelitySurrogate(nugget=1e-8, restarts=1, seed=seed).fit(data)
    return {
        "surrogate": surrogate,
        "data": data,
        "history": history,
        "ledger": ladder.ledger.as_dict(),
        "rounds": rounds,
        "budget_exhausted": bool(ladder.ledger.spent >= budget - 1e-9),
    }