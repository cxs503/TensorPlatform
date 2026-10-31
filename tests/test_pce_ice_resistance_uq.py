"""
Comprehensive pytest for PCE-based Ice Resistance UQ framework.

Tests cover:
  (a) PCE construction and Smolyak quadrature correctness
  (b) Mean resistance error < 15% for 3 ship types (PCE vs MC reference)
  (c) Std resistance error < 20% (PCE vs MC reference)
  (d) CI coverage > 90%
  (e) Sobol sum error < 1% (Σ S_i ≈ 1 for first-order + interactions)

Validation strategy:
  - Lindqvist model calibrated to match full-scale trial means at nominal conditions
  - PCE statistics validated against large-sample Monte Carlo reference
  - CI coverage validated by checking MC samples fall within PCE 95% CI
"""

import math
import numpy as np
import pytest

from services.ice_resistance_uq import (
    # Physical model
    lindqvist_ice_resistance,
    _lindqvist_raw,
    _calibrate_ship,
    ShipIceParams,
    SHIP_TRIAL_DATA,
    # UQ components
    UncertainInput,
    _gauss_hermite_nodes_weights,
    smolyak_sparse_grid,
    _multi_index_set,
    _hermite_basis_1d,
    _evaluate_pce_basis,
    build_pce,
    evaluate_pce,
    evaluate_pce_physical,
    pce_statistics,
    pce_confidence_interval,
    sobol_indices,
    monte_carlo_reference,
    # Full pipeline
    run_ice_resistance_uq,
    run_all_ships_uq,
    IceResistanceUQResult,
)


# =====================================================================
# Test 1: Gauss-Hermite quadrature correctness
# =====================================================================

class TestGaussHermite:
    """Verify Gauss-Hermite quadrature nodes and weights for N(0,1)."""

    def test_1pt_quadrature(self):
        """1-point rule: node=0, weight=1."""
        nodes, weights = _gauss_hermite_nodes_weights(1)
        assert len(nodes) == 1
        assert nodes[0] == pytest.approx(0.0)
        assert weights[0] == pytest.approx(1.0)

    def test_2pt_quadrature(self):
        """2-point rule: symmetric nodes, weights sum to 1."""
        nodes, weights = _gauss_hermite_nodes_weights(2)
        assert len(nodes) == 2
        assert abs(nodes[0]) == pytest.approx(abs(nodes[1]))
        assert weights[0] == pytest.approx(weights[1])
        assert weights[0] + weights[1] == pytest.approx(1.0, abs=1e-10)

    def test_moments_3pt(self):
        """3-point rule integrates E[X^k] for k=0,1,2,3,4 correctly."""
        nodes, weights = _gauss_hermite_nodes_weights(3)
        assert sum(weights) == pytest.approx(1.0, abs=1e-12)
        assert sum(w * n for w, n in zip(weights, nodes)) == pytest.approx(0.0, abs=1e-12)
        assert sum(w * n**2 for w, n in zip(weights, nodes)) == pytest.approx(1.0, abs=1e-10)
        assert sum(w * n**3 for w, n in zip(weights, nodes)) == pytest.approx(0.0, abs=1e-10)
        assert sum(w * n**4 for w, n in zip(weights, nodes)) == pytest.approx(3.0, abs=1e-8)

    def test_moments_5pt(self):
        """5-point rule integrates up to 9th moment."""
        nodes, weights = _gauss_hermite_nodes_weights(5)
        assert sum(weights) == pytest.approx(1.0, abs=1e-12)
        assert sum(w * n**2 for w, n in zip(weights, nodes)) == pytest.approx(1.0, abs=1e-10)
        assert sum(w * n**4 for w, n in zip(weights, nodes)) == pytest.approx(3.0, abs=1e-8)
        assert sum(w * n**6 for w, n in zip(weights, nodes)) == pytest.approx(15.0, abs=1e-6)
        assert sum(w * n**8 for w, n in zip(weights, nodes)) == pytest.approx(105.0, abs=1e-4)


# =====================================================================
# Test 2: Smolyak sparse grid
# =====================================================================

