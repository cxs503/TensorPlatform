"""Budget dynamic adaptation — platform-level, zero domain semantics.

Uses runtime telemetry (convergence rate, intermediate quality, resource
consumption) to decide whether to UP-ADJUST, DOWN-ADJUST, or TERMINATE
a task's budget.  The decision model combines logistic regression (for
P(need_adjust)) with a gradient-boosting-style scoring for adjustment
magnitude.

All feature names, thresholds, and labels are generic.  Synthetic task
names are used in tests and simulations.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetrySnapshot:
    """A single telemetry reading from a running task."""
    task_id: str
    timestamp: float  # epoch seconds
    elapsed_ratio: float  # elapsed_budget / total_budget ∈ [0, 1]
    convergence_rate: float  # improvement per unit time (normalized)
    intermediate_quality: float  # current objective value (normalized 0..1)
    resource_consumption: float  # CPU/memory/IO composite (normalized 0..1)
    iteration_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetDecision:
    """Output of the budget adaptation model."""
    task_id: str
    action: str  # "up_adjust", "down_adjust", "terminate", "continue"
    confidence: float  # P(need_adjust) ∈ [0, 1]
    adjustment_ratio: float  # multiplier for remaining budget (e.g., 1.5 = +50%)
    reason: str
    telemetry_used: int  # number of snapshots consumed
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _extract_features(snapshots: List[TelemetrySnapshot]) -> np.ndarray:
    """Convert telemetry sequence to feature vector for decision model.

    Features (8-dim):
    0: elapsed_ratio (latest)
    1: convergence_rate (latest)
    2: convergence_rate_trend (slope of last 3 readings)
    3: intermediate_quality (latest)
    4: quality_improvement_rate (slope)
    5: resource_consumption (latest)
    6: resource_trend (slope)
    7: iteration_density (iterations / elapsed_ratio, normalized)
    """
    if not snapshots:
        return np.zeros(8)
    s = sorted(snapshots, key=lambda x: x.timestamp)
    latest = s[-1]
    features = np.zeros(8)
    features[0] = latest.elapsed_ratio
    features[1] = latest.convergence_rate
    features[3] = latest.intermediate_quality
    features[5] = latest.resource_consumption
    # Trends from last 3 (or fewer)
    if len(s) >= 2:
        recent = s[-min(3, len(s)):]
        # Convergence rate trend
        if len(recent) >= 2:
            cr_vals = [r.convergence_rate for r in recent]
            features[2] = _slope(cr_vals)
            # Quality improvement rate
            q_vals = [r.intermediate_quality for r in recent]
            features[4] = _slope(q_vals)
            # Resource trend
            r_vals = [r.resource_consumption for r in recent]
            features[6] = _slope(r_vals)
    # Iteration density (normalized by 100 to keep scale similar to other features)
    if latest.elapsed_ratio > 0.01:
        features[7] = latest.iteration_count / latest.elapsed_ratio / 100.0
    return features


def _slope(values: List[float]) -> float:
    """Simple linear slope (least squares) for a sequence."""
    n = len(values)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    y = np.array(values, dtype=float)
    x_mean = x.mean()
    y_mean = y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom < 1e-12:
        return 0.0
    return ((x - x_mean) * (y - y_mean)).sum() / denom


# ---------------------------------------------------------------------------
# Logistic regression model (hand-rolled, no sklearn dependency)
# ---------------------------------------------------------------------------

class LogisticRegressionModel:
    """Minimal logistic regression for P(need_adjust).

    Weights are calibrated from simulation data to discriminate between
    up-adjust, down-adjust, and terminate scenarios.
    """

    # Calibrated weights (8 features + bias)
    # Feature importance based on simulation analysis:
    # - quality (idx 3): strongest discriminator (low=up/term, high=down)
    # - convergence_rate (idx 1): moderate (low=up/term, high=down)
    # - resource (idx 5): moderate (high=term)
    # - conv_trend (idx 2): weak
    # - quality_trend (idx 4): weak
    # - resource_trend (idx 6): weak
    # - elapsed_ratio (idx 0): weak (always ~1 at decision time)
    # - iter_density (idx 7): weak (always ~1)
    WEIGHTS = np.array([
        0.1,   # elapsed_ratio: weak signal
        -2.0,  # convergence_rate: low → need adjustment
        -1.0,  # convergence_trend: negative → need adjustment
        -4.0,  # intermediate_quality: low → need adjustment (strongest)
        -1.5,  # quality_improvement: negative → need adjustment
        2.5,   # resource_consumption: high → need adjustment
        1.0,   # resource_trend: positive → need adjustment
        0.1,   # iteration_density: weak signal
    ])
    BIAS = 1.5  # Shift to get reasonable probabilities

    def predict_proba(self, features: np.ndarray) -> float:
        """Return P(need_adjust) ∈ [0, 1]."""
        z = np.dot(features, self.WEIGHTS) + self.BIAS
        return 1.0 / (1.0 + math.exp(-np.clip(z, -20, 20)))


# ---------------------------------------------------------------------------
# Gradient-boosting-style adjustment scorer
# ---------------------------------------------------------------------------

class AdjustmentScorer:
    """Determines adjustment magnitude and direction.

    Uses a rule-based ensemble (mimicking gradient boosting output)
    to compute:
    - action: up_adjust / down_adjust / terminate / continue
    - adjustment_ratio: multiplier for remaining budget
    """

    def score(self, features: np.ndarray, p_adjust: float) -> Tuple[str, float, str]:
        """Return (action, adjustment_ratio, reason).

        Feature indices:
        0: elapsed_ratio
        1: convergence_rate
        2: convergence_trend
        3: intermediate_quality
        4: quality_trend
        5: resource_consumption
        6: resource_trend
        7: iteration_density
        """
        elapsed = features[0]
        conv_rate = features[1]
        conv_trend = features[2]
        quality = features[3]
        quality_trend = features[4]
        resource = features[5]
        resource_trend = features[6]

        reasons = []
        action = "continue"
        ratio = 1.0

        # --- Terminate conditions ---
        if resource > 0.65 and quality < 0.15:
            action = "terminate"
            ratio = 0.0
            reasons.append("high resource with very low quality")
        elif conv_rate < 0.06 and quality < 0.1 and elapsed > 0.9:
            action = "terminate"
            ratio = 0.0
            reasons.append("stagnant convergence with poor quality")

        # --- Down-adjust conditions ---
        elif quality > 0.8 and conv_rate > 0.05:
            action = "down_adjust"
            ratio = 0.4  # cut remaining budget to 40%
            reasons.append("high quality with good convergence")
        elif quality > 0.75 and resource < 0.45:
            action = "down_adjust"
            ratio = 0.5
            reasons.append("high quality with low resource use")

        # --- Up-adjust conditions ---
        elif quality < 0.55 and conv_rate < 0.2:
            action = "up_adjust"
            ratio = 1.8  # +80% remaining budget
            reasons.append("low quality with slow convergence")
        elif quality < 0.6 and resource < 0.55:
            action = "up_adjust"
            ratio = 1.5
            reasons.append("moderate quality, room for improvement")

        # --- Confidence gate ---
        if p_adjust < 0.2 and action != "continue":
            action = "continue"
            ratio = 1.0
            reasons.append("low confidence in adjustment")

        if not reasons:
            reasons.append("within expected bounds")

        return action, ratio, "; ".join(reasons)


# ---------------------------------------------------------------------------
# Budget adaptation engine
# ---------------------------------------------------------------------------

class BudgetAdaptationEngine:
    """Main entry point for budget adaptation decisions."""

    def __init__(self,
                 lr_model: Optional[LogisticRegressionModel] = None,
                 scorer: Optional[AdjustmentScorer] = None):
        self.lr_model = lr_model or LogisticRegressionModel()
        self.scorer = scorer or AdjustmentScorer()

    def decide(self, task_id: str,
               snapshots: List[TelemetrySnapshot],
               remaining_budget: float = 1.0) -> BudgetDecision:
        """Make a budget decision based on telemetry history."""
        features = _extract_features(snapshots)
        p_adjust = self.lr_model.predict_proba(features)
        action, ratio, reason = self.scorer.score(features, p_adjust)
        return BudgetDecision(
            task_id=task_id,
            action=action,
            confidence=p_adjust,
            adjustment_ratio=ratio,
            reason=reason,
            telemetry_used=len(snapshots),
        )


# ---------------------------------------------------------------------------
# Simulation harness for validation
# ---------------------------------------------------------------------------

@dataclass
class SimulatedTask:
    """A synthetic task for budget adaptation testing."""
    task_id: str
    true_convergence_speed: float  # how fast it converges (higher = faster)
    true_resource_efficiency: float  # how efficiently it uses resources
    true_target_quality: float  # maximum achievable quality
    initial_budget: float = 100.0
    snapshots: List[TelemetrySnapshot] = field(default_factory=list)


def simulate_task_progress(task: SimulatedTask,
                           n_snapshots: int = 10,
                           noise: float = 0.05) -> List[TelemetrySnapshot]:
    """Generate realistic telemetry for a simulated task."""
    rng = np.random.RandomState(hash(task.task_id) % 2**31)
    snapshots = []
    budget = task.initial_budget
    for i in range(n_snapshots):
        elapsed_ratio = (i + 1) / n_snapshots
        elapsed = elapsed_ratio * budget
        # Quality grows logistically toward target
        quality = task.true_target_quality * (1 - math.exp(-task.true_convergence_speed * elapsed_ratio * 5))
        quality = min(quality, task.true_target_quality)
        quality += rng.normal(0, noise)
        quality = float(np.clip(quality, 0, 1))
        # Convergence rate (derivative of quality)
        conv_rate = task.true_convergence_speed * task.true_target_quality * \
                    math.exp(-task.true_convergence_speed * elapsed_ratio * 5) * 5
        conv_rate += rng.normal(0, noise * 0.5)
        conv_rate = max(0, conv_rate)
        # Resource consumption (inverse of efficiency)
        resource = (1 - task.true_resource_efficiency) * 0.5 + 0.3
        resource += rng.normal(0, noise * 0.3)
        resource = float(np.clip(resource, 0, 1))
        snap = TelemetrySnapshot(
            task_id=task.task_id,
            timestamp=1000.0 + i * 10.0,
            elapsed_ratio=elapsed_ratio,
            convergence_rate=float(conv_rate),
            intermediate_quality=quality,
            resource_consumption=resource,
            iteration_count=int(elapsed_ratio * 100),
        )
        snapshots.append(snap)
    return snapshots


def run_budget_simulation(n_tasks: int = 10,
                          seed: int = 42) -> Dict[str, Any]:
    """Run a full budget adaptation simulation.

    Creates tasks with known characteristics and evaluates whether the
    adaptation engine makes correct decisions.

    Returns dict with:
    - decisions: list of BudgetDecision
    - accuracy_up: fraction of up-adjust tasks correctly identified
    - accuracy_down: fraction of down-adjust tasks correctly identified
    - accuracy_terminate: fraction of terminate tasks correctly identified
    - resource_savings: estimated resource savings vs static budget
    """
    rng = np.random.RandomState(seed)
    engine = BudgetAdaptationEngine()

    # Create tasks with known ground truth
    # 5 up-adjust, 3 down-adjust, 2 terminate
    tasks = []
    # Up-adjust tasks: slow convergence, low quality target
    for i in range(5):
        tasks.append(SimulatedTask(
            task_id=f"sim_up_{i}",
            true_convergence_speed=0.3 + rng.uniform(-0.1, 0.1),
            true_resource_efficiency=0.6 + rng.uniform(-0.1, 0.1),
            true_target_quality=0.5 + rng.uniform(-0.1, 0.1),
        ))
    # Down-adjust tasks: fast convergence, high quality target
    for i in range(3):
        tasks.append(SimulatedTask(
            task_id=f"sim_down_{i}",
            true_convergence_speed=0.8 + rng.uniform(-0.1, 0.1),
            true_resource_efficiency=0.8 + rng.uniform(-0.1, 0.1),
            true_target_quality=0.9 + rng.uniform(-0.05, 0.05),
        ))
    # Terminate tasks: very slow convergence, low quality, high resource
    for i in range(2):
        tasks.append(SimulatedTask(
            task_id=f"sim_term_{i}",
            true_convergence_speed=0.1 + rng.uniform(-0.05, 0.05),
            true_resource_efficiency=0.2 + rng.uniform(-0.1, 0.1),
            true_target_quality=0.3 + rng.uniform(-0.1, 0.1),
        ))

    decisions = []
    correct_up = 0
    correct_down = 0
    correct_term = 0
    total_up = 0
    total_down = 0
    total_term = 0
    static_budget_total = 0.0
    adaptive_budget_total = 0.0

    for task in tasks:
        snapshots = simulate_task_progress(task, n_snapshots=10)
        decision = engine.decide(task.task_id, snapshots)
        decisions.append(decision)

        static_budget_total += task.initial_budget
        adaptive_budget = task.initial_budget
        if decision.action == "up_adjust":
            adaptive_budget *= decision.adjustment_ratio
        elif decision.action == "down_adjust":
            adaptive_budget *= decision.adjustment_ratio
        elif decision.action == "terminate":
            adaptive_budget *= 0.5  # only used half
        adaptive_budget_total += adaptive_budget

        # Check correctness
        if task.task_id.startswith("sim_up_"):
            total_up += 1
            if decision.action == "up_adjust":
                correct_up += 1
        elif task.task_id.startswith("sim_down_"):
            total_down += 1
            if decision.action == "down_adjust":
                correct_down += 1
        elif task.task_id.startswith("sim_term_"):
            total_term += 1
            if decision.action == "terminate":
                correct_term += 1

    accuracy_up = correct_up / total_up if total_up > 0 else 0.0
    accuracy_down = correct_down / total_down if total_down > 0 else 0.0
    accuracy_term = correct_term / total_term if total_term > 0 else 0.0
    # Resource savings: budget saved by down-adjust and terminate tasks
    # (up-adjust tasks need extra budget to succeed, which is necessary cost)
    budget_saved = 0.0
    for task, decision in zip(tasks, decisions):
        if decision.action == "down_adjust":
            budget_saved += task.initial_budget * (1 - decision.adjustment_ratio)
        elif decision.action == "terminate":
            budget_saved += task.initial_budget * 0.5  # saved half
    resource_savings = budget_saved / static_budget_total if static_budget_total > 0 else 0.0

    return {
        "decisions": [
            {
                "task_id": d.task_id,
                "action": d.action,
                "confidence": d.confidence,
                "adjustment_ratio": d.adjustment_ratio,
                "reason": d.reason,
            }
            for d in decisions
        ],
        "accuracy_up": accuracy_up,
        "accuracy_down": accuracy_down,
        "accuracy_terminate": accuracy_term,
        "resource_savings": resource_savings,
        "static_budget_total": static_budget_total,
        "adaptive_budget_total": adaptive_budget_total,
        "n_tasks": len(tasks),
    }


__all__ = [
    "TelemetrySnapshot", "BudgetDecision",
    "LogisticRegressionModel", "AdjustmentScorer",
    "BudgetAdaptationEngine",
    "SimulatedTask", "simulate_task_progress", "run_budget_simulation",
]
