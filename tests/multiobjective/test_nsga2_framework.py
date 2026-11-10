"""
Comprehensive test suite for NSGA-II multi-objective optimization framework.

Verification surfaces:
1. Analytical test functions (ZDT1-3, DTLZ1-2)
2. Convergence metrics (GD, IGD)
3. Diversity metrics (Spread, Hypervolume)
4. Ship resistance-seakeeping dual-objective instance
"""
import pytest
import numpy as np
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from optimizer.multiobjective import (
    NSGA2, ZDT1, ZDT2, ZDT3, DTLZ1, DTLZ2,
    generational_distance, inverted_generational_distance,
    spread, hypervolume
)


class TestNSGA2Core:
    """Test NSGA-II core algorithms."""
    
    def test_non_dominated_sort_simple(self):
        """Test non-dominated sorting with known fronts."""
        from optimizer.multiobjective.nsga2 import fast_non_dominated_sort
        
        # 4 points: 3 on front 0 (non-dominated), 1 on front 1
        objs = np.array([
            [1.0, 4.0],  # Front 0
            [2.0, 3.0],  # Front 0
            [4.0, 2.5],  # Front 0 (non-dominated with above)
            [3.5, 3.5],  # Front 1 (dominated by [2.0, 3.0])
        ])
        
        fronts = fast_non_dominated_sort(objs)
        
        assert len(fronts) == 2
        assert len(fronts[0]) == 3  # First front has 3 non-dominated points
        assert len(fronts[1]) == 1  # Second front has 1 dominated point
    
    def test_crowding_distance(self):
        """Test crowding distance computation."""
        from optimizer.multiobjective.nsga2 import crowding_distance
        
        objs = np.array([
            [0.0, 1.0],
            [0.5, 0.5],
            [1.0, 0.0],
        ])
        
        front = [0, 1, 2]
        cd = crowding_distance(objs, front)
        
        assert cd[0] == np.inf
        assert cd[2] == np.inf
        assert cd[1] < np.inf
        assert cd[1] > 0
    
    def test_hypervolume_2d(self):
        """Test 2D hypervolume computation."""
        from optimizer.multiobjective.nsga2 import compute_hypervolume_2d
        
        # Single point
        pts = np.array([[0.5, 0.5]])
        ref = np.array([1.0, 1.0])
        hv = compute_hypervolume_2d(pts, ref)
        assert abs(hv - 0.25) < 1e-10  # (1-0.5) * (1-0.5) = 0.25
        
        # Two non-dominated points
        pts = np.array([[0.2, 0.8], [0.8, 0.2]])
        ref = np.array([1.0, 1.0])
        hv = compute_hypervolume_2d(pts, ref)
        # Expected: 0.8*0.2 + 0.2*0.6 = 0.16 + 0.12 = 0.28
        assert abs(hv - 0.28) < 1e-10


class TestAnalyticalFunctions:
    """Test analytical test functions."""
    
    def test_zdt1_pareto_set(self):
        """Test ZDT1 Pareto set (x2=...=xn=0)."""
        zdt1 = ZDT1(n_var=10)
        
        x = np.zeros(10)
        x[0] = 0.5
        obj = zdt1(x)
        
        expected_f2 = 1.0 - np.sqrt(0.5)
        assert abs(obj[1] - expected_f2) < 1e-10
    
    def test_zdt2_pareto_set(self):
        """Test ZDT2 Pareto set."""
        zdt2 = ZDT2(n_var=10)
        
        x = np.zeros(10)
        x[0] = 0.3
        obj = zdt2(x)
        
        expected_f2 = 1.0 - 0.3 ** 2
        assert abs(obj[1] - expected_f2) < 1e-10
    
    def test_zdt3_disconnected(self):
        """Test ZDT3 has disconnected Pareto front."""
        zdt3 = ZDT3(n_var=10)
        pf = zdt3.true_pareto_front(n_points=100)
        assert len(pf) > 50
    
    def test_dtlz1_linear_front(self):
        """Test DTLZ1 has linear Pareto front (sum = 0.5)."""
        dtlz1 = DTLZ1(n_var=7, n_obj=3)
        
        x = np.ones(7) * 0.5
        obj = dtlz1(x)
        
        assert abs(np.sum(obj) - 0.5) < 1e-6
    
    def test_dtlz2_spherical_front(self):
        """Test DTLZ2 has spherical Pareto front (sum of squares = 1)."""
        dtlz2 = DTLZ2(n_var=12, n_obj=3)
        
        x = np.ones(12) * 0.5
        obj = dtlz2(x)
        
        assert abs(np.sum(obj ** 2) - 1.0) < 1e-6


