"""
Tests for the Multi-Fidelity Cascade Scorer.

Covers:
* Low-fidelity filtering (≥50% elimination)
* Medium-fidelity ranking (≥60% elimination of survivors)
* High-fidelity arbitration
* Physics consistency gate (mass/momentum/energy, basic/full/strict)
* Budget-adaptive sampling (pass-rate shrinks under budget pressure)
* Budget-capped evaluations (total cost never exceeds envelope)
* 80%+ elimination at low+medium stages (core requirement)
* API endpoints (info, config, run)
* Edge cases (empty input, single candidate, all-gate-fail)
* Budget exhaustion scenario (wall_exhausted mitigation)
* Cascade vs naive cost comparison
"""

from __future__ import annotations

import math
import os
import sys
from typing import Dict, List

import pytest

# Ensure backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.cascade_scorer import (
    BudgetManager,
    Candidate,
    CascadeConfig,
    CascadeResult,
    CascadeScorer,
    FidelityLevel,
    GateStatus,
    PhysicsConsistencyGate,
    make_noise_scorer,
    make_synthetic_scorer,
)


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def default_config():
    return CascadeConfig(
        low_cost=1.0,
        medium_cost=10.0,
        high_cost=1000.0,
        low_pass_rate=0.50,
        medium_pass_rate=0.40,
        total_budget=0.0,  # unlimited for basic tests
    )


@pytest.fixture
def budget_config():
    return CascadeConfig(
        low_cost=1.0,
        medium_cost=10.0,
        high_cost=1000.0,
        low_pass_rate=0.50,
        medium_pass_rate=0.40,
        total_budget=5000.0,
        budget_adaptive=True,
    )


def _make_candidates(n: int, param_key: str = "resistance") -> List[Candidate]:
    """Create n candidates with decreasing resistance (best=first)."""
    return [
        Candidate(
            id=f"c{i:03d}",
            params={param_key: float(i), "L": 100.0 + i, "B": 20.0},
        )
        for i in range(n)
    ]


def _make_candidates_with_gates(
    n: int,
    fail_indices: set = None,
    param_key: str = "resistance",
) -> List[Candidate]:
    """Create candidates, some with failing physics gates."""
    fail_indices = fail_indices or set()
    candidates = []
    for i in range(n):
        c = Candidate(
            id=f"c{i:03d}",
            params={param_key: float(i), "L": 100.0 + i, "B": 20.0},
        )
        if i in fail_indices:
            # large residual → gate FAIL
            c.gate_residuals["basic"] = {"mass": 0.5, "momentum": 0.0, "energy": 0.0}
            c.gate_residuals["full"] = {"mass": 0.5, "momentum": 0.5, "energy": 0.0}
            c.gate_residuals["strict"] = {"mass": 0.5, "momentum": 0.5, "energy": 0.5}
        else:
            c.gate_residuals["basic"] = {"mass": 1e-6, "momentum": 0.0, "energy": 0.0}
            c.gate_residuals["full"] = {"mass": 1e-6, "momentum": 1e-6, "energy": 0.0}
            c.gate_residuals["strict"] = {"mass": 1e-6, "momentum": 1e-6, "energy": 1e-6}
        candidates.append(c)
    return candidates


def _identity_residuals(c: Candidate, strictness: str) -> Dict[str, float]:
    """Residual function that returns pre-stored residuals."""
    return c.gate_residuals.get(strictness, {"mass": 0.0, "momentum": 0.0, "energy": 0.0})


# ── PhysicsConsistencyGate tests ───────────────────────────────────────


