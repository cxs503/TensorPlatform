"""``tools.pce`` -- sample-driven non-intrusive polynomial chaos (PCE).

Public API
----------
``pce_fit(X, Y, degree, ...) -> dict``
    Fit an orthonormal Legendre/Hermite chaos expansion to ``(input samples,
    responses)`` and return ``{coefficients, mean, variance, sobol_first,
    sobol_total, ...}`` -- moments and ANOVA indices are closed form from the
    coefficients, no resampling.

``pce_predict(fit, X)``
    Evaluate the surrogate at new physical points.

``pce_band(fit, probs, n_surrogate, seed)``
    Output quantiles / confidence band from the surrogate (cheap draws, zero
    extra model evaluations).

``quadrature_design``, ``physical_design``, ``unit_design``
    Experiment designs: Gauss-Legendre / Gauss-Hermite tensor rules, Smolyak
    sparse grids, Latin hypercube / scrambled Sobol' sequences.

``case_runner`` (module)
    Generic, case-file-driven benchmark runner: it reads a JSON case that
    injects the black-box model, the input laws, the design, the Monte Carlo
    budget and the acceptance thresholds, then writes a machine-readable
    ledger (see ``config/uq_cases``).

The package is *domain free* by construction (see ``kernel`` module docstring):
it never mentions a physical quantity, a fleet or a solver.  The domain data
arrives only through the sample matrix / the injected case file.
"""

from __future__ import annotations

from .kernel import (
    REQUIRED_KEYS,
    Marginal,
    pce_band,
    pce_fit,
    pce_predict,
    resolve_marginals,
    result_summary,
    sobol_second_order,
)
from .sampling import physical_design, quadrature_design, unit_design

__all__ = [
    "REQUIRED_KEYS",
    "Marginal",
    "pce_fit",
    "pce_predict",
    "pce_band",
    "sobol_second_order",
    "resolve_marginals",
    "result_summary",
    "unit_design",
    "physical_design",
    "quadrature_design",
]

__version__ = "1.0.0"