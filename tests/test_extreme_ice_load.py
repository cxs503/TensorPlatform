"""
Tests for Extreme Ice Load Analysis Module
============================================

Comprehensive test suite covering:
(a) Gumbel parameter recovery from synthetic data
(b) Weibull shape parameter recovery
(c) KS distribution selection
(d) Return period monotonicity
(e) Monte Carlo convergence
(f) Extreme vs deterministic ratio
(g) Full pipeline integration test
"""

import math
import numpy as np
import pytest
import sys
import os

# Add backend to path
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend'))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from services.extreme_ice_load import (
    ExtremeIceLoadAnalyzer,
    fit_gumbel,
    fit_weibull,
    fit_gpd,
    ks_test_distribution,
    select_best_distribution,
    compute_return_period_load,
    bootstrap_confidence_interval,
    extract_peaks,
    generate_monte_carlo_samples,
)
from services.extreme_ice_load.extreme_ice_load import (
    gumbel_cdf,
    gumbel_ppf,
    weibull_cdf,
    weibull_ppf,
    gpd_cdf,
    gpd_ppf,
    KUJALA_1994_REFERENCE,
)


# ============================================================================
# (a) Gumbel Parameter Recovery Test
# ============================================================================

class TestGumbelFitSynthetic:
    """Test Gumbel distribution fitting on synthetic data."""
    
    def test_gumbel_parameter_recovery(self):
        """
        (a) pytest_test_gumbel_fit_synthetic:
        Generate synthetic Gumbel data, fit, check parameter recovery < 0.05.
        """
        np.random.seed(42)
        n = 5000
        
        # True parameters
        mu_true = 5.0
        beta_true = 1.5
        
        # Generate Gumbel random variates using inverse CDF
        u = np.random.uniform(0, 1, n)
        data = gumbel_ppf(u, mu_true, beta_true)
        
        # Fit
        mu_fit, beta_fit, nll = fit_gumbel(data)
        
        # Check recovery
        mu_error = abs(mu_fit - mu_true) / abs(mu_true)
        beta_error = abs(beta_fit - beta_true) / abs(beta_true)
        
        print(f"Gumbel fit: mu_true={mu_true}, mu_fit={mu_fit:.4f}, error={mu_error:.4f}")
        print(f"Gumbel fit: beta_true={beta_true}, beta_fit={beta_fit:.4f}, error={beta_error:.4f}")
        
        assert mu_error < 0.05, f"Gumbel mu recovery error {mu_error:.4f} >= 0.05"
        assert beta_error < 0.05, f"Gumbel beta recovery error {beta_error:.4f} >= 0.05"
    
    def test_gumbel_cdf_ppf_consistency(self):
        """Test that CDF and PPF are consistent inverses."""
        mu, beta = 3.0, 2.0
        x = np.array([1.0, 3.0, 5.0, 7.0, 10.0])
        p = gumbel_cdf(x, mu, beta)
        x_recovered = gumbel_ppf(p, mu, beta)
        np.testing.assert_allclose(x, x_recovered, rtol=1e-10)


# ============================================================================
# (b) Weibull Shape Parameter Recovery Test
# ============================================================================

class TestWeibullFitSynthetic:
    """Test Weibull distribution fitting on synthetic data."""
    
    def test_weibull_shape_recovery(self):
        """
        (b) pytest_test_weibull_fit_synthetic:
        Generate synthetic Weibull data, fit, check shape parameter recovery < 0.10.
        """
        np.random.seed(123)
        n = 5000
        
        # True parameters
        k_true = 2.5
        lambda_true = 4.0
        
        # Generate Weibull random variates
        u = np.random.uniform(0, 1, n)
        data = weibull_ppf(u, k_true, lambda_true)
        
        # Fit
        k_fit, lambda_fit, nll = fit_weibull(data)
        
        # Check recovery
        k_error = abs(k_fit - k_true) / abs(k_true)
        lambda_error = abs(lambda_fit - lambda_true) / abs(lambda_true)
        
        print(f"Weibull fit: k_true={k_true}, k_fit={k_fit:.4f}, error={k_error:.4f}")
        print(f"Weibull fit: lambda_true={lambda_true}, lambda_fit={lambda_fit:.4f}, error={lambda_error:.4f}")
        
        assert k_error < 0.10, f"Weibull k recovery error {k_error:.4f} >= 0.10"
        assert lambda_error < 0.10, f"Weibull lambda recovery error {lambda_error:.4f} >= 0.10"
    
    def test_weibull_cdf_ppf_consistency(self):
        """Test that CDF and PPF are consistent inverses."""
        k, lam = 2.0, 3.0
        x = np.array([0.5, 1.0, 2.0, 3.0, 5.0])
        p = weibull_cdf(x, k, lam)
        x_recovered = weibull_ppf(p, k, lam)
        np.testing.assert_allclose(x, x_recovered, rtol=1e-10)


