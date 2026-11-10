"""
Heterogeneous time-stepping coordination for multi-solver coupling.

Handles solvers with different time step sizes (e.g., LBM Δt << FEM Δt)
through sub-stepping and temporal interpolation.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple, Optional
import numpy as np


class InterpolationMethod(Enum):
    """Temporal interpolation methods."""
    ZERO_ORDER = "zero_order"  # Piecewise constant (hold)
    LINEAR = "linear"  # Linear interpolation
    CUBIC = "cubic"  # Cubic spline interpolation


@dataclass
class TimeStepConfig:
    """
    Configuration for a solver's time stepping.

    Attributes:
        dt: time step size
        t_start: simulation start time
        t_end: simulation end time
        solver_name: synthetic domain identifier
    """
    dt: float
    t_start: float
    t_end: float
    solver_name: str

    @property
    def n_steps(self) -> int:
        """Number of time steps."""
        return int(np.ceil((self.t_end - self.t_start) / self.dt))

    def time_at_step(self, step: int) -> float:
        """Get simulation time at given step."""
        return self.t_start + step * self.dt


class TimeCoordinator:
    """
    Coordinates time stepping between solvers with different Δt.

    Features:
    - Sub-stepping: fine solver takes multiple steps per coarse solver step
    - Temporal interpolation: aggregate fine solver output for coarse solver
    - Synchronization points: ensure data exchange at consistent times
    """

    def __init__(
        self,
        fine_config: TimeStepConfig,
        coarse_config: TimeStepConfig
    ):
        """
        Args:
            fine_config: time step config for fine solver (small Δt)
            coarse_config: time step config for coarse solver (large Δt)
        """
        if fine_config.dt >= coarse_config.dt:
            raise ValueError(
                f"Fine solver dt ({fine_config.dt}) must be < coarse solver dt ({coarse_config.dt})"
            )

        self.fine = fine_config
        self.coarse = coarse_config
        self._substep_ratio = int(np.round(coarse_config.dt / fine_config.dt))

        # Verify exact ratio
        expected_coarse_dt = self._substep_ratio * fine_config.dt
        if abs(expected_coarse_dt - coarse_config.dt) > 1e-10 * coarse_config.dt:
            raise ValueError(
                f"Coarse dt ({coarse_config.dt}) is not an integer multiple of fine dt ({fine_config.dt})"
            )

    @property
    def substep_ratio(self) -> int:
        """Number of fine steps per coarse step."""
        return self._substep_ratio

    def get_fine_steps_for_coarse_step(self, coarse_step: int) -> List[int]:
        """
        Get fine solver step indices corresponding to a coarse step.

        Args:
            coarse_step: coarse solver step index

        Returns:
            List of fine solver step indices
        """
        start_fine = coarse_step * self._substep_ratio
        return list(range(start_fine, start_fine + self._substep_ratio))

    def interpolate_fine_to_coarse(
        self,
        fine_data: List[np.ndarray],
        fine_times: List[float],
        target_time: float,
        method: InterpolationMethod = InterpolationMethod.LINEAR
    ) -> np.ndarray:
        """
        Interpolate fine solver data to coarse solver time.

        Args:
            fine_data: list of field snapshots from fine solver
            fine_times: corresponding times for each snapshot
            target_time: target time for interpolation
            method: interpolation method

        Returns:
            Interpolated field at target_time
        """
        if len(fine_data) != len(fine_times):
            raise ValueError("fine_data and fine_times must have same length")

        if len(fine_data) == 0:
            raise ValueError("No fine data provided")

        fine_times = np.array(fine_times)
        target_time = float(target_time)

        # Check bounds
        if target_time < fine_times[0] - 1e-10:
            raise ValueError(
                f"Target time {target_time} is before first fine time {fine_times[0]}"
            )
        if target_time > fine_times[-1] + 1e-10:
            # Extrapolation warning - use last value
            return fine_data[-1].copy()

        if method == InterpolationMethod.ZERO_ORDER:
            return self._zero_order_interpolate(fine_data, fine_times, target_time)
        elif method == InterpolationMethod.LINEAR:
            return self._linear_interpolate(fine_data, fine_times, target_time)
        elif method == InterpolationMethod.CUBIC:
            return self._cubic_interpolate(fine_data, fine_times, target_time)
        else:
            raise ValueError(f"Unknown interpolation method: {method}")

    def _zero_order_interpolate(
        self,
        data: List[np.ndarray],
        times: np.ndarray,
        target: float
    ) -> np.ndarray:
        """Zero-order hold: use value at nearest past time."""
        idx = np.searchsorted(times, target, side='right') - 1
        idx = max(0, min(idx, len(data) - 1))
        return data[idx].copy()

    def _linear_interpolate(
        self,
        data: List[np.ndarray],
        times: np.ndarray,
        target: float
    ) -> np.ndarray:
        """Linear interpolation between adjacent time points."""
        idx = np.searchsorted(times, target, side='right') - 1
        idx = max(0, min(idx, len(times) - 2))

        t0, t1 = times[idx], times[idx + 1]
        if abs(t1 - t0) < 1e-14:
            return data[idx].copy()

        alpha = (target - t0) / (t1 - t0)
        alpha = max(0.0, min(1.0, alpha))  # Clamp to [0, 1]

        return (1.0 - alpha) * data[idx] + alpha * data[idx + 1]

    def _cubic_interpolate(
        self,
        data: List[np.ndarray],
        times: np.ndarray,
        target: float
    ) -> np.ndarray:
        """Cubic spline interpolation."""
        if len(data) < 4:
            # Fall back to linear if not enough points
            return self._linear_interpolate(data, times, target)

        # Find interval
        idx = np.searchsorted(times, target, side='right') - 1
        idx = max(1, min(idx, len(times) - 3))

        # Get 4 points for cubic
        t_pts = times[idx - 1:idx + 3]
        d_pts = data[idx - 1:idx + 3]

        # Normalize time to [0, 1]
        t0 = t_pts[1]
        dt = t_pts[2] - t_pts[1]
        if abs(dt) < 1e-14:
            return data[idx].copy()

        s = (target - t0) / dt
        s = max(-1.0, min(2.0, s))  # Clamp

        # Catmull-Rom spline (cubic interpolation)
        # P(s) = 0.5 * [(2*P1) + (-P0+P2)*s + (2*P0-5*P1+4*P2-P3)*s^2 + (-P0+3*P1-3*P2+P3)*s^3]
        P0, P1, P2, P3 = d_pts
        result = (
            2.0 * P1 +
            (-P0 + P2) * s +
            (2.0 * P0 - 5.0 * P1 + 4.0 * P2 - P3) * s * s +
            (-P0 + 3.0 * P1 - 3.0 * P2 + P3) * s * s * s
        ) * 0.5

        return result

    def aggregate_fine_to_coarse(
        self,
        fine_data: List[np.ndarray],
        method: str = "average"
    ) -> np.ndarray:
        """
        Aggregate fine solver data over one coarse time step.

        Args:
            fine_data: list of field snapshots (one per fine step)
            method: 'average', 'max', 'min', or 'sum'

        Returns:
            Aggregated field
        """
        if len(fine_data) == 0:
            raise ValueError("No fine data to aggregate")

        stacked = np.stack(fine_data, axis=0)

        if method == "average":
            return np.mean(stacked, axis=0)
        elif method == "max":
            return np.max(stacked, axis=0)
        elif method == "min":
            return np.min(stacked, axis=0)
        elif method == "sum":
            return np.sum(stacked, axis=0)
        else:
            raise ValueError(f"Unknown aggregation method: {method}")

    def extrapolate_coarse_to_fine(
        self,
        coarse_data_prev: np.ndarray,
        coarse_data_curr: np.ndarray,
        coarse_dt: float,
        fine_substep: int,
        method: str = "linear"
    ) -> np.ndarray:
        """
        Extrapolate coarse solver data to fine solver sub-step.

        Used when coarse solver provides boundary conditions for fine solver.

        Args:
            coarse_data_prev: coarse data at previous time
            coarse_data_curr: coarse data at current time
            coarse_dt: coarse time step
            fine_substep: which fine sub-step (0 to substep_ratio-1)
            method: 'zero_order' or 'linear'

        Returns:
            Extrapolated data for fine solver
        """
        if method == "zero_order":
            return coarse_data_curr.copy()
        elif method == "linear":
            alpha = (fine_substep + 1) / self._substep_ratio
            return (1.0 - alpha) * coarse_data_prev + alpha * coarse_data_curr
        else:
            raise ValueError(f"Unknown extrapolation method: {method}")

    def verify_temporal_accuracy(
        self,
        analytic_signal: callable,
        method: InterpolationMethod,
        n_fine_steps: int = 100
    ) -> dict:
        """
        Verify temporal interpolation accuracy against analytic signal.

        Args:
            analytic_signal: function f(t) -> array, known exact solution
            method: interpolation method to test
            n_fine_steps: number of fine time steps for sampling

        Returns:
            Dictionary with error metrics
        """
        # Sample analytic signal at fine times
        fine_times = np.linspace(self.fine.t_start, self.fine.t_end, n_fine_steps + 1)
        fine_data = [analytic_signal(t) for t in fine_times]

        # Test interpolation at coarse times
        coarse_times = np.linspace(
            self.coarse.t_start + self.coarse.dt * 0.5,
            self.coarse.t_end - self.coarse.dt * 0.5,
            self.coarse.n_steps
        )

        errors = []
        for t_coarse in coarse_times:
            interpolated = self.interpolate_fine_to_coarse(
                fine_data, fine_times.tolist(), t_coarse, method
            )
            exact = analytic_signal(t_coarse)
            error = np.linalg.norm(interpolated - exact) / (np.linalg.norm(exact) + 1e-14)
            errors.append(error)

        return {
            "method": method.value,
            "max_error": float(np.max(errors)),
            "mean_error": float(np.mean(errors)),
            "rms_error": float(np.sqrt(np.mean(np.square(errors))))
        }
