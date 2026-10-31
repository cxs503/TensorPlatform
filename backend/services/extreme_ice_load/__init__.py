"""
Extreme Ice Load Analysis Module
=================================

Probabilistic extreme value analysis for ice loads on ship hulls.
Implements peak extraction, extreme value distribution fitting (Gumbel, Weibull, GPD),
goodness-of-fit testing, and return period estimation.

References:
- Kujala, P. (1994). "Statistics of ice loads on the midbody cross section of 
  multifuel icebreaker MS Botnica in the Bothnian Bay."
- Coles, S. (2001). "An Introduction to Statistical Modeling of Extreme Values."
- Gumbel, E.J. (1958). "Statistics of Extremes."
"""

from .extreme_ice_load import (
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

__all__ = [
    "ExtremeIceLoadAnalyzer",
    "fit_gumbel",
    "fit_weibull",
    "fit_gpd",
    "ks_test_distribution",
    "select_best_distribution",
    "compute_return_period_load",
    "bootstrap_confidence_interval",
    "extract_peaks",
    "generate_monte_carlo_samples",
]
