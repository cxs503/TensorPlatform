"""
Multi-Fidelity Cascade Scorer
=============================

Low → Medium → High fidelity three-level filtering architecture for
design-space exploration.  Designed to address the ``wall_exhausted``
failure mode (plan 41 / IN25-T3) by eliminating ≥80 % of candidates
at low/medium fidelity stages, conserving expensive high-fidelity budget.

Architecture
------------
::

    Candidates (N_total)
        │
        ▼
    ┌─────────────────────────┐
    │  LOW FIDELITY           │  analytical / empirical formulas
    │  cost ≈ 1 unit/cand     │  target elimination ≥ 50 %
    │  physics gate: basic    │
    └───────────┬─────────────┘
                │ survivors
                ▼
    ┌─────────────────────────┐
    │  MEDIUM FIDELITY        │  reduced-order / surrogate model
    │  cost ≈ 10 unit/cand    │  target elimination ≥ 60 % of survivors
    │  physics gate: full     │
    └───────────┬─────────────┘
                │ survivors
                ▼
    ┌─────────────────────────┐
    │  HIGH FIDELITY          │  full-order CFD / experiment
    │  cost ≈ 1000 unit/cand  │  budget-adaptive sample size
    │  physics gate: strict   │
    └───────────┬─────────────┘
                │ winners
                ▼
            ranked results

Key features
------------
* **Physics consistency gate** — mass / momentum / energy conservation
  checks at every fidelity level with configurable tolerance.
* **Budget-adaptive sampling** — remaining budget drives dynamic pass-rate
  adjustment *and* evaluation capping so total cost never exceeds the
  envelope.  When budget is tight, candidates are pre-ranked using the
  previous stage's score and only the top-K affordable are evaluated.
* **Configurable** — all thresholds, costs, and gate tolerances are
  externalised via :class:`CascadeConfig`.

This module is domain-agnostic (zero domain semantics per platform rule).
Domain-specific scoring functions are injected via callables.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── enums ──────────────────────────────────────────────────────────────


class FidelityLevel(IntEnum):
    """Fidelity hierarchy (higher = more accurate, more expensive)."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2


class GateStatus(IntEnum):
    """Physics-consistency gate verdict."""

    PASS = 0
    WARN = 1
    FAIL = 2


# ── data structures ────────────────────────────────────────────────────


@dataclass
class Candidate:
    """A single design candidate flowing through the cascade."""

    id: str
    params: Dict[str, float]
    # scores populated stage-by-stage
    low_score: Optional[float] = None
    medium_score: Optional[float] = None
    high_score: Optional[float] = None
    # physics-consistency residuals per stage
    gate_residuals: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # bookkeeping
    eliminated_at: Optional[FidelityLevel] = None
    elimination_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def best_score(self) -> Optional[float]:
        """Return the highest-fidelity score available."""
        for lvl in (FidelityLevel.HIGH, FidelityLevel.MEDIUM, FidelityLevel.LOW):
            val = {FidelityLevel.LOW: self.low_score,
                   FidelityLevel.MEDIUM: self.medium_score,
                   FidelityLevel.HIGH: self.high_score}[lvl]
            if val is not None:
                return val
        return None


@dataclass
class StageResult:
    """Result of one cascade stage."""

    level: FidelityLevel
    n_input: int
    n_passed: int
    n_eliminated: int
    n_gate_failed: int
    n_budget_capped: int
    cost_spent: float
    elapsed_s: float
    survivors: List[Candidate] = field(default_factory=list)


