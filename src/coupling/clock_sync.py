"""
Clock synchronization for multi-solver coupling.

Implements two synchronization semantics:
1. Conservative synchronization: slow solver dictates pace (barrier sync)
2. Optimistic synchronization: fast solver proceeds ahead with rollback capability

All timing is synthetic (no domain-specific semantics).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum
import numpy as np


class SyncMode(Enum):
    """Synchronization mode."""
    CONSERVATIVE = "conservative"  # Slow solver dictates pace
    OPTIMISTIC = "optimistic"      # Fast solver proceeds with rollback


@dataclass
class SolverClock:
    """
    Represents a solver's local simulation clock.

    Attributes:
        solver_id: unique identifier for the solver
        current_time: current simulation time
        dt: time step size
        max_time: maximum simulation time
        step_count: number of steps taken
    """
    solver_id: str
    current_time: float = 0.0
    dt: float = 0.01
    max_time: float = 1.0
    step_count: int = 0

    @property
    def remaining_steps(self) -> int:
        """Estimated remaining steps."""
        if self.dt <= 0:
            return 0
        remaining = (self.max_time - self.current_time) / self.dt
        if remaining < 1e-10:
            return 0
        return max(0, int(np.ceil(remaining)))

    @property
    def is_finished(self) -> bool:
        """Whether the solver has reached max_time."""
        return self.current_time >= self.max_time - 1e-14

    def step(self) -> float:
        """Advance clock by one step. Returns new time."""
        if self.is_finished:
            return self.current_time
        self.current_time += self.dt
        self.step_count += 1
        return self.current_time

    def reset(self):
        """Reset clock to initial state."""
        self.current_time = 0.0
        self.step_count = 0


@dataclass
class SyncState:
    """
    State of the synchronization manager.

    Attributes:
        mode: synchronization mode
        global_time: current global synchronized time
        n_rollbacks: number of rollbacks performed (optimistic mode)
        n_barriers: number of barrier synchronizations (conservative mode)
        solver_states: per-solver state snapshots
    """
    mode: SyncMode
    global_time: float = 0.0
    n_rollbacks: int = 0
    n_barriers: int = 0
    solver_states: Dict[str, dict] = field(default_factory=dict)

    def snapshot_solver(self, clock: SolverClock, state_data: Optional[dict] = None):
        """Save a snapshot of a solver's state for potential rollback."""
        self.solver_states[clock.solver_id] = {
            "current_time": clock.current_time,
            "step_count": clock.step_count,
            "state_data": state_data,
        }

    def rollback_solver(self, clock: SolverClock, target_time: float) -> bool:
        """
        Rollback a solver to a previous state.

        Args:
            clock: solver clock to rollback
            target_time: time to rollback to

        Returns:
            True if rollback was successful
        """
        if clock.solver_id not in self.solver_states:
            return False

        snapshot = self.solver_states[clock.solver_id]
        if snapshot["current_time"] <= target_time:
            # Already at or before target time
            return True

        clock.current_time = target_time
        clock.step_count = snapshot["step_count"]
        self.n_rollbacks += 1
        return True


@dataclass
class SyncResult:
    """
    Result of a synchronization step.

    Attributes:
        mode: synchronization mode used
        global_time: synchronized global time after sync
        solver_times: dict of solver_id -> synchronized time
        n_rollbacks: rollbacks performed in this step
        is_barrier: whether a barrier was hit
        all_solvers_synced: whether all solvers are at the same time
    """
    mode: SyncMode
    global_time: float
    solver_times: Dict[str, float]
    n_rollbacks: int
    is_barrier: bool
    all_solvers_synced: bool

    def summary(self) -> dict:
        return {
            "mode": self.mode.value,
            "global_time": self.global_time,
            "solver_times": self.solver_times,
            "n_rollbacks": self.n_rollbacks,
            "is_barrier": self.is_barrier,
            "all_solvers_synced": self.all_solvers_synced,
        }