class TestConvergenceMetrics:
    """Test convergence metrics (GD, IGD)."""
    
    def test_gd_perfect_convergence(self):
        """GD should be 0 when approx == reference."""
        ref = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
        approx = ref.copy()
        
        gd = generational_distance(approx, ref)
        assert gd < 1e-10
    
    def test_gd_poor_convergence(self):
        """GD should be large when approx is far from reference."""
        ref = np.array([[0.0, 1.0], [1.0, 0.0]])
        approx = np.array([[0.5, 0.5], [0.6, 0.6]])
        
        gd = generational_distance(approx, ref)
        assert gd > 0.1
    
    def test_igd_perfect_coverage(self):
        """IGD should be 0 when approx covers reference perfectly."""
        ref = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
        approx = ref.copy()
        
        igd = inverted_generational_distance(approx, ref)
        assert igd < 1e-10
    
    def test_igd_poor_coverage(self):
        """IGD should be large when approx doesn't cover reference."""
        ref = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
        approx = np.array([[0.5, 0.5]])
        
        igd = inverted_generational_distance(approx, ref)
        assert igd > 0.1


class TestDiversityMetrics:
    """Test diversity metrics (Spread, Hypervolume)."""
    
    def test_spread_uniform(self):
        """Spread should be low for uniform distribution."""
        approx = np.array([
            [0.0, 1.0],
            [0.25, 0.75],
            [0.5, 0.5],
            [0.75, 0.25],
            [1.0, 0.0],
        ])
        ref = approx.copy()
        
        sp = spread(approx, ref)
        assert sp < 0.5
    
    def test_spread_clustered(self):
        """Spread should be high for clustered distribution."""
        approx = np.array([
            [0.0, 1.0],
            [0.01, 0.99],
            [0.02, 0.98],
            [0.99, 0.01],
            [1.0, 0.0],
        ])
        ref = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
        
        sp = spread(approx, ref)
        assert sp > 0.5
    
    def test_hypervolume_single_point(self):
        """Test hypervolume with single point."""
        pts = np.array([[0.5, 0.5]])
        ref = np.array([1.0, 1.0])
        
        hv = hypervolume(pts, ref)
        assert abs(hv - 0.25) < 1e-10
    
    def test_hypervolume_two_points(self):
        """Test hypervolume with two non-dominated points."""
        pts = np.array([
            [0.3, 0.7],
            [0.7, 0.3],
        ])
        ref = np.array([1.0, 1.0])
        
        hv = hypervolume(pts, ref)
        # Expected: 0.7*0.3 + 0.3*0.4 = 0.21 + 0.12 = 0.33
        assert abs(hv - 0.33) < 1e-10


class TestZDTConvergence:
    """Test NSGA-II convergence on ZDT problems."""
    
    def test_zdt1_convergence(self):
        """Test NSGA-II converges on ZDT1 (GD<1e-3, IGD<1e-2)."""
        zdt1 = ZDT1(n_var=10)
        
        opt = NSGA2(
            n_var=10, n_obj=2,
            pop_size=100, n_gen=200,
            seed=42
        )
        
        result = opt.optimize(
            zdt1, zdt1.lb, zdt1.ub,
            reference_point=np.array([1.1, 1.1]),
            true_pareto=zdt1.true_pareto_front(100)
        )
        
        final_gd = result['history']['gd'][-1]
        final_igd = result['history']['igd'][-1]
        
        assert final_gd < 1e-3, f"GD={final_gd} >= 1e-3"
        assert final_igd < 1e-2, f"IGD={final_igd} >= 1e-2"
    
    def test_zdt2_convergence(self):
        """Test NSGA-II converges on ZDT2."""
        zdt2 = ZDT2(n_var=10)
        
        opt = NSGA2(
            n_var=10, n_obj=2,
            pop_size=100, n_gen=200,
            seed=42
        )
        
        result = opt.optimize(
            zdt2, zdt2.lb, zdt2.ub,
            reference_point=np.array([1.1, 1.1]),
            true_pareto=zdt2.true_pareto_front(100)
        )
        
        final_gd = result['history']['gd'][-1]
        final_igd = result['history']['igd'][-1]
        
        assert final_gd < 1e-3
        assert final_igd < 1e-2
    
    def test_zdt3_convergence(self):
        """Test NSGA-II converges on ZDT3 (disconnected front)."""
        zdt3 = ZDT3(n_var=10)
        
        opt = NSGA2(
            n_var=10, n_obj=2,
            pop_size=100, n_gen=200,
            seed=42
        )
        
        result = opt.optimize(
            zdt3, zdt3.lb, zdt3.ub,
            reference_point=np.array([1.1, 1.1]),
            true_pareto=zdt3.true_pareto_front(100)
        )
        
        final_gd = result['history']['gd'][-1]
        
        # ZDT3 is harder due to disconnected front
        assert final_gd < 0.01