@dataclass
class CascadeResult:
    """Full cascade run result."""

    stages: List[StageResult] = field(default_factory=list)
    total_cost: float = 0.0
    total_elapsed_s: float = 0.0
    budget: float = 0.0
    budget_remaining: float = 0.0
    n_candidates: int = 0
    n_final: int = 0
    elimination_rate_low_medium: float = 0.0
    config: Optional["CascadeConfig"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stages": [
                {
                    "level": s.level.name,
                    "n_input": s.n_input,
                    "n_passed": s.n_passed,
                    "n_eliminated": s.n_eliminated,
                    "n_gate_failed": s.n_gate_failed,
                    "n_budget_capped": s.n_budget_capped,
                    "cost_spent": round(s.cost_spent, 2),
                    "elapsed_s": round(s.elapsed_s, 4),
                }
                for s in self.stages
            ],
            "total_cost": round(self.total_cost, 2),
            "total_elapsed_s": round(self.total_elapsed_s, 4),
            "budget": self.budget,
            "budget_remaining": round(self.budget_remaining, 2),
            "n_candidates": self.n_candidates,
            "n_final": self.n_final,
            "elimination_rate_low_medium": round(
                self.elimination_rate_low_medium, 4
            ),
        }


# ── configuration ──────────────────────────────────────────────────────

# Type map for validation
_CONFIG_TYPES = {
    "low_cost": float,
    "medium_cost": float,
    "high_cost": float,
    "low_pass_rate": float,
    "medium_pass_rate": float,
    "mass_tol": float,
    "momentum_tol": float,
    "energy_tol": float,
    "budget_adaptive": bool,
    "min_survivors": int,
    "min_pass_rate_fraction": float,
    "total_budget": float,
}


@dataclass
class CascadeConfig:
    """All tunable parameters for the cascade scorer.

    Costs are in abstract *budget units*; the caller maps them to
    wall-clock seconds, CPU-hours, or dollars as needed.
    """

    # per-candidate cost at each fidelity level
    low_cost: float = 1.0
    medium_cost: float = 10.0
    high_cost: float = 1000.0

    # fraction of candidates to *pass* each stage (upper bound before
    # budget adaptation kicks in)
    low_pass_rate: float = 0.50
    medium_pass_rate: float = 0.40

    # physics-consistency gate tolerances (relative)
    mass_tol: float = 1e-3
    momentum_tol: float = 1e-2
    energy_tol: float = 1e-2

    # budget adaptation
    budget_adaptive: bool = True
    # minimum candidates to keep at each stage (floor)
    min_survivors: int = 1
    # when budget is tight, allow pass-rate to shrink to this fraction
    # of the nominal rate
    min_pass_rate_fraction: float = 0.10

    # total budget envelope (0 = unlimited)
    total_budget: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "low_cost": self.low_cost,
            "medium_cost": self.medium_cost,
            "high_cost": self.high_cost,
            "low_pass_rate": self.low_pass_rate,
            "medium_pass_rate": self.medium_pass_rate,
            "mass_tol": self.mass_tol,
            "momentum_tol": self.momentum_tol,
            "energy_tol": self.energy_tol,
            "budget_adaptive": self.budget_adaptive,
            "min_survivors": self.min_survivors,
            "min_pass_rate_fraction": self.min_pass_rate_fraction,
            "total_budget": self.total_budget,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CascadeConfig":
        """Build config from dict, validating types.

        Raises ``TypeError`` if a value cannot be coerced to the expected
        type.
        """
        known = set(_CONFIG_TYPES.keys())
        kwargs: Dict[str, Any] = {}
        for k, v in d.items():
            if k not in known:
                continue
            expected_type = _CONFIG_TYPES[k]
            try:
                if expected_type is bool:
                    # bool must be checked before int (bool is subclass)
                    if isinstance(v, str):
                        kwargs[k] = v.lower() in ("true", "1", "yes")
                    else:
                        kwargs[k] = bool(v)
                elif expected_type is int:
                    kwargs[k] = int(v)
                elif expected_type is float:
                    kwargs[k] = float(v)
                else:
                    kwargs[k] = expected_type(v)
            except (ValueError, TypeError) as exc:
                raise TypeError(
                    f"Config field '{k}' expects {expected_type.__name__}, "
                    f"got {type(v).__name__} ({v!r}): {exc}"
                ) from exc
        return cls(**kwargs)