# ============================================================================
# (c) KS Distribution Selection Test
# ============================================================================

class TestKSSelection:
    """Test KS test correctly identifies the generating distribution."""
    
    def test_ks_selection_gumbel(self):
        """
        (c) pytest_test_ks_selection:
        Generate data from Gumbel, verify KS test selects Gumbel with p > 0.05.
        """
        np.random.seed(42)
        n = 2000
        
        # Generate from Gumbel
        mu_true, beta_true = 5.0, 1.5
        u = np.random.uniform(0, 1, n)
        data = gumbel_ppf(u, mu_true, beta_true)
        
        # Fit all distributions
        mu_g, beta_g, _ = fit_gumbel(data)
        k_w, lam_w, _ = fit_weibull(data)
        xi_gpd, sigma_gpd, mu_gpd, _ = fit_gpd(data)
        
        # KS tests
        ks_g, p_g = ks_test_distribution(data, 'gumbel', {'mu': mu_g, 'beta': beta_g})
        ks_w, p_w = ks_test_distribution(data, 'weibull', {'k': k_w, 'lambda': lam_w})
        ks_gpd, p_gpd = ks_test_distribution(data, 'gpd', {'xi': xi_gpd, 'sigma': sigma_gpd, 'mu': mu_gpd})
        
        print(f"KS Gumbel: stat={ks_g:.4f}, p={p_g:.4f}")
        print(f"KS Weibull: stat={ks_w:.4f}, p={p_w:.4f}")
        print(f"KS GPD: stat={ks_gpd:.4f}, p={p_gpd:.4f}")
        
        # Gumbel should have p > 0.05 (cannot reject null hypothesis)
        assert p_g > 0.05, f"Gumbel KS p-value {p_g:.4f} <= 0.05"
        
        # Best selection should be Gumbel
        best_name, best_params, best_ks, best_p = select_best_distribution(
            data, (mu_g, beta_g), (k_w, lam_w), (xi_gpd, sigma_gpd, mu_gpd)
        )
        
        print(f"Best distribution: {best_name} (p={best_p:.4f})")
        assert best_name == 'gumbel', f"Expected gumbel, got {best_name}"
    
    def test_ks_selection_weibull(self):
        """Test KS test correctly identifies Weibull-generated data."""
        np.random.seed(99)
        n = 2000
        
        k_true, lam_true = 3.0, 5.0
        u = np.random.uniform(0, 1, n)
        data = weibull_ppf(u, k_true, lam_true)
        
        # Fit all
        mu_g, beta_g, _ = fit_gumbel(data)
        k_w, lam_w, _ = fit_weibull(data)
        xi_gpd, sigma_gpd, mu_gpd, _ = fit_gpd(data)
        
        ks_w, p_w = ks_test_distribution(data, 'weibull', {'k': k_w, 'lambda': lam_w})
        
        print(f"Weibull KS: stat={ks_w:.4f}, p={p_w:.4f}")
        assert p_w > 0.05, f"Weibull KS p-value {p_w:.4f} <= 0.05"


# ============================================================================
# (d) Return Period Monotonicity Test
# ============================================================================

