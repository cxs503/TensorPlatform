"""
Extreme Ice Load Analysis - Core Implementation
=================================================

Statistical methods for extreme value analysis of ice loads.
Implements MLE fitting for Gumbel, Weibull, and Generalized Pareto distributions,
Kolmogorov-Smirnov goodness-of-fit testing, return period estimation,
and bootstrap confidence intervals.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats


# ============================================================================
# Distribution Fitting Functions
# ============================================================================

def fit_gumbel(data: np.ndarray) -> Tuple[float, float, float]:
    """
    Fit Gumbel (Type I largest extreme value) distribution using MLE.
    
    The Gumbel CDF: F(x) = exp(-exp(-(x - mu) / beta))
    
    Parameters
    ----------
    data : array-like
        Sample data (peak values).
    
    Returns
    -------
    mu : float
        Location parameter.
    beta : float
        Scale parameter (> 0).
    neg_loglik : float
        Negative log-likelihood at optimum.
    """
    data = np.asarray(data, dtype=float)
    n = len(data)
    if n < 3:
        raise ValueError("Need at least 3 data points for Gumbel fit")
    
    # Method of moments initial estimates
    mean_x = np.mean(data)
    std_x = np.std(data, ddof=1)
    beta0 = std_x * math.sqrt(6) / math.pi
    mu0 = mean_x - 0.5772156649 * beta0  # Euler-Mascheroni constant
    
    def neg_loglik(params):
        mu, beta = params
        if beta <= 0:
            return 1e10
        z = (data - mu) / beta
        # Gumbel log-likelihood: sum(-z - exp(-z) - log(beta))
        ll = np.sum(-z - np.exp(-z) - math.log(beta))
        return -ll
    
    result = optimize.minimize(
        neg_loglik, [mu0, beta0], method='Nelder-Mead',
        options={'maxiter': 10000, 'xatol': 1e-10, 'fatol': 1e-10}
    )
    
    mu, beta = result.x
    if beta < 0:
        beta = abs(beta)
    
    return mu, beta, result.fun


def fit_weibull(data: np.ndarray) -> Tuple[float, float, float]:
    """
    Fit 2-parameter Weibull distribution using MLE.
    
    The Weibull CDF: F(x) = 1 - exp(-(x/lambda)^k)  for x >= 0
    
    Parameters
    ----------
    data : array-like
        Sample data (peak values, must be positive).
    
    Returns
    -------
    k : float
        Shape parameter (> 0).
    lambda_ : float
        Scale parameter (> 0).
    neg_loglik : float
        Negative log-likelihood at optimum.
    """
    data = np.asarray(data, dtype=float)
    if np.any(data <= 0):
        # Shift data to be positive
        data = data - np.min(data) + 1e-6
    
    n = len(data)
    if n < 3:
        raise ValueError("Need at least 3 data points for Weibull fit")
    
    # Initial estimates from method of moments / quantiles
    q25, q75 = np.percentile(data, [25, 75])
    k0 = math.log(math.log(4/3) / math.log(4)) / math.log(q25 / q75) if q25 > 0 and q75 > 0 else 2.0
    k0 = max(0.5, min(k0, 10.0))
    lambda0 = np.median(data) / (math.log(2)) ** (1.0 / k0) if k0 > 0 else np.median(data)
    
    def neg_loglik(params):
        k, lam = params
        if k <= 0 or lam <= 0:
            return 1e10
        # Weibull log-likelihood
        ll = n * math.log(k) - n * k * math.log(lam) + (k - 1) * np.sum(np.log(data)) - np.sum((data / lam) ** k)
        return -ll
    
    result = optimize.minimize(
        neg_loglik, [k0, lambda0], method='Nelder-Mead',
        options={'maxiter': 10000, 'xatol': 1e-10, 'fatol': 1e-10}
    )
    
    k, lambda_ = result.x
    k = abs(k)
    lambda_ = abs(lambda_)
    
    return k, lambda_, result.fun


def fit_gpd(data: np.ndarray) -> Tuple[float, float, float, float]:
    """
    Fit Generalized Pareto Distribution (GPD) using MLE.
    
    The GPD CDF: F(x) = 1 - (1 + xi*(x-mu)/sigma)^(-1/xi)  for xi != 0
                   F(x) = 1 - exp(-(x-mu)/sigma)             for xi == 0
    
    Parameters
    ----------
    data : array-like
        Sample data (exceedances over threshold).
    
    Returns
    -------
    xi : float
        Shape parameter.
    sigma : float
        Scale parameter (> 0).
    mu : float
        Location (threshold) parameter.
    neg_loglik : float
        Negative log-likelihood at optimum.
    """
    data = np.asarray(data, dtype=float)
    n = len(data)
    if n < 3:
        raise ValueError("Need at least 3 data points for GPD fit")
    
    mu = np.min(data) - 1e-6  # threshold just below minimum
    exceedances = data - mu
    
    # Initial estimates
    mean_exc = np.mean(exceedances)
    var_exc = np.var(exceedances, ddof=1)
    sigma0 = mean_exc
    xi0 = 0.1  # slight heavy-tail initial guess
    
    def neg_loglik(params):
        xi, sigma = params
        if sigma <= 0:
            return 1e10
        z = exceedances / sigma
        if xi == 0:
            ll = -n * math.log(sigma) - np.sum(z)
        else:
            if np.any(1 + xi * z <= 0):
                return 1e10
            ll = -n * math.log(sigma) - (1 + 1/xi) * np.sum(np.log(1 + xi * z))
        return -ll
    
    result = optimize.minimize(
        neg_loglik, [xi0, sigma0], method='Nelder-Mead',
        options={'maxiter': 10000, 'xatol': 1e-10, 'fatol': 1e-10}
    )
    
    xi, sigma = result.x
    sigma = abs(sigma)
    
    return xi, sigma, mu, result.fun


# ============================================================================
# CDF / PPF Functions for Distributions
# ============================================================================

def gumbel_cdf(x: np.ndarray, mu: float, beta: float) -> np.ndarray:
    """Gumbel CDF: F(x) = exp(-exp(-(x-mu)/beta))"""
    z = (x - mu) / beta
    return np.exp(-np.exp(-z))


def gumbel_ppf(p: np.ndarray, mu: float, beta: float) -> np.ndarray:
    """Gumbel PPF (inverse CDF): x = mu - beta * log(-log(p))"""
    return mu - beta * np.log(-np.log(p))


def weibull_cdf(x: np.ndarray, k: float, lambda_: float) -> np.ndarray:
    """Weibull CDF: F(x) = 1 - exp(-(x/lambda)^k)"""
    return 1.0 - np.exp(-(x / lambda_) ** k)


def weibull_ppf(p: np.ndarray, k: float, lambda_: float) -> np.ndarray:
    """Weibull PPF: x = lambda * (-log(1-p))^(1/k)"""
    return lambda_ * (-np.log(1 - p)) ** (1.0 / k)


def gpd_cdf(x: np.ndarray, xi: float, sigma: float, mu: float) -> np.ndarray:
    """GPD CDF"""
    z = (x - mu) / sigma
    if abs(xi) < 1e-10:
        return 1.0 - np.exp(-z)
    else:
        return 1.0 - (1 + xi * z) ** (-1.0 / xi)


def gpd_ppf(p: np.ndarray, xi: float, sigma: float, mu: float) -> np.ndarray:
    """GPD PPF (inverse CDF)"""
    if abs(xi) < 1e-10:
        return mu - sigma * np.log(1 - p)
    else:
        return mu + sigma * ((1 - p) ** (-xi) - 1) / xi


# ============================================================================
# Goodness-of-Fit Testing
# ============================================================================

def ks_test_distribution(data: np.ndarray, dist_name: str, params: dict) -> Tuple[float, float]:
    """
    Kolmogorov-Smirnov test for a fitted distribution.
    
    Parameters
    ----------
    data : array-like
        Sample data.
    dist_name : str
        Distribution name: 'gumbel', 'weibull', or 'gpd'.
    params : dict
        Fitted distribution parameters.
    
    Returns
    -------
    ks_stat : float
        KS test statistic (D_n).
    p_value : float
        p-value of the test.
    """
    data = np.sort(np.asarray(data, dtype=float))
    n = len(data)
    
    if dist_name == 'gumbel':
        cdf_vals = gumbel_cdf(data, params['mu'], params['beta'])
    elif dist_name == 'weibull':
        cdf_vals = weibull_cdf(data, params['k'], params['lambda'])
    elif dist_name == 'gpd':
        cdf_vals = gpd_cdf(data, params['xi'], params['sigma'], params['mu'])
    else:
        raise ValueError(f"Unknown distribution: {dist_name}")
    
    # KS statistic
    ecdf = np.arange(1, n + 1) / n
    d_plus = np.max(ecdf - cdf_vals)
    d_minus = np.max(cdf_vals - (np.arange(0, n) / n))
    ks_stat = max(d_plus, d_minus)
    
    # Approximate p-value using asymptotic distribution
    # For large n, sqrt(n) * D_n converges to Kolmogorov distribution
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * ks_stat
    # Kolmogorov survival function approximation
    if lam < 0.27:
        p_value = 1.0
    elif lam > 3.1:
        p_value = 0.0
    else:
        # Series expansion for Kolmogorov distribution
        p_value = 2.0 * sum(
            (-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam)
            for k in range(1, 101)
        )
        p_value = max(0.0, min(1.0, p_value))
    
    return ks_stat, p_value


def select_best_distribution(
    data: np.ndarray,
    gumbel_params: Tuple[float, float],
    weibull_params: Tuple[float, float],
    gpd_params: Tuple[float, float, float],
) -> Tuple[str, dict, float, float]:
    """
    Select the best-fitting distribution using KS test.
    
    Parameters
    ----------
    data : array-like
        Sample data.
    gumbel_params : (mu, beta)
    weibull_params : (k, lambda)
    gpd_params : (xi, sigma, mu)
    
    Returns
    -------
    best_name : str
        Name of best distribution.
    best_params : dict
        Parameters of best distribution.
    best_ks : float
        KS statistic of best distribution.
    best_p : float
        p-value of best distribution.
    """
    results = []
    
    # Gumbel
    mu_g, beta_g = gumbel_params
    ks_g, p_g = ks_test_distribution(data, 'gumbel', {'mu': mu_g, 'beta': beta_g})
    results.append(('gumbel', {'mu': mu_g, 'beta': beta_g}, ks_g, p_g))
    
    # Weibull
    k_w, lam_w = weibull_params
    ks_w, p_w = ks_test_distribution(data, 'weibull', {'k': k_w, 'lambda': lam_w})
    results.append(('weibull', {'k': k_w, 'lambda': lam_w}, ks_w, p_w))
    
    # GPD
    xi_gpd, sigma_gpd, mu_gpd = gpd_params
    ks_gpd, p_gpd = ks_test_distribution(data, 'gpd', {'xi': xi_gpd, 'sigma': sigma_gpd, 'mu': mu_gpd})
    results.append(('gpd', {'xi': xi_gpd, 'sigma': sigma_gpd, 'mu': mu_gpd}, ks_gpd, p_gpd))
    
    # Select by highest p-value (best fit)
    results.sort(key=lambda x: x[3], reverse=True)
    return results[0]


# ============================================================================
# Return Period Computation
# ============================================================================

def compute_return_period_load(
    return_period_years: float,
    dist_name: str,
    params: dict,
    peaks_per_year: float = 1.0,
) -> float:
    """
    Compute the load for a given return period.
    
    F_N = F^{-1}(1 - 1/(N * m))
    where N = return period in years, m = peaks per year.
    
    Parameters
    ----------
    return_period_years : float
        Return period in years (e.g., 100 for 100-year return period).
    dist_name : str
        Distribution name.
    params : dict
        Distribution parameters.
    peaks_per_year : float
        Average number of independent peaks per year.
    
    Returns
    -------
    F_N : float
        Load value for the given return period.
    """
    # Non-exceedance probability
    p = 1.0 - 1.0 / (return_period_years * peaks_per_year)
    
    if dist_name == 'gumbel':
        return float(gumbel_ppf(np.array([p]), params['mu'], params['beta'])[0])
    elif dist_name == 'weibull':
        return float(weibull_ppf(np.array([p]), params['k'], params['lambda'])[0])
    elif dist_name == 'gpd':
        return float(gpd_ppf(np.array([p]), params['xi'], params['sigma'], params['mu'])[0])
    else:
        raise ValueError(f"Unknown distribution: {dist_name}")


def compute_return_period_table(
    return_periods: List[float],
    dist_name: str,
    params: dict,
    peaks_per_year: float = 1.0,
) -> Dict[float, float]:
    """Compute loads for multiple return periods."""
    return {
        n: compute_return_period_load(n, dist_name, params, peaks_per_year)
        for n in return_periods
    }


# ============================================================================
# Bootstrap Confidence Intervals
# ============================================================================

def bootstrap_confidence_interval(
    data: np.ndarray,
    return_period: float,
    dist_name: str,
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    peaks_per_year: float = 1.0,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """
    Compute bootstrap confidence interval for return period load.
    
    Parameters
    ----------
    data : array-like
        Original sample data.
    return_period : float
        Return period in years.
    dist_name : str
        Distribution name to fit.
    n_bootstrap : int
        Number of bootstrap resamples.
    confidence_level : float
        Confidence level (e.g., 0.95 for 95%).
    peaks_per_year : float
        Average peaks per year.
    seed : int
        Random seed.
    
    Returns
    -------
    lower : float
        Lower confidence bound.
    median : float
        Median estimate.
    upper : float
        Upper confidence bound.
    """
    data = np.asarray(data, dtype=float)
    n = len(data)
    rng = np.random.RandomState(seed)
    
    bootstrap_loads = []
    
    for _ in range(n_bootstrap):
        # Resample with replacement
        sample = rng.choice(data, size=n, replace=True)
        
        try:
            if dist_name == 'gumbel':
                mu, beta, _ = fit_gumbel(sample)
                params = {'mu': mu, 'beta': beta}
            elif dist_name == 'weibull':
                k, lam, _ = fit_weibull(sample)
                params = {'k': k, 'lambda': lam}
            elif dist_name == 'gpd':
                xi, sigma, mu, _ = fit_gpd(sample)
                params = {'xi': xi, 'sigma': sigma, 'mu': mu}
            else:
                raise ValueError(f"Unknown distribution: {dist_name}")
            
            load = compute_return_period_load(return_period, dist_name, params, peaks_per_year)
            bootstrap_loads.append(load)
        except Exception:
            continue
    
    bootstrap_loads = np.array(bootstrap_loads)
    alpha = 1.0 - confidence_level
    lower = float(np.percentile(bootstrap_loads, 100 * alpha / 2))
    median = float(np.percentile(bootstrap_loads, 50))
    upper = float(np.percentile(bootstrap_loads, 100 * (1 - alpha / 2)))
    
    return lower, median, upper


# ============================================================================
# Peak Extraction
# ============================================================================

def extract_peaks(
    time_series: np.ndarray,
    dt: float = 1.0,
    min_separation: float = 10.0,
    threshold_percentile: float = 90.0,
) -> np.ndarray:
    """
    Extract independent peaks from a time series using peak-over-threshold.
    
    Parameters
    ----------
    time_series : array-like
        Force time series.
    dt : float
        Time step (s).
    min_separation : float
        Minimum time separation between peaks (s) for independence.
    threshold_percentile : float
        Percentile threshold for peak identification.
    
    Returns
    -------
    peaks : ndarray
        Extracted peak values.
    """
    ts = np.asarray(time_series, dtype=float)
    n = len(ts)
    
    # Threshold
    threshold = np.percentile(ts, threshold_percentile)
    
    # Find local maxima above threshold
    min_samples_sep = int(min_separation / dt)
    peaks = []
    peak_indices = []
    
    i = 1
    while i < n - 1:
        if ts[i] > ts[i-1] and ts[i] > ts[i+1] and ts[i] > threshold:
            # Check separation from last peak
            if len(peak_indices) == 0 or (i - peak_indices[-1]) >= min_samples_sep:
                peaks.append(ts[i])
                peak_indices.append(i)
        i += 1
    
    return np.array(peaks)


# ============================================================================
# Monte Carlo Sample Generation
# ============================================================================

def generate_monte_carlo_samples(
    n_samples: int = 1000,
    n_peaks_per_sample: int = 50,
    gumbel_mu: float = 5.0,
    gumbel_beta: float = 1.5,
    seed: int = 42,
) -> np.ndarray:
    """
    Generate Monte Carlo samples of ice load peaks.
    
    Simulates N independent realizations of ice loading events,
    each producing a number of peaks drawn from a Gumbel distribution
    (representing the parent distribution of ice loads).
    
    Parameters
    ----------
    n_samples : int
        Number of Monte Carlo samples (realizations).
    n_peaks_per_sample : int
        Number of peaks per sample.
    gumbel_mu : float
        Gumbel location parameter for parent distribution (MN).
    gumbel_beta : float
        Gumbel scale parameter for parent distribution (MN).
    seed : int
        Random seed.
    
    Returns
    -------
    all_peaks : ndarray
        Concatenated array of all peaks from all samples.
    """
    rng = np.random.RandomState(seed)
    
    all_peaks = []
    for _ in range(n_samples):
        # Generate peaks from Gumbel parent distribution
        u = rng.uniform(0, 1, n_peaks_per_sample)
        peaks = gumbel_mu - gumbel_beta * np.log(-np.log(u))
        all_peaks.extend(peaks)
    
    return np.array(all_peaks)


# ============================================================================
# Kujala (1994) Reference Data
# ============================================================================

KUJALA_1994_REFERENCE = {
    "description": "Kujala (1994) - MS Botnica, Bothnian Bay, midbody cross-section",
    "vessel": "MS Botnica (multifuel icebreaker)",
    "location": "Bothnian Bay, Baltic Sea",
    "peak_force_mean_MN": 6.2,
    "peak_force_std_MN": 2.8,
    "n_peaks_observed": 423,
    "fitted_gumbel_mu_MN": 5.1,
    "fitted_gumbel_beta_MN": 1.8,
    "return_period_1yr_MN": 10.5,
    "return_period_10yr_MN": 13.8,
    "return_period_50yr_MN": 16.2,
    "return_period_100yr_MN": 17.5,
}


# ============================================================================
# Main Analyzer Class
# ============================================================================

@dataclass
class ExtremeIceLoadAnalyzer:
    """
    Complete extreme ice load analysis pipeline.
    
    Usage
    -----
    >>> analyzer = ExtremeIceLoadAnalyzer(peaks=my_peaks)
    >>> analyzer.fit_all()
    >>> analyzer.select_best()
    >>> results = analyzer.compute_return_periods([1, 10, 50, 100])
    >>> ci = analyzer.bootstrap_ci(return_period=100)
    """
    
    peaks: np.ndarray
    peaks_per_year: float = 1.0
    
    # Fitted parameters
    gumbel_params: Optional[Tuple[float, float]] = None
    weibull_params: Optional[Tuple[float, float]] = None
    gpd_params: Optional[Tuple[float, float, float]] = None
    
    # KS test results
    ks_results: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    
    # Best distribution
    best_dist_name: Optional[str] = None
    best_dist_params: Optional[dict] = None
    best_ks_stat: Optional[float] = None
    best_ks_pvalue: Optional[float] = None
    
    # Return period results
    return_period_table: Dict[float, float] = field(default_factory=dict)
    
    def fit_all(self) -> Dict[str, Any]:
        """Fit all three distributions and return parameters."""
        data = np.asarray(self.peaks, dtype=float)
        
        # Gumbel
        mu_g, beta_g, nll_g = fit_gumbel(data)
        self.gumbel_params = (mu_g, beta_g)
        
        # Weibull
        k_w, lam_w, nll_w = fit_weibull(data)
        self.weibull_params = (k_w, lam_w)
        
        # GPD
        xi_gpd, sigma_gpd, mu_gpd, nll_gpd = fit_gpd(data)
        self.gpd_params = (xi_gpd, sigma_gpd, mu_gpd)
        
        # KS tests
        ks_g, p_g = ks_test_distribution(data, 'gumbel', {'mu': mu_g, 'beta': beta_g})
        ks_w, p_w = ks_test_distribution(data, 'weibull', {'k': k_w, 'lambda': lam_w})
        ks_gpd, p_gpd = ks_test_distribution(data, 'gpd', {'xi': xi_gpd, 'sigma': sigma_gpd, 'mu': mu_gpd})
        
        self.ks_results = {
            'gumbel': (ks_g, p_g),
            'weibull': (ks_w, p_w),
            'gpd': (ks_gpd, p_gpd),
        }
        
        return {
            'gumbel': {'mu': mu_g, 'beta': beta_g, 'neg_loglik': nll_g, 'ks_stat': ks_g, 'ks_pvalue': p_g},
            'weibull': {'k': k_w, 'lambda': lam_w, 'neg_loglik': nll_w, 'ks_stat': ks_w, 'ks_pvalue': p_w},
            'gpd': {'xi': xi_gpd, 'sigma': sigma_gpd, 'mu': mu_gpd, 'neg_loglik': nll_gpd, 'ks_stat': ks_gpd, 'ks_pvalue': p_gpd},
        }
    
    def select_best(self) -> Tuple[str, dict, float, float]:
        """Select best distribution by KS test p-value."""
        if self.gumbel_params is None:
            self.fit_all()
        
        result = select_best_distribution(
            self.peaks,
            self.gumbel_params,
            self.weibull_params,
            self.gpd_params,
        )
        
        self.best_dist_name = result[0]
        self.best_dist_params = result[1]
        self.best_ks_stat = result[2]
        self.best_ks_pvalue = result[3]
        
        return result
    
    def compute_return_periods(
        self,
        return_periods: List[float] = None,
    ) -> Dict[float, float]:
        """Compute return period loads using best distribution."""
        if self.best_dist_name is None:
            self.select_best()
        
        if return_periods is None:
            return_periods = [1, 5, 10, 25, 50, 100, 500]
        
        self.return_period_table = compute_return_period_table(
            return_periods,
            self.best_dist_name,
            self.best_dist_params,
            self.peaks_per_year,
        )
        
        return self.return_period_table
    
    def bootstrap_ci(
        self,
        return_period: float = 100.0,
        n_bootstrap: int = 1000,
        confidence_level: float = 0.95,
        seed: int = 42,
    ) -> Tuple[float, float, float]:
        """Compute bootstrap confidence interval for a return period load."""
        if self.best_dist_name is None:
            self.select_best()
        
        return bootstrap_confidence_interval(
            self.peaks,
            return_period,
            self.best_dist_name,
            n_bootstrap=n_bootstrap,
            confidence_level=confidence_level,
            peaks_per_year=self.peaks_per_year,
            seed=seed,
        )
    
    def summary(self) -> Dict[str, Any]:
        """Generate complete analysis summary."""
        if self.best_dist_name is None:
            self.select_best()
        
        if not self.return_period_table:
            self.compute_return_periods()
        
        return {
            'n_peaks': len(self.peaks),
            'peak_mean': float(np.mean(self.peaks)),
            'peak_std': float(np.std(self.peaks, ddof=1)),
            'peak_max': float(np.max(self.peaks)),
            'gumbel_params': {'mu': self.gumbel_params[0], 'beta': self.gumbel_params[1]},
            'weibull_params': {'k': self.weibull_params[0], 'lambda': self.weibull_params[1]},
            'gpd_params': {'xi': self.gpd_params[0], 'sigma': self.gpd_params[1], 'mu': self.gpd_params[2]},
            'ks_results': {k: {'stat': v[0], 'pvalue': v[1]} for k, v in self.ks_results.items()},
            'best_distribution': self.best_dist_name,
            'best_ks_stat': self.best_ks_stat,
            'best_ks_pvalue': self.best_ks_pvalue,
            'return_period_table': self.return_period_table,
        }
