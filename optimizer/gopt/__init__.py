"""G-OPT — optimization library for the TensorPlatform ecosystem.

Domain-free optimization kernels + solver/gradient protocols:

``cmaes``       derivative-free CMA-ES (restarts, noise resampling, full
                termination-criterion set)
``slsqp``       own SLSQP (gradient callback + dense convex QP with active-set
                polish), for smooth constrained problems
``interfaces``  the standard "solver -> gradient function -> optimizer" adjoint
                protocol plus finite-difference / Taylor consistency audits
``benchmarks``  reference problems with closed-form optima

Concrete physics is *injected*: see ``optimizer/gopt/cases/`` for a worked
example that plugs a resistance model into both optimizers through the same
protocol.
"""
from .cmaes import CMAESOptimizer, CMAESResult, minimize_cmaes
from .interfaces import (
    AdjointSolver,
    ConstraintSet,
    FunctionObjective,
    InjectedAdjointObjective,
    Objective,
    PrimalResult,
    adjoint_consistency_check,
    finite_difference_gradient,
)
from .slsqp import SLSQPOptimizer, SLSQPResult, minimize_slsqp, solve_qp

__all__ = [
    "CMAESOptimizer",
    "CMAESResult",
    "minimize_cmaes",
    "SLSQPOptimizer",
    "SLSQPResult",
    "minimize_slsqp",
    "solve_qp",
    "Objective",
    "FunctionObjective",
    "ConstraintSet",
    "AdjointSolver",
    "PrimalResult",
    "InjectedAdjointObjective",
    "finite_difference_gradient",
    "adjoint_consistency_check",
]

__version__ = "1.0.0"