class TestShipInstance:
    """Test ship resistance-seakeeping optimization.

    The ship instance is *domain* content and lives in a domain package; the
    generic framework must not depend on it.  When the domain module is absent
    (as in the domain-free TensorPlatform tree) these tests skip instead of
    breaking collection of the whole generic suite.
    """

    @pytest.fixture(autouse=True)
    def _ship_module(self):
        mod = pytest.importorskip("optimizer.multiobjective.ship_instance")
        self.Ship = mod.ShipResistanceSeakeeping
    
    def test_ship_evaluation(self):
        """Test ship instance evaluation."""
        ship = self.Ship()
        
        x = np.array([7.0, 3.0, 0.65])
        obj = ship(x)
        
        assert len(obj) == 2
        # Resistance should be positive (model-dependent, can be high for simplified model)
        assert obj[0] > 0
        # RAO should be positive and reasonable
        assert 0.1 < obj[1] < 60
    
    def test_ship_design_info(self):
        """Test ship design parameter extraction."""
        ship = self.Ship()
        
        x = np.array([7.0, 3.0, 0.65])
        info = ship.get_design_info(x)
        
        assert 'L' in info
        assert 'B' in info
        assert 'T' in info
        assert 'Cb' in info
        assert info['L'] == 150.0
        assert abs(info['B'] - 150.0 / 7.0) < 1e-6
    
    def test_ship_optimization(self):
        """Test NSGA-II on ship instance finds trade-off."""
        ship = self.Ship()
        
        opt = NSGA2(
            n_var=3, n_obj=2,
            pop_size=50, n_gen=100,
            seed=42
        )
        
        result = opt.optimize(
            ship, ship.lb, ship.ub,
            reference_point=np.array([10000.0, 20.0])
        )
        
        pf = result['pareto_front']
        assert len(pf) > 5
        assert np.all(pf > 0)
        # Should have trade-off (not all same)
        assert np.std(pf[:, 0]) > 1.0
        assert np.std(pf[:, 1]) > 0.1


class TestReproducibility:
    """Test reproducibility with input hashing."""
    
    def test_input_hash_deterministic(self):
        """Test that input hash is deterministic."""
        opt1 = NSGA2(n_var=5, n_obj=2, pop_size=20, n_gen=10, seed=42)
        opt2 = NSGA2(n_var=5, n_obj=2, pop_size=20, n_gen=10, seed=42)
        
        zdt1 = ZDT1(n_var=5)
        
        result1 = opt1.optimize(zdt1, zdt1.lb, zdt1.ub)
        result2 = opt2.optimize(zdt1, zdt1.lb, zdt1.ub)
        
        assert result1['input_hash'] == result2['input_hash']
    
    def test_seed_reproducibility(self):
        """Test that same seed gives same results."""
        zdt1 = ZDT1(n_var=5)
        
        opt1 = NSGA2(n_var=5, n_obj=2, pop_size=20, n_gen=10, seed=123)
        opt2 = NSGA2(n_var=5, n_obj=2, pop_size=20, n_gen=10, seed=123)
        
        result1 = opt1.optimize(zdt1, zdt1.lb, zdt1.ub)
        result2 = opt2.optimize(zdt1, zdt1.lb, zdt1.ub)
        
        np.testing.assert_array_almost_equal(
            result1['pareto_front'],
            result2['pareto_front']
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
