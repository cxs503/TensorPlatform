"""
Generate figures for Extreme Ice Load Analysis Report
"""

import numpy as np
import matplotlib.pyplot as plt
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
    generate_monte_carlo_samples,
)
from services.extreme_ice_load.extreme_ice_load import (
    gumbel_cdf,
    gumbel_ppf,
    weibull_cdf,
    gpd_cdf,
    KUJALA_1994_REFERENCE,
)

# Set style
plt.style.use('seaborn-v0_8-darkgrid')
plt.rcParams['figure.figsize'] = (12, 8)
plt.rcParams['font.size'] = 11
plt.rcParams['axes.labelsize'] = 12
plt.rcParams['axes.titlesize'] = 14


def generate_distribution_fit_figure():
    """Figure 1: Distribution fits with empirical CDF"""
    np.random.seed(42)
    
    # Generate synthetic data
    n = 2000
    mu_true, beta_true = 5.0, 1.5
    u = np.random.uniform(0, 1, n)
    data = gumbel_ppf(u, mu_true, beta_true)
    
    # Fit distributions
    mu_g, beta_g, _ = fit_gumbel(data)
    k_w, lam_w, _ = fit_weibull(data)
    xi_gpd, sigma_gpd, mu_gpd, _ = fit_gpd(data)
    
    # Sort data for empirical CDF
    data_sorted = np.sort(data)
    ecdf = np.arange(1, n + 1) / n
    
    # Generate CDF curves
    x_range = np.linspace(np.min(data), np.max(data), 200)
    cdf_gumbel = gumbel_cdf(x_range, mu_g, beta_g)
    cdf_weibull = weibull_cdf(x_range, k_w, lam_w)
    cdf_gpd = gpd_cdf(x_range, xi_gpd, sigma_gpd, mu_gpd)
    
    fig, ax = plt.subplots(figsize=(10, 7))
    
    # Empirical CDF
    ax.step(data_sorted, ecdf, 'k-', linewidth=2, label='Empirical CDF', alpha=0.7)
    
    # Fitted distributions
    ax.plot(x_range, cdf_gumbel, 'b--', linewidth=2, label=f'Gumbel (μ={mu_g:.2f}, β={beta_g:.2f})')
    ax.plot(x_range, cdf_weibull, 'r-.', linewidth=2, label=f'Weibull (k={k_w:.2f}, λ={lam_w:.2f})')
    ax.plot(x_range, cdf_gpd, 'g:', linewidth=2, label=f'GPD (ξ={xi_gpd:.2f}, σ={sigma_gpd:.2f})')
    
    ax.set_xlabel('Ice Load Peak Force (MN)', fontsize=13)
    ax.set_ylabel('Cumulative Probability F(x)', fontsize=13)
    ax.set_title('Distribution Fits to Synthetic Ice Load Peaks\n(Gumbel-generated data, N=2000)', fontsize=14, fontweight='bold')
    ax.legend(loc='lower right', fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_xlim([np.min(data) - 0.5, np.max(data) + 0.5])
    
    plt.tight_layout()
    plt.savefig('/tmp/fig1_distribution_fits.png', dpi=150, bbox_inches='tight')
    print("Generated: fig1_distribution_fits.png")
    return '/tmp/fig1_distribution_fits.png'


def generate_return_period_figure():
    """Figure 2: Return period loads with confidence intervals"""
    np.random.seed(42)
    
    # Generate data
    n = 3000
    mu_true, beta_true = 5.0, 1.5
    u = np.random.uniform(0, 1, n)
    data = gumbel_ppf(u, mu_true, beta_true)
    
    # Analyze
    analyzer = ExtremeIceLoadAnalyzer(peaks=data, peaks_per_year=50.0)
    analyzer.fit_all()
    analyzer.select_best()
    
    # Return periods
    return_periods = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    loads = []
    lower_bounds = []
    upper_bounds = []
    
    for n_yr in return_periods:
        rp_table = analyzer.compute_return_periods([n_yr])
        loads.append(rp_table[n_yr])
        
        # Bootstrap CI
        lower, median, upper = analyzer.bootstrap_ci(
            return_period=n_yr, n_bootstrap=300, confidence_level=0.95
        )
        lower_bounds.append(lower)
        upper_bounds.append(upper)
    
    loads = np.array(loads)
    lower_bounds = np.array(lower_bounds)
    upper_bounds = np.array(upper_bounds)
    
    fig, ax = plt.subplots(figsize=(10, 7))
    
    # Plot return period curve
    ax.semilogy(return_periods, loads, 'b-o', linewidth=2.5, markersize=8, 
                label=f'{analyzer.best_dist_name.capitalize()} fit', zorder=3)
    
    # Confidence intervals
    ax.fill_between(return_periods, lower_bounds, upper_bounds, 
                    alpha=0.3, color='blue', label='95% Bootstrap CI')
    
    # Reference lines
    ax.axhline(y=np.max(data), color='r', linestyle='--', linewidth=1.5, 
               label=f'Max observed: {np.max(data):.2f} MN', alpha=0.7)
    
    ax.set_xlabel('Return Period (years)', fontsize=13)
    ax.set_ylabel('Ice Load (MN)', fontsize=13)
    ax.set_title('Return Period Loads with 95% Bootstrap Confidence Intervals\n' +
                 f'Best distribution: {analyzer.best_dist_name.capitalize()} ' +
                 f'(KS p-value = {analyzer.best_ks_pvalue:.3f})', 
                 fontsize=14, fontweight='bold')
    ax.legend(loc='upper left', fontsize=11)
    ax.grid(True, alpha=0.3, which='both')
    ax.set_xlim([0.8, 600])
    
    # Add text annotations
    for i, (n_yr, load) in enumerate(zip(return_periods[::2], loads[::2])):
        ax.annotate(f'{n_yr}-yr: {load:.1f} MN', 
                   xy=(n_yr, load), xytext=(10, 10),
                   textcoords='offset points', fontsize=9,
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.7))
    
    plt.tight_layout()
    plt.savefig('/tmp/fig2_return_periods.png', dpi=150, bbox_inches='tight')
    print("Generated: fig2_return_periods.png")
    return '/tmp/fig2_return_periods.png'


