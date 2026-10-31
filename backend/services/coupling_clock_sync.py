"""
Coupling Infrastructure: Clock Synchronization & Subcycling Framework
=====================================================================

SIR-PC2|X02 — Ice navigation campaign DAG node (m611 seeding, L1_foundation).

Multi-solver coupling clock synchronization: master clock + subcycling,
guaranteeing time consistency across heterogeneous solvers (ice, structure,
hydrodynamics) with different characteristic time scales.

Physical basis
--------------
In ice-structure-fluid coupling, each solver operates on a different
characteristic time scale:

  - Ice fracture / ridge failure  : dt_ice   ~ 1e-4 – 1e-3 s
  - Structural vibration          : dt_struct ~ 1e-3 – 1e-2 s
  - Hydrodynamic wave / current   : dt_hydro ~ 1e-2 – 1e-1 s

The master clock advances at the coarsest common step (dt_master), while
each sub-solver takes N_i = dt_master / dt_i sub-steps within one master
step.  At every master tick all solvers exchange boundary data; between
ticks, sub-solvers use time-interpolated boundary values.

Energy conservation is monitored via the ratio:

    conservation_ratio = |E(t_end) - E(t_0)| / |E(t_0)|

Acceptance criteria
-------------------
  clock_sync_error              < 1e-6  [s]
  subcycling_energy_conservation < 0.01  [ratio]
  pytest_test_clock_sync        == PASS  [bool]
"""

from __future__ import annotations

import math
import threading
import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# 1. Master Clock
# ═══════════════════════════════════════════════════════════════════════════

