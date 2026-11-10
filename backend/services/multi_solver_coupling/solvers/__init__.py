"""Sub-domain solvers used by the multi-solver coupling verification.

All solvers speak the same protocol (:class:`~.base.SubdomainSolver`):
``solve(left_bc, right_bc) -> Solution`` with fluxes in the canonical ``+x``
convention.  ``import_solver`` maps a numerical-method family name to a class;
physics enters only through ``k`` and ``source``.
"""
from __future__ import annotations

from .base import (
    CANONICAL_X,
    DIRICHLET,
    NEUMANN,
    OWN_OUTWARD,
    BoundaryCondition,
    Solution,
    SubdomainSolver,
    dirichlet,
    import_solver,
    neumann,
)
from .bem1d import BEM1D
from .fem1d import FEM1D
from .fvm1d import FVM1D

__all__ = [
    "BEM1D",
    "BoundaryCondition",
    "CANONICAL_X",
    "DIRICHLET",
    "FEM1D",
    "FVM1D",
    "NEUMANN",
    "OWN_OUTWARD",
    "Solution",
    "SubdomainSolver",
    "dirichlet",
    "import_solver",
    "neumann",
]