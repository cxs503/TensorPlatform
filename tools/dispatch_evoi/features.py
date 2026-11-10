"""Streaming, leakage-safe feature construction for the dispatch slate.

Two families of columns:

* **intake columns** -- read straight off the order record (repo, tier,
  ``n_criteria``, ``target_words``, ``novelty``, ``budget_units``, ``value_units``);
* **history columns** -- ``family_fail_rate`` (FAIL share of previously dispatched
  orders of the same upstream family) and ``repo_scale_success`` (shrunk pass
  rate of the same repo x scale bucket).  They are produced by
  :class:`AggregateState`, which is advanced round by round and only ever sees
  rounds *strictly before* the one being featurised.  That makes the walk-forward
  backtest honest: the aggregate a model sees at round ``t`` is exactly the one
  the live dispatcher would have had.

All columns are Beta/Gaussian-shrunk towards the running global prior so early
rounds (thin history) degrade to a sensible prior instead of to noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .schema import DISPATCH_FIELDS, assert_dispatch_time, assert_no_post_hoc_columns

__all__ = [
    "AggregateState",
    "NUMERIC_FEATURES",
    "CATEGORICAL_FEATURES",
    "numeric_row",
    "build_design",
    "design_names",
    "SCALE_ORDER",
    "tier_order",
]

#: stable ordering of the scale buckets used for the history aggregate
SCALE_ORDER: tuple[str, ...] = ("small", "medium", "large")
_TIER_ORDER = {"T1": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5}

#: numeric columns (order is part of the contract, see ``design_names``)
NUMERIC_FEATURES: tuple[str, ...] = (
    "n_criteria",
    "log_target_words",
    "novelty",
    "log_budget",
    "family_fail_rate_dev",
    "cell_success_dev",
    "global_pass_rate",
    "repo_hist_n",
    "crit_band",
    "tier_ord",
    "scale_ord",
)

# The raw family/cell rates are *not* columns: ``dev = raw - global_pass_rate``
# makes the raw version exactly collinear with the deviation and with the global
# rate, which would split the coefficient three ways and make the interpretability
# figure unreadable.  The deviations carry the same information, re-centred.
#
#: no one-hot block: repo identity enters through its history aggregates
#: (``repo_scale_success`` / ``repo_hist_n``).  With <=1000 labelled rounds a
#: 12-column dummy block would spend the whole ridge budget on sparse columns.
CATEGORICAL_FEATURES: tuple[str, ...] = ()


def tier_order(tier: str) -> int:
    return int(_TIER_ORDER[str(tier)])


def scale_bucket(budget_units: float, cuts: Sequence[float] = (60.0, 160.0)) -> str:
    if budget_units < cuts[0]:
        return "small"
    if budget_units < cuts[1]:
        return "medium"
    return "large"


@dataclass
class AggregateState:
    """Running history used by the history columns (no future information)."""

    fam_pass: dict[str, float] = field(default_factory=dict)
    fam_n: dict[str, float] = field(default_factory=dict)
    cell_pass: dict[tuple[str, str], float] = field(default_factory=dict)
    cell_n: dict[tuple[str, str], float] = field(default_factory=dict)
    repo_n: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    passed: float = 0.0
    #: Beta pseudo-counts for the family aggregate
    fam_prior_n: float = 4.0
    #: Beta pseudo-counts for the repo x scale cell
    cell_prior_n: float = 6.0

    # -- reads ---------------------------------------------------------------
    @property
    def global_pass_rate(self) -> float:
        """Running pass rate with a weakly informative Beta(2,2) prior."""
        return (self.passed + 2.0) / (self.total + 4.0)

    def family_fail_rate(self, family: str) -> float:
        g = 1.0 - self.global_pass_rate
        n = self.fam_n.get(family, 0.0)
        fails = self.fam_n.get(family, 0.0) - self.fam_pass.get(family, 0.0)
        return (fails + self.fam_prior_n * g) / (n + self.fam_prior_n)

    def repo_scale_success(self, repo: str, scale: str) -> float:
        g = self.global_pass_rate
        n = self.cell_n.get((repo, scale), 0.0)
        p = self.cell_pass.get((repo, scale), 0.0)
        return (p + self.cell_prior_n * g) / (n + self.cell_prior_n)

    # -- writes --------------------------------------------------------------
    def family_fail_dev(self, family: str) -> float:
        """Family fail rate minus the running global fail rate (regime-neutral)."""
        return self.family_fail_rate(family) - (1.0 - self.global_pass_rate)

    def cell_success_dev(self, repo: str, scale: str) -> float:
        """Repo x scale pass rate minus the running global pass rate."""
        return self.repo_scale_success(repo, scale) - self.global_pass_rate

    def repo_history(self, repo: str) -> float:
        return self.repo_n.get(repo, 0.0)

    def update(self, *, family: str, repo: str, scale: str, passed: bool) -> None:
        y = 1.0 if passed else 0.0
        self.total += 1.0
        self.passed += y
        self.fam_n[family] = self.fam_n.get(family, 0.0) + 1.0
        self.fam_pass[family] = self.fam_pass.get(family, 0.0) + y
        key = (repo, scale)
        self.cell_n[key] = self.cell_n.get(key, 0.0) + 1.0
        self.cell_pass[key] = self.cell_pass.get(key, 0.0) + y
        self.repo_n[repo] = self.repo_n.get(repo, 0.0) + 1.0


def numeric_row(order: Mapping[str, Any], state: AggregateState) -> dict[str, float]:
    """Numeric columns for one candidate order given the pre-round history."""
    scale = scale_bucket(float(order["budget_units"]))
    n_crit = float(order["n_criteria"])
    return {
        "n_criteria": n_crit,
        "log_target_words": math.log1p(float(order["target_words"]) / 1000.0),
        "novelty": float(order["novelty"]),
        "log_budget": math.log1p(float(order["budget_units"])),
        "family_fail_rate": state.family_fail_rate(str(order["family"])),
        "repo_scale_success": state.repo_scale_success(str(order["repo"]), scale),
        "family_fail_rate_dev": state.family_fail_dev(str(order["family"])),
        "cell_success_dev": state.cell_success_dev(str(order["repo"]), scale),
        "global_pass_rate": state.global_pass_rate,
        "repo_hist_n": math.log1p(state.repo_history(str(order["repo"]))),
        "crit_band": -((n_crit - 6.0) ** 2) / 12.0,
        "tier_ord": float(tier_order(order["tier"])),
        "scale_ord": float(SCALE_ORDER.index(scale)),
    }


def design_names(repos: Sequence[str]) -> list[str]:
    names = list(NUMERIC_FEATURES)
    for cap in CATEGORICAL_FEATURES:
        if cap == "repo":
            names += [f"repo={r}" for r in repos]
    assert_no_post_hoc_columns(names)
    return names


def build_design(dataset: Any) -> dict[str, Any]:
    """Featurise every candidate of every round in chronological order.

    Returns a dict of flat arrays over *candidates* (``rounds x slate`` rows):

    ``X``            design matrix (n, d)
    ``names``        column names
    ``y_cf``         counterfactual outcome of every candidate (simulator truth)
    ``y_logged``     outcome observed for the dispatched candidate (-1 elsewhere)
    ``logged``       mask of dispatched rows
    ``propensity``   pi0(a0 | slate) for dispatched rows (1 elsewhere)
    ``priority``     static dispatcher score of every candidate
    ``cost``         simulated wall-clock cost (core-seconds / 60)
    ``value``        declared capability value of every candidate
    ``round_id``     round index of every row
    ``p_true``       simulator ground-truth probability (reporting ONLY -- never a
                     column of ``X``; used to measure the ceiling gap)
    ``tier_ord``     declared tier ordinate (for reporting only)
    """
    state = AggregateState()
    rows: list[dict[str, float]] = []
    names: list[str] = []
    y_cf: list[float] = []
    y_logged: list[float] = []
    logged: list[float] = []
    prop: list[float] = []
    priority: list[float] = []
    cost: list[float] = []
    value: list[float] = []
    round_ids: list[int] = []
    p_true: list[float] = []
    tier_ords: list[float] = []

    repos: list[str] = []
    for rnd in dataset.rounds:
        for j, order in enumerate(rnd.candidates):
            if rnd.dispatch_ts is not None:
                assert_dispatch_time(order, rnd.dispatch_ts, used=DISPATCH_FIELDS)
            num = numeric_row(order, state)
            if not names:
                names = design_names(repos)
            rows.append(dict(num))
            y_cf.append(float(rnd.outcomes[j]))
            is_logged = j == rnd.logged_index
            y_logged.append(float(rnd.outcomes[j]) if is_logged else -1.0)
            logged.append(1.0 if is_logged else 0.0)
            prop.append(float(rnd.propensity[rnd.logged_index]) if is_logged else 1.0)
            priority.append(float(rnd.priority[j]))
            cost.append(float(rnd.cost[j]))
            value.append(float(order["value_units"]))
            round_ids.append(int(rnd.round_id))
            p_true.append(float(rnd.p_true[j]))
            tier_ords.append(float(tier_order(order["tier"])))
        chosen = rnd.candidates[rnd.logged_index]
        state.update(
            family=str(chosen["family"]),
            repo=str(chosen["repo"]),
            scale=scale_bucket(float(chosen["budget_units"])),
            passed=bool(rnd.outcomes[rnd.logged_index]),
        )

    if not names:
        names = design_names(repos)
    X = np.asarray([[r[n] for n in names] for r in rows], dtype=float)
    return {
        "X": X,
        "names": names,
        "y_cf": np.asarray(y_cf, dtype=float),
        "y_logged": np.asarray(y_logged, dtype=float),
        "logged": np.asarray(logged, dtype=bool),
        "propensity": np.asarray(prop, dtype=float),
        "priority": np.asarray(priority, dtype=float),
        "cost": np.asarray(cost, dtype=float),
        "value": np.asarray(value, dtype=float),
        "round_id": np.asarray(round_ids, dtype=int),
        "p_true": np.asarray(p_true, dtype=float),
        "tier_ord": np.asarray(tier_ords, dtype=float),
        "slate": int(dataset.slate_size),
        "n_rounds": len(dataset.rounds),
    }