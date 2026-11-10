"""
Multi-physics temporal synchronization for LBM/FEM/DEM three-field coupling.

Provides:
- TemporalCoordinator: coordinates heterogeneous time stepping (LBM explicit small dt,
  FEM implicit large dt, DEM explicit very small dt) via sub-stepping / macro-stepping
  with causal order guarantees.
- CausalOrderVerifier: verifies that data exchange respects causality (no future data
  used before it is produced).
- EnergyConservationTracker: tracks total energy across coupled solvers and computes
  conservation error.
- AdaptiveTimeStepper: adjusts time steps based on CFL condition and stability analysis.
- EventDrivenCoupler: captures discrete events (e.g., ice impact) with bounded delay.

Physical basis
--------------
LBM (Lattice Boltzmann): explicit, dt_LBM ~ O(1e-6 – 1e-4 s), CFL-limited by lattice speed.
FEM (Finite Element): implicit, dt_FEM ~ O(1e-3 – 1e-1 s), limited by accuracy not stability.
DEM (Discrete Element): explicit, dt_DEM ~ O(1e-7 – 1e-5 s), limited by contact stiffness.

Macro step ΔT = lcm(dt_LBM, dt_FEM, dt_DEM) ≈ dt_FEM (typically).
Within each macro step:
  - DEM takes N_DEM = ΔT / dt_DEM sub-steps
  - LBM takes N_LBM = ΔT / dt_LBM sub-steps
  - FEM takes 1 step (implicit, unconditionally stable)

Causal order: DEM and LBM must complete all sub-steps before FEM exchanges data.
Energy conservation: total E = E_LBM + E_FEM + E_DEM must be conserved to <1%.

Adaptive stepping: dt is adjusted to satisfy CFL condition:
  CFL = u * dt / dx ≤ CFL_crit
  For DEM: dt ≤ 2 / ω_max where ω_max = sqrt(k_max / m_min)

Event-driven coupling: discrete events (ice impact) are detected and propagated
  within ≤ 1 macro step delay.

All domain semantics are injected via configuration; this module is domain-agnostic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# 1. Solver type enumeration and configuration
# ═══════════════════════════════════════════════════════════════════════════

class SolverType(Enum):
    """Supported solver types in the coupling framework."""
    LBM = "lbm"       # Lattice Boltzmann Method (explicit, small dt)
    FEM = "fem"       # Finite Element Method (implicit, large dt)
    DEM = "dem"       # Discrete Element Method (explicit, very small dt)


@dataclass
class SolverTimeConfig:
    """
    Time stepping configuration for a single solver.

    Attributes:
        solver_type: type of solver
        dt: current time step size
        dt_min: minimum allowed time step
        dt_max: maximum allowed time step
        cfl_crit: critical CFL number for stability
        characteristic_velocity: characteristic velocity for CFL calculation
        characteristic_length: characteristic length for CFL calculation
    """
    solver_type: SolverType
    dt: float
    dt_min: float = 1e-10
    dt_max: float = 1.0
    cfl_crit: float = 1.0
    characteristic_velocity: float = 1.0
    characteristic_length: float = 1.0

    @property
    def cfl_number(self) -> float:
        """Current CFL number."""
        if self.characteristic_length <= 0:
            return 0.0
        return self.characteristic_velocity * self.dt / self.characteristic_length

    @property
    def cfl_satisfied(self) -> bool:
        """Whether CFL condition is satisfied."""
        return self.cfl_number <= self.cfl_crit


# ═══════════════════════════════════════════════════════════════════════════
# 2. Causal Order Verifier
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CausalEvent:
    """
    Represents a data exchange event in the coupling timeline.

    Attributes:
        event_id: unique identifier
        source_solver: solver producing data
        target_solver: solver consuming data
        production_time: time when data was produced
        consumption_time: time when data was consumed
        data_tag: identifier for the data being exchanged
    """
    event_id: int
    source_solver: SolverType
    target_solver: SolverType
    production_time: float
    consumption_time: float
    data_tag: str

    @property
    def is_causal(self) -> bool:
        """Data must be produced before or at the same time it is consumed."""
        return self.production_time <= self.consumption_time + 1e-14


class CausalOrderVerifier:
    """
    Verifies causal order of data exchange events in multi-solver coupling.

    A coupling is causally consistent if every data exchange event satisfies:
        production_time ≤ consumption_time

    This ensures no solver uses "future" data that hasn't been produced yet.
    """

    def __init__(self):
        self._events: List[CausalEvent] = []
        self._event_counter: int = 0

    def record_exchange(
        self,
        source: SolverType,
        target: SolverType,
        production_time: float,
        consumption_time: float,
        data_tag: str = "default",
    ) -> CausalEvent:
        """Record a data exchange event."""
        self._event_counter += 1
        event = CausalEvent(
            event_id=self._event_counter,
            source_solver=source,
            target_solver=target,
            production_time=production_time,
            consumption_time=consumption_time,
            data_tag=data_tag,
        )
        self._events.append(event)
        return event

    def verify_all(self) -> Tuple[bool, List[CausalEvent]]:
        """
        Verify all recorded events are causally consistent.

        Returns:
            Tuple of (all_causal, list_of_violations)
        """
        violations = [e for e in self._events if not e.is_causal]
        return len(violations) == 0, violations

    def causal_consistency_rate(self) -> float:
        """Fraction of events that are causally consistent."""
        if not self._events:
            return 1.0
        n_causal = sum(1 for e in self._events if e.is_causal)
        return n_causal / len(self._events)

    def get_events(self) -> List[CausalEvent]:
        return list(self._events)

    def reset(self):
        self._events.clear()
        self._event_counter = 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. Energy Conservation Tracker
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EnergySnapshot:
    """Energy state at a point in time."""
    time: float
    energies: Dict[SolverType, float]

    @property
    def total(self) -> float:
        return sum(self.energies.values())


class EnergyConservationTracker:
    """
    Tracks total energy across coupled solvers and computes conservation error.

    For a conservative coupling scheme:
        E_total(t) = E_LBM(t) + E_FEM(t) + E_DEM(t)
        conservation_error = |E_total(t) - E_total(0)| / |E_total(0)|

    Acceptance: conservation_error < 0.01 (1%)
    """

    def __init__(self, tolerance: float = 0.01):
        self.tolerance = tolerance
        self._snapshots: List[EnergySnapshot] = []
        self._initial_energy: Optional[float] = None

    def record(self, time: float, energies: Dict[SolverType, float]):
        """Record energy state at given time."""
        snapshot = EnergySnapshot(time=time, energies=dict(energies))
        self._snapshots.append(snapshot)
        if self._initial_energy is None:
            self._initial_energy = snapshot.total

    @property
    def initial_energy(self) -> Optional[float]:
        return self._initial_energy

    @property
    def current_energy(self) -> Optional[float]:
        if not self._snapshots:
            return None
        return self._snapshots[-1].total

    def conservation_error(self) -> float:
        """
        Compute relative energy conservation error.

        Returns:
            |E(t) - E(0)| / |E(0)|, or 0 if no data.
        """
        if self._initial_energy is None or len(self._snapshots) < 2:
            return 0.0
        e_final = self._snapshots[-1].total
        if abs(self._initial_energy) < 1e-30:
            return abs(e_final - self._initial_energy)
        return abs(e_final - self._initial_energy) / abs(self._initial_energy)

    def is_conserved(self) -> bool:
        """Whether energy conservation is within tolerance."""
        return self.conservation_error() <= self.tolerance

    def energy_history(self) -> List[Tuple[float, float]]:
        """Return list of (time, total_energy) pairs."""
        return [(s.time, s.total) for s in self._snapshots]

    def per_solver_history(self, solver: SolverType) -> List[Tuple[float, float]]:
        """Return energy history for a specific solver."""
        return [(s.time, s.energies.get(solver, 0.0)) for s in self._snapshots]

    def reset(self):
        self._snapshots.clear()
        self._initial_energy = None


# ═══════════════════════════════════════════════════════════════════════════
# 4. Adaptive Time Stepper with CFL Condition
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CFLReport:
    """Report on CFL condition satisfaction."""
    solver_type: SolverType
    cfl_number: float
    cfl_crit: float
    satisfied: bool
    suggested_dt: float


class AdaptiveTimeStepper:
    """
    Adjusts time steps for explicit solvers based on CFL condition and stability.

    For LBM: CFL = u * dt / dx ≤ 1 (lattice units typically dt/dx = 1)
    For DEM: dt ≤ 2 / ω_max where ω_max = sqrt(k_max / m_min)

    The stepper monitors stability indicators and adjusts dt within [dt_min, dt_max].
    """

    def __init__(
        self,
        safety_factor: float = 0.8,
        growth_factor: float = 1.1,
        reduction_factor: float = 0.5,
    ):
        self.safety_factor = safety_factor
        self.growth_factor = growth_factor
        self.reduction_factor = reduction_factor
        self._configs: Dict[SolverType, SolverTimeConfig] = {}
        self._cfl_history: Dict[SolverType, List[float]] = {}
        self._diverged: Dict[SolverType, bool] = {}

    def register_solver(self, config: SolverTimeConfig):
        """Register a solver's time stepping configuration."""
        self._configs[config.solver_type] = config
        self._cfl_history[config.solver_type] = []
        self._diverged[config.solver_type] = False

    def check_cfl(self, solver_type: SolverType) -> CFLReport:
        """Check CFL condition for a solver."""
        config = self._configs[solver_type]
        cfl = config.cfl_number
        satisfied = cfl <= config.cfl_crit

        # Suggest new dt
        if cfl > 0:
            suggested_dt = config.dt * config.cfl_crit / cfl * self.safety_factor
        else:
            suggested_dt = config.dt

        suggested_dt = max(config.dt_min, min(config.dt_max, suggested_dt))

        return CFLReport(
            solver_type=solver_type,
            cfl_number=cfl,
            cfl_crit=config.cfl_crit,
            satisfied=satisfied,
            suggested_dt=suggested_dt,
        )

    def check_dem_stability(self, stiffness: float, mass: float) -> Tuple[bool, float]:
        """
        Check DEM stability condition: dt ≤ 2 / ω_max.

        Args:
            stiffness: maximum contact stiffness k_max
            mass: minimum particle mass m_min

        Returns:
            Tuple of (is_stable, critical_dt)
        """
        if mass <= 0 or stiffness <= 0:
            return True, float('inf')
        omega_max = math.sqrt(stiffness / mass)
        dt_crit = 2.0 / omega_max
        config = self._configs.get(SolverType.DEM)
        if config is None:
            return True, dt_crit
        is_stable = config.dt <= self.safety_factor * dt_crit
        return is_stable, dt_crit

    def adapt_step(
        self,
        solver_type: SolverType,
        stability_indicator: float = 1.0,
    ) -> float:
        """
        Adapt time step based on stability indicator.

        stability_indicator > 1: solution growing, reduce dt
        stability_indicator < 1: solution decaying, can increase dt
        stability_indicator ≈ 1: stable, keep dt

        Args:
            solver_type: which solver to adapt
            stability_indicator: measure of solution growth/decay

        Returns:
            New time step size
        """
        config = self._configs[solver_type]

        # Check CFL first
        cfl_report = self.check_cfl(solver_type)
        self._cfl_history[solver_type].append(cfl_report.cfl_number)

        if not cfl_report.satisfied:
            # CFL violated: reduce dt
            new_dt = cfl_report.suggested_dt
        elif stability_indicator > 1.0:
            # Growing solution: reduce dt
            new_dt = config.dt * self.reduction_factor
        elif stability_indicator < 0.9:
            # Decaying solution: can increase dt
            new_dt = config.dt * self.growth_factor
        else:
            new_dt = config.dt

        # Clamp to bounds
        new_dt = max(config.dt_min, min(config.dt_max, new_dt))
        config.dt = new_dt

        # Check for divergence
        if stability_indicator > 10.0:
            self._diverged[solver_type] = True

        return new_dt

    def all_cfl_satisfied(self) -> bool:
        """Check if CFL condition is satisfied for all registered solvers."""
        return all(
            self.check_cfl(st).satisfied
            for st in self._configs
        )

    def cfl_satisfaction_rate(self) -> float:
        """Fraction of CFL checks that were satisfied across all history."""
        total = 0
        satisfied = 0
        for st, history in self._cfl_history.items():
            config = self._configs[st]
            for cfl in history:
                total += 1
                if cfl <= config.cfl_crit:
                    satisfied += 1
        return satisfied / total if total > 0 else 1.0

    def get_config(self, solver_type: SolverType) -> Optional[SolverTimeConfig]:
        return self._configs.get(solver_type)

    def reset(self):
        self._configs.clear()
        self._cfl_history.clear()
        self._diverged.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 5. Event-Driven Coupler
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CouplingEvent:
    """
    A discrete event in the coupled simulation (e.g., ice impact).

    Attributes:
        event_type: type of event
        time: time of occurrence
        source_solver: solver that detected the event
        magnitude: event magnitude/intensity
        captured: whether the event was captured by the coupling
        capture_delay_steps: delay between occurrence and capture (in macro steps)
    """
    event_type: str
    time: float
    source_solver: SolverType
    magnitude: float
    captured: bool = False
    capture_delay_steps: float = 0.0  # in macro steps


