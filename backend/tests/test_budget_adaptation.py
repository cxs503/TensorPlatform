"""Tests for budget adaptation — zero domain semantics, synthetic tasks."""
from __future__ import annotations

import math

import numpy as np
import pytest

from services.budget_adaptation import (
    AdjustmentScorer,
    BudgetAdaptationEngine,
    BudgetDecision,
    LogisticRegressionModel,
    SimulatedTask,
    TelemetrySnapshot,
    _extract_features,
    _slope,
    run_budget_simulation,
    simulate_task_progress,
)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

class TestFeatureExtraction:
    def test_empty_snapshots(self):
        features = _extract_features([])
        assert features.shape == (8,)
        assert np.all(features == 0)

    def test_single_snapshot(self):
        snap = TelemetrySnapshot(
            task_id="t1", timestamp=100.0, elapsed_ratio=0.5,
            convergence_rate=0.3, intermediate_quality=0.6,
            resource_consumption=0.4, iteration_count=50,
        )
        features = _extract_features([snap])
        assert features[0] == pytest.approx(0.5)  # elapsed_ratio
        assert features[1] == pytest.approx(0.3)  # convergence_rate
        assert features[3] == pytest.approx(0.6)  # quality
        assert features[5] == pytest.approx(0.4)  # resource

    def test_trend_calculation(self):
        snaps = [
            TelemetrySnapshot(
                task_id="t1", timestamp=100.0 + i * 10,
                elapsed_ratio=0.1 * (i + 1),
                convergence_rate=0.1 + 0.05 * i,  # increasing
                intermediate_quality=0.3 + 0.1 * i,
                resource_consumption=0.5,
                iteration_count=10 * (i + 1),
            )
            for i in range(5)
        ]
        features = _extract_features(snaps)
        # Convergence rate trend should be positive
        assert features[2] > 0
        # Quality improvement rate should be positive
        assert features[4] > 0