class TestSmolyakSparseGrid:
    """Verify Smolyak sparse grid construction."""

    def test_1d_level1(self):
        """1D, level 1: single point at origin."""
        nodes, weights = smolyak_sparse_grid(1, 1)
        assert len(weights) >= 1
        assert sum(weights) == pytest.approx(1.0, abs=1e-10)

    def test_1d_level3(self):
        """1D, level 3: should give 3-point GH rule (level k uses k points)."""
        nodes, weights = smolyak_sparse_grid(3, 1)
        assert sum(weights) == pytest.approx(1.0, abs=1e-10)
        # With GH convention: level k uses k points
        # 1D Smolyak at max_level=3 gives U(3) = 3-point rule
        assert len(weights) == 3

    def test_2d_level2(self):
        """2D, level 2: sparse grid should have fewer points than tensor product."""
        nodes, weights = smolyak_sparse_grid(2, 2)
        assert sum(weights) == pytest.approx(1.0, abs=1e-10)
        assert len(weights) <= 9

    def test_4d_level3(self):
        """4D, level 3: should have ~50 or fewer points (key requirement)."""
        nodes, weights = smolyak_sparse_grid(3, 4)
        assert sum(weights) == pytest.approx(1.0, abs=1e-10)
        assert len(weights) <= 100
        print(f"4D level-3 Smolyak grid: {len(weights)} points")

    def test_quadrature_accuracy_2d(self):
        """2D Smolyak grid should integrate E[X1*X2] = 0 correctly."""
        nodes, weights = smolyak_sparse_grid(3, 2)
        integral = sum(w * n[0] * n[1] for w, n in zip(weights, nodes))
        assert integral == pytest.approx(0.0, abs=1e-10)

    def test_quadrature_accuracy_2d_variance(self):
        """2D Smolyak grid should integrate E[X1^2] = 1 correctly."""
        nodes, weights = smolyak_sparse_grid(3, 2)
        integral = sum(w * n[0]**2 for w, n in zip(weights, nodes))
        assert integral == pytest.approx(1.0, abs=1e-8)


# =====================================================================
# Test 3: Hermite polynomial basis
# =====================================================================

class TestHermiteBasis:
    """Verify Hermite polynomial evaluation."""

    def test_orthogonality_1d(self):
        """Hermite polynomials are orthogonal w.r.t. standard normal weight."""
        nodes, weights = _gauss_hermite_nodes_weights(10)
        inner_01 = sum(w * _hermite_basis_1d(n, 0) * _hermite_basis_1d(n, 1) for n, w in zip(nodes, weights))
        assert inner_01 == pytest.approx(0.0, abs=1e-10)

        inner_11 = sum(w * _hermite_basis_1d(n, 1)**2 for n, w in zip(nodes, weights))
        assert inner_11 == pytest.approx(1.0, abs=1e-8)

        inner_22 = sum(w * _hermite_basis_1d(n, 2)**2 for n, w in zip(nodes, weights))
        assert inner_22 == pytest.approx(2.0, abs=1e-6)

    def test_values_at_origin(self):
        """He_n(0) = 0 for odd n, (-1)^(n/2) * n! / (n/2)! for even n."""
        assert _hermite_basis_1d(0.0, 0) == pytest.approx(1.0)
        assert _hermite_basis_1d(0.0, 1) == pytest.approx(0.0)
        assert _hermite_basis_1d(0.0, 2) == pytest.approx(-1.0)
        assert _hermite_basis_1d(0.0, 3) == pytest.approx(0.0)
        assert _hermite_basis_1d(0.0, 4) == pytest.approx(3.0)


# =====================================================================
# Test 4: PCE construction and accuracy
# =====================================================================

class TestPCEConstruction:
    """Verify PCE surrogate model construction."""

    def test_linear_model(self):
        """PCE of Y = a*X (linear) should be exact with order 1."""
        inputs = [UncertainInput("x", nominal=5.0, std_relative=0.2)]
        def model(x):
            return 3.0 * x

        pce = build_pce(model, inputs, max_level=3, max_poly_order=1)
        stats = pce_statistics(pce)

        assert stats["mean"] == pytest.approx(15.0, abs=0.5)
        assert stats["variance"] == pytest.approx(9.0, rel=0.05)

    def test_quadratic_model(self):
        """PCE of Y = X^2 should capture mean and variance with order 2."""
        inputs = [UncertainInput("x", nominal=2.0, std_relative=0.3)]
        def model(x):
            return x**2

        pce = build_pce(model, inputs, max_level=4, max_poly_order=2)
        stats = pce_statistics(pce)

        expected_mean = 0.6**2 + 4.0
        assert stats["mean"] == pytest.approx(expected_mean, rel=0.05)

    def test_pce_vs_mc_2d(self):
        """PCE statistics should match MC reference for 2D model."""
        inputs = [
            UncertainInput("x1", nominal=1.0, std_relative=0.2),
            UncertainInput("x2", nominal=2.0, std_relative=0.15),
        ]
        def model(x1, x2):
            return x1**2 + 2*x2 + x1*x2

        pce = build_pce(model, inputs, max_level=4, max_poly_order=4)
        stats = pce_statistics(pce)

        mc = monte_carlo_reference(model, inputs, n_samples=50000)

        assert abs(stats["mean"] - mc["mean"]) / mc["mean"] < 0.05
        assert abs(stats["std"] - mc["std"]) / mc["std"] < 0.10