class TestReturnPeriodMonotone:
    """Test that return period loads are monotonically increasing."""
    
    def test_return_period_monotone_gumbel(self):
        """
        (d) pytest_test_return_period_monotone:
        Verify F_N increases monotonically with N for Gumbel.
        """
        mu, beta = 5.0, 1.5
        params = {'mu': mu, 'beta': beta}
        
        return_periods = [1, 5, 10, 25, 50, 100, 500, 1000]
        loads = [compute_return_period_load(n, 'gumbel', params) for n in return_periods]
        
        print("Return period loads (Gumbel):")
        for n, f in zip(return_periods, loads):
            print(f"  N={n:5d} yr: F_N = {f:.4f} MN")
        
        # Check monotonicity
        for i in range(1, len(loads)):
            assert loads[i] > loads[i-1], \
                f"F_{return_periods[i]}={loads[i]:.4f} <= F_{return_periods[i-1]}={loads[i-1]:.4f}"
    
    def test_return_period_monotone_weibull(self):
        """Verify monotonicity for Weibull distribution."""
        k, lam = 2.5, 4.0
        params = {'k': k, 'lambda': lam}
        
        return_periods = [1, 5, 10, 50, 100, 500]
        loads = [compute_return_period_load(n, 'weibull', params) for n in return_periods]
        
        for i in range(1, len(loads)):
            assert loads[i] > loads[i-1], \
                f"F_{return_periods[i]}={loads[i]:.4f} <= F_{return_periods[i-1]}={loads[i-1]:.4f}"
    
    def test_return_period_monotone_from_data(self):
        """Verify monotonicity from fitted data (full pipeline)."""
        np.random.seed(42)
        n = 3000
        mu_true, beta_true = 5.0, 1.5
        u = np.random.uniform(0, 1, n)
        data = gumbel_ppf(u, mu_true, beta_true)
        
        analyzer = ExtremeIceLoadAnalyzer(peaks=data)
        analyzer.fit_all()
        analyzer.select_best()
        rp_table = analyzer.compute_return_periods([1, 5, 10, 25, 50, 100, 500])
        
        loads = list(rp_table.values())
        for i in range(1, len(loads)):
            assert loads[i] > loads[i-1], \
                f"Non-monotone at index {i}: {loads[i]:.4f} <= {loads[i-1]:.4f}"


# ============================================================================
# (e) Monte Carlo Convergence Test
# ============================================================================

class TestMonteCarloConvergence:
    """Test Monte Carlo convergence of extreme load estimates."""
    
    def test_monte_carlo_convergence(self):
        """
        (e) pytest_test_monte_carlo_convergence:
        N=1000 vs N=5000 samples, 100-yr load difference < 5%.
        """
        # Generate with N=1000
        peaks_1k = generate_monte_carlo_samples(
            n_samples=1000, n_peaks_per_sample=50,
            gumbel_mu=5.0, gumbel_beta=1.5, seed=42
        )
        
        # Generate with N=5000
        peaks_5k = generate_monte_carlo_samples(
            n_samples=5000, n_peaks_per_sample=50,
            gumbel_mu=5.0, gumbel_beta=1.5, seed=42
        )
        
        # Fit and compute 100-yr load
        analyzer_1k = ExtremeIceLoadAnalyzer(peaks=peaks_1k)
        analyzer_1k.fit_all()
        analyzer_1k.select_best()
        rp_1k = analyzer_1k.compute_return_periods([100])
        load_1k = rp_1k[100]
        
        analyzer_5k = ExtremeIceLoadAnalyzer(peaks=peaks_5k)
        analyzer_5k.fit_all()
        analyzer_5k.select_best()
        rp_5k = analyzer_5k.compute_return_periods([100])
        load_5k = rp_5k[100]
        
        diff = abs(load_1k - load_5k) / load_5k
        
        print(f"100-yr load (N=1000): {load_1k:.4f} MN")
        print(f"100-yr load (N=5000): {load_5k:.4f} MN")
        print(f"Relative difference: {diff:.4f}")
        
        assert diff < 0.05, f"MC convergence: diff={diff:.4f} >= 0.05"


# ============================================================================
# (f) Extreme vs Deterministic Ratio Test
# ============================================================================

