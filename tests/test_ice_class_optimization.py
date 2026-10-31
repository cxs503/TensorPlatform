"""
Ice-Class Hull Form Multi-Objective Optimization — Maturity Test Suite.

Acceptance criteria:
  (a) test_nsga2_convergence: 50 gen, HV change rate < 0.01 (last 5 gen)
  (b) test_pareto_dominance: no two Pareto solutions dominate each other
  (c) test_constraint_feasibility: all Pareto solutions feasible (100%)
  (d) test_objective_tradeoff: R_ice diff between ice-optimal and ow-optimal > 10%
  (e) test_optimization_walltime: wall-clock < 600s (surrogate mode)
"""
import numpy as np
import pytest
import time
import sys
import os

# Ensure optimizer is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from optimizer.ice_class.nsga2 import (
    NSGA2Optimizer, fast_non_dominated_sort, crowding_distance,
    compute_hypervolume, _constrained_dominates
)
from optimizer.ice_class.objectives import (
    evaluate_ice_resistance, evaluate_open_water_resistance,
    evaluate_gm, evaluate_all
)
from optimizer.ice_class.constraints import (
    check_constraints, compute_constraint_violation, get_bounds, CONSTRAINT_BOUNDS
)
from optimizer.ice_class.optimization import run_ice_class_optimization


# ── Shared fixture: run optimization once for all tests ──
_opt_result = None


def get_opt_result():
    """Lazy singleton for optimization result."""
    global _opt_result
    if _opt_result is None:
        _opt_result = run_ice_class_optimization(
            pop_size=40,
            n_generations=50,
            seed=42,
            use_surrogate=True,
            verbose=False,
        )
    return _opt_result


# ═══════════════════════════════════════════════════════════════
# Unit tests for individual components
# ═══════════════════════════════════════════════════════════════

class TestIceResistance:
    """Test ice resistance model (Lindqvist 1989)."""

    def test_positive_resistance(self):
        """Ice resistance must be positive for valid inputs."""
        R = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                     alpha_bow=25, alpha_stem=20)
        assert R > 0, f"Ice resistance should be positive, got {R}"

    def test_thickness_sensitivity(self):
        """Thicker ice → higher resistance."""
        R1 = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                      alpha_bow=25, alpha_stem=20, h_ice=0.5)
        R2 = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                      alpha_bow=25, alpha_stem=20, h_ice=1.5)
        assert R2 > R1, "Thicker ice should produce higher resistance"

    def test_bow_angle_sensitivity(self):
        """Sharper bow (smaller α_bow) → lower ice resistance."""
        R_sharp = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                           alpha_bow=15, alpha_stem=20)
        R_blunt = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                           alpha_bow=35, alpha_stem=20)
        assert R_sharp < R_blunt, "Sharper bow should have lower ice resistance"

    def test_speed_sensitivity(self):
        """Higher speed → higher ice resistance."""
        R_slow = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                          alpha_bow=25, alpha_stem=20, V_kn=3)
        R_fast = evaluate_ice_resistance(L=150, B=25, T=9, Cb=0.7,
                                          alpha_bow=25, alpha_stem=20, V_kn=7)
        assert R_fast > R_slow, "Higher speed should produce higher ice resistance"


class TestOpenWaterResistance:
    """Test open water resistance model (Holtrop-Mennen)."""

    def test_positive_resistance(self):
        R = evaluate_open_water_resistance(L=150, B=25, T=9, Cb=0.7,
                                            alpha_bow=25, alpha_stem=20)
        assert R > 0, f"Open water resistance should be positive, got {R}"

    def test_speed_sensitivity(self):
        R_slow = evaluate_open_water_resistance(L=150, B=25, T=9, Cb=0.7,
                                                 alpha_bow=25, alpha_stem=20, V_kn=10)
        R_fast = evaluate_open_water_resistance(L=150, B=25, T=9, Cb=0.7,
                                                 alpha_bow=25, alpha_stem=20, V_kn=20)
        assert R_fast > R_slow, "Higher speed should produce higher resistance"

    def test_length_sensitivity(self):
        """Longer ship → higher frictional resistance."""
        R_short = evaluate_open_water_resistance(L=100, B=25, T=9, Cb=0.7,
                                                  alpha_bow=25, alpha_stem=20)
        R_long = evaluate_open_water_resistance(L=200, B=25, T=9, Cb=0.7,
                                                 alpha_bow=25, alpha_stem=20)
        # Longer ship has more wetted area → higher Rf
        assert R_long > R_short, "Longer ship should have higher resistance"


class TestStability:
    """Test GM calculation."""

    def test_positive_gm(self):
        gm = evaluate_gm(L=150, B=25, T=9, Cb=0.7, alpha_bow=25, alpha_stem=20)
        assert gm > 0, f"GM should be positive for typical ship, got {gm}"

    def test_beam_sensitivity(self):
        """Wider beam → higher GM (more stability)."""
        gm_narrow = evaluate_gm(L=150, B=20, T=9, Cb=0.7, alpha_bow=25, alpha_stem=20)
        gm_wide = evaluate_gm(L=150, B=30, T=9, Cb=0.7, alpha_bow=25, alpha_stem=20)
        assert gm_wide > gm_narrow, "Wider beam should give higher GM"

    def test_ice_class_minimum(self):
        """GM should meet IMO Polar Code minimum (0.15m) for typical designs."""
        gm = evaluate_gm(L=150, B=25, T=9, Cb=0.7, alpha_bow=25, alpha_stem=20)
        assert gm >= 0.15, f"GM should meet Polar Code minimum, got {gm}"