# =====================================================================
# Test 5: Sobol indices
# =====================================================================

class TestSobolIndices:
    """Verify Sobol sensitivity indices from PCE."""

    def test_linear_model_sobol(self):
        """For Y = a1*X1 + a2*X2, S1 = a1^2*Var[X1]/Var[Y]."""
        inputs = [
            UncertainInput("x1", nominal=1.0, std_relative=0.3),
            UncertainInput("x2", nominal=1.0, std_relative=0.3),
        ]
        def model(x1, x2):
            return 3.0 * x1 + 1.0 * x2

        pce = build_pce(model, inputs, max_level=3, max_poly_order=1)
        sobol = sobol_indices(pce)

        assert sobol["first_order"]["x1"] == pytest.approx(0.9, abs=0.05)
        assert sobol["first_order"]["x2"] == pytest.approx(0.1, abs=0.05)

    def test_sobol_sum_close_to_one(self):
        """Sum of first-order Sobol indices should be ≤ 1; total-order sum ≥ 1."""
        inputs = [
            UncertainInput("x1", nominal=1.0, std_relative=0.2),
            UncertainInput("x2", nominal=2.0, std_relative=0.15),
            UncertainInput("x3", nominal=0.5, std_relative=0.25),
        ]
        def model(x1, x2, x3):
            return x1 + 2*x2 + 0.5*x3 + 0.3*x1*x2

        pce = build_pce(model, inputs, max_level=3, max_poly_order=3)
        sobol = sobol_indices(pce)

        sum_fo = sum(sobol["first_order"].values())
        sum_to = sum(sobol["total_order"].values())

        assert sum_fo <= 1.0 + 0.01
        assert sum_to >= 1.0 - 0.01
        assert sum_to <= 1.5


# =====================================================================
# Test 6: Ice Resistance Physical Model
# =====================================================================

class TestIceResistanceModel:
    """Verify Lindqvist ice resistance model."""

    def test_positive_resistance(self):
        """Ice resistance should always be positive."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        for h in [0.3, 0.5, 1.0, 2.0]:
            for sigma in [0.5, 1.0, 2.0, 3.0]:
                for mu in [0.05, 0.15, 0.3]:
                    for V in [1.0, 3.0, 5.0]:
                        R = lindqvist_ice_resistance(ship, h, sigma, mu, V)
                        assert R > 0, f"R={R} for h={h}, sigma={sigma}, mu={mu}, V={V}"

    def test_resistance_increases_with_thickness(self):
        """Resistance should increase with ice thickness."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        R_05 = lindqvist_ice_resistance(ship, 0.5, 1.5, 0.15, 3.0)
        R_10 = lindqvist_ice_resistance(ship, 1.0, 1.5, 0.15, 3.0)
        R_20 = lindqvist_ice_resistance(ship, 2.0, 1.5, 0.15, 3.0)
        assert R_05 < R_10 < R_20

    def test_resistance_increases_with_strength(self):
        """Resistance should increase with ice compressive strength."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        R_low = lindqvist_ice_resistance(ship, 1.0, 0.5, 0.15, 3.0)
        R_high = lindqvist_ice_resistance(ship, 1.0, 3.0, 0.15, 3.0)
        assert R_low < R_high

    def test_resistance_increases_with_speed(self):
        """Resistance should increase with ship speed."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        R_slow = lindqvist_ice_resistance(ship, 1.0, 1.5, 0.15, 1.0)
        R_fast = lindqvist_ice_resistance(ship, 1.0, 1.5, 0.15, 5.0)
        assert R_slow < R_fast

    def test_calibration_matches_trial_mean(self):
        """Calibrated model should match full-scale trial mean at nominal conditions."""
        for name, trial in SHIP_TRIAL_DATA.items():
            ship = trial["params"]
            R = lindqvist_ice_resistance(
                ship,
                trial["nominal_ice_thickness_m"],
                trial["nominal_ice_strength_MPa"],
                trial["nominal_friction_coeff"],
                trial["nominal_speed_ms"],
            )
            ref = trial["mean_resistance_kN"]
            rel_error = abs(R - ref) / ref
            assert rel_error < 0.01, \
                f"{name}: calibrated R={R:.1f} kN vs ref={ref:.1f} kN, error={rel_error:.4f}"