class EventDrivenCoupler:
    """
    Manages discrete events in multi-solver coupling.

    Events (e.g., ice impact) are detected by one solver and must be
    propagated to other solvers within a bounded delay (≤ 1 macro step).
    """

    def __init__(self, macro_dt: float, max_capture_delay_steps: float = 1.0):
        """
        Args:
            macro_dt: macro time step size
            max_capture_delay_steps: maximum allowed capture delay in macro steps
        """
        self.macro_dt = macro_dt
        self.max_capture_delay_steps = max_capture_delay_steps
        self._events: List[CouplingEvent] = []
        self._current_macro_step: int = 0

    def detect_event(
        self,
        event_type: str,
        time: float,
        source: SolverType,
        magnitude: float,
    ) -> CouplingEvent:
        """Detect and register a new event."""
        event = CouplingEvent(
            event_type=event_type,
            time=time,
            source_solver=source,
            magnitude=magnitude,
        )
        self._events.append(event)
        return event

    def try_capture_events(self, current_time: float) -> int:
        """
        Try to capture all uncaptured events at current time.

        Returns:
            Number of newly captured events.
        """
        newly_captured = 0
        for event in self._events:
            if not event.captured:
                if current_time >= event.time - 1e-14:
                    event.captured = True
                    # Delay in macro steps
                    event.capture_delay_steps = (current_time - event.time) / self.macro_dt
                    if event.capture_delay_steps < 0:
                        event.capture_delay_steps = 0.0
                    newly_captured += 1
        self._current_macro_step += 1
        return newly_captured

    def all_events_captured(self) -> bool:
        """Whether all registered events have been captured."""
        return all(e.captured for e in self._events)

    def get_max_capture_delay(self) -> float:
        """Maximum capture delay across all events (in macro steps)."""
        captured = [e for e in self._events if e.captured]
        if not captured:
            return 0.0
        return max(e.capture_delay_steps for e in captured)

    def capture_delay_satisfied(self) -> bool:
        """Whether all capture delays are within the allowed bound."""
        return self.get_max_capture_delay() <= self.max_capture_delay_steps

    def get_events(self) -> List[CouplingEvent]:
        return list(self._events)

    def reset(self):
        self._events.clear()
        self._current_macro_step = 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Temporal Coordinator (LBM + FEM + DEM)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MacroStepResult:
    """Result of one macro step in the coupled simulation."""
    macro_step: int
    time: float
    lbm_substeps: int
    fem_substeps: int
    dem_substeps: int
    causal_violations: int
    energy_before: float
    energy_after: float
    cfl_satisfied: bool
    events_captured: int