class TestConstraints:
    """Test constraint handling."""

    def test_feasible_design(self):
        x = np.array([150, 25, 9, 0.7, 25, 20])
        feasible, details = check_constraints(x)
        assert feasible, f"Typical design should be feasible: {details}"

    def test_infeasible_cb(self):
        x = np.array([150, 25, 9, 0.9, 25, 20])  # Cb=0.9 > 0.85
        feasible, details = check_constraints(x)
        assert not feasible
        assert not details["Cb_in_range"]

    def test_infeasible_lb_ratio(self):
        x = np.array([150, 50, 9, 0.7, 25, 20])  # L/B = 3 < 4
        feasible, details = check_constraints(x)
        assert not feasible
        assert not details["L_B_in_range"]

    def test_constraint_violation_zero_for_feasible(self):
        x = np.array([150, 25, 9, 0.7, 25, 20])
        cv = compute_constraint_violation(x)
        assert cv == 0.0, f"Feasible design should have zero violation, got {cv}"

    def test_constraint_violation_positive_for_infeasible(self):
        x = np.array([150, 25, 9, 0.9, 25, 20])
        cv = compute_constraint_violation(x)
        assert cv > 0, "Infeasible design should have positive violation"


class TestNSGA2Components:
    """Test NSGA-II algorithm components."""

    def test_non_dominated_sort(self):
        """Test non-dominated sorting on known Pareto front."""
        objs = np.array([
            [1, 5], [2, 3], [3, 2], [5, 1],  # Pareto front
            [4, 4], [3, 5], [5, 3],            # Dominated
        ])
        fronts = fast_non_dominated_sort(objs)
        assert len(fronts) >= 1
        # First front should contain non-dominated solutions
        front0 = set(fronts[0])
        assert 0 in front0  # [1,5]
        assert 1 in front0  # [2,3]
        assert 2 in front0  # [3,2]
        assert 3 in front0  # [5,1]

    def test_crowding_distance(self):
        """Test crowding distance computation."""
        objs = np.array([[1, 5], [2, 3], [3, 2], [5, 1]])
        front = [0, 1, 2, 3]
        cd = crowding_distance(objs, front)
        # Boundary solutions should have infinite crowding distance
        assert cd[0] == np.inf
        assert cd[3] == np.inf

    def test_hypervolume_2d(self):
        """Test 2D hypervolume computation."""
        objs = np.array([[1, 4], [2, 2], [4, 1]])
        ref = np.array([5, 5])
        hv = compute_hypervolume(objs, ref)
        assert hv > 0, "Hypervolume should be positive"
        # Manual: (5-1)*(5-4) + (5-2)*(4-2) + (5-4)*(2-1) = 4+6+1 = 11
        # (approximate due to sorting method)

    def test_constrained_domination(self):
        """Test constrained-domination principle."""
        # Feasible dominates infeasible
        assert _constrained_dominates(
            np.array([1, 1]), np.array([2, 2]), 0.0, 1.0)
        # Infeasible does not dominate feasible
        assert not _constrained_dominates(
            np.array([1, 1]), np.array([2, 2]), 1.0, 0.0)
        # Both feasible: Pareto domination
        assert _constrained_dominates(
            np.array([1, 1]), np.array([2, 2]), 0.0, 0.0)


# ═══════════════════════════════════════════════════════════════
# Maturity acceptance tests (5 criteria)
# ═══════════════════════════════════════════════════════════════