def generate_kujala_comparison_figure():
    """Figure 3: Comparison with Kujala (1994) reference data"""
    np.random.seed(42)
    
    # Kujala reference
    mu_kujala = KUJALA_1994_REFERENCE['fitted_gumbel_mu_MN']
    beta_kujala = KUJALA_1994_REFERENCE['fitted_gumbel_beta_MN']
    
    # Generate data matching Kujala conditions
    n = 500
    u = np.random.uniform(0, 1, n)
    data = gumbel_ppf(u, mu_kujala, beta_kujala)
    
    # Fit
    analyzer = ExtremeIceLoadAnalyzer(peaks=data)
    analyzer.fit_all()
    analyzer.select_best()
    
    # Return periods
    return_periods = [1, 10, 50, 100]
    loads_fitted = []
    loads_kujala = []
    
    for n_yr in return_periods:
        rp_table = analyzer.compute_return_periods([n_yr])
        loads_fitted.append(rp_table[n_yr])
        
        # Kujala reference values (approximate from literature)
        if n_yr == 1:
            loads_kujala.append(KUJALA_1994_REFERENCE['return_period_1yr_MN'])
        elif n_yr == 10:
            loads_kujala.append(KUJALA_1994_REFERENCE['return_period_10yr_MN'])
        elif n_yr == 50:
            loads_kujala.append(KUJALA_1994_REFERENCE['return_period_50yr_MN'])
        elif n_yr == 100:
            loads_kujala.append(KUJALA_1994_REFERENCE['return_period_100yr_MN'])
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Left panel: Parameter comparison
    params = ['μ (location)', 'β (scale)']
    fitted_vals = [analyzer.gumbel_params[0], analyzer.gumbel_params[1]]
    kujala_vals = [mu_kujala, beta_kujala]
    
    x = np.arange(len(params))
    width = 0.35
    
    bars1 = ax1.bar(x - width/2, kujala_vals, width, label='Kujala (1994)', color='steelblue', alpha=0.8)
    bars2 = ax1.bar(x + width/2, fitted_vals, width, label='Fitted (this study)', color='coral', alpha=0.8)
    
    ax1.set_ylabel('Parameter Value', fontsize=12)
    ax1.set_title('Gumbel Distribution Parameters\nComparison with Kujala (1994)', fontsize=13, fontweight='bold')
    ax1.set_xticks(x)
    ax1.set_xticklabels(params, fontsize=11)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3, axis='y')
    
    # Add value labels
    for bar in bars1:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.2f}', ha='center', va='bottom', fontsize=10)
    for bar in bars2:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.2f}', ha='center', va='bottom', fontsize=10)
    
    # Right panel: Return period comparison
    ax2.semilogy(return_periods, loads_kujala, 'ro-', linewidth=2.5, markersize=10, 
                label='Kujala (1994)', zorder=3)
    ax2.semilogy(return_periods, loads_fitted, 'bs-', linewidth=2.5, markersize=10, 
                label='This study', zorder=3)
    
    ax2.set_xlabel('Return Period (years)', fontsize=12)
    ax2.set_ylabel('Ice Load (MN)', fontsize=12)
    ax2.set_title('Return Period Loads\nMS Botnica, Bothnian Bay', fontsize=13, fontweight='bold')
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3, which='both')
    
    # Add value labels
    for i, (n_yr, load_k, load_f) in enumerate(zip(return_periods, loads_kujala, loads_fitted)):
        ax2.annotate(f'{load_k:.1f}', xy=(n_yr, load_k), xytext=(5, 5),
                    textcoords='offset points', fontsize=9, color='red')
        ax2.annotate(f'{load_f:.1f}', xy=(n_yr, load_f), xytext=(5, -15),
                    textcoords='offset points', fontsize=9, color='blue')
    
    plt.tight_layout()
    plt.savefig('/tmp/fig3_kujala_comparison.png', dpi=150, bbox_inches='tight')
    print("Generated: fig3_kujala_comparison.png")
    return '/tmp/fig3_kujala_comparison.png'