class ClockSynchronizer:
    """
    Manages clock synchronization between coupled solvers.

    Conservative mode:
    - All solvers wait at barrier until the slowest catches up
    - No rollbacks needed
    - Guaranteed causality but potentially slow

    Optimistic mode:
    - Fast solvers proceed ahead speculatively
    - When slow solver catches up, fast solvers may need to rollback
    - Faster overall but requires state saving and rollback capability
    """

    def __init__(
        self,
        mode: SyncMode = SyncMode.CONSERVATIVE,
        max_rollbacks: int = 10,
        checkpoint_interval: int = 5
    ):
        """
        Args:
            mode: synchronization mode
            max_rollbacks: maximum allowed rollbacks before forcing conservative
            checkpoint_interval: how often to save state snapshots (optimistic mode)
        """
        self.mode = mode
        self.max_rollbacks = max_rollbacks
        self.checkpoint_interval = checkpoint_interval
        self._clocks: Dict[str, SolverClock] = {}
        self._sync_state = SyncState(mode=mode)
        self._sync_history: List[dict] = []
        self._total_rollbacks = 0

    def register_solver(self, clock: SolverClock):
        """
        Register a solver's clock with the synchronizer.

        Args:
            clock: solver clock to register
        """
        self._clocks[clock.solver_id] = clock

    def synchronize(self) -> SyncResult:
        """
        Perform one synchronization step.

        Returns:
            SyncResult with synchronization details
        """
        if not self._clocks:
            raise ValueError("No solvers registered")

        if self.mode == SyncMode.CONSERVATIVE:
            return self._conservative_sync()
        else:
            return self._optimistic_sync()

    def _conservative_sync(self) -> SyncResult:
        """
        Conservative synchronization: barrier at slowest solver's time.

        All solvers advance to the minimum time among all solvers,
        then wait at a barrier until all have caught up.
        """
        # Find the slowest solver (minimum current time)
        min_time = min(c.current_time for c in self._clocks.values())

        # Advance all solvers that are behind
        solver_times = {}
        for sid, clock in self._clocks.items():
            if clock.current_time < min_time - 1e-14:
                # This shouldn't happen in conservative mode, but handle it
                clock.current_time = min_time
            solver_times[sid] = clock.current_time

            # Advance each solver by one step if not finished
            if not clock.is_finished:
                clock.step()
                solver_times[sid] = clock.current_time

        # Barrier: global time is the minimum of all solver times
        global_time = min(solver_times.values())
        self._sync_state.global_time = global_time
        self._sync_state.n_barriers += 1

        all_synced = all(
            abs(t - global_time) < 1e-10 for t in solver_times.values()
        )

        result = SyncResult(
            mode=SyncMode.CONSERVATIVE,
            global_time=global_time,
            solver_times=solver_times,
            n_rollbacks=0,
            is_barrier=True,
            all_solvers_synced=all_synced,
        )

        self._sync_history.append(result.summary())
        return result

    def _optimistic_sync(self) -> SyncResult:
        """
        Optimistic synchronization: fast solvers proceed, rollback if needed.

        Fast solvers advance speculatively. When a slow solver catches up,
        fast solvers may need to rollback to maintain causality.
        """
        n_rollbacks = 0

        # Save checkpoints periodically
        if self._clocks:
            first_clock = next(iter(self._clocks.values()))
            if first_clock.step_count % self.checkpoint_interval == 0:
                for clock in self._clocks.values():
                    self._sync_state.snapshot_solver(clock)

        # Advance all solvers by one step
        solver_times = {}
        for sid, clock in self._clocks.items():
            if not clock.is_finished:
                clock.step()
            solver_times[sid] = clock.current_time

        # Check for causality violations
        # A violation occurs if any solver is ahead by more than one coarse step
        times = list(solver_times.values())
        max_time = max(times)
        min_time = min(times)

        # If the gap is too large, rollback the fast solvers
        dt_max = max(c.dt for c in self._clocks.values())
        if max_time - min_time > 2.0 * dt_max and self._total_rollbacks < self.max_rollbacks:
            # Rollback fast solvers to min_time + dt
            target_time = min_time + dt_max
            for sid, clock in self._clocks.items():
                if clock.current_time > target_time + 1e-14:
                    self._sync_state.rollback_solver(clock, target_time)
                    solver_times[sid] = target_time
                    n_rollbacks += 1
                    self._total_rollbacks += 1

        global_time = min(solver_times.values())
        self._sync_state.global_time = global_time

        all_synced = all(
            abs(t - global_time) < 1e-10 for t in solver_times.values()
        )

        result = SyncResult(
            mode=SyncMode.OPTIMISTIC,
            global_time=global_time,
            solver_times=solver_times,
            n_rollbacks=n_rollbacks,
            is_barrier=False,
            all_solvers_synced=all_synced,
        )

        self._sync_history.append(result.summary())
        return result

    def run_simulation(self, max_steps: int = 100) -> List[SyncResult]:
        """
        Run a full simulation with synchronization.

        Args:
            max_steps: maximum number of sync steps

        Returns:
            List of SyncResult for each step
        """
        results = []
        for _ in range(max_steps):
            # Check if all solvers are finished
            if all(c.is_finished for c in self._clocks.values()):
                break
            result = self.synchronize()
            results.append(result)
        return results

    def get_sync_history(self) -> List[dict]:
        """Get history of all synchronization steps."""
        return self._sync_history.copy()

    def get_statistics(self) -> dict:
        """Get synchronization statistics."""
        if not self._sync_history:
            return {"n_steps": 0}

        total_rollbacks = sum(r["n_rollbacks"] for r in self._sync_history)
        n_barriers = sum(1 for r in self._sync_history if r["is_barrier"])

        return {
            "n_steps": len(self._sync_history),
            "total_rollbacks": total_rollbacks,
            "n_barriers": n_barriers,
            "mode": self.mode.value,
            "final_global_time": self._sync_state.global_time,
        }

    def switch_mode(self, new_mode: SyncMode):
        """
        Switch synchronization mode.

        Args:
            new_mode: new synchronization mode
        """
        self.mode = new_mode
        self._sync_state.mode = new_mode