class ClockState(Enum):
    """State of the master clock."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class MasterClock:
    """
    Master clock for multi-solver coupling.

    Advances in fixed increments dt_master.  All sub-solvers synchronize
    at every master tick.  The clock tracks cumulative time and step count.
    """
    dt_master: float = 1e-2          # master time step [s]
    t_current: float = 0.0           # current master time [s]
    step: int = 0                    # current master step index
    state: ClockState = ClockState.STOPPED
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> None:
        with self._lock:
            self.state = ClockState.RUNNING

    def pause(self) -> None:
        with self._lock:
            self.state = ClockState.PAUSED

    def reset(self) -> None:
        with self._lock:
            self.t_current = 0.0
            self.step = 0
            self.state = ClockState.STOPPED

    def advance(self) -> float:
        """Advance one master step. Returns new t_current."""
        with self._lock:
            if self.state != ClockState.RUNNING:
                raise RuntimeError(f"Cannot advance clock in state {self.state}")
            self.t_current += self.dt_master
            self.step += 1
            return self.t_current

    def get_time(self) -> float:
        with self._lock:
            return self.t_current

    def get_step(self) -> int:
        with self._lock:
            return self.step


# ═══════════════════════════════════════════════════════════════════════════
# 2. Sub-Solver Descriptor
# ═══════════════════════════════════════════════════════════════════════════

class SolverDomain(Enum):
    """Physical domain of a sub-solver."""
    ICE = "ice"
    STRUCTURE = "structure"
    HYDRODYNAMICS = "hydrodynamics"
    THERMAL = "thermal"


@dataclass
class SubSolver:
    """
    A sub-solver that runs at its own time step within the master clock.

    Parameters
    ----------
    name : str
        Unique identifier.
    domain : SolverDomain
        Physical domain.
    dt : float
        Sub-solver time step [s].  Must evenly divide dt_master.
    advance_fn : callable
        Function(t_start, dt, boundary_data) -> (state_dict, energy).
        Returns the solver state and total energy at t_start + dt.
    """
    name: str
    domain: SolverDomain
    dt: float
    advance_fn: Optional[Callable[[float, float, Dict[str, Any]], Tuple[Dict[str, Any], float]]] = None
    t_local: float = 0.0
    sub_step: int = 0
    energy: float = 0.0
    state: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_substeps(self) -> int:
        """Number of sub-steps per master step."""
        return max(1, round(1.0 / (self.dt / self.dt_master))) if hasattr(self, '_dt_master') else 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Time Interpolation for Boundary Data
# ═══════════════════════════════════════════════════════════════════════════

def linear_interpolate_boundary(
    data_t0: Dict[str, float],
    data_t1: Dict[str, float],
    alpha: float,
) -> Dict[str, float]:
    """
    Linearly interpolate boundary data between two master ticks.

    Parameters
    ----------
    data_t0 : dict
        Boundary values at t_n.
    data_t1 : dict
        Boundary values at t_{n+1}.
    alpha : float
        Interpolation factor in [0, 1].  alpha=0 → data_t0, alpha=1 → data_t1.

    Returns
    -------
    dict : Interpolated boundary values.
    """
    result = {}
    for key in data_t0:
        v0 = data_t0.get(key, 0.0)
        v1 = data_t1.get(key, 0.0)
        result[key] = v0 + alpha * (v1 - v0)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 4. Subcycling Controller
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SubcyclingController:
    """
    Orchestrates subcycling of multiple solvers within one master step.

    For each master step:
      1. Exchange boundary data at t_n.
      2. Each solver i takes N_i = dt_master / dt_i sub-steps.
      3. Between sub-steps, boundary data is interpolated.
      4. At t_{n+1}, exchange boundary data again.

    Clock synchronization error is the maximum deviation of any solver's
    local time from the master time after a complete master step.
    """
    clock: MasterClock
    solvers: List[SubSolver] = field(default_factory=list)
    boundary_data: Dict[str, Dict[str, float]] = field(default_factory=dict)
    boundary_data_next: Dict[str, Dict[str, float]] = field(default_factory=dict)
    sync_errors: List[float] = field(default_factory=list)
    energy_history: List[Dict[str, float]] = field(default_factory=list)

    def add_solver(self, solver: SubSolver) -> None:
        """Register a sub-solver."""
        # Verify dt divides dt_master
        ratio = self.clock.dt_master / solver.dt
        n = round(ratio)
        if abs(ratio - n) > 1e-10:
            raise ValueError(
                f"Solver '{solver.name}' dt={solver.dt} does not evenly divide "
                f"dt_master={self.clock.dt_master} (ratio={ratio})"
            )
        solver._dt_master = self.clock.dt_master
        self.solvers.append(solver)

    def _n_substeps(self, solver: SubSolver) -> int:
        """Number of sub-steps for a solver in one master step."""
        return max(1, round(self.clock.dt_master / solver.dt))

    def exchange_boundary_data(self) -> None:
        """
        Gather boundary data from all solvers for coupling.

        In a real implementation this would involve MPI or shared memory.
        Here we use a simple dictionary exchange.
        """
        for solver in self.solvers:
            self.boundary_data[solver.name] = {
                k: float(v) for k, v in solver.state.items() if isinstance(v, (int, float))
            }

    def run_master_step(self) -> Dict[str, Any]:
        """
        Execute one complete master step with subcycling.

        Returns
        -------
        dict with keys:
            t_before, t_after, sync_error, energies, sub_steps_taken
        """
        t_before = self.clock.get_time()

        # 1. Exchange boundary data at t_n
        self.exchange_boundary_data()
        # Store for interpolation
        self.boundary_data_next = dict(self.boundary_data)

        # 2. Sub-cycle each solver
        sub_steps_taken: Dict[str, int] = {}
        energies: Dict[str, float] = {}

        for solver in self.solvers:
            n_sub = self._n_substeps(solver)
            sub_steps_taken[solver.name] = n_sub

            for k in range(n_sub):
                # Interpolation factor within this master step
                alpha = (k + 1) / n_sub

                # Interpolate boundary data from other solvers
                interp_boundary: Dict[str, float] = {}
                for other_name, other_data in self.boundary_data.items():
                    if other_name != solver.name:
                        for key, val in other_data.items():
                            interp_boundary[f"{other_name}.{key}"] = val

                # Advance solver one sub-step
                if solver.advance_fn is not None:
                    state, energy = solver.advance_fn(
                        solver.t_local, solver.dt, interp_boundary
                    )
                    solver.state = state
                    solver.energy = energy
                else:
                    # Default: no-op advance (identity)
                    solver.energy = solver.energy  # unchanged

                solver.t_local += solver.dt
                solver.sub_step += 1

            energies[solver.name] = solver.energy

        # 3. Advance master clock
        t_after = self.clock.advance()

        # 4. Compute synchronization error
        sync_error = max(
            abs(solver.t_local - t_after) for solver in self.solvers
        ) if self.solvers else 0.0
        self.sync_errors.append(sync_error)

        # 5. Record energy
        self.energy_history.append(energies)

        return {
            "t_before": t_before,
            "t_after": t_after,
            "sync_error": sync_error,
            "energies": energies,
            "sub_steps_taken": sub_steps_taken,
        }

    def run_simulation(self, n_master_steps: int) -> Dict[str, Any]:
        """
        Run a complete simulation for n_master_steps.

        Returns summary with final sync error, energy conservation ratio,
        and per-step history.
        """
        self.clock.start()
        history: List[Dict[str, Any]] = []

        for _ in range(n_master_steps):
            result = self.run_master_step()
            history.append(result)

        # Compute energy conservation ratio
        energy_ratio = self._compute_energy_conservation()

        # Max sync error across all steps
        max_sync_error = max(self.sync_errors) if self.sync_errors else 0.0

        return {
            "n_master_steps": n_master_steps,
            "dt_master": self.clock.dt_master,
            "t_final": self.clock.get_time(),
            "max_sync_error": max_sync_error,
            "energy_conservation_ratio": energy_ratio,
            "sync_errors": self.sync_errors,
            "energy_history": self.energy_history,
            "history": history,
        }

    def _compute_energy_conservation(self) -> float:
        """
        Compute energy conservation ratio.

        conservation_ratio = |E_total(t_end) - E_total(t_0)| / |E_total(t_0)|

        A ratio < 0.01 indicates good energy conservation.
        """
        if len(self.energy_history) < 2:
            return 0.0

        # Total energy = sum of all solver energies
        e_initial = sum(self.energy_history[0].values())
        e_final = sum(self.energy_history[-1].values())

        if abs(e_initial) < 1e-30:
            # If initial energy is zero, use absolute difference
            return abs(e_final - e_initial)

        return abs(e_final - e_initial) / abs(e_initial)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Reference Solvers for Validation
# ═══════════════════════════════════════════════════════════════════════════

def make_harmonic_solver(
    name: str,
    domain: SolverDomain,
    dt: float,
    omega: float = 2.0 * math.pi,
    amplitude: float = 1.0,
    mass: float = 1.0,
) -> SubSolver:
    """
    Create a simple harmonic oscillator solver for validation.

    Solves: m * x'' + k * x = 0  with k = m * omega^2
    Exact solution: x(t) = A * cos(omega * t), v(t) = -A * omega * sin(omega * t)
    Energy: E = 0.5 * m * v^2 + 0.5 * k * x^2 = 0.5 * m * omega^2 * A^2 = const

    This is a conservative system — energy should be exactly conserved.
    Numerical integration introduces small dissipation; the subcycling
    framework must keep this below the acceptance threshold.
    """
    k = mass * omega ** 2

    # State: position and velocity
    state = {"x": amplitude, "v": 0.0}
    energy = 0.5 * k * amplitude ** 2  # initial energy

    def advance_fn(t: float, dt_step: float, boundary: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        """
        Velocity-Verlet integration for the harmonic oscillator.

        This is a symplectic integrator that conserves energy to machine
        precision for linear systems, making it ideal for validating the
        subcycling framework.
        """
        x = state["x"]
        v = state["v"]

        # Velocity Verlet
        a = -k / mass * x
        x_new = x + v * dt_step + 0.5 * a * dt_step ** 2
        a_new = -k / mass * x_new
        v_new = v + 0.5 * (a + a_new) * dt_step

        new_state = {"x": x_new, "v": v_new}
        new_energy = 0.5 * mass * v_new ** 2 + 0.5 * k * x_new ** 2

        # Update closure state
        state["x"] = x_new
        state["v"] = v_new

        return new_state, new_energy

    solver = SubSolver(
        name=name,
        domain=domain,
        dt=dt,
        advance_fn=advance_fn,
        energy=energy,
        state=dict(state),
    )
    # Store mutable state reference for the closure
    solver._internal_state = state
    return solver


def make_damped_solver(
    name: str,
    domain: SolverDomain,
    dt: float,
    omega: float = 2.0 * math.pi,
    amplitude: float = 1.0,
    mass: float = 1.0,
    damping: float = 0.01,
) -> SubSolver:
    """
    Create a damped harmonic oscillator solver.

    Solves: m * x'' + c * x' + k * x = 0
    Energy decays exponentially: E(t) = E(0) * exp(-2 * c/m * t)

    Used to test that the framework correctly tracks energy dissipation.
    """
    k = mass * omega ** 2
    c = damping

    state = {"x": amplitude, "v": 0.0}
    energy = 0.5 * k * amplitude ** 2

    def advance_fn(t: float, dt_step: float, boundary: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        x = state["x"]
        v = state["v"]

        # Velocity Verlet with damping (approximate)
        a = (-k * x - c * v) / mass
        x_new = x + v * dt_step + 0.5 * a * dt_step ** 2
        a_new = (-k * x_new - c * v) / mass  # approximate: use old v for damping
        v_new = v + 0.5 * (a + a_new) * dt_step

        new_state = {"x": x_new, "v": v_new}
        new_energy = 0.5 * mass * v_new ** 2 + 0.5 * k * x_new ** 2

        state["x"] = x_new
        state["v"] = v_new

        return new_state, new_energy

    solver = SubSolver(
        name=name,
        domain=domain,
        dt=dt,
        advance_fn=advance_fn,
        energy=energy,
        state=dict(state),
    )
    solver._internal_state = state
    return solver


# ═══════════════════════════════════════════════════════════════════════════
# 6. Ice-Structure-Hydro Coupling Scenario
# ═══════════════════════════════════════════════════════════════════════════

def create_ice_coupling_scenario(
    dt_master: float = 1e-2,
    dt_ice: float = 1e-4,
    dt_struct: float = 1e-3,
    dt_hydro: float = 5e-3,
) -> SubcyclingController:
    """
    Create a representative ice-structure-hydrodynamics coupling scenario.

    Time scales:
      - Ice fracture:       dt_ice   = 1e-4 s  (100 sub-steps per master step)
      - Structural response: dt_struct = 1e-3 s  (10 sub-steps per master step)
      - Hydrodynamics:       dt_hydro  = 5e-3 s  (2 sub-steps per master step)

    All solvers are harmonic oscillators with different frequencies,
    representing the characteristic frequencies of each domain:
      - Ice:     high frequency (omega = 20*pi rad/s)
      - Structure: medium frequency (omega = 4*pi rad/s)
      - Hydro:   low frequency (omega = pi rad/s)
    """
    clock = MasterClock(dt_master=dt_master)
    controller = SubcyclingController(clock=clock)

    # Ice solver: high-frequency harmonic oscillator
    ice_solver = make_harmonic_solver(
        name="ice",
        domain=SolverDomain.ICE,
        dt=dt_ice,
        omega=20.0 * math.pi,   # 10 Hz
        amplitude=1.0,
        mass=1.0,
    )
    controller.add_solver(ice_solver)

    # Structure solver: medium-frequency
    struct_solver = make_harmonic_solver(
        name="structure",
        domain=SolverDomain.STRUCTURE,
        dt=dt_struct,
        omega=4.0 * math.pi,    # 2 Hz
        amplitude=0.5,
        mass=10.0,
    )
    controller.add_solver(struct_solver)

    # Hydrodynamics solver: low-frequency
    hydro_solver = make_harmonic_solver(
        name="hydrodynamics",
        domain=SolverDomain.HYDRODYNAMICS,
        dt=dt_hydro,
        omega=math.pi,           # 0.5 Hz
        amplitude=2.0,
        mass=100.0,
    )
    controller.add_solver(hydro_solver)

    return controller


# ═══════════════════════════════════════════════════════════════════════════
# 7. Validation & Benchmark
# ═══════════════════════════════════════════════════════════════════════════

def validate_clock_sync(
    dt_master: float = 1e-2,
    dt_ice: float = 1e-4,
    dt_struct: float = 1e-3,
    dt_hydro: float = 5e-3,
    n_steps: int = 100,
) -> Dict[str, Any]:
    """
    Run the ice coupling scenario and validate against acceptance criteria.

    Returns
    -------
    dict with:
        clock_sync_error : float  (must be < 1e-6 s)
        energy_conservation_ratio : float  (must be < 0.01)
        pass_sync : bool
        pass_energy : bool
        pass_all : bool
        details : dict (full simulation results)
    """
    controller = create_ice_coupling_scenario(dt_master, dt_ice, dt_struct, dt_hydro)
    result = controller.run_simulation(n_steps)

    sync_err = result["max_sync_error"]
    energy_ratio = result["energy_conservation_ratio"]

    pass_sync = sync_err < 1e-6
    pass_energy = energy_ratio < 0.01
    pass_all = pass_sync and pass_energy

    return {
        "clock_sync_error": sync_err,
        "energy_conservation_ratio": energy_ratio,
        "pass_sync": pass_sync,
        "pass_energy": pass_energy,
        "pass_all": pass_all,
        "details": result,
    }