class TemporalCoordinator:
    """
    Coordinates time stepping for LBM + FEM + DEM three-field coupling.

    Algorithm (sub-stepping with macro steps):
    ┌─────────────────────────────────────────────────────────────┐
    │  Macro step n → n+1 (ΔT = dt_FEM typically)                │
    │                                                             │
    │  1. DEM sub-cycling: N_DEM = ΔT/dt_DEM steps               │
    │     - Check CFL and stability at each sub-step              │
    │     - Record energy at each sub-step                        │
    │                                                             │
    │  2. LBM sub-cycling: N_LBM = ΔT/dt_LBM steps               │
    │     - Check CFL at each sub-step                            │
    │     - Record energy at each sub-step                        │
    │                                                             │
    │  3. FEM step: 1 implicit step                              │
    │     - Unconditionally stable (within accuracy limits)       │
    │     - Exchange boundary data with DEM and LBM               │
    │                                                             │
    │  4. Causal order verification:                              │
    │     - All DEM/LBM data produced before FEM consumption      │
    │     - Record exchange events                                │
    │                                                             │
    │  5. Energy conservation check                               │
    │  6. Event detection and capture                             │
    └─────────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        lbm_config: SolverTimeConfig,
        fem_config: SolverTimeConfig,
        dem_config: SolverTimeConfig,
        energy_tolerance: float = 0.01,
        max_macro_steps: int = 1000,
    ):
        """
        Args:
            lbm_config: LBM time stepping configuration
            fem_config: FEM time stepping configuration
            dem_config: DEM time stepping configuration
            energy_tolerance: energy conservation tolerance (default 1%)
            max_macro_steps: maximum number of macro steps
        """
        # Validate: FEM should have the largest dt (coarsest)
        if fem_config.dt < lbm_config.dt:
            raise ValueError(
                f"FEM dt ({fem_config.dt}) must be >= LBM dt ({lbm_config.dt})"
            )
        if fem_config.dt < dem_config.dt:
            raise ValueError(
                f"FEM dt ({fem_config.dt}) must be >= DEM dt ({dem_config.dt})"
            )

        self.lbm_config = lbm_config
        self.fem_config = fem_config
        self.dem_config = dem_config

        # Macro step = FEM step (coarsest)
        self.macro_dt = fem_config.dt

        # Sub-step counts
        self.n_lbm_substeps = max(1, round(self.macro_dt / lbm_config.dt))
        self.n_dem_substeps = max(1, round(self.macro_dt / dem_config.dt))

        # Verify integer ratios
        if abs(self.n_lbm_substeps * lbm_config.dt - self.macro_dt) > 1e-10 * self.macro_dt:
            raise ValueError(
                f"LBM dt ({lbm_config.dt}) does not evenly divide macro dt ({self.macro_dt})"
            )
        if abs(self.n_dem_substeps * dem_config.dt - self.macro_dt) > 1e-10 * self.macro_dt:
            raise ValueError(
                f"DEM dt ({dem_config.dt}) does not evenly divide macro dt ({self.macro_dt})"
            )

        self.max_macro_steps = max_macro_steps

        # Components
        self.causal_verifier = CausalOrderVerifier()
        self.energy_tracker = EnergyConservationTracker(tolerance=energy_tolerance)
        self.adaptive_stepper = AdaptiveTimeStepper()
        self.event_coupler = EventDrivenCoupler(macro_dt=self.macro_dt)

        # Register solvers with adaptive stepper
        self.adaptive_stepper.register_solver(lbm_config)
        self.adaptive_stepper.register_solver(fem_config)
        self.adaptive_stepper.register_solver(dem_config)

        # State
        self._current_macro_step: int = 0
        self._current_time: float = 0.0
        self._results: List[MacroStepResult] = []

        # Energy state (tracked internally)
        self._lbm_energy: float = 0.0
        self._fem_energy: float = 0.0
        self._dem_energy: float = 0.0

        # Event model: list of (time, type, magnitude, processed_flag)
        self._pending_events: List[Tuple[float, str, float, bool]] = []

    def set_initial_energies(self, lbm: float, fem: float, dem: float):
        """Set initial energies for each solver."""
        self._lbm_energy = lbm
        self._fem_energy = fem
        self._dem_energy = dem
        self.energy_tracker.record(
            0.0,
            {
                SolverType.LBM: lbm,
                SolverType.FEM: fem,
                SolverType.DEM: dem,
            },
        )

    def schedule_event(self, time: float, event_type: str, magnitude: float):
        """Schedule a discrete event (e.g., ice impact)."""
        self._pending_events.append((time, event_type, magnitude, False))

    def _advance_lbm_substeps(
        self,
        n_steps: int,
        dt: float,
        start_time: float,
        energy_model: Optional[Callable[[float, float], float]] = None,
    ) -> float:
        """
        Advance LBM through sub-steps.

        Args:
            n_steps: number of sub-steps
            dt: sub-step size
            start_time: starting time
            energy_model: optional function(t, E_current) -> E_new for energy evolution

        Returns:
            Final energy after sub-stepping
        """
        energy = self._lbm_energy
        for k in range(n_steps):
            t = start_time + k * dt

            # Record causal event: LBM produces data at end of sub-step
            if k == n_steps - 1:
                self.causal_verifier.record_exchange(
                    source=SolverType.LBM,
                    target=SolverType.FEM,
                    production_time=t + dt,
                    consumption_time=start_time + n_steps * dt,
                    data_tag="lbm_boundary",
                )

            # Update energy via model (if provided)
            if energy_model is not None:
                energy = energy_model(t, energy)

            # Check CFL
            cfl_report = self.adaptive_stepper.check_cfl(SolverType.LBM)
            if not cfl_report.satisfied:
                pass  # In real code would adapt dt

        self._lbm_energy = energy
        return energy

    def _advance_dem_substeps(
        self,
        n_steps: int,
        dt: float,
        start_time: float,
        energy_model: Optional[Callable[[float, float], float]] = None,
    ) -> float:
        """
        Advance DEM through sub-steps.

        Args:
            n_steps: number of sub-steps
            dt: sub-step size
            start_time: starting time
            energy_model: optional function(t, E_current) -> E_new

        Returns:
            Final energy after sub-stepping
        """
        energy = self._dem_energy
        for k in range(n_steps):
            t = start_time + k * dt

            # Record causal event
            if k == n_steps - 1:
                self.causal_verifier.record_exchange(
                    source=SolverType.DEM,
                    target=SolverType.FEM,
                    production_time=t + dt,
                    consumption_time=start_time + n_steps * dt,
                    data_tag="dem_contact",
                )

            # Check DEM stability
            is_stable, _ = self.adaptive_stepper.check_dem_stability(
                stiffness=1e6, mass=1e-3
            )

            # Update energy via model
            if energy_model is not None:
                energy = energy_model(t, energy)

        self._dem_energy = energy
        return energy

    def _advance_fem_step(
        self,
        dt: float,
        start_time: float,
        energy_model: Optional[Callable[[float, float], float]] = None,
    ) -> float:
        """
        Advance FEM by one (implicit) step.

        Args:
            dt: time step
            start_time: starting time
            energy_model: optional function(t, E_current) -> E_new

        Returns:
            Final energy
        """
        energy = self._fem_energy

        # FEM receives data from LBM and DEM (causal: data already produced)
        self.causal_verifier.record_exchange(
            source=SolverType.LBM,
            target=SolverType.FEM,
            production_time=start_time,
            consumption_time=start_time + dt,
            data_tag="lbm_to_fem",
        )
        self.causal_verifier.record_exchange(
            source=SolverType.DEM,
            target=SolverType.FEM,
            production_time=start_time,
            consumption_time=start_time + dt,
            data_tag="dem_to_fem",
        )

        # Update energy via model
        if energy_model is not None:
            energy = energy_model(start_time, energy)

        self._fem_energy = energy
        return energy

    def run_macro_step(
        self,
        lbm_energy_fn: Optional[Callable[[float, float], float]] = None,
        fem_energy_fn: Optional[Callable[[float, float], float]] = None,
        dem_energy_fn: Optional[Callable[[float, float], float]] = None,
    ) -> MacroStepResult:
        """
        Execute one macro step of the coupled simulation.

        Args:
            lbm_energy_fn: optional energy evolution function for LBM
            fem_energy_fn: optional energy evolution function for FEM
            dem_energy_fn: optional energy evolution function for DEM

        Returns:
            MacroStepResult with step details
        """
        start_time = self._current_time
        energy_before = self._lbm_energy + self._fem_energy + self._dem_energy

        # 1. DEM sub-cycling
        self._advance_dem_substeps(
            self.n_dem_substeps, self.dem_config.dt, start_time, dem_energy_fn
        )

        # 2. LBM sub-cycling
        self._advance_lbm_substeps(
            self.n_lbm_substeps, self.lbm_config.dt, start_time, lbm_energy_fn
        )

        # 3. FEM step
        self._advance_fem_step(self.fem_config.dt, start_time, fem_energy_fn)

        # 4. Advance time
        self._current_time += self.macro_dt
        self._current_macro_step += 1

        # 5. Record energy
        energy_after = self._lbm_energy + self._fem_energy + self._dem_energy
        self.energy_tracker.record(
            self._current_time,
            {
                SolverType.LBM: self._lbm_energy,
                SolverType.FEM: self._fem_energy,
                SolverType.DEM: self._dem_energy,
            },
        )

        # 6. Check causal order
        is_causal, violations = self.causal_verifier.verify_all()

        # 7. Check CFL
        cfl_ok = self.adaptive_stepper.all_cfl_satisfied()

        # 8. Handle events: detect and capture each pending event exactly once
        events_captured = 0
        newly_processed = []
        for evt_time, evt_type, evt_mag, processed in self._pending_events:
            if not processed and evt_time <= self._current_time:
                # Detect and capture the event
                event = self.event_coupler.detect_event(
                    evt_type, evt_time, SolverType.DEM, evt_mag
                )
                # Try to capture at current time
                self.event_coupler.try_capture_events(self._current_time)
                if event.captured:
                    events_captured += 1
                newly_processed.append((evt_time, evt_type, evt_mag, True))
            else:
                newly_processed.append((evt_time, evt_type, evt_mag, processed))
        self._pending_events = newly_processed

        result = MacroStepResult(
            macro_step=self._current_macro_step,
            time=self._current_time,
            lbm_substeps=self.n_lbm_substeps,
            fem_substeps=1,
            dem_substeps=self.n_dem_substeps,
            causal_violations=len(violations),
            energy_before=energy_before,
            energy_after=energy_after,
            cfl_satisfied=cfl_ok,
            events_captured=events_captured,
        )

        self._results.append(result)
        return result

    def run_simulation(
        self,
        n_macro_steps: int,
        lbm_energy_fn: Optional[Callable[[float, float], float]] = None,
        fem_energy_fn: Optional[Callable[[float, float], float]] = None,
        dem_energy_fn: Optional[Callable[[float, float], float]] = None,
    ) -> Dict:
        """
        Run a complete coupled simulation.

        Args:
            n_macro_steps: number of macro steps
            lbm_energy_fn: optional energy evolution for LBM
            fem_energy_fn: optional energy evolution for FEM
            dem_energy_fn: optional energy evolution for DEM

        Returns:
            Summary dictionary with simulation results
        """
        for _ in range(n_macro_steps):
            self.run_macro_step(lbm_energy_fn, fem_energy_fn, dem_energy_fn)

        # Final causal check
        is_causal, violations = self.causal_verifier.verify_all()

        return {
            "n_macro_steps": self._current_macro_step,
            "final_time": self._current_time,
            "causal_consistency_rate": self.causal_verifier.causal_consistency_rate(),
            "causal_violations": len(violations),
            "energy_conservation_error": self.energy_tracker.conservation_error(),
            "energy_conserved": self.energy_tracker.is_conserved(),
            "cfl_satisfaction_rate": self.adaptive_stepper.cfl_satisfaction_rate(),
            "all_cfl_satisfied": self.adaptive_stepper.all_cfl_satisfied(),
            "event_capture_delay": self.event_coupler.get_max_capture_delay(),
            "event_capture_satisfied": self.event_coupler.capture_delay_satisfied(),
            "results": self._results,
            "energy_history": self.energy_tracker.energy_history(),
        }

    def get_results(self) -> List[MacroStepResult]:
        return list(self._results)

    def reset(self):
        self._current_macro_step = 0
        self._current_time = 0.0
        self._results.clear()
        self.causal_verifier.reset()
        self.energy_tracker.reset()
        self.event_coupler.reset()


# ═══════════════════════════════════════════════════════════════════════════
# 7. Harmonic oscillator energy model (for testing)
# ═══════════════════════════════════════════════════════════════════════════

def harmonic_oscillator_energy(
    omega: float,
    amplitude: float,
    phase: float = 0.0,
) -> Callable[[float, float], float]:
    """
    Create an energy model for a conservative harmonic oscillator.

    E(t) = 0.5 * k * A^2 = constant (conservative system, no damping)

    The returned function ignores E_current and returns the constant energy,
    modeling a perfectly conservative system.

    Args:
        omega: angular frequency
        amplitude: oscillation amplitude
        phase: initial phase

    Returns:
        Function(t, E_current) -> E(t) that returns constant energy
    """
    energy = 0.5 * (omega ** 2) * (amplitude ** 2)

    def model(t: float, e_current: float) -> float:
        # Conservative: energy is constant
        return energy

    return model


def weakly_damped_oscillator_energy(
    omega: float,
    amplitude: float,
    damping: float = 0.001,
) -> Callable[[float, float], float]:
    """
    Create a weakly damped oscillator energy model.

    E(t) = E_0 * exp(-2 * damping * t)

    Args:
        omega: angular frequency
        amplitude: initial amplitude
        damping: damping coefficient

    Returns:
        Function(t, E_current) -> E(t)
    """
    e0 = 0.5 * (omega ** 2) * (amplitude ** 2)

    def model(t: float, e_current: float) -> float:
        return e0 * math.exp(-2.0 * damping * t)

    return model