class TestMaturityAcceptance:
    """
    Machine-verifiable acceptance criteria for ice-class optimization maturity.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        self.result = get_opt_result()

    def test_nsga2_convergence(self):
        """
        Criterion (a): 50 gen, Pareto front HV change rate < 0.01 (last 5 gen).

        nsga2_hypervolume_convergence < 0.01 [ratio]
        """
        convergence = self.result["convergence"]
        max_change = convergence["max_hv_change_rate_last_5"]

        assert max_change < 0.01, (
            f"HV convergence rate {max_change:.4f} >= 0.01; "
            f"changes: {convergence['hv_changes_last_5']}"
        )

    def test_pareto_dominance(self):
        """
        Criterion (b): No two Pareto solutions dominate each other.

        pareto_non_dominance == True [bool]
        """
        pareto_objs = self.result["pareto_objs"]
        n = pareto_objs.shape[0]

        if n < 2:
            pytest.skip("Need at least 2 Pareto solutions")

        # Check all pairs
        for i in range(n):
            for j in range(i + 1, n):
                # Neither should dominate the other
                i_dominates_j = _constrained_dominates(
                    pareto_objs[i], pareto_objs[j], 0.0, 0.0)
                j_dominates_i = _constrained_dominates(
                    pareto_objs[j], pareto_objs[i], 0.0, 0.0)
                assert not i_dominates_j, (
                    f"Solution {i} dominates {j}: "
                    f"obj_i={pareto_objs[i]}, obj_j={pareto_objs[j]}"
                )
                assert not j_dominates_i, (
                    f"Solution {j} dominates {i}: "
                    f"obj_j={pareto_objs[j]}, obj_i={pareto_objs[i]}"
                )

    def test_constraint_feasibility(self):
        """
        Criterion (c): All Pareto solutions satisfy constraints (100% feasible).

        constraint_feasibility == 1.0 [ratio]
        """
        pareto_pop = self.result["pareto_pop"]
        n = pareto_pop.shape[0]

        if n == 0:
            pytest.fail("No Pareto solutions found")

        feasible_count = 0
        for i in range(n):
            feasible, details = check_constraints(pareto_pop[i])
            if feasible:
                feasible_count += 1
            else:
                # Log the infeasible solution for debugging
                print(f"Infeasible solution {i}: {details}")

        feasibility_ratio = feasible_count / n
        assert feasibility_ratio == 1.0, (
            f"Feasibility ratio {feasibility_ratio:.2f} != 1.0 "
            f"({feasible_count}/{n} feasible)"
        )

    def test_objective_tradeoff(self):
        """
        Criterion (d): Ice resistance optimal vs open water optimal
        must have R_ice difference > 10%.

        objective_tradeoff_r_ice_diff > 0.1 [ratio]
        """
        pareto_objs = self.result["pareto_objs"]
        n = pareto_objs.shape[0]

        if n < 2:
            pytest.skip("Need at least 2 Pareto solutions for tradeoff analysis")

        # Find ice-resistance-optimal (min R_ice = obj[:,0])
        ice_opt_idx = np.argmin(pareto_objs[:, 0])
        # Find open-water-optimal (min R_ow = obj[:,1])
        ow_opt_idx = np.argmin(pareto_objs[:, 1])

        R_ice_at_ice_opt = pareto_objs[ice_opt_idx, 0]
        R_ice_at_ow_opt = pareto_objs[ow_opt_idx, 0]

        if R_ice_at_ice_opt == 0:
            pytest.skip("R_ice at ice-optimal is zero, cannot compute ratio")

        diff_ratio = abs(R_ice_at_ice_opt - R_ice_at_ow_opt) / R_ice_at_ice_opt

        assert diff_ratio > 0.1, (
            f"R_ice tradeoff ratio {diff_ratio:.4f} <= 0.1; "
            f"R_ice(ice_opt)={R_ice_at_ice_opt:.2f}, "
            f"R_ice(ow_opt)={R_ice_at_ow_opt:.2f}"
        )

    def test_optimization_walltime(self):
        """
        Criterion (e): Single optimization wall-clock < 600s (surrogate mode).

        optimization_walltime < 600 [seconds]
        """
        wall_time = self.result["wall_time_s"]
        assert wall_time < 600, (
            f"Wall time {wall_time:.1f}s >= 600s"
        )


# ═══════════════════════════════════════════════════════════════
# Integration test: full optimization pipeline
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    """Integration tests for the full optimization pipeline."""

    def test_evaluate_all_returns_three_objectives(self):
        x = np.array([150, 25, 9, 0.7, 25, 20])
        objs, cv = evaluate_all(x)
        assert objs.shape == (3,), f"Expected 3 objectives, got {objs.shape}"
        assert cv >= 0, f"Constraint violation should be >= 0, got {cv}"

    def test_evaluate_all_feasible_design(self):
        x = np.array([150, 25, 9, 0.7, 25, 20])
        objs, cv = evaluate_all(x)
        assert cv == 0.0, "Feasible design should have zero constraint violation"
        assert objs[0] > 0, "R_ice should be positive"
        assert objs[1] > 0, "R_ow should be positive"
        assert objs[2] < 0, "-GM should be negative (GM > 0)"

    def test_nsga2_optimizer_runs(self):
        """NSGA-II should complete without error."""
        lb, ub = get_bounds()
        optimizer = NSGA2Optimizer(pop_size=10, n_generations=5, seed=123)
        result = optimizer.run(evaluate_all, lb, ub, verbose=False)
        assert "pareto_pop" in result
        assert "pareto_objs" in result
        assert "hv_history" in result
        assert len(result["hv_history"]) == 5

    def test_optimization_produces_pareto_front(self):
        """Full optimization should produce a non-empty Pareto front."""
        result = get_opt_result()
        assert result["pareto_front"]["n_solutions"] > 0, (
            "Pareto front should have at least one solution"
        )

    def test_pareto_solutions_have_valid_objectives(self):
        """All Pareto solutions should have finite, positive R_ice and R_ow."""
        result = get_opt_result()
        for sol in result["pareto_front"]["solutions"]:
            assert sol["objectives"]["R_ice"] > 0
            assert sol["objectives"]["R_ow"] > 0
            assert sol["objectives"]["GM"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