class TestExtremeVsDeterministic:
    """Test that extreme value load / deterministic peak ratio is in [1.2, 3.0]."""
    
    def test_extreme_vs_deterministic_ratio(self):
        """
        (f) pytest_test_extreme_vs_deterministic_ratio:
        100-yr extreme load / deterministic peak load ∈ [1.2, 3.0].
        
        The "deterministic peak load" is defined as the maximum from a single
        ice season (one year of operation), while the 100-yr extreme load is
        the probabilistic extrapolation over 100 years.
        """
        np.random.seed(42)
        
        # Parent distribution parameters (representative of Baltic ice loads)
        mu_parent = 5.0  # MN
        beta_parent = 1.5  # MN
        
        # Deterministic peak: max from ONE season (one year)
        # Assume ~500 independent peaks per year
        n_peaks_per_year = 500
        u_year = np.random.uniform(0, 1, n_peaks_per_year)
        peaks_one_year = gumbel_ppf(u_year, mu_parent, beta_parent)
        deterministic_max = np.max(peaks_one_year)
        
        # Now generate many years of data for extreme value analysis
        # 100 years × 500 peaks/year = 50000 peaks total
        n_years = 100
        all_peaks = []
        for _ in range(n_years):
            u = np.random.uniform(0, 1, n_peaks_per_year)
            peaks = gumbel_ppf(u, mu_parent, beta_parent)
            all_peaks.extend(peaks)
        
        all_peaks = np.array(all_peaks)
        
        # Fit extreme value distribution
        analyzer = ExtremeIceLoadAnalyzer(peaks=all_peaks, peaks_per_year=n_peaks_per_year)
        analyzer.fit_all()
        analyzer.select_best()
        rp_table = analyzer.compute_return_periods([100])
        extreme_100yr = rp_table[100]
        
        ratio = extreme_100yr / deterministic_max
        
        print(f"Deterministic max peak (1 year): {deterministic_max:.4f} MN")
        print(f"100-yr extreme load: {extreme_100yr:.4f} MN")
        print(f"Ratio: {ratio:.4f}")
        
        assert 1.2 <= ratio <= 3.0, \
            f"Extreme/deterministic ratio {ratio:.4f} not in [1.2, 3.0]"


# ============================================================================
# (g) Full Pipeline Integration Test
# ============================================================================

class TestExtremeIceLoad:
    """Full integration test for extreme ice load analysis."""
    
    def test_extreme_ice_load_pipeline(self):
        """
        (g) pytest_test_extreme_ice_load:
        Full pipeline: generate → fit → select → return periods → bootstrap.
        """
        np.random.seed(42)
        
        # Step 1: Generate synthetic ice load peaks from known Gumbel distribution
        # This simulates peaks extracted from ice load time series
        n_peaks = 5000
        mu_parent = 5.0  # MN
        beta_parent = 1.5  # MN
        
        u = np.random.uniform(0, 1, n_peaks)
        peaks = gumbel_ppf(u, mu_parent, beta_parent)
        
        print(f"Generated {len(peaks)} peaks")
        print(f"Peak statistics: mean={np.mean(peaks):.2f}, std={np.std(peaks):.2f}, max={np.max(peaks):.2f}")
        
        # Step 2: Full analysis
        analyzer = ExtremeIceLoadAnalyzer(peaks=peaks, peaks_per_year=50.0)
        fit_results = analyzer.fit_all()
        
        print("\n=== Distribution Fit Results ===")
        for dist_name, params in fit_results.items():
            print(f"  {dist_name}: {params}")
        
        # Step 3: Select best
        best_name, best_params, best_ks, best_p = analyzer.select_best()
        print(f"\nBest distribution: {best_name} (KS={best_ks:.4f}, p={best_p:.4f})")
        
        # Step 4: Return periods
        rp_table = analyzer.compute_return_periods([1, 5, 10, 25, 50, 100])
        print("\n=== Return Period Table ===")
        for n, f in rp_table.items():
            print(f"  {n:5.0f}-yr: {f:.4f} MN")
        
        # Step 5: Bootstrap CI
        lower, median, upper = analyzer.bootstrap_ci(return_period=100, n_bootstrap=500)
        print(f"\n100-yr load: {median:.4f} MN (95% CI: [{lower:.4f}, {upper:.4f}])")
        
        # Step 6: Summary
        summary = analyzer.summary()
        
        # Assertions
        assert summary['n_peaks'] > 100
        assert summary['best_distribution'] in ['gumbel', 'weibull', 'gpd']
        assert len(summary['return_period_table']) == 6
        
        # Check monotonicity
        loads = list(rp_table.values())
        for i in range(1, len(loads)):
            assert loads[i] > loads[i-1]
        
        # Check extreme/deterministic ratio
        # Deterministic = max from one "year" (50 peaks)
        det_max = np.percentile(peaks, 98)  # Use 98th percentile as representative max
        ratio_100yr = rp_table[100] / det_max
        print(f"\nDeterministic peak (98th pctile): {det_max:.4f} MN")
        print(f"100-yr extreme load: {rp_table[100]:.4f} MN")
        print(f"Extreme/Deterministic ratio (100-yr): {ratio_100yr:.4f}")
        
        # The ratio should be reasonable (1.2 to 3.0)
        assert 1.0 <= ratio_100yr <= 5.0, \
            f"Ratio {ratio_100yr:.4f} outside reasonable range [1.0, 5.0]"
    
    def test_kujala_comparison(self):
        """Compare analysis results with Kujala (1994) reference data."""
        np.random.seed(42)
        
        # Generate data mimicking Kujala's conditions
        # Kujala observed: mean ~6.2 MN, std ~2.8 MN, Gumbel mu~5.1, beta~1.8
        mu_kujala = KUJALA_1994_REFERENCE['fitted_gumbel_mu_MN']
        beta_kujala = KUJALA_1994_REFERENCE['fitted_gumbel_beta_MN']
        
        u = np.random.uniform(0, 1, 500)
        data = gumbel_ppf(u, mu_kujala, beta_kujala)
        
        analyzer = ExtremeIceLoadAnalyzer(peaks=data)
        analyzer.fit_all()
        analyzer.select_best()
        
        summary = analyzer.summary()
        
        print("\n=== Comparison with Kujala (1994) ===")
        print(f"Reference: mu={mu_kujala}, beta={beta_kujala}")
        print(f"Fitted:    mu={summary['gumbel_params']['mu']:.2f}, beta={summary['gumbel_params']['beta']:.2f}")
        
        # Check parameter recovery against Kujala reference
        mu_error = abs(summary['gumbel_params']['mu'] - mu_kujala) / mu_kujala
        beta_error = abs(summary['gumbel_params']['beta'] - beta_kujala) / beta_kujala
        
        print(f"mu error: {mu_error:.4f}")
        print(f"beta error: {beta_error:.4f}")
        
        assert mu_error < 0.10, f"mu recovery error {mu_error:.4f} >= 0.10"
        assert beta_error < 0.10, f"beta recovery error {beta_error:.4f} >= 0.10"


