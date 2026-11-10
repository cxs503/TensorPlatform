"""
Coupling stability and convergence acceleration for partitioned multi-solver coupling.

Implements:
- Aitken dynamic relaxation for partitioned coupling
- Convergence monitoring and criteria
- CFL-like stability conditions for coupling
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np


@dataclass
class CouplingConvergence:
    """
    Tracks convergence of partitioned coupling iterations.

    Attributes:
        iteration: current iteration number
        residual_norm: ||x^{k+1} - x^k|| / ||x^k||
        converged: whether convergence criterion is met
        max_iterations: maximum allowed iterations
        tolerance: convergence tolerance
        history: list of residual norms
    """
    iteration: int
    residual_norm: float
    converged: bool
    max_iterations: int
    tolerance: float
    history: List[float]

    def __repr__(self):
        status = "CONVERGED" if self.converged else "NOT_CONVERGED"
        return (
            f"CouplingConvergence(iter={self.iteration}, "
            f"residual={self.residual_norm:.2e} [{status}])"
        )


class AitkenRelaxation:
    """
    Aitken dynamic relaxation for accelerating partitioned coupling convergence.

    The Aitken method computes an optimal relaxation parameter ω based on
    the history of coupling residuals, significantly accelerating convergence
    for loosely coupled partitioned schemes.

    Reference: Küttler & Wall (2008), "Vector extrapolation for strong
    coupling of partitioned fluid-structure interaction problems."
    """

    def __init__(
        self,
        initial_omega: float = 1.0,
        min_omega: float = 0.01,
        max_omega: float = 2.0,
        tolerance: float = 1e-6,
        max_iterations: int = 20
    ):
        """
        Args:
            initial_omega: initial relaxation parameter
            min_omega: minimum allowed omega
            max_omega: maximum allowed omega
            tolerance: convergence tolerance for relative residual
            max_iterations: maximum coupling iterations
        """
        self.omega = initial_omega
        self.min_omega = min_omega
        self.max_omega = max_omega
        self.tolerance = tolerance
        self.max_iterations = max_iterations

        self._prev_residual: Optional[np.ndarray] = None
        self._prev_delta_x: Optional[np.ndarray] = None
        self._prev_x: Optional[np.ndarray] = None
        self._iteration: int = 0
        self._residual_history: List[float] = []

    def reset(self):
        """Reset Aitken state for new coupling sequence."""
        self.omega = 1.0  # Reset to initial
        self._prev_residual = None
        self._prev_delta_x = None
        self._prev_x = None
        self._iteration = 0
        self._residual_history = []

    def compute_relaxation(
        self,
        current_solution: np.ndarray,
        coupled_solution: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        """
        Compute Aitken-relaxed solution update.

        Given current iterate x^k and coupled solver output R(x^k),
        compute relaxed update: x^{k+1} = x^k + ω * (R(x^k) - x^k)

        Args:
            current_solution: x^k, current coupling iterate
            coupled_solution: R(x^k), output from coupled solver

        Returns:
            Tuple of (relaxed_solution, omega_used)
        """
        residual = coupled_solution - current_solution

        if self._iteration == 0 or self._prev_residual is None:
            # First iteration: use initial omega
            omega = self.omega
            delta_x = omega * residual
        else:
            # Aitken's delta-squared method
            delta_r = residual - self._prev_residual

            delta_r_norm_sq = np.dot(delta_r, delta_r)
            if delta_r_norm_sq > 1e-30:
                # ω_{k+1} = -ω_k * (Δr_{k-1} · Δr_k) / ||Δr_k||^2
                omega = -self.omega * np.dot(self._prev_residual, delta_r) / delta_r_norm_sq
                omega = max(self.min_omega, min(self.max_omega, abs(omega)))
            else:
                omega = self.omega

            delta_x = omega * residual

        # Apply relaxation
        relaxed = current_solution + delta_x

        # Update state
        self._prev_residual = residual.copy()
        self._prev_delta_x = delta_x.copy()
        self.omega = omega
        self._iteration += 1

        # Track convergence: ||x^{k+1} - x^k|| / ||x^k||
        x_change = relaxed - current_solution
        x_norm = np.linalg.norm(current_solution)
        if x_norm > 1e-14:
            rel_change = np.linalg.norm(x_change) / x_norm
        else:
            rel_change = np.linalg.norm(x_change)
        self._residual_history.append(rel_change)

        return relaxed, omega

    def check_convergence(
        self,
        current_solution: np.ndarray,
        coupled_solution: np.ndarray
    ) -> CouplingConvergence:
        """
        Check if coupling has converged.

        Args:
            current_solution: x^k
            coupled_solution: R(x^k)

        Returns:
            CouplingConvergence with current status
        """
        residual = coupled_solution - current_solution
        x_norm = np.linalg.norm(current_solution)

        if x_norm > 1e-14:
            relative_residual = np.linalg.norm(residual) / x_norm
        else:
            relative_residual = np.linalg.norm(residual)

        converged = relative_residual <= self.tolerance

        return CouplingConvergence(
            iteration=self._iteration,
            residual_norm=relative_residual,
            converged=converged,
            max_iterations=self.max_iterations,
            tolerance=self.tolerance,
            history=self._residual_history.copy()
        )

    def run_coupling_iteration(
        self,
        current_solution: np.ndarray,
        coupled_solver: callable
    ) -> Tuple[np.ndarray, CouplingConvergence]:
        """
        Perform one complete coupling iteration with Aitken relaxation.

        Args:
            current_solution: x^k, current iterate
            coupled_solver: function R(x) -> R(x), the coupled solver

        Returns:
            Tuple of (new_solution, convergence_info)
        """
        # Get coupled solver output
        coupled_output = coupled_solver(current_solution)

        # Apply Aitken relaxation
        new_solution, omega = self.compute_relaxation(current_solution, coupled_output)

        # Check convergence based on change in x
        x_change = new_solution - current_solution
        x_norm = np.linalg.norm(current_solution)
        if x_norm > 1e-14:
            rel_change = np.linalg.norm(x_change) / x_norm
        else:
            rel_change = np.linalg.norm(x_change)

        converged = rel_change <= self.tolerance

        convergence = CouplingConvergence(
            iteration=self._iteration,
            residual_norm=rel_change,
            converged=converged,
            max_iterations=self.max_iterations,
            tolerance=self.tolerance,
            history=self._residual_history.copy()
        )

        return new_solution, convergence

    def solve_coupled_system(
        self,
        initial_solution: np.ndarray,
        coupled_solver: callable
    ) -> Tuple[np.ndarray, CouplingConvergence]:
        """
        Solve coupled system using Aitken relaxation to convergence.

        Args:
            initial_solution: x^0, initial guess
            coupled_solver: function R(x) -> R(x)

        Returns:
            Tuple of (converged_solution, final_convergence)
        """
        self.reset()
        x = initial_solution.copy()

        for k in range(self.max_iterations):
            x_new, convergence = self.run_coupling_iteration(x, coupled_solver)

            if convergence.converged:
                return x_new, convergence

            x = x_new

        # Did not converge within max_iterations
        return x, convergence


class CFLCouplingCondition:
    """
    CFL-like stability condition for partitioned coupling.

    For explicit partitioned coupling, the coupling time step must satisfy
    a stability criterion analogous to the CFL condition.
    """

    @staticmethod
    def compute_critical_coupling_dt(
        interface_mass: float,
        interface_stiffness: float,
        added_mass_factor: float = 1.0
    ) -> float:
        """
        Compute critical coupling time step for stability.

        For fluid-structure interaction, the added mass effect can cause
        instability if the coupling time step is too large.

        dt_crit ≈ 2 * sqrt(m_interface / k_interface) / (1 + added_mass_factor)

        Args:
            interface_mass: effective mass at interface
            interface_stiffness: effective stiffness at interface
            added_mass_factor: ratio of added mass to structural mass

        Returns:
            Critical coupling time step
        """
        if interface_stiffness <= 0:
            return float('inf')  # No stiffness constraint

        omega_n = np.sqrt(interface_stiffness / interface_mass)
        dt_crit = 2.0 / (omega_n * (1.0 + added_mass_factor))

        return dt_crit

    @staticmethod
    def check_stability(
        coupling_dt: float,
        interface_mass: float,
        interface_stiffness: float,
        added_mass_factor: float = 1.0,
        safety_factor: float = 0.8
    ) -> Tuple[bool, float]:
        """
        Check if coupling time step satisfies stability condition.

        Args:
            coupling_dt: actual coupling time step
            interface_mass: effective interface mass
            interface_stiffness: effective interface stiffness
            added_mass_factor: added mass ratio
            safety_factor: safety factor (< 1 for stability margin)

        Returns:
            Tuple of (is_stable, critical_dt)
        """
        dt_crit = CFLCouplingCondition.compute_critical_coupling_dt(
            interface_mass, interface_stiffness, added_mass_factor
        )

        dt_allowed = safety_factor * dt_crit
        is_stable = coupling_dt <= dt_allowed

        return is_stable, dt_crit

    @staticmethod
    def compute_added_mass_ratio(
        fluid_density: float,
        structural_density: float,
        characteristic_length: float
    ) -> float:
        """
        Estimate added mass ratio for FSI stability analysis.

        For simple geometries, added mass ≈ C_m * ρ_fluid * L^3
        where C_m is added mass coefficient.

        Args:
            fluid_density: fluid density
            structural_density: structural density
            characteristic_length: characteristic length scale

        Returns:
            Added mass ratio (added_mass / structural_mass)
        """
        # Simplified estimate: C_m ≈ 1 for flat plate
        C_m = 1.0
        added_mass = C_m * fluid_density * characteristic_length ** 3
        structural_mass = structural_density * characteristic_length ** 3

        return added_mass / structural_mass