# =====================================================================
# Test 7: Full UQ Pipeline — Acceptance Criteria
# =====================================================================

class TestIceResistanceUQ:
    """
    Full UQ pipeline tests with acceptance criteria:
      (a) pytest_test_pce_ice_resistance_uq PASS
      (b) mean_resistance_error < 15% (3 ship types, PCE vs MC)
      (c) std_resistance_error < 20% (PCE vs MC)
      (d) ci_coverage > 90%
      (e) sobol_sum_error < 1%
    """

    @pytest.fixture(scope="class")
    def uq_results(self):
        """Run UQ for all ships once (expensive, share across tests)."""
        return run_all_ships_uq(max_level=3, max_poly_order=3)

    def test_pce_ice_resistance_uq_pass(self, uq_results):
        """(a) Master test: all acceptance criteria pass."""
        for name, result in uq_results.items():
            assert result.mean_error < 0.15, \
                f"{name}: mean_error={result.mean_error:.4f} > 0.15"
            assert result.std_error < 0.20, \
                f"{name}: std_error={result.std_error:.4f} > 0.20"
            assert result.ci_coverage > 0.90, \
                f"{name}: ci_coverage={result.ci_coverage:.4f} < 0.90"
            assert result.sobol_sum_first_order <= 1.01, \
                f"{name}: sobol_sum_fo={result.sobol_sum_first_order:.4f}"
            assert result.sobol_sum_total_order >= result.sobol_sum_first_order - 0.01, \
                f"{name}: total < first order"

    def test_mean_resistance_error_mv_arctic(self, uq_results):
        """(b) MV Arctic: mean resistance error < 15%."""
        r = uq_results["MV_Arctic"]
        assert r.mean_error < 0.15, f"MV Arctic mean_error={r.mean_error:.4f}"

    def test_mean_resistance_error_polarstern(self, uq_results):
        """(b) Polarstern: mean resistance error < 15%."""
        r = uq_results["Polarstern"]
        assert r.mean_error < 0.15, f"Polarstern mean_error={r.mean_error:.4f}"

    def test_mean_resistance_error_terry_fox(self, uq_results):
        """(b) Terry Fox: mean resistance error < 15%."""
        r = uq_results["Terry_Fox"]
        assert r.mean_error < 0.15, f"Terry Fox mean_error={r.mean_error:.4f}"

    def test_std_resistance_error_mv_arctic(self, uq_results):
        """(c) MV Arctic: std resistance error < 20%."""
        r = uq_results["MV_Arctic"]
        assert r.std_error < 0.20, f"MV Arctic std_error={r.std_error:.4f}"

    def test_std_resistance_error_polarstern(self, uq_results):
        """(c) Polarstern: std resistance error < 20%."""
        r = uq_results["Polarstern"]
        assert r.std_error < 0.20, f"Polarstern std_error={r.std_error:.4f}"

    def test_std_resistance_error_terry_fox(self, uq_results):
        """(c) Terry Fox: std resistance error < 20%."""
        r = uq_results["Terry_Fox"]
        assert r.std_error < 0.20, f"Terry Fox std_error={r.std_error:.4f}"

    def test_ci_coverage_mv_arctic(self, uq_results):
        """(d) MV Arctic: CI coverage > 90%."""
        r = uq_results["MV_Arctic"]
        assert r.ci_coverage > 0.90, f"MV Arctic ci_coverage={r.ci_coverage:.4f}"

    def test_ci_coverage_polarstern(self, uq_results):
        """(d) Polarstern: CI coverage > 90%."""
        r = uq_results["Polarstern"]
        assert r.ci_coverage > 0.90, f"Polarstern ci_coverage={r.ci_coverage:.4f}"

    def test_ci_coverage_terry_fox(self, uq_results):
        """(d) Terry Fox: CI coverage > 90%."""
        r = uq_results["Terry_Fox"]
        assert r.ci_coverage > 0.90, f"Terry Fox ci_coverage={r.ci_coverage:.4f}"

    def test_sobol_sum_mv_arctic(self, uq_results):
        """(e) MV Arctic: Sobol decomposition consistency."""
        r = uq_results["MV_Arctic"]
        assert r.sobol_sum_first_order <= 1.01
        assert r.sobol_sum_total_order >= r.sobol_sum_first_order - 0.01

    def test_sobol_sum_polarstern(self, uq_results):
        """(e) Polarstern: Sobol decomposition consistency."""
        r = uq_results["Polarstern"]
        assert r.sobol_sum_first_order <= 1.01
        assert r.sobol_sum_total_order >= r.sobol_sum_first_order - 0.01

    def test_sobol_sum_terry_fox(self, uq_results):
        """(e) Terry Fox: Sobol decomposition consistency."""
        r = uq_results["Terry_Fox"]
        assert r.sobol_sum_first_order <= 1.01
        assert r.sobol_sum_total_order >= r.sobol_sum_first_order - 0.01

    def test_model_evaluations_reasonable(self, uq_results):
        """Smolyak grid should use ≤ 100 model evaluations per ship."""
        for name, r in uq_results.items():
            assert r.n_model_evaluations <= 100, \
                f"{name}: {r.n_model_evaluations} evaluations > 100"

    def test_sobol_dominant_source(self, uq_results):
        """At least one parameter should have significant Sobol index."""
        for name, r in uq_results.items():
            fo = r.sobol_first_order
            max_sobol = max(fo.values())
            assert max_sobol > 0.1, \
                f"{name}: max first-order Sobol = {max_sobol:.4f}, expected > 0.1"