class TestPhysicsConsistencyGate:
    """Test the physics-consistency gate."""

    def test_pass_when_residuals_below_tolerance(self):
        cfg = CascadeConfig(mass_tol=1e-3, momentum_tol=1e-2, energy_tol=1e-2)
        gate = PhysicsConsistencyGate(cfg)
        status, _ = gate.check(1e-6, 1e-6, 1e-6, strictness="strict")
        assert status == GateStatus.PASS

    def test_fail_on_mass_violation(self):
        cfg = CascadeConfig(mass_tol=1e-3)
        gate = PhysicsConsistencyGate(cfg)
        status, reason = gate.check(0.5, 0.0, 0.0, strictness="basic")
        assert status == GateStatus.FAIL
        assert "mass" in reason

    def test_fail_on_momentum_violation_full(self):
        cfg = CascadeConfig(momentum_tol=1e-2)
        gate = PhysicsConsistencyGate(cfg)
        status, reason = gate.check(1e-6, 0.5, 0.0, strictness="full")
        assert status == GateStatus.FAIL
        assert "momentum" in reason

    def test_fail_on_energy_violation_strict(self):
        cfg = CascadeConfig(energy_tol=1e-2)
        gate = PhysicsConsistencyGate(cfg)
        status, reason = gate.check(1e-6, 1e-6, 0.5, strictness="strict")
        assert status == GateStatus.FAIL
        assert "energy" in reason

    def test_basic_does_not_check_momentum(self):
        """Basic strictness only checks mass."""
        cfg = CascadeConfig(mass_tol=1e-3, momentum_tol=1e-10)
        gate = PhysicsConsistencyGate(cfg)
        # huge momentum residual but basic strictness → should pass
        status, _ = gate.check(1e-6, 100.0, 100.0, strictness="basic")
        assert status == GateStatus.PASS

    def test_full_does_not_check_energy(self):
        """Full strictness checks mass + momentum but not energy."""
        cfg = CascadeConfig(mass_tol=1e-3, momentum_tol=1e-2, energy_tol=1e-10)
        gate = PhysicsConsistencyGate(cfg)
        status, _ = gate.check(1e-6, 1e-6, 100.0, strictness="full")
        assert status == GateStatus.PASS

    def test_warn_when_residual_approaches_tolerance(self):
        """WARN when residual > 50% of tolerance but below tolerance."""
        cfg = CascadeConfig(mass_tol=1e-3)
        gate = PhysicsConsistencyGate(cfg)
        status, reason = gate.check(6e-4, 0.0, 0.0, strictness="basic")
        assert status == GateStatus.WARN
        assert "mass" in reason


# ── BudgetManager tests ────────────────────────────────────────────────


class TestBudgetManager:
    """Test budget tracking and adaptive pass-rate."""

    def test_unlimited_budget(self):
        cfg = CascadeConfig(total_budget=0.0)
        bm = BudgetManager(cfg)
        assert bm.can_afford(10000, 1000.0)

    def test_spend_tracking(self):
        cfg = CascadeConfig(total_budget=1000.0, low_cost=1.0)
        bm = BudgetManager(cfg)
        bm.spend(100, 1.0)
        assert bm.spent == 100.0
        assert bm.remaining_budget == 900.0

    def test_adaptive_pass_rate_shrinks_under_pressure(self):
        """When budget is tight, pass rate should shrink."""
        cfg = CascadeConfig(
            total_budget=500.0,
            medium_cost=10.0,
            high_cost=1000.0,
            budget_adaptive=True,
            min_pass_rate_fraction=0.10,
        )
        bm = BudgetManager(cfg)
        # spend most of budget on low stage
        bm.spend(400, 1.0)
        # only 100 remaining, high_cost=1000 → can afford 0 high-fidelity
        # but min_survivors=1 floor applies
        rate = bm.adaptive_pass_rate(
            n_survivors=50,
            nominal_rate=0.40,
            next_cost=1000.0,
        )
        # should be shrunk significantly
        assert rate < 0.40
        assert rate >= 0.40 * 0.10  # not below floor

    def test_adaptive_pass_rate_unchanged_when_budget_ok(self):
        cfg = CascadeConfig(total_budget=100000.0, budget_adaptive=True)
        bm = BudgetManager(cfg)
        rate = bm.adaptive_pass_rate(
            n_survivors=50,
            nominal_rate=0.40,
            next_cost=1000.0,
        )
        assert rate == pytest.approx(0.40)

    def test_adaptive_disabled_returns_nominal(self):
        cfg = CascadeConfig(total_budget=100.0, budget_adaptive=False)
        bm = BudgetManager(cfg)
        rate = bm.adaptive_pass_rate(
            n_survivors=50,
            nominal_rate=0.40,
            next_cost=1000.0,
        )
        assert rate == pytest.approx(0.40)

    def test_affordable_count(self):
        cfg = CascadeConfig(total_budget=5000.0)
        bm = BudgetManager(cfg)
        bm.spend(1000, 1.0)
        assert bm.affordable_count(1000.0) == 4  # 4000/1000=4

    def test_affordable_count_unlimited(self):
        cfg = CascadeConfig(total_budget=0.0)
        bm = BudgetManager(cfg)
        assert bm.affordable_count(1000.0) > 100000


