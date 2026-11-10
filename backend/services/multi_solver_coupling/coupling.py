"""Partitioned (Schwarz) Dirichlet--Neumann coupling iteration.

Algorithm
---------
Domain decomposition at the interface ``x_I``: sub-domain ``A`` owns
``[a, x_I]`` and is solved by ``left_solver``; sub-domain ``B`` owns
``[x_I, b]`` and is solved by ``right_solver``.  One iteration is

1. solve ``A`` with the interface **Dirichlet** trace ``T_I^k``  ->  flux
   ``q^k = q_right(A)`` (canonical ``+x``),
2. solve ``B`` with that flux as **Neumann** data  ->  new interface trace
   ``T_I^* = T( x_I ; B )``,
3. relaxation: ``T_I^{k+1} = T_I^k + omega^k (T_I^* - T_I^k)``.

The interface residual is the normalised fixed-point defect

.. math::  r^k = \\frac{|T_I^* - T_I^k|}{T_{ref}}

with ``T_ref = max(|T_I^k|, |T_I^*|, T_scale)`` and ``T_scale`` the magnitude of
the imposed outer data, so ``r^k`` is dimensionless and directly comparable to
the ``1e-8`` acceptance bound.  ``omega^k`` is either a fixed value or the Aitken
dynamic relaxation factor

.. math::  \\omega^k = -\\omega^{k-1} \\frac{d^{k-1}}{d^{k} - d^{k-1}}.

At convergence the Dirichlet trace imposed on ``A`` equals the trace produced by
``B``, so both solvers share one interface state; the energy check then compares
the two *independently computed* interface fluxes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from .energy import coupled_energy_report
from .solvers.base import BoundaryCondition, Solution, dirichlet, neumann

Relaxation = Union[str, float]


@dataclass
class CouplingResult:
    pair: Tuple[str, str]
    interface: float
    t_interface: float
    iterations: int
    residual_history: List[float]
    omega_history: List[float]
    final_residual: float
    converged: bool
    flux_rel_error: float
    left: Optional[Solution] = None
    right: Optional[Solution] = None
    energy: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "pair": list(self.pair),
            "interface": self.interface,
            "t_interface": self.t_interface,
            "iterations": self.iterations,
            "final_residual": self.final_residual,
            "converged": self.converged,
            "flux_rel_error": self.flux_rel_error,
            "residual_history": self.residual_history,
            "omega_history": self.omega_history,
            "energy": self.energy,
        }


class DirichletNeumannCoupler:
    """Schwarz Dirichlet--Neumann iteration for two sub-domain solvers."""

    def __init__(
        self,
        left_solver,
        right_solver,
        interface: float,
        left_bc: BoundaryCondition,
        right_bc: BoundaryCondition,
        tol: float = 1e-8,
        max_iter: int = 200,
        relaxation: Relaxation = "aitken",
        omega0: float = 0.5,
        omega_bounds: Tuple[float, float] = (0.02, 1.0),
        t_interface0: Optional[float] = None,
        t_scale: Optional[float] = None,
    ) -> None:
        if not (left_solver.a < interface < right_solver.b):
            raise ValueError("interface must lie strictly inside the coupled domain")
        if abs(left_solver.b - interface) > 1e-12:
            raise ValueError("left sub-domain must end at the interface")
        if abs(right_solver.a - interface) > 1e-12:
            raise ValueError("right sub-domain must start at the interface")
        self.left = left_solver
        self.right = right_solver
        self.interface = float(interface)
        self.left_bc = left_bc
        self.right_bc = right_bc
        self.tol = float(tol)
        self.max_iter = int(max_iter)
        self.relaxation = relaxation
        self.omega0 = float(omega0)
        self.omega_bounds = omega_bounds
        if t_scale is None:
            refs = [
                abs(bc.value)
                for bc in (left_bc, right_bc)
                if bc.is_dirichlet
            ]
            t_scale = max(refs) if refs else 1.0
        self.t_scale = float(max(t_scale, 1e-30))
        self.t_interface0 = t_interface0

    # -- initial guess ------------------------------------------------------
    def _initial_trace(self) -> float:
        if self.t_interface0 is not None:
            return float(self.t_interface0)
        # linear interpolation of the outer Dirichlet data (exact for a
        # uniform-conductivity problem, hence a good starting point)
        a, b = self.left.a, self.right.b
        if self.left_bc.is_dirichlet and self.right_bc.is_dirichlet:
            w = (self.interface - a) / (b - a)
            return float((1.0 - w) * self.left_bc.value + w * self.right_bc.value)
        if self.left_bc.is_dirichlet:
            return float(self.left_bc.value)
        if self.right_bc.is_dirichlet:
            return float(self.right_bc.value)
        return 0.0

    # -- iteration ----------------------------------------------------------
    def run(self) -> CouplingResult:
        t_i = self._initial_trace()
        residuals: List[float] = []
        omegas: List[float] = []
        omega = self.omega0 if isinstance(self.relaxation, str) else float(self.relaxation)
        d_prev: Optional[float] = None
        omega_prev: Optional[float] = None
        sol_a: Optional[Solution] = None
        sol_b: Optional[Solution] = None
        converged = False
        residual = np.inf

        for _ in range(self.max_iter):
            sol_a = self.left.solve(self.left_bc, dirichlet(t_i))
            q = sol_a.q_right
            sol_b = self.right.solve(neumann(q), self.right_bc)
            t_star = sol_b.t_left
            d = t_star - t_i
            scale = max(abs(t_i), abs(t_star), self.t_scale)
            residual = abs(d) / scale
            residuals.append(float(residual))
            omegas.append(float(omega))
            if residual <= self.tol:
                converged = True
                break
            if isinstance(self.relaxation, str) and self.relaxation.lower() == "aitken":
                if d_prev is not None and omega_prev is not None:
                    denom = d - d_prev
                    if abs(denom) > 1e-14 * max(abs(d), abs(d_prev), 1e-30):
                        omega_new = -omega_prev * d_prev / denom
                        lo, hi = self.omega_bounds
                        omega = float(np.clip(omega_new, lo, hi))
                d_prev = d
                omega_prev = omega
            t_i = t_i + omega * d   # type: ignore[operator]

        if sol_a is None or sol_b is None:  # pragma: no cover - guarded above
            raise RuntimeError("coupling produced no solution")

        src_a = float(self.left.source_integral()) if hasattr(self.left, "source_integral") else 0.0
        src_b = (
            float(self.right.source_integral())
            if hasattr(self.right, "source_integral")
            else 0.0
        )
        energy = coupled_energy_report(sol_a, sol_b, src_a, src_b)
        return CouplingResult(
            pair=(sol_a.solver, sol_b.solver),
            interface=self.interface,
            t_interface=float(t_i),
            iterations=len(residuals),
            residual_history=residuals,
            omega_history=omegas,
            final_residual=float(residuals[-1]),
            converged=converged,
            flux_rel_error=float(energy["interface_flux_rel_error"]),
            left=sol_a,
            right=sol_b,
            energy=energy,
        )


def coupled_solution_profile(result: CouplingResult) -> Tuple[np.ndarray, np.ndarray]:
    """Concatenate the two sub-domain profiles into one ``(x, T)`` pair."""
    if result.left is None or result.right is None:
        raise ValueError("result carries no solutions")
    x = np.concatenate([result.left.x, result.right.x])
    T = np.concatenate([result.left.T, result.right.T])
    order = np.argsort(x)
    return x[order], T[order]