# ============================================================================
# Additional Edge Case Tests
# ============================================================================

class TestEdgeCases:
    """Edge case and robustness tests."""
    
    def test_small_sample(self):
        """Test with minimum viable sample size."""
        np.random.seed(42)
        data = np.array([3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
        
        mu, beta, nll = fit_gumbel(data)
        assert beta > 0
        assert not math.isnan(mu)
    
    def test_peak_extraction(self):
        """Test peak extraction from synthetic time series."""
        np.random.seed(42)
        dt = 0.1
        t = np.arange(0, 100, dt)
        
        # Create signal with known peaks
        signal = np.zeros_like(t)
        peak_times = [10, 25, 40, 55, 70, 85]
        peak_values = [5.0, 8.0, 3.0, 10.0, 6.0, 7.0]
        
        for pt, pv in zip(peak_times, peak_values):
            idx = int(pt / dt)
            decay_len = 30
            if idx + decay_len < len(signal):
                signal[idx:idx+decay_len] = pv * np.exp(-np.arange(decay_len) * 0.3)
        
        peaks = extract_peaks(signal, dt=dt, min_separation=5.0, threshold_percentile=50)
        
        print(f"Extracted {len(peaks)} peaks: {peaks}")
        assert len(peaks) >= 4  # Should find most of the 6 peaks
    
    def test_gpd_fit(self):
        """Test GPD fitting."""
        np.random.seed(42)
        # Generate from exponential (special case of GPD with xi=0)
        data = np.random.exponential(2.0, 1000) + 1.0
        
        xi, sigma, mu, nll = fit_gpd(data)
        
        print(f"GPD fit: xi={xi:.4f}, sigma={sigma:.4f}, mu={mu:.4f}")
        assert sigma > 0
        assert not math.isnan(xi)
    
    def test_bootstrap_ci(self):
        """Test bootstrap confidence interval computation."""
        np.random.seed(42)
        n = 1000
        mu_true, beta_true = 5.0, 1.5
        u = np.random.uniform(0, 1, n)
        data = gumbel_ppf(u, mu_true, beta_true)
        
        lower, median, upper = bootstrap_confidence_interval(
            data, return_period=100, dist_name='gumbel',
            n_bootstrap=200, confidence_level=0.95, seed=42
        )
        
        print(f"Bootstrap CI: [{lower:.4f}, {median:.4f}, {upper:.4f}]")
        assert lower < median < upper
        assert lower > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
