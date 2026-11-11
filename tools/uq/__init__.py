"""``tools.uq`` -- generic uncertainty-quantification primitive (PCE + MC + Sobol').

The engine answers three questions about a black-box response ``f(x)`` whose
inputs are uncertain:

1. **What are the output statistics?** -- a non-intrusive polynomial chaos
   expansion (PCE) gives mean / variance / quantiles in closed form from its
   orthonormal coefficients, and an i.i.d. Monte Carlo reference at ``10**4``
   draws provides the independent truth.
2. **Which input drives the spread?** -- first- and total-order Sobol' indices,
   computed two independent ways (PCE coefficient decomposition and a
   variance-based pick-freeze estimator).
3. **Can the surrogate be trusted?** -- two guards: the leave-one-out error
   against the observed response (<= 5%) and the drift of the statistics between
   successive chaos degrees (<= 1%).  A surrogate that fails either guard is
   reported as *insufficient*, never silently quoted.

Supported input laws: uniform, normal and beta (the beta family is the extension
over the pre-existing ``backend.services.pce_uq`` kernel, which covers uniform /
normal / lognormal).  The module is domain free: it knows probabilities and
polynomials only -- see :mod:`tools.uq.purity` for the mechanical zero-domain
check.

Typical use::

    from tools.uq import Marginal, fit_pce, leave_one_out, monte_carlo, sobol_mc
    marginals = [Marginal.uniform("x1", -1, 1), Marginal.beta("x2", 2, 5)]
    X = sample_matrix(marginals, 512, seed=1)
    fit = fit_pce(response, X, marginals, degree=3)
    fit.moments(), fit.sobol(), leave_one_out(fit)
    monte_carlo(response, marginals, n_samples=10_000)
    sobol_mc(response, marginals, n_base=8192)
"""

from __future__ import annotations

from .cases import (
    branin,
    branin_marginals,
    branin_minimum,
    control_marginals,
    control_exact,
    ishigami,
    ishigami_marginals,
    ishigami_reference,
    nonlinear_control,
    smooth_exact,
    smooth_marginals,
    smooth_mixed,
)
from .guard import (
    DEFAULT_DRIFT_TOL,
    DEFAULT_LOO_TOL,
    all_pass,
    criterion,
    degree_guard,
    moment_guard,
    sensitivity_guard,
    surrogate_guard,
)
from .marginals import (
    FAMILIES,
    Marginal,
    exact_moments,
    multi_index,
    orthonormal_matrix,
    sample_matrix,
    tensor_quadrature,
    to_standard_matrix,
)
from .mc import DEFAULT_N_MC, compare_moments, monte_carlo, sobol_mc
from .pce import (
    PCEFit,
    SurrogateError,
    adaptive_fit,
    convergence_study,
    evaluate_response,
    fit_pce,
    guard_or_raise,
    leave_one_out,
)

__all__ = [
    "Marginal", "FAMILIES", "sample_matrix", "to_standard_matrix", "orthonormal_matrix",
    "multi_index", "tensor_quadrature", "exact_moments",
    "PCEFit", "SurrogateError", "fit_pce", "leave_one_out", "convergence_study",
    "adaptive_fit", "evaluate_response", "guard_or_raise",
    "monte_carlo", "sobol_mc", "compare_moments", "DEFAULT_N_MC",
    "criterion", "all_pass", "surrogate_guard", "degree_guard", "moment_guard",
    "sensitivity_guard", "DEFAULT_LOO_TOL", "DEFAULT_DRIFT_TOL",
    "smooth_mixed", "smooth_marginals", "smooth_exact",
    "ishigami", "ishigami_marginals", "ishigami_reference",
    "branin", "branin_marginals", "branin_minimum",
    "nonlinear_control", "control_marginals", "control_exact",
]