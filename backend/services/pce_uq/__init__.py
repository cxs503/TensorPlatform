"""Non-intrusive polynomial chaos (PCE) uncertainty quantification kernel.

Public surface
--------------
``Marginal``                      -- one uncertain input (uniform/normal/lognormal)
``multi_index_set``               -- total-degree / hyperbolic truncation
``smolyak_grid``, ``tensor_grid`` -- non-intrusive quadrature designs
``fit_projection``, ``fit_regression``, ``fit``
                                  -- build a truncated PCE from a black box
``PCE``                           -- prediction, spectral moments, Sobol' indices
``monte_carlo``, ``compare_moments``
                                  -- direct MC reference + acceleration accounting
``CaseSpec``                      -- data-driven case wiring (JSON)

The kernel is domain free by construction: physical content arrives through a
case specification (``config/uq_cases/*.json``), never through code here.
"""

from .basis import design_matrix, multi_index_set, one_dimensional_basis, support
from .kernel import (
    PCE,
    evaluate_scalar_model,
    fit,
    fit_projection,
    fit_regression,
    latin_hypercube,
    make_unit_sample,
    sobol_sequence,
)
from .marginals import Marginal, marginals_from_spec, to_physical, to_standard_matrix
from .mcbench import MCRun, compare_moments, monte_carlo
from .quadrature import QuadratureGrid, build_grid, gauss_rule_1d, smolyak_grid, tensor_grid
from .spec import CaseSpec, ModelSpec, resolve_callable

__all__ = [
    "PCE",
    "CaseSpec",
    "MCRun",
    "Marginal",
    "ModelSpec",
    "QuadratureGrid",
    "build_grid",
    "compare_moments",
    "design_matrix",
    "evaluate_scalar_model",
    "fit",
    "fit_projection",
    "fit_regression",
    "gauss_rule_1d",
    "latin_hypercube",
    "make_unit_sample",
    "marginals_from_spec",
    "monte_carlo",
    "multi_index_set",
    "one_dimensional_basis",
    "resolve_callable",
    "smolyak_grid",
    "sobol_sequence",
    "support",
    "tensor_grid",
    "to_physical",
    "to_standard_matrix",
]
