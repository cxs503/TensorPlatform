"""Solver-agnostic interface types for multi-solver coupling.

Sign convention (the crux of any coupling contract)
---------------------------------------------------
Every scalar flux exchanged through this package is reported **positive along
the canonical direction ``+x``** of the global coordinate axis, i.e. ``q`` is
the ``x``-component of the flux vector ``q = -k dT/dx`` -- *not* the flux along
each solver's own outward normal.  With this single global convention the
conservation statement at a shared interface is a plain equality::

    q_right(sub-domain A) == q_left(sub-domain B)

The tempting convention "each solver reports its own outward flux" makes the
same statement read ``q_A + q_B = 0`` and is the single most common source of
sign bugs in partitioned coupling; :data:`OWN_OUTWARD` exists only so tests can
inject that mistake deliberately and show that it is detected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

DIRICHLET = "dirichlet"
NEUMANN = "neumann"

#: Orientation modes for a flux value handed to a solver / reported by it.
CANONICAL_X = "canonical_x"   # positive along +x (the only accepted convention)
OWN_OUTWARD = "own_outward"   # positive along each domain's own outward normal


@dataclass(frozen=True)
class BoundaryCondition:
    """One end condition of a 1-D sub-domain.

    ``value`` is a temperature [K] for ``kind='dirichlet'`` and a flux density
    [W/m] (unit cross-section, 1-D) for ``kind='neumann'``, positive along
    ``orientation``.
    """

    kind: str
    value: float
    orientation: str = CANONICAL_X

    def __post_init__(self) -> None:
        if self.kind not in (DIRICHLET, NEUMANN):
            raise ValueError(f"unknown BC kind {self.kind!r}")
        if self.orientation not in (CANONICAL_X, OWN_OUTWARD):
            raise ValueError(f"unknown orientation {self.orientation!r}")

    @property
    def is_dirichlet(self) -> bool:
        return self.kind == DIRICHLET

    def canonical_flux(self, side: str) -> float:
        """Flux in the canonical ``+x`` convention.

        ``side`` is ``'left'`` or ``'right'``: an own-outward flux at the left
        end already points along ``+x`` (no flip), at the right end it points
        along ``-x`` (flip).
        """
        if self.kind != NEUMANN:
            raise TypeError("canonical_flux is only defined for Neumann data")
        if self.orientation == CANONICAL_X:
            return float(self.value)
        sign = 1.0 if side == "left" else -1.0
        return sign * float(self.value)


def dirichlet(value: float) -> BoundaryCondition:
    return BoundaryCondition(DIRICHLET, float(value))


def neumann(flux: float, orientation: str = CANONICAL_X) -> BoundaryCondition:
    return BoundaryCondition(NEUMANN, float(flux), orientation)


@dataclass
class Solution:
    """Discrete solution of one sub-domain.

    Attributes
    ----------
    x, T:
        Sample abscissae and the temperatures there (nodal for FEM/BEM,
        cell-centred for FVM).
    q_left, q_right:
        Fluxes at the two ends in the canonical ``+x`` convention.
    t_left, t_right:
        Temperature *at the two end faces* (not merely at the first sample;
        for FVM this is extrapolated/semi-cell reconstructed from the BC).
    """

    solver: str
    a: float
    b: float
    x: np.ndarray
    T: np.ndarray
    q_left: float
    q_right: float
    t_left: float
    t_right: float

    # -- derived quantities -------------------------------------------------
    def gradient(self) -> np.ndarray:
        """``dT/dx`` at the sample points (2nd-order interior, 1st-order ends)."""
        if len(self.x) < 2:
            raise ValueError("need at least two samples")
        return np.gradient(self.T, self.x)

    def flux_profile(self, k: float | np.ndarray) -> np.ndarray:
        """``-k dT/dx`` at the sample points."""
        return -np.asarray(k, dtype=float) * self.gradient()

    def flux_balance(self, source_integral: float) -> float:
        """Residual of ``q(b) - q(a) = integral(f)`` in the canonical frame."""
        return (self.q_right - self.q_left) - float(source_integral)


@runtime_checkable
class SubdomainSolver(Protocol):
    """Minimal protocol every coupled sub-domain solver implements."""

    name: str
    a: float
    b: float

    def solve(self, left: BoundaryCondition, right: BoundaryCondition) -> Solution:
        ...


def import_solver(family: str, a: float, b: float, n: int, **kwargs):
    """Instantiate the solver of a given numerical-method family.

    The mapping is purely a numerical-method registry: it holds no knowledge of
    the physics application, which arrives through ``k`` / ``source``.
    """
    key = family.upper()
    if key == "FVM":
        from .fvm1d import FVM1D

        return FVM1D(a, b, n, **kwargs)
    if key == "FEM":
        from .fem1d import FEM1D

        return FEM1D(a, b, n, **kwargs)
    if key == "BEM":
        from .bem1d import BEM1D

        return BEM1D(a, b, n, **kwargs)
    raise KeyError(f"no solver registered for family {family!r}")