# ── physics consistency gate ───────────────────────────────────────────


class PhysicsConsistencyGate:
    """Mass / momentum / energy conservation checker.

    The gate receives *residuals* (relative errors) for three conservation
    laws and returns a :class:`GateStatus`.

    Residuals are computed by the caller (domain-specific) and passed in;
    the gate itself is domain-agnostic — it only checks thresholds.
    """

    def __init__(self, config: CascadeConfig):
        self.cfg = config

    def check(
        self,
        mass_residual: float,
        momentum_residual: float,
        energy_residual: float,
        strictness: str = "full",
    ) -> Tuple[GateStatus, str]:
        """Check conservation residuals against tolerances.

        Parameters
        ----------
        mass_residual, momentum_residual, energy_residual
            Relative residuals (|computed - expected| / |expected|).
        strictness
            ``"basic"`` — only mass checked (low fidelity);
            ``"full"``  — mass + momentum (medium fidelity);
            ``"strict"`` — all three (high fidelity).

        Returns
        -------
        (status, reason)
        """
        checks: List[Tuple[str, float, float, str]] = []

        # mass is always checked
        checks.append(("mass", mass_residual, self.cfg.mass_tol, "basic"))

        if strictness in ("full", "strict"):
            checks.append(
                ("momentum", momentum_residual, self.cfg.momentum_tol, "full")
            )

        if strictness == "strict":
            checks.append(
                ("energy", energy_residual, self.cfg.energy_tol, "strict")
            )

        worst = GateStatus.PASS
        worst_reason = ""
        for name, residual, tol, level in checks:
            if residual > tol:
                worst = GateStatus.FAIL
                worst_reason = (
                    f"{name} conservation violated: "
                    f"residual={residual:.2e} > tol={tol:.2e}"
                )
                break
            elif residual > tol * 0.5 and worst == GateStatus.PASS:
                worst = GateStatus.WARN
                worst_reason = (
                    f"{name} residual {residual:.2e} approaching "
                    f"tolerance {tol:.2e}"
                )

        return worst, worst_reason

    def check_candidate(
        self, candidate: Candidate, strictness: str
    ) -> Tuple[GateStatus, str]:
        """Check a candidate using its stored gate residuals."""
        residuals = candidate.gate_residuals.get(strictness, {})
        return self.check(
            residuals.get("mass", 0.0),
            residuals.get("momentum", 0.0),
            residuals.get("energy", 0.0),
            strictness=strictness,
        )


# ── budget manager ─────────────────────────────────────────────────────


class BudgetManager:
    """Tracks remaining budget and adapts pass rates.

    When ``budget_adaptive`` is enabled, the manager computes the maximum
    number of candidates that can be afforded at the next (more expensive)
    stage and shrinks the pass rate if necessary.  It also caps the number
    of candidates *evaluated* at the current stage when budget is tight.
    """

    def __init__(self, config: CascadeConfig):
        self.cfg = config
        self.remaining = config.total_budget
        self.spent = 0.0

    def can_afford(self, n: int, cost_per: float) -> bool:
        if self.cfg.total_budget <= 0:
            return True  # unlimited
        return self.remaining >= n * cost_per

    def spend(self, n: int, cost_per: float) -> float:
        cost = n * cost_per
        self.spent += cost
        self.remaining -= cost
        return cost

    def affordable_count(self, cost_per: float) -> int:
        """How many candidates can be evaluated at ``cost_per``."""
        if self.cfg.total_budget <= 0 or cost_per <= 0:
            return 10**9  # effectively unlimited
        return max(0, int(self.remaining / cost_per))

    def adaptive_pass_rate(
        self,
        n_survivors: int,
        nominal_rate: float,
        next_cost: float,
    ) -> float:
        """Compute the pass rate that fits within remaining budget.

        If the nominal rate would produce more survivors than the budget
        can afford at the next stage, the rate is shrunk (down to
        ``min_pass_rate_fraction`` of nominal).
        """
        if not self.cfg.budget_adaptive or self.cfg.total_budget <= 0:
            return nominal_rate

        nominal_n = max(1, int(math.ceil(n_survivors * nominal_rate)))
        affordable_n = int(self.remaining / next_cost) if next_cost > 0 else nominal_n

        # never go below floor
        affordable_n = max(self.cfg.min_survivors, affordable_n)

        if affordable_n >= nominal_n:
            return nominal_rate

        # shrink rate
        shrunk_rate = affordable_n / n_survivors if n_survivors > 0 else 0.0
        min_rate = nominal_rate * self.cfg.min_pass_rate_fraction
        return max(min_rate, shrunk_rate)

    @property
    def remaining_budget(self) -> float:
        return self.remaining


