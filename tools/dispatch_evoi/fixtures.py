"""Synthetic dispatch log with **known ground truth** (offline fixed fixture).

Why synthetic
-------------
The working tree carries no versioned, fully labelled dispatch log (the live log
is a database behind the platform API and the evaluation must be offline and
replayable), so the *evaluation machinery* is validated against a data-generating
process (DGP) whose pass-probability function, cost function and logging policy
are written down here and asserted in ``tests/test_dispatch_evoi.py``.  That is
what makes the walk-forward numbers falsifiable: a model that recovers the DGP
has a computable ceiling, and a model that cheats (reads a post-hoc field)
produces a *measurable, wrong* jump.

Everything is drawn from a single ``numpy.random.default_rng(seed)`` stream in a
fixed order, so two runs with the same seed are bit-identical.

The DGP
-------
Per round ``t`` a slate of ``S`` candidate orders is offered.  For candidate
``i`` the simulator knows the *observable* dispatch-time features only
(:mod:`tools.dispatch_evoi.features`) and draws

    z_i        = b0 + b_nov * novelty_i
                    + b_fail * family_fail_rate_i            (negative: burnt family)
                    + b_cell * repo_scale_success_i          (positive: proven cell)
                    + b_band * crit_band_i                   (sweet band of criteria count)
                    + b_words * log1p(target_words/1000)
                    + b_tier * tier_ord_i                    (weak on purpose)
    P(pass_i)  = sigmoid(z_i)

The outcome is an independent Bernoulli draw, *for every candidate* -- that is
the simulator's counterfactual ground truth, and it is what lets precision@20%
be measured without outcome labels for non-dispatched orders (a real log cannot
do that; see the report's limitations section).

    wall_sec_i = 90 * 2.2**scale_ord * (1+0.45*log1p(words/1000))
                   * repo_speed[repo] * lognormal(0, 0.35)
    cores_i    = 2**scale_ord
    cost_i     = cores_i * wall_sec_i / 60          (core-minutes)

The logging policy ``pi0`` is the platform's *static priority* rule (tier and
criterion count only -- see :func:`priority`) turned into a probability by a
softmax, then mixed with a uniform exploration floor ``eps`` (the platform's
exploration budget).  ``pi0(a | s)`` is recorded, which is exactly the propensity
IPS/SNIPS need.  Because ``pi0`` ignores novelty / family history / repo-cell
history, the logged sample is **self-selected**, and the naive estimate of a new
policy's value is biased -- the effect the off-policy section measures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .features import AggregateState, numeric_row, scale_bucket, tier_order

__all__ = [
    "REPOS",
    "FAMILIES",
    "TIERS",
    "TRUE_COEF",
    "TRUE_COST",
    "Dataset",
    "Round",
    "make_dataset",
    "priority",
    "true_logit",
    "true_cost",
]

REPOS: tuple[str, ...] = tuple(f"r{i:02d}" for i in range(12))
FAMILIES: tuple[str, ...] = ("fam_a", "fam_b", "fam_c", "fam_d", "fam_e")
TIERS: tuple[str, ...] = ("T1", "T2", "T3", "T4", "T5")

#: ground-truth logit coefficients (asserted by the known-truth test)
TRUE_COEF: dict[str, float] = {
    "intercept": -0.10,
    "novelty": 2.20,
    "family_fail_rate_dev": -4.00,
    "cell_success_dev": 5.00,
    "crit_band": 0.80,
    "log_target_words": -0.90,
    "tier_ord": 0.10,   # deliberately weak: the static dispatcher must not be able to see the signal
    "scale_ord": -0.35, # big-compute orders fail a bit more often (value density matters)
}

#: ground-truth cost coefficients (on log core-minutes)
TRUE_COST: dict[str, float] = {
    "intercept": math.log(1.5),
    "log_target_words": 0.45,
    "scale_ord": 0.75,
    "tier_ord": 0.05,
}

_SCALE_ORD = {"small": 0, "medium": 1, "large": 2}


def priority(order: dict[str, Any]) -> float:
    """The current dispatcher's **static** priority for an order.

    Deterministic, tier-first, criterion-count second -- mirrors the fleet
    dispatcher's static ordering (``backend/services/mission_gradient.py`` orders
    work by tier then size and carries no outcome model).  This is the baseline
    the EVOI policy has to beat.
    """
    return 10.0 * tier_order(order["tier"]) + 0.2 * float(order["n_criteria"])


def true_logit(num: dict[str, float], coef: dict[str, float] = TRUE_COEF) -> float:
    """Ground-truth log-odds.  A function of *dispatch-time observables only*
    (deviations relative to the running global pass rate), so the DGP has no
    hidden confounder: a correctly specified model can reach the ceiling."""
    z = coef["intercept"]
    z += coef["novelty"] * num["novelty"]
    z += coef["family_fail_rate_dev"] * num["family_fail_rate_dev"]
    z += coef["cell_success_dev"] * num["cell_success_dev"]
    z += coef["crit_band"] * num["crit_band"]
    z += coef["log_target_words"] * num["log_target_words"]
    z += coef["tier_ord"] * num["tier_ord"]
    z += coef["scale_ord"] * num["scale_ord"]
    return float(z)


def true_cost(num: dict[str, float], coef: dict[str, float] = TRUE_COST) -> float:
    """Expected log core-minutes (multiplicative noise is added by the caller)."""
    return float(
        coef["intercept"]
        + coef["log_target_words"] * num["log_target_words"]
        + coef["scale_ord"] * num["scale_ord"]
        + coef["tier_ord"] * num["tier_ord"]
    )


@dataclass
class Round:
    round_id: int
    dispatch_ts: float
    candidates: list[dict[str, Any]]
    priority: list[float]
    propensity: list[float]
    logged_index: int
    outcomes: list[int]
    cost: list[float]
    wall_sec: list[float]
    p_true: list[float]

    @property
    def logged_order(self) -> dict[str, Any]:
        return self.candidates[self.logged_index]


@dataclass
class Dataset:
    rounds: list[Round]
    repos: tuple[str, ...]
    families: tuple[str, ...]
    tiers: tuple[str, ...]
    slate_size: int
    seed: int
    explore_eps: float
    temperature: float
    spec: dict[str, Any]


def make_dataset(
    *,
    seed: int = 7,
    n_rounds: int = 1200,
    slate_size: int = 8,
    explore_eps: float = 0.35,
    temperature: float = 1.0,
    repo_speed_seed: int | None = None,
) -> Dataset:
    """Simulate one dispatch log (deterministic given ``seed``)."""
    rng = np.random.default_rng(seed)
    speed_rng = np.random.default_rng(seed if repo_speed_seed is None else repo_speed_seed)
    repo_speed = {r: float(speed_rng.lognormal(0.0, 0.22)) for r in REPOS}
    state = AggregateState()
    rounds: list[Round] = []
    day = 0.0
    for t in range(n_rounds):
        # ~2.2 dispatch rounds per simulated day; timestamps are whole seconds
        day += 0.45 + 0.1 * float(rng.random())
        dispatch_ts = round(day * 86400.0, 3)
        cands: list[dict[str, Any]] = []
        for _ in range(slate_size):
            budget = float(rng.choice([40.0, 90.0, 140.0, 220.0, 320.0]))
            n_crit = int(rng.integers(2, 11))
            words = float(rng.integers(400, 6000))
            tier = str(rng.choice(TIERS))
            order = {
                "repo": str(rng.choice(REPOS)),
                "family": str(rng.choice(FAMILIES)),
                "tier": tier,
                "n_criteria": n_crit,
                "target_words": words,
                "novelty": float(np.round(rng.beta(2.0, 2.0), 4)),
                "budget_units": budget,
            }
            order["value_units"] = float(
                np.round(10.0 * tier_order(tier) * (0.6 + 0.4 * order["novelty"]), 3)
            )
            # every field above is stamped at intake, i.e. strictly before dispatch
            order["field_ts"] = {k: dispatch_ts for k in order if k != "field_ts"}
            cands.append(order)

        nums = [numeric_row(o, state) for o in cands]
        p_true = [1.0 / (1.0 + math.exp(-true_logit(n))) for n in nums]
        outcomes = [int(rng.random() < p) for p in p_true]

        walls: list[float] = []
        costs: list[float] = []
        for o in cands:
            scale = scale_bucket(float(o["budget_units"]))
            k = _SCALE_ORD[scale]
            cores = 2.0**k
            wall = (
                90.0
                * (2.2**k)
                * (1.0 + 0.45 * math.log1p(float(o["target_words"]) / 1000.0))
                * repo_speed[str(o["repo"])]
                * float(rng.lognormal(0.0, 0.35))
            )
            walls.append(wall)
            costs.append(cores * wall / 60.0)

        pr = [priority(o) for o in cands]
        # the dispatcher's softmax is taken over the *standardised* static
        # priority so that the exploration floor is not drowned by the raw
        # 10..52 range of the tier-first score
        raw = np.asarray(pr, dtype=float)
        logits = (raw - raw.mean()) / (raw.std() + 1e-9) / temperature
        logits -= logits.max()
        soft = np.exp(logits)
        soft /= soft.sum()
        pi = explore_eps / slate_size + (1.0 - explore_eps) * soft
        pi /= pi.sum()
        logged = int(rng.choice(slate_size, p=pi))

        rounds.append(
            Round(
                round_id=t,
                dispatch_ts=dispatch_ts,
                candidates=cands,
                priority=pr,
                propensity=[float(x) for x in pi],
                logged_index=logged,
                outcomes=outcomes,
                cost=costs,
                wall_sec=walls,
                p_true=p_true,
            )
        )
        chosen = cands[logged]
        state.update(
            family=str(chosen["family"]),
            repo=str(chosen["repo"]),
            scale=scale_bucket(float(chosen["budget_units"])),
            passed=bool(outcomes[logged]),
        )

    spec = {
        "generator": "tools.dispatch_evoi.fixtures.make_dataset",
        "seed": seed,
        "n_rounds": n_rounds,
        "slate_size": slate_size,
        "explore_eps": explore_eps,
        "temperature": temperature,
        "true_coef": dict(TRUE_COEF),
        "true_cost": dict(TRUE_COST),
        "priority_rule": "10*tier_ord + 0.2*n_criteria (static, tier-first)",
        "repo_speed": {k: round(v, 6) for k, v in repo_speed.items()},
    }
    return Dataset(
        rounds=rounds,
        repos=REPOS,
        families=FAMILIES,
        tiers=TIERS,
        slate_size=slate_size,
        seed=seed,
        explore_eps=explore_eps,
        temperature=temperature,
        spec=spec,
    )