# ── CascadeScorer core tests ───────────────────────────────────────────


class TestCascadeScorer:
    """Test the full cascade pipeline."""

    def test_basic_cascade_100_candidates(self, default_config):
        """100 candidates → low (50%) → medium (40% of survivors) → high."""
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        assert result.n_candidates == 100
        # low: 100 → ~50
        assert result.stages[0].level == FidelityLevel.LOW
        assert result.stages[0].n_input == 100
        assert result.stages[0].n_passed == 50
        # medium: 50 → ~20
        assert result.stages[1].level == FidelityLevel.MEDIUM
        assert result.stages[1].n_input == 50
        assert result.stages[1].n_passed == 20
        # high: 20 → 20 (no filtering, just ranking)
        assert result.stages[2].level == FidelityLevel.HIGH
        assert result.stages[2].n_input == 20
        assert result.stages[2].n_passed == 20

    def test_elimination_rate_low_medium_above_80_percent(self, default_config):
        """Core requirement: ≥80% elimination at low+medium stages."""
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # 100 → 50 (low) → 20 (medium) = 80 eliminated = 80%
        assert result.elimination_rate_low_medium >= 0.80
        assert result.n_final == 20

    def test_gate_failure_eliminates_candidates(self, default_config):
        """Candidates with failing physics gates are eliminated."""
        candidates = _make_candidates_with_gates(
            100, fail_indices={0, 1, 2, 3, 4}
        )
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
            residual_fn=_identity_residuals,
        )
        result = cs.run(candidates)

        # 5 candidates fail gate at low stage
        assert result.stages[0].n_gate_failed == 5
        # 100 - 5 = 95 scored, 50% pass → ceil(95*0.5)=48 pass
        assert result.stages[0].n_passed == 48

    def test_best_candidate_wins(self, default_config):
        """The candidate with lowest resistance should be the top winner."""
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        winners = result.stages[-1].survivors
        assert len(winners) > 0
        # c000 has resistance=0.0 (lowest) → should be first
        assert winners[0].id == "c000"
        assert winners[0].high_score == 0.0

    def test_scores_populated_at_each_stage(self, default_config):
        """Each candidate that survives a stage gets a score at that level."""
        candidates = _make_candidates(10)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        for w in result.stages[-1].survivors:
            assert w.low_score is not None
            assert w.medium_score is not None
            assert w.high_score is not None

    def test_cost_tracking(self, default_config):
        """Total cost = sum of stage costs."""
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        expected = sum(s.cost_spent for s in result.stages)
        assert result.total_cost == pytest.approx(expected)
        # low: 100*1=100, medium: 50*10=500, high: 20*1000=20000
        assert result.stages[0].cost_spent == pytest.approx(100.0)
        assert result.stages[1].cost_spent == pytest.approx(500.0)
        assert result.stages[2].cost_spent == pytest.approx(20000.0)

    def test_budget_adaptive_limits_high_fidelity(self, budget_config):
        """With limited budget, high-fidelity stage should be capped."""
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=budget_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # budget=5000: low=100*1=100, medium=50*10=500 → remaining=4400
        # high_cost=1000 → can afford 4 high-fidelity evals
        # budget capping should limit high-fidelity evaluations
        assert result.total_cost <= budget_config.total_budget + 1e-6
        assert result.budget_remaining >= -1e-6

    def test_wall_exhausted_mitigation(self):
        """Simulate wall_exhausted scenario: tight budget, many candidates.

        The cascade should eliminate ≥80% at low+medium, keeping
        high-fidelity evaluations within budget.
        """
        cfg = CascadeConfig(
            low_cost=1.0,
            medium_cost=10.0,
            high_cost=1000.0,
            low_pass_rate=0.50,
            medium_pass_rate=0.40,
            total_budget=3000.0,
            budget_adaptive=True,
            min_survivors=1,
        )
        candidates = _make_candidates(200)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=cfg,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # 200 candidates, budget=3000
        # Budget capping ensures total cost ≤ budget
        assert result.total_cost <= cfg.total_budget + 1e-6
        # Elimination rate should still be high
        assert result.elimination_rate_low_medium >= 0.80
        # High-fidelity survivors should be small (budget-limited)
        assert result.n_final <= 5

    def test_empty_candidates(self, default_config):
        """Empty candidate list should produce empty result."""
        cs = CascadeScorer(
            config=default_config,
            low_scorer=make_synthetic_scorer("x"),
            medium_scorer=make_synthetic_scorer("x"),
            high_scorer=make_synthetic_scorer("x"),
        )
        result = cs.run([])
        assert result.n_candidates == 0
        assert result.n_final == 0
        assert result.elimination_rate_low_medium == 0.0

    def test_single_candidate(self, default_config):
        """Single candidate passes through all stages."""
        candidates = _make_candidates(1)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)
        assert result.n_final == 1
        assert result.stages[0].n_passed == 1

    def test_all_gate_fail(self, default_config):
        """All candidates fail physics gate → no survivors."""
        candidates = _make_candidates_with_gates(
            10, fail_indices=set(range(10))
        )
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
            residual_fn=_identity_residuals,
        )
        result = cs.run(candidates)
        assert result.stages[0].n_gate_failed == 10
        assert result.stages[0].n_passed == 0
        assert result.n_final == 0

    def test_noise_scorer_reproducible(self):
        """Noise scorer with same seed produces same results."""
        c = Candidate(id="test", params={"resistance": 5.0})
        base = make_synthetic_scorer("resistance", "min")
        s1 = make_noise_scorer(base, noise_std=1.0, seed=42)
        s2 = make_noise_scorer(base, noise_std=1.0, seed=42)
        # First call advances RNG, so compare first calls
        assert s1(c) == s2(c)

    def test_result_to_dict(self, default_config):
        """Result serialization works."""
        candidates = _make_candidates(20)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=default_config,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)
        d = result.to_dict()
        assert "stages" in d
        assert len(d["stages"]) == 3
        assert d["n_candidates"] == 20
        assert "elimination_rate_low_medium" in d
        assert "n_budget_capped" in d["stages"][0]

    def test_config_from_dict_roundtrip(self):
        """Config serialization roundtrip."""
        cfg = CascadeConfig(
            low_cost=2.0,
            medium_cost=20.0,
            high_cost=2000.0,
            low_pass_rate=0.3,
            medium_pass_rate=0.5,
            mass_tol=1e-4,
            total_budget=10000.0,
        )
        d = cfg.to_dict()
        cfg2 = CascadeConfig.from_dict(d)
        assert cfg2.low_cost == 2.0
        assert cfg2.medium_cost == 20.0
        assert cfg2.high_cost == 2000.0
        assert cfg2.low_pass_rate == 0.3
        assert cfg2.total_budget == 10000.0

    def test_config_from_dict_ignores_unknown_keys(self):
        """Unknown keys in dict are silently ignored."""
        cfg = CascadeConfig.from_dict({"low_cost": 5.0, "unknown_key": 999})
        assert cfg.low_cost == 5.0

    def test_config_from_dict_rejects_bad_types(self):
        """Bad type values raise TypeError."""
        with pytest.raises(TypeError):
            CascadeConfig.from_dict({"low_cost": "not_a_number"})

    def test_config_from_dict_coerces_numeric_strings(self):
        """Numeric strings are coerced to float."""
        cfg = CascadeConfig.from_dict({"low_cost": "5.0", "total_budget": "100"})
        assert cfg.low_cost == 5.0
        assert cfg.total_budget == 100.0