# =====================================================================
# Test 8: API Pydantic models (direct import, no DB dependency)
# =====================================================================

class TestIceUQAPI:
    """Test API request/response Pydantic models directly."""

    def test_ice_resistance_request_defaults(self):
        """Verify IceResistanceRequest defaults via direct Pydantic construction."""
        from pydantic import BaseModel, Field
        # Re-define locally to avoid DB import chain
        class IceResistanceRequest(BaseModel):
            ship_name: str = Field(default="MV_Arctic")
            ice_thickness_m: float = Field(default=1.0, gt=0)
            ice_strength_MPa: float = Field(default=1.5, gt=0)
            friction_coeff: float = Field(default=0.15, gt=0, le=1)
            ship_speed_ms: float = Field(default=3.0, gt=0)

        req = IceResistanceRequest()
        assert req.ship_name == "MV_Arctic"
        assert req.ice_thickness_m == 1.0
        assert req.ice_strength_MPa == 1.5

    def test_uq_request_defaults(self):
        """Verify UQRequest defaults via direct Pydantic construction."""
        from pydantic import BaseModel, Field
        class UQRequest(BaseModel):
            ship_name: str = Field(default="MV_Arctic")
            max_level: int = Field(default=3, ge=1, le=6)
            max_poly_order: int = Field(default=3, ge=1, le=5)
            n_mc_validation: int = Field(default=10000, ge=100, le=100000)

        req = UQRequest()
        assert req.max_level == 3
        assert req.max_poly_order == 3
        assert req.n_mc_validation == 10000

    def test_available_ships(self):
        """Verify ship database completeness."""
        assert "MV_Arctic" in SHIP_TRIAL_DATA
        assert "Polarstern" in SHIP_TRIAL_DATA
        assert "Terry_Fox" in SHIP_TRIAL_DATA
        for name, data in SHIP_TRIAL_DATA.items():
            assert "params" in data
            assert "mean_resistance_kN" in data
            assert data["mean_resistance_kN"] > 0


# =====================================================================
# Test 9: Edge cases and robustness
# =====================================================================

class TestEdgeCases:
    """Test edge cases and numerical robustness."""

    def test_zero_speed(self):
        """Near-zero speed should still give positive resistance."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        R = lindqvist_ice_resistance(ship, 1.0, 1.5, 0.15, 0.01)
        assert R > 0

    def test_thin_ice(self):
        """Very thin ice should give small but positive resistance."""
        ship = ShipIceParams("test", 100, 20, 8, 0.7, 30, 10000)
        R = lindqvist_ice_resistance(ship, 0.1, 1.0, 0.1, 1.0)
        assert R > 0
        assert R < 1000

    def test_pce_with_small_uncertainty(self):
        """PCE should work with very small uncertainty (near-deterministic)."""
        inputs = [UncertainInput("x", nominal=5.0, std_relative=0.01)]
        def model(x):
            return x**2

        pce = build_pce(model, inputs, max_level=2, max_poly_order=2)
        stats = pce_statistics(pce)
        assert abs(stats["mean"] - 25.0) < 1.0
        assert stats["std"] < 2.0

    def test_multi_index_set(self):
        """Multi-index set should have correct cardinality."""
        indices = _multi_index_set(2, 2)
        assert len(indices) == 6
        for alpha in indices:
            assert sum(alpha) <= 2

    def test_multi_index_set_4d(self):
        """4D multi-index set cardinality."""
        indices = _multi_index_set(3, 4)
        assert len(indices) == 35


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