# ── cascade scorer ─────────────────────────────────────────────────────

# Type aliases for scoring functions
ScoreFn = Callable[[Candidate], float]
ResidualFn = Callable[[Candidate, str], Dict[str, float]]


class CascadeScorer:
    """Orchestrates the low → medium → high fidelity cascade.

    Parameters
    ----------
    config
        :class:`CascadeConfig` with costs, tolerances, and budget.
    low_scorer
        Callable that takes a :class:`Candidate` and returns a float
        score (lower = better, consistent with minimisation).
    medium_scorer
        Same signature, medium-fidelity evaluation.
    high_scorer
        Same signature, high-fidelity evaluation.
    residual_fn
        Callable ``(candidate, strictness) -> {"mass": r, "momentum": r,
        "energy": r}``.  If ``None``, residuals default to zero (gate
        always passes) — useful for synthetic tests.
    """

    def __init__(
        self,
        config: CascadeConfig,
        low_scorer: ScoreFn,
        medium_scorer: ScoreFn,
        high_scorer: ScoreFn,
        residual_fn: Optional[ResidualFn] = None,
    ):
        self.cfg = config
        self.gate = PhysicsConsistencyGate(config)
        self.budget = BudgetManager(config)
        self.low_scorer = low_scorer
        self.medium_scorer = medium_scorer
        self.high_scorer = high_scorer
        self.residual_fn = residual_fn or _default_residuals

    # ── public API ──────────────────────────────────────────────────

    def run(self, candidates: List[Candidate]) -> CascadeResult:
        """Run the full cascade on a list of candidates."""
        t0 = time.perf_counter()
        result = CascadeResult(
            budget=self.cfg.total_budget,
            n_candidates=len(candidates),
            config=self.cfg,
        )

        # ── Stage 1: Low fidelity ───────────────────────────────────
        low_result = self._run_stage(
            candidates,
            FidelityLevel.LOW,
            self.low_scorer,
            strictness="basic",
            pass_rate=self.cfg.low_pass_rate,
            cost_per=self.cfg.low_cost,
            next_cost=self.cfg.medium_cost,
            prev_score_attr=None,  # no previous score for low stage
        )
        result.stages.append(low_result)
        survivors = low_result.survivors

        # ── Stage 2: Medium fidelity ────────────────────────────────
        med_result = self._run_stage(
            survivors,
            FidelityLevel.MEDIUM,
            self.medium_scorer,
            strictness="full",
            pass_rate=self.cfg.medium_pass_rate,
            cost_per=self.cfg.medium_cost,
            next_cost=self.cfg.high_cost,
            prev_score_attr="low_score",
        )
        result.stages.append(med_result)
        survivors = med_result.survivors

        # ── Stage 3: High fidelity ──────────────────────────────────
        # High fidelity: evaluate survivors (budget-capped), rank, no
        # pass-rate filtering — this is the final arbitration stage.
        high_result = self._run_high_stage(survivors)
        result.stages.append(high_result)

        # ── summary ────────────────────────────────────────────────
        result.total_cost = self.budget.spent
        result.total_elapsed_s = time.perf_counter() - t0
        result.budget_remaining = self.budget.remaining_budget
        result.n_final = len(high_result.survivors)

        n_eliminated_low_med = (
            low_result.n_eliminated + med_result.n_eliminated
        )
        n_input = len(candidates)
        result.elimination_rate_low_medium = (
            n_eliminated_low_med / n_input if n_input > 0 else 0.0
        )

        logger.info(
            "Cascade complete: %d → %d (elimination L+M=%.1f%%, cost=%.1f/%.1f)",
            n_input,
            result.n_final,
            result.elimination_rate_low_medium * 100,
            result.total_cost,
            self.cfg.total_budget,
        )
        return result

    # ── internals ──────────────────────────────────────────────────

    def _budget_cap_candidates(
        self,
        candidates: List[Candidate],
        cost_per: float,
        prev_score_attr: Optional[str],
    ) -> Tuple[List[Candidate], int]:
        """Cap the number of candidates based on remaining budget.

        If the budget cannot afford all candidates, pre-rank using the
        previous stage's score and select only the top-K affordable.

        Returns
        -------
        (capped_candidates, n_capped)
        """
        n_in = len(candidates)
        if n_in == 0:
            return [], 0

        affordable = self.budget.affordable_count(cost_per)
        if affordable >= n_in or self.cfg.total_budget <= 0:
            return candidates, 0

        # Budget cap: pre-rank using previous stage score
        if prev_score_attr:
            scored = [
                (getattr(c, prev_score_attr) if getattr(c, prev_score_attr) is not None else float("inf"), c)
                for c in candidates
            ]
            scored.sort(key=lambda x: x[0])
            capped = [c for _, c in scored[:affordable]]
        else:
            # No previous score — just take first K (low stage, all equal)
            capped = candidates[:affordable]

        n_capped = n_in - len(capped)
        # Mark capped candidates as eliminated
        capped_ids = {c.id for c in capped}
        for c in candidates:
            if c.id not in capped_ids:
                if c.eliminated_at is None:
                    c.eliminated_at = FidelityLevel.LOW  # generic
                    c.elimination_reason = "budget_capped"

        return capped, n_capped

    def _run_stage(
        self,
        candidates: List[Candidate],
        level: FidelityLevel,
        scorer: ScoreFn,
        strictness: str,
        pass_rate: float,
        cost_per: float,
        next_cost: float,
        prev_score_attr: Optional[str],
    ) -> StageResult:
        """Run one filtering stage."""
        t0 = time.perf_counter()
        n_input = len(candidates)
        n_gate_failed = 0

        # Budget cap: limit evaluations to what we can afford
        eval_candidates, n_budget_capped = self._budget_cap_candidates(
            candidates, cost_per, prev_score_attr
        )

        # score + gate
        scored: List[Tuple[float, Candidate]] = []
        for c in eval_candidates:
            # compute physics residuals
            residuals = self.residual_fn(c, strictness)
            c.gate_residuals[strictness] = residuals

            status, reason = self.gate.check(
                residuals.get("mass", 0.0),
                residuals.get("momentum", 0.0),
                residuals.get("energy", 0.0),
                strictness=strictness,
            )

            if status == GateStatus.FAIL:
                c.eliminated_at = level
                c.elimination_reason = reason
                n_gate_failed += 1
                continue

            # score
            score = scorer(c)
            if level == FidelityLevel.LOW:
                c.low_score = score
            elif level == FidelityLevel.MEDIUM:
                c.medium_score = score
            scored.append((score, c))

        # budget-adaptive pass rate
        n_scored = len(scored)
        effective_rate = self.budget.adaptive_pass_rate(
            n_scored, pass_rate, next_cost
        )
        n_pass = max(
            self.cfg.min_survivors,
            int(math.ceil(n_scored * effective_rate)),
        )
        n_pass = min(n_pass, n_scored)  # can't pass more than we have

        # sort by score (lower = better) and select top n_pass
        scored.sort(key=lambda x: x[0])
        survivors = [c for _, c in scored[:n_pass]]

        # mark eliminated
        n_eliminated = n_input - len(survivors)
        for _, c in scored[n_pass:]:
            c.eliminated_at = level
            c.elimination_reason = "ranked below threshold"

        # spend budget (only for evaluated candidates)
        cost = self.budget.spend(len(eval_candidates), cost_per)

        elapsed = time.perf_counter() - t0
        return StageResult(
            level=level,
            n_input=n_input,
            n_passed=len(survivors),
            n_eliminated=n_eliminated,
            n_gate_failed=n_gate_failed,
            n_budget_capped=n_budget_capped,
            cost_spent=cost,
            elapsed_s=elapsed,
            survivors=survivors,
        )

    def _run_high_stage(self, candidates: List[Candidate]) -> StageResult:
        """High-fidelity stage: evaluate survivors (budget-capped), rank."""
        t0 = time.perf_counter()
        n_input = len(candidates)
        n_gate_failed = 0

        # Budget cap for high-fidelity evaluations
        eval_candidates, n_budget_capped = self._budget_cap_candidates(
            candidates, self.cfg.high_cost, "medium_score"
        )

        scored: List[Tuple[float, Candidate]] = []
        for c in eval_candidates:
            residuals = self.residual_fn(c, "strict")
            c.gate_residuals["strict"] = residuals

            status, reason = self.gate.check(
                residuals.get("mass", 0.0),
                residuals.get("momentum", 0.0),
                residuals.get("energy", 0.0),
                strictness="strict",
            )

            if status == GateStatus.FAIL:
                c.eliminated_at = FidelityLevel.HIGH
                c.elimination_reason = reason
                n_gate_failed += 1
                continue

            score = self.high_scorer(c)
            c.high_score = score
            scored.append((score, c))

        scored.sort(key=lambda x: x[0])
        survivors = [c for _, c in scored]

        cost = self.budget.spend(len(eval_candidates), self.cfg.high_cost)
        elapsed = time.perf_counter() - t0
        return StageResult(
            level=FidelityLevel.HIGH,
            n_input=n_input,
            n_passed=len(survivors),
            n_eliminated=n_input - len(survivors),
            n_gate_failed=n_gate_failed,
            n_budget_capped=n_budget_capped,
            cost_spent=cost,
            elapsed_s=elapsed,
            survivors=survivors,
        )