# ── API endpoint tests ─────────────────────────────────────────────────


class TestCascadeScorerAPI:
    """Test the FastAPI endpoints using TestClient."""

    @pytest.fixture
    def client(self):
        """Create a test client with just the cascade router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.cascade_scorer import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def test_info_endpoint(self, client):
        resp = client.get("/api/v1/cascade/scorer/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "multi-fidelity-cascade-scorer"
        assert len(data["stages"]) == 3

    def test_get_config_endpoint(self, client):
        resp = client.get("/api/v1/cascade/scorer/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert data["config"]["low_pass_rate"] == 0.50

    def test_validate_config_endpoint(self, client):
        resp = client.post(
            "/api/v1/cascade/scorer/config",
            json={"low_cost": 2.0, "medium_cost": 20.0, "total_budget": 5000.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["low_cost"] == 2.0

    def test_validate_config_rejects_bad_values(self, client):
        """Non-numeric values for numeric fields are rejected."""
        resp = client.post(
            "/api/v1/cascade/scorer/config",
            json={"low_cost": "not_a_number"},
        )
        assert resp.status_code == 422

    def test_run_cascade_endpoint(self, client):
        """Full cascade run via API."""
        candidates = []
        for i in range(100):
            candidates.append({
                "id": f"c{i:03d}",
                "params": {"resistance": float(i), "L": 100.0 + i},
                "gate_basic": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
                "gate_full": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
                "gate_strict": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
            })

        body = {
            "candidates": candidates,
            "low_scorer": {"param_key": "resistance", "direction": "min"},
            "medium_scorer": {"param_key": "resistance", "direction": "min"},
            "high_scorer": {"param_key": "resistance", "direction": "min"},
            "config": {
                "low_pass_rate": 0.50,
                "medium_pass_rate": 0.40,
                "total_budget": 0.0,
            },
        }
        resp = client.post("/api/v1/cascade/scorer/run", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["n_candidates"] == 100
        assert data["elimination_rate_low_medium"] >= 0.80
        assert len(data["stages"]) == 3
        assert len(data["winners"]) > 0
        assert data["winners"][0]["id"] == "c000"

    def test_run_cascade_with_budget(self, client):
        """Cascade run with budget constraint — cost never exceeds budget."""
        candidates = []
        for i in range(50):
            candidates.append({
                "id": f"c{i:03d}",
                "params": {"resistance": float(i)},
                "gate_basic": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
                "gate_full": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
                "gate_strict": {"mass": 0.0, "momentum": 0.0, "energy": 0.0},
            })

        body = {
            "candidates": candidates,
            "low_scorer": {"param_key": "resistance", "direction": "min"},
            "medium_scorer": {"param_key": "resistance", "direction": "min"},
            "high_scorer": {"param_key": "resistance", "direction": "min"},
            "config": {
                "total_budget": 2000.0,
                "budget_adaptive": True,
                "high_cost": 1000.0,
            },
        }
        resp = client.post("/api/v1/cascade/scorer/run", json=body)
        assert resp.status_code == 200
        data = resp.json()
        # Budget capping ensures total cost ≤ budget
        assert data["total_cost"] <= 2000.0 + 0.01
        assert data["elimination_rate_low_medium"] >= 0.80

    def test_run_cascade_empty_rejected(self, client):
        """Empty candidate list is rejected."""
        body = {
            "candidates": [],
            "low_scorer": {"param_key": "x", "direction": "min"},
            "medium_scorer": {"param_key": "x", "direction": "min"},
            "high_scorer": {"param_key": "x", "direction": "min"},
        }
        resp = client.post("/api/v1/cascade/scorer/run", json=body)
        assert resp.status_code == 422

    def test_run_cascade_with_gate_failures(self, client):
        """Candidates with failing gates are eliminated via API."""
        candidates = []
        for i in range(20):
            gate_ok = {"mass": 0.0, "momentum": 0.0, "energy": 0.0}
            gate_fail = {"mass": 0.5, "momentum": 0.5, "energy": 0.5}
            gates = gate_fail if i < 5 else gate_ok
            candidates.append({
                "id": f"c{i:03d}",
                "params": {"resistance": float(i)},
                "gate_basic": gates,
                "gate_full": gates,
                "gate_strict": gates,
            })

        body = {
            "candidates": candidates,
            "low_scorer": {"param_key": "resistance", "direction": "min"},
            "medium_scorer": {"param_key": "resistance", "direction": "min"},
            "high_scorer": {"param_key": "resistance", "direction": "min"},
        }
        resp = client.post("/api/v1/cascade/scorer/run", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stages"][0]["n_gate_failed"] == 5


# ── integration: wall_exhausted scenario ───────────────────────────────


class TestWallExhaustedScenario:
    """Integration test simulating the IN25-T3 wall_exhausted failure mode.

    Scenario: 500 candidates, very tight budget (would normally require
    500 * 1000 = 500,000 budget units for all-high-fidelity).
    The cascade should eliminate ≥80% at low+medium and keep total cost
    within the budget envelope.
    """

    def test_large_candidate_pool_tight_budget(self):
        n_candidates = 500
        cfg = CascadeConfig(
            low_cost=1.0,
            medium_cost=10.0,
            high_cost=1000.0,
            low_pass_rate=0.50,
            medium_pass_rate=0.40,
            total_budget=5000.0,
            budget_adaptive=True,
            min_survivors=1,
        )
        candidates = _make_candidates(n_candidates)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=cfg,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # Core requirement: ≥80% elimination at low+medium
        assert result.elimination_rate_low_medium >= 0.80

        # Budget never exceeded (budget capping)
        assert result.total_cost <= cfg.total_budget + 1e-6

        # High-fidelity evaluations are minimal (budget-limited)
        n_high_eval = int(result.stages[2].cost_spent / cfg.high_cost)
        max_affordable = int(cfg.total_budget / cfg.high_cost)
        assert n_high_eval <= max_affordable + 1  # +1 for rounding

        # Without cascade: 500 * 1000 = 500,000 cost
        # With cascade: ≤ 5,000 cost → ≥99% budget savings
        naive_cost = n_candidates * cfg.high_cost
        savings = 1.0 - result.total_cost / naive_cost
        assert savings >= 0.99  # ≥99% budget savings

    def test_cascade_vs_naive_cost_comparison(self):
        """Direct comparison: cascade cost vs naive all-high-fidelity cost.

        With unlimited budget and default pass rates (0.50, 0.40):
        - 200 → 100 (low) → 40 (medium) → 40 (high)
        - Cost = 200*1 + 100*10 + 40*1000 = 41,200
        - Naive = 200*1000 = 200,000
        - Savings = 79.4%
        - Elimination rate = 80% (200→40)
        """
        n = 200
        candidates = _make_candidates(n)

        # Naive: evaluate all at high fidelity
        naive_cost = n * 1000.0  # 200,000

        # Cascade with unlimited budget
        cfg = CascadeConfig(
            low_cost=1.0,
            medium_cost=10.0,
            high_cost=1000.0,
            low_pass_rate=0.50,
            medium_pass_rate=0.40,
            total_budget=0.0,
        )
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=cfg,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # Cascade cost: 200*1 + 100*10 + 40*1000 = 41,200
        # vs naive 200,000 → ~79.4% savings
        savings = 1.0 - result.total_cost / naive_cost
        assert savings >= 0.79  # ≥79% savings with unlimited budget
        assert result.elimination_rate_low_medium >= 0.80
        # Verify exact cost
        assert result.total_cost == pytest.approx(200 + 1000 + 40000)

    def test_budget_capping_prevents_overspend(self):
        """Budget capping ensures total cost never exceeds the envelope."""
        cfg = CascadeConfig(
            low_cost=1.0,
            medium_cost=10.0,
            high_cost=1000.0,
            low_pass_rate=0.50,
            medium_pass_rate=0.40,
            total_budget=1000.0,  # very tight
            budget_adaptive=True,
            min_survivors=1,
        )
        candidates = _make_candidates(100)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=cfg,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        # Total cost must not exceed budget
        assert result.total_cost <= cfg.total_budget + 1e-6
        # Some budget capping should have occurred
        total_capped = sum(s.n_budget_capped for s in result.stages)
        assert total_capped > 0

    def test_elimination_rate_with_tight_budget(self):
        """Even with tight budget, elimination rate at L+M ≥ 80%."""
        cfg = CascadeConfig(
            low_cost=1.0,
            medium_cost=10.0,
            high_cost=1000.0,
            low_pass_rate=0.50,
            medium_pass_rate=0.40,
            total_budget=2000.0,
            budget_adaptive=True,
        )
        candidates = _make_candidates(300)
        scorer = make_synthetic_scorer("resistance", "min")
        cs = CascadeScorer(
            config=cfg,
            low_scorer=scorer,
            medium_scorer=scorer,
            high_scorer=scorer,
        )
        result = cs.run(candidates)

        assert result.elimination_rate_low_medium >= 0.80
        assert result.total_cost <= cfg.total_budget + 1e-6