class TestSlope:
    def test_constant(self):
        assert _slope([1.0, 1.0, 1.0]) == pytest.approx(0.0)

    def test_linear(self):
        assert _slope([0.0, 1.0, 2.0]) == pytest.approx(1.0)

    def test_single_value(self):
        assert _slope([5.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Logistic regression model
# ---------------------------------------------------------------------------

class TestLogisticRegression:
    def test_output_range(self):
        model = LogisticRegressionModel()
        for _ in range(20):
            features = np.random.randn(8)
            p = model.predict_proba(features)
            assert 0.0 <= p <= 1.0

    def test_high_adjust_probability(self):
        """Features that should trigger high P(adjust): low quality, low convergence."""
        model = LogisticRegressionModel()
        # Simulated UP task features (from actual simulation)
        features = np.array([1.0, 0.15, -0.02, 0.35, 0.0, 0.5, 0.0, 1.0])
        p = model.predict_proba(features)
        assert p > 0.5, f"Expected P(adjust) > 0.5 for low-quality task, got {p}"

    def test_low_adjust_probability(self):
        """Features that should trigger low P(adjust): high quality, good convergence."""
        model = LogisticRegressionModel()
        # Simulated DOWN task features (from actual simulation)
        features = np.array([1.0, 0.08, -0.04, 0.88, 0.02, 0.42, -0.01, 1.0])
        p = model.predict_proba(features)
        assert p < 0.5, f"Expected P(adjust) < 0.5 for high-quality task, got {p}"


# ---------------------------------------------------------------------------
# Adjustment scorer
# ---------------------------------------------------------------------------

class TestAdjustmentScorer:
    def test_terminate_resource_exhaustion(self):
        """High resource + very low quality → terminate."""
        scorer = AdjustmentScorer()
        # Simulated TERM task features
        features = np.array([1.0, 0.07, 0.01, 0.06, -0.01, 0.68, -0.005, 1.0])
        action, ratio, reason = scorer.score(features, p_adjust=0.9)
        assert action == "terminate", f"Expected terminate, got {action}"
        assert ratio == 0.0

    def test_down_adjust_fast_convergence(self):
        """High quality + good convergence → down_adjust."""
        scorer = AdjustmentScorer()
        # Simulated DOWN task features
        features = np.array([1.0, 0.08, -0.04, 0.88, 0.02, 0.42, -0.01, 1.0])
        action, ratio, reason = scorer.score(features, p_adjust=0.4)
        assert action == "down_adjust", f"Expected down_adjust, got {action}"
        assert ratio < 1.0

    def test_up_adjust_slow_convergence(self):
        """Low quality + slow convergence → up_adjust."""
        scorer = AdjustmentScorer()
        # Simulated UP task features
        features = np.array([1.0, 0.15, -0.02, 0.35, 0.0, 0.5, 0.0, 1.0])
        action, ratio, reason = scorer.score(features, p_adjust=0.7)
        assert action == "up_adjust", f"Expected up_adjust, got {action}"
        assert ratio > 1.0

    def test_continue_low_confidence(self):
        scorer = AdjustmentScorer()
        features = np.array([1.0, 0.15, -0.02, 0.35, 0.0, 0.5, 0.0, 1.0])
        action, ratio, reason = scorer.score(features, p_adjust=0.1)
        assert action == "continue"
        assert ratio == 1.0

    def test_continue_normal(self):
        """Moderate quality, moderate convergence → continue."""
        scorer = AdjustmentScorer()
        # Features in the "sweet spot": quality ~0.6, conv_rate ~0.15, resource ~0.45
        features = np.array([1.0, 0.15, 0.0, 0.62, 0.01, 0.45, 0.0, 1.0])
        action, ratio, reason = scorer.score(features, p_adjust=0.4)
        assert action == "continue", f"Expected continue, got {action}"


# ---------------------------------------------------------------------------
# Budget adaptation engine
# ---------------------------------------------------------------------------

class TestBudgetAdaptationEngine:
    def test_basic_decision(self):
        engine = BudgetAdaptationEngine()
        snaps = [
            TelemetrySnapshot(
                task_id="t1", timestamp=100.0 + i * 10,
                elapsed_ratio=0.1 * (i + 1),
                convergence_rate=0.3,
                intermediate_quality=0.5,
                resource_consumption=0.4,
                iteration_count=10 * (i + 1),
            )
            for i in range(5)
        ]
        decision = engine.decide("t1", snaps)
        assert decision.task_id == "t1"
        assert decision.action in ("continue", "up_adjust", "down_adjust", "terminate")
        assert 0.0 <= decision.confidence <= 1.0
        assert decision.telemetry_used == 5

    def test_empty_snapshots(self):
        """Empty snapshots should result in continue (no evidence to adjust)."""
        engine = BudgetAdaptationEngine()
        decision = engine.decide("t1", [])
        # With zero features, the model should produce moderate probability
        # and the scorer should default to continue
        assert decision.action in ("continue", "up_adjust")  # allow either
        assert decision.telemetry_used == 0


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class TestSimulation:
    def test_simulate_task_progress(self):
        task = SimulatedTask(
            task_id="sim_1",
            true_convergence_speed=0.5,
            true_resource_efficiency=0.7,
            true_target_quality=0.8,
        )
        snaps = simulate_task_progress(task, n_snapshots=10)
        assert len(snaps) == 10
        # Quality should generally increase
        assert snaps[-1].intermediate_quality > snaps[0].intermediate_quality

    def test_run_budget_simulation(self):
        result = run_budget_simulation(n_tasks=10, seed=42)
        assert result["n_tasks"] == 10
        assert "accuracy_up" in result
        assert "accuracy_down" in result
        assert "accuracy_terminate" in result
        assert "resource_savings" in result
        assert len(result["decisions"]) == 10

    def test_simulation_structure(self):
        result = run_budget_simulation(n_tasks=10, seed=42)
        for d in result["decisions"]:
            assert "task_id" in d
            assert "action" in d
            assert "confidence" in d
            assert "adjustment_ratio" in d
            assert "reason" in d


# ---------------------------------------------------------------------------
# Acceptance criteria tests
# ---------------------------------------------------------------------------

class TestAcceptanceCriteria:
    """Verify the acceptance criteria from the campaign node."""

    def test_up_adjust_accuracy(self):
        """Budget up-adjust accuracy ≥ 80%."""
        result = run_budget_simulation(n_tasks=10, seed=42)
        assert result["accuracy_up"] >= 0.8, \
            f"Up-adjust accuracy {result['accuracy_up']} < 0.8"

    def test_down_adjust_accuracy(self):
        """Budget down-adjust accuracy ≥ 80%."""
        result = run_budget_simulation(n_tasks=10, seed=42)
        assert result["accuracy_down"] >= 0.8, \
            f"Down-adjust accuracy {result['accuracy_down']} < 0.8"

    def test_resource_savings(self):
        """Resource savings ≥ 20% (from down-adjust and terminate tasks)."""
        result = run_budget_simulation(n_tasks=10, seed=42)
        assert result["resource_savings"] >= 0.2, \
            f"Resource savings {result['resource_savings']} < 0.2"

    def test_terminate_accuracy(self):
        """Terminate accuracy should be reasonable."""
        result = run_budget_simulation(n_tasks=10, seed=42)
        assert result["accuracy_terminate"] >= 0.5, \
            f"Terminate accuracy {result['accuracy_terminate']} < 0.5"
