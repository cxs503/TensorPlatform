"""Multi-fidelity modelling framework (generic mechanism layer, zero domain semantics).

The framework answers one question: *given several models of the same quantity at
different cost/accuracy, how should a fixed evaluation budget be spent so that the
prediction error is minimised?*

Four primitives, each in its own module:

``levels``     fidelity ladder: ``(name, rank, cost, evaluator)`` entries plus cost
               accounting.  No domain vocabulary: the ladder is injected.
``gpr``        Gaussian-process regression (anisotropic squared-exponential kernel,
               marginal-likelihood hyper-parameter fit) — the single-fidelity baseline.
``cokriging``  autoregressive (Kennedy–O'Hagan) co-kriging: ``z_k(x) = rho_k z_{k-1}(x)
               + delta_k(x)``, recursively stacked over any number of levels.
``pareto``     cost–accuracy non-dominated front construction and the machine check
               "at least N levels, cost and accuracy monotone, non-dominated".
``allocator``  budget-constrained fidelity allocation: static (closed-form error law)
               and adaptive (expected-variance-reduction per unit cost) selection.

Every quantity that is domain specific (what a level *is*, what it costs, where the
design space lies) enters through the ``levels``/``ladder`` objects built by the
caller.  The modules and their tests therefore contain no domain vocabulary; the
domain cases live outside this package (see ``docs/m12638/`` for the worked cases).

Typical use
-----------
>>> from tools.multifidelity import Level, Ladder, MultiFidelitySurrogate
>>> ladder = Ladder([Level("coarse", rank=0, cost=1.0, evaluate=f0),
...                  Level("fine",   rank=1, cost=20.0, evaluate=f1)])
>>> surrogate = MultiFidelitySurrogate().fit({0: (X0, y0), 1: (X1, y1)})
>>> mean, var = surrogate.predict(X_test)
"""

from __future__ import annotations

from .allocator import (
    Allocation,
    adaptive_allocation,
    expected_error_reduction,
    fit_error_law,
    optimal_static_allocation,
)
from .cokriging import MultiFidelitySurrogate
from .gpr import GPR
from .levels import Ladder, Level
from .pareto import dominates, pareto_front
from .selection import candidate_subsets, cross_validated_choice

__all__ = [
    "Allocation",
    "GPR",
    "Ladder",
    "Level",
    "MultiFidelitySurrogate",
    "adaptive_allocation",
    "dominates",
    "expected_error_reduction",
    "fit_error_law",
    "optimal_static_allocation",
    "pareto_front",
    "candidate_subsets",
    "cross_validated_choice",
]

__version__ = "1.0.0"