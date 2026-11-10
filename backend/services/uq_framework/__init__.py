"""Systematic uncertainty quantification framework (v1) -- domain-free.

Three uncertainty sources are quantified by one contract and reported together:

=====================  =========================================  ====================
leg                    estimator                                  acceptance
=====================  =========================================  ====================
input propagation      Monte Carlo + Legendre chaos on the        C1, C2
                       isoprobabilistic unit box (exact moments,
                       closed-form Sobol' shares, cost counted
                       in *model evaluations*)
numerical error        three-grid GCI (reuses ``tools/gci``)      C3
                       + formal-order check + residual gate
                       + measured float32/float64 round-off
model form             multi-model spread, BMA weights,           C4
                       law-of-total-variance decomposition
=====================  =========================================  ====================

Public API
----------
``UQConfig``    declarative definition of an analysis (marginals, budgets,
                evidence specs, acceptance thresholds);
``run_uq``      ``(solver_or_mapping, config) -> UQResult``;
``UQResult``    JSON-serialisable payload + per-criterion verdicts;
``write_report``/``load_report``  machine-parsable report + CI/sensitivity figures;
``InputDistribution``  normal / uniform / triangular marginals with inverse CDF.

Everything here is free of domain vocabulary: inputs are ``x0..xn``, and the
figures take their labels from the caller's config.  Concrete engineering cases
live in the domain repositories and inject their models through this API
(``run_uq(solvers, config)``), so no domain vocabulary appears here.

Quick start
-----------
>>> from backend.services.uq_framework import (UQConfig, InputDistribution,
...                                            run_uq, write_report)
>>> cfg = UQConfig("linear_demo",
...                [InputDistribution.normal("x0", 0.0, 1.0),
...                 InputDistribution.uniform("x1", -1.0, 1.0)],
...                n_mc=1000, pce_order=2, reference={"mean": 0.0, "std": 2.0})
>>> res = run_uq(lambda x0, x1: x0 + 2.0 * x1, cfg)
>>> res.all_criteria_pass, res.headline_method  # doctest: +SKIP
(True, 'pce')
"""

from __future__ import annotations

from .config import DEFAULT_CRITERIA, PROPAGATION_METHODS, UQConfig
from .marginals import InputDistribution, distributions_from_specs, validate_distributions
from .modelform import ModelFormResult, bma_weights, compare_models, run_model_form
from .montecarlo import call_in_unit_box, run_monte_carlo, sobol_complement
from .numeric import (
    NumericalResult,
    grid_convergence,
    residual_norm,
    roundoff_study,
    run_numerical_study,
)
from .basis import MarginalBasis, basis_for
from .pce import PCEResult, design_matrix, fit_pce, fit_pce_from_model, total_degree_indices
from .report import ReportBundle, figure_confidence_interval, figure_sensitivity, load_report, write_report
from .result import UQResult, evaluate_criteria
from .runner import run_uq, summarise_agreement

__all__ = [
    "DEFAULT_CRITERIA",
    "PROPAGATION_METHODS",
    "InputDistribution",
    "ModelFormResult",
    "NumericalResult",
    "PCEResult",
    "ReportBundle",
    "UQConfig",
    "UQResult",
    "bma_weights",
    "call_in_unit_box",
    "compare_models",
    "design_matrix",
    "distributions_from_specs",
    "evaluate_criteria",
    "figure_confidence_interval",
    "figure_sensitivity",
    "fit_pce",
    "fit_pce_from_model",
    "basis_for",
    "MarginalBasis",
    "grid_convergence",
    "load_report",
    "residual_norm",
    "roundoff_study",
    "run_model_form",
    "run_monte_carlo",
    "run_numerical_study",
    "run_uq",
    "sobol_complement",
    "summarise_agreement",
    "total_degree_indices",
    "validate_distributions",
    "write_report",
]

__version__ = "1.0.0"