def generate_monte_carlo_convergence_figure():
    """Figure 4: Monte Carlo convergence study"""
    
    sample_sizes = [500, 1000, 2000, 3000, 5000, 10000]
    loads_100yr = []
    
    for n_samples in sample_sizes:
        peaks = generate_monte_carlo_samples(
            n_samples=n_samples, n_peaks_per_sample=50,
            gumbel_mu=5.0, gumbel_beta=1.5, seed=42
        )
        
        analyzer = ExtremeIceLoadAnalyzer(peaks=peaks)
        analyzer.fit_all()
        analyzer.select_best()
        rp_table = analyzer.compute_return_periods([100])
        loads_100yr.append(rp_table[100])
    
    loads_100yr = np.array(loads_100yr)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Left panel: Convergence curve
    ax1.semilogx(sample_sizes, loads_100yr, 'bo-', linewidth=2.5, markersize=10)
    ax1.axhline(y=loads_100yr[-1], color='r', linestyle='--', linewidth=1.5, 
               label=f'Asymptotic: {loads_100yr[-1]:.2f} MN', alpha=0.7)
    
    ax1.set_xlabel('Number of Monte Carlo Samples', fontsize=12)
    ax1.set_ylabel('100-yr Return Period Load (MN)', fontsize=12)
    ax1.set_title('Monte Carlo Convergence Study\n100-year Return Period Load', fontsize=13, fontweight='bold')
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3, which='both')
    
    # Add value labels
    for n, load in zip(sample_sizes, loads_100yr):
        ax1.annotate(f'{load:.2f}', xy=(n, load), xytext=(5, 5),
                    textcoords='offset points', fontsize=9)
    
    # Right panel: Relative error
    reference = loads_100yr[-1]
    rel_errors = np.abs(loads_100yr - reference) / reference * 100
    
    ax2.semilogx(sample_sizes, rel_errors, 'rs-', linewidth=2.5, markersize=10)
    ax2.axhline(y=5.0, color='g', linestyle='--', linewidth=1.5, 
               label='5% convergence criterion', alpha=0.7)
    
    ax2.set_xlabel('Number of Monte Carlo Samples', fontsize=12)
    ax2.set_ylabel('Relative Error (%)', fontsize=12)
    ax2.set_title('Convergence Error\nRelative to N=10000', fontsize=13, fontweight='bold')
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3, which='both')
    ax2.set_ylim([0, max(rel_errors) * 1.2])
    
    # Add value labels
    for n, err in zip(sample_sizes, rel_errors):
        ax2.annotate(f'{err:.2f}%', xy=(n, err), xytext=(5, 5),
                    textcoords='offset points', fontsize=9)
    
    plt.tight_layout()
    plt.savefig('/tmp/fig4_monte_carlo_convergence.png', dpi=150, bbox_inches='tight')
    print("Generated: fig4_monte_carlo_convergence.png")
    return '/tmp/fig4_monte_carlo_convergence.png'


if __name__ == '__main__':
    print("Generating figures for Extreme Ice Load Analysis Report...")
    print("=" * 60)
    
    fig1 = generate_distribution_fit_figure()
    fig2 = generate_return_period_figure()
    fig3 = generate_kujala_comparison_figure()
    fig4 = generate_monte_carlo_convergence_figure()
    
    print("=" * 60)
    print("All figures generated successfully!")
    print(f"  {fig1}")
    print(f"  {fig2}")
    print(f"  {fig3}")
    print(f"  {fig4}")