# ── helpers ────────────────────────────────────────────────────────────


def _default_residuals(
    candidate: Candidate, strictness: str
) -> Dict[str, float]:
    """Default residual function: zero residuals (gate always passes).

    Production callers should inject a domain-specific residual function
    that computes actual conservation-law residuals from the candidate's
    parameters and the fidelity level's physics model.
    """
    return {"mass": 0.0, "momentum": 0.0, "energy": 0.0}


def make_synthetic_scorer(
    param_key: str, direction: str = "min"
) -> ScoreFn:
    """Create a simple synthetic scorer from a single parameter.

    Useful for testing and demonstration.  In production, replace with
    domain-specific analytical / surrogate / CFD evaluation functions.
    """
    sign = 1.0 if direction == "min" else -1.0

    def scorer(c: Candidate) -> float:
        return sign * c.params.get(param_key, 0.0)

    return scorer


def make_noise_scorer(
    base_scorer: ScoreFn, noise_std: float = 0.0, seed: int = 42
) -> ScoreFn:
    """Wrap a scorer with Gaussian noise (surrogate model uncertainty)."""
    import random

    rng = random.Random(seed)

    def scorer(c: Candidate) -> float:
        base = base_scorer(c)
        return base + rng.gauss(0, noise_std)

    return scorer
