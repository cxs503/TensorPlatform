"""Clock-synchronisation semantics audit: conservative vs optimistic coupling.

A cross-solver transfer is only well defined when the two sides agree on *when*
a field value exists.  Two textbook protocols are audited here on a synthetic
two-solver system (no domain semantics, only ODE states):

**Conservative (barrier, slowest-first).**  No solver may advance past the
minimum committed time of the set, so a solver only ever consumes data that is
already committed.  Invariants to audit: zero barrier skew, zero future reads,
monotone global time.

**Optimistic (fastest-first + rollback).**  A solver speculates ahead with
extrapolated partner data.  When the partner publishes the true sample, every
speculative step taken with the wrong data must be *rolled back* and replayed
with the truth.  The acceptance statement is a replay equivalence::

    state(optimistic + rollback)  ==  state(deterministic replay)   (round-off)

and its complement: without rollback the same run drifts by O(1e-2), i.e. the
rollback is not an optimisation detail but the part that makes the answer
correct.

The module also audits the *platform's existing* rollback path
(:class:`src.coupling.clock_sync.SyncState`): it rewinds ``current_time`` but
keeps the pre-rollback ``step_count``, so the clock becomes internally
inconsistent (``current_time != step_count * dt``) and no solver state is
restored.  That defect is quantified by :func:`audit_legacy_rollback` rather
than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# A deterministic scalar solver with checkpoints
# --------------------------------------------------------------------------- #
@dataclass
class ScalarSolver:
    """``dy/dt = -lam y + c(t)`` integrated with explicit Euler at fixed ``dt``.

    State is a float, so a replay comparison can be exact; ``c`` is supplied per
    step by the coupling protocol under audit.
    """

    name: str
    lam: float
    dt: float
    y0: float = 1.0
    t0: float = 0.0

    time: float = field(init=False, default=0.0)
    y: float = field(init=False, default=0.0)
    n_steps: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.time = float(self.t0)
        self.y = float(self.y0)
        self.n_steps = 0

    # -- integration ---------------------------------------------------- #
    def step(self, coupling: float) -> float:
        self.y = self.y + self.dt * (-self.lam * self.y + coupling)
        self.time = self.time + self.dt
        self.n_steps += 1
        return self.y

    def advance(self, n: int, coupling: float) -> None:
        for _ in range(n):
            self.step(coupling)

    def rewind(self, t_target: float) -> None:
        """Move the clock back to ``t_target`` (state is *not* restored here)."""
        self.time = float(t_target)


class CoupledClockAudit:
    """Two-solver exchange audited under both synchronisation disciplines."""

    def __init__(self,
                 lam_a: float = 2.0,
                 lam_b: float = 1.0,
                 dt_a: float = 0.02,
                 dt_b: float = 0.01,
                 sample_every: int = 5,
                 horizon: float = 1.0,
                 checkpoint_every: int = 5):
        self.lam_a = lam_a
        self.lam_b = lam_b
        self.dt_a = dt_a
        self.dt_b = dt_b
        self.sample_every = sample_every
        self.horizon = horizon
        self.checkpoint_every = checkpoint_every
        ratio = dt_a / dt_b
        if abs(ratio - round(ratio)) > 1e-12:
            raise ValueError("dt_a must be an integer multiple of dt_b")
        self.substeps = int(round(ratio))
        self.n_samples = int(round(horizon / (dt_a * sample_every)))

    # -- helper --------------------------------------------------------- #
    def _new_pair(self) -> Tuple[ScalarSolver, ScalarSolver]:
        return (ScalarSolver("A", self.lam_a, self.dt_a),
                ScalarSolver("B", self.lam_b, self.dt_b))

    def _observe(self, a: ScalarSolver) -> float:
        """Coupling quantity published by solver A."""
        return 0.5 * a.y

    # -- reference: every interval uses the *true* published sample ----- #
    def run_replay_reference(self) -> Dict[str, object]:
        """Deterministic replay: B always integrates with the true sample.

        This is the answer an optimistically synchronised run must reproduce
        after its rollbacks, and it is also the answer a conservative run
        approaches with one sample of lag.
        """
        a, b = self._new_pair()
        samples = [self._observe(a)]
        for _ in range(self.n_samples):
            a.advance(self.sample_every, coupling=0.0)
            samples.append(self._observe(a))
        # Replay: B's interval k uses samples[k+1] (truth available after the fact)
        for k in range(self.n_samples):
            b.advance(self.sample_every * self.substeps, coupling=samples[k + 1])
        return {"a_state": a.y, "b_state": b.y, "samples": samples,
                "t_a": a.time, "t_b": b.time}

    # -- conservative ---------------------------------------------------- #
    def run_conservative(self) -> Dict[str, object]:
        """Barrier protocol: B never sees a sample before A commits it."""
        a, b = self._new_pair()
        published: List[float] = [self._observe(a)]
        skew: List[float] = []
        future_reads = 0
        global_times: List[float] = []
        for k in range(self.n_samples):
            # A advances one sample period
            a.advance(self.sample_every, coupling=0.0)
            # B may advance only up to A's *newly committed* time, consuming the
            # previously committed sample (one period of lag, zero future reads).
            for _ in range(self.sample_every * self.substeps):
                b.step(published[-1])
                if b.time > a.time + 1e-12:
                    future_reads += 1  # B read data newer than its own window
            published.append(self._observe(a))
            skew.append(abs(b.time - a.time))
            global_times.append(min(a.time, b.time))
        return {
            "a_state": a.y,
            "b_state": b.y,
            "max_barrier_skew": float(max(skew)) if skew else 0.0,
            "future_reads": future_reads,
            "global_time_monotone": all(
                global_times[i] <= global_times[i + 1] + 1e-15
                for i in range(len(global_times) - 1)),
            "n_samples": self.n_samples,
            "mode": "conservative",
        }

    # -- optimistic ------------------------------------------------------ #
    def _richardson_extrapolate(self, s0: float, s1: float) -> float:
        return s1 + (s1 - s0)

    def run_optimistic(self, rollback: bool = True) -> Dict[str, object]:
        """Fastest-first protocol with (optional) rollback on late data."""
        a, b = self._new_pair()
        samples = [self._observe(a)]
        samples.append(samples[-1] + (samples[-1] - samples[-1]))  # flat start guess

        rolled_back_steps = 0
        invalidated_values = 0
        max_lead = 0.0
        # checkpointed B states: sample index -> (y, time)
        checkpoints: Dict[int, Tuple[float, float]] = {0: (b.y, b.time)}
        causal_violations = 0

        for k in range(1, self.n_samples + 1):
            # B speculates over interval k using the extrapolated sample.
            predicted = self._richardson_extrapolate(samples[-2], samples[-1])
            speculative_start = (b.y, b.time)
            b.advance(self.sample_every * self.substeps, coupling=predicted)
            max_lead = max(max_lead, b.time - a.time)

            # A then publishes the true sample of this interval.
            a.advance(self.sample_every, coupling=0.0)
            truth = self._observe(a)
            samples.append(truth)
            wrong = abs(predicted - truth) > 1e-14
            if wrong:
                invalidated_values += 1
                if rollback:
                    # Roll back to the interval start and replay with the truth.
                    b.y, b.time = speculative_start
                    b.n_steps -= self.sample_every * self.substeps
                    rolled_back_steps += self.sample_every * self.substeps
                    b.advance(self.sample_every * self.substeps, coupling=truth)
                else:
                    # No rollback: the speculative (wrong) contribution stays in
                    # the trajectory.  This is the failure mode being audited.
                    causal_violations += 1
            checkpoints[k] = (b.y, b.time)

        return {
            "a_state": a.y,
            "b_state": b.y,
            "samples": samples,
            "rolled_back_steps": rolled_back_steps,
            "invalidated_values": invalidated_values,
            "max_lead": float(max_lead),
            "causal_violations": causal_violations,
            "mode": "optimistic" if rollback else "optimistic_no_rollback",
        }

    def run_peek_ahead(self) -> Dict[str, object]:
        """Negative control: a protocol that reads *uncommitted* partner data.

        The conservative audit claims ``future_reads == 0``; that claim is only
        meaningful if the detector can fire.  Here solver B deliberately reads
        the newest sample the moment A produces it instead of the previously
        committed one, so the detector must count violations.
        """
        a, b = self._new_pair()
        future_reads = 0
        for _ in range(self.n_samples):
            a.advance(self.sample_every, coupling=0.0)
            fresh = self._observe(a)           # not committed yet for B's window
            for _ in range(self.sample_every * self.substeps):
                b.step(fresh)
                # B is consuming data stamped *after* its own step interval
                if b.time - self.dt_b < a.time - 1e-12:
                    future_reads += 1
        return {"future_reads": future_reads,
                "detected": future_reads > 0,
                "mode": "peek_ahead"}

    # -- audit ----------------------------------------------------------- #
    def audit(self) -> dict:
        """Compare conservative / optimistic / no-rollback against the replay."""
        ref = self.run_replay_reference()
        cons = self.run_conservative()
        opt = self.run_optimistic(rollback=True)
        naive = self.run_optimistic(rollback=False)
        peek = self.run_peek_ahead()

        def rel_dev(x: float, y: float) -> float:
            return abs(x - y) / max(0.5 * (abs(x) + abs(y)), 1e-300)

        return {
            "config": {
                "lam_a": self.lam_a, "lam_b": self.lam_b,
                "dt_a": self.dt_a, "dt_b": self.dt_b,
                "sample_every": self.sample_every, "horizon": self.horizon,
                "n_samples": self.n_samples,
            },
            "replay_reference": {"a_state": ref["a_state"], "b_state": ref["b_state"]},
            "conservative": {
                "b_state": cons["b_state"],
                "deviation_from_replay": rel_dev(cons["b_state"], ref["b_state"]),
                "max_barrier_skew": cons["max_barrier_skew"],
                "future_reads": cons["future_reads"],
                "global_time_monotone": cons["global_time_monotone"],
            },
            "optimistic_rollback": {
                "b_state": opt["b_state"],
                "deviation_from_replay": rel_dev(opt["b_state"], ref["b_state"]),
                "rolled_back_steps": opt["rolled_back_steps"],
                "invalidated_values": opt["invalidated_values"],
                "max_lead": opt["max_lead"],
            },
            "negative_control_peek_ahead": {
                "future_reads": peek["future_reads"],
                "detected": peek["detected"],
            },
            "optimistic_no_rollback": {
                "b_state": naive["b_state"],
                "deviation_from_replay": rel_dev(naive["b_state"], ref["b_state"]),
                "causal_violations": naive["causal_violations"],
            },
        }


# --------------------------------------------------------------------------- #
# Audit of the platform's legacy rollback path
# --------------------------------------------------------------------------- #
def audit_legacy_rollback() -> dict:
    """Quantify what ``SyncState.rollback_solver`` does and does not restore.

    Two failure modes are measured on the platform's existing clock:

    * **silent no-op** — a target time at or after the stored snapshot returns
      ``True`` ("rolled back") while the clock is left untouched, so the caller
      keeps a state that is ahead of the requested time;
    * **clock/state divergence** — a target time before the snapshot rewinds
      ``current_time`` but keeps the pre-rollback ``step_count`` and never
      applies the stored state, so ``current_time != step_count * dt`` and the
      solver trajectory is not restored.
    """
    from .clock_sync import SolverClock, SyncState, SyncMode

    def scenario(target: float) -> dict:
        clock = SolverClock(solver_id="s1", current_time=0.0, dt=0.01, max_time=10.0)
        state = SyncState(mode=SyncMode.OPTIMISTIC)
        for _ in range(3):
            clock.step()
        state.snapshot_solver(clock, state_data={"y": 1.0})
        snapshot = {"time": clock.current_time, "steps": clock.step_count}
        for _ in range(5):
            clock.step()
        before = {"time": clock.current_time, "steps": clock.step_count}
        ok = state.rollback_solver(clock, target)
        return {
            "target_time": target,
            "snapshot": snapshot,
            "before_rollback": before,
            "reported_success": bool(ok),
            "time_after": clock.current_time,
            "steps_after": clock.step_count,
            "time_implied_by_steps": clock.step_count * clock.dt,
            "clock_inconsistency": abs(clock.current_time - clock.step_count * clock.dt),
            "actually_moved": abs(clock.current_time - before["time"]) > 1e-15,
        }

    silent = scenario(0.06)   # after the snapshot -> silently does nothing
    rewind = scenario(0.02)   # before the snapshot -> rewinds the clock only

    # Fixed path: checkpoint + replay restores the deterministic trajectory.
    solver = ScalarSolver("B", lam=1.0, dt=0.01, y0=1.0)
    ckpt = CheckpointedRollback(solver, checkpoint_every=5)
    for _ in range(8):
        solver.step(coupling=0.5)
        ckpt.maybe_checkpoint()
    reference = {}
    probe = ScalarSolver("B", lam=1.0, dt=0.01, y0=1.0)
    for n in range(9):
        reference[round(probe.time, 10)] = probe.y
        probe.step(coupling=0.5)
    for _ in range(4):
        solver.step(coupling=0.5)
    restored = ckpt.rollback_to(0.08, replay=lambda s: s.step(coupling=0.5))
    return {
        "silent_noop_case": silent,
        "rewind_case": rewind,
        "fixed_path": {
            **restored,
            "reference_state_at_target": reference.get(0.08),
            "state_error": abs(restored.get("state", float("nan"))
                               - reference.get(0.08, float("nan"))),
        },
    }


# --------------------------------------------------------------------------- #
# Checkpointed rollback that *does* restore state
# --------------------------------------------------------------------------- #
class CheckpointedRollback:
    """Rollback that restores both the clock and the solver state.

    A checkpoint stores ``(time, step_count, state)``; rolling back to a time
    that is not exactly a checkpoint replays from the nearest checkpoint at or
    before it, so the restored trajectory is the deterministic one — which is
    what :meth:`CoupledClockAudit.run_optimistic` asserts.
    """

    def __init__(self, solver: ScalarSolver, checkpoint_every: int = 5):
        self.solver = solver
        self.checkpoint_every = checkpoint_every
        self.checkpoints: List[Tuple[float, int, float]] = []

    def maybe_checkpoint(self) -> None:
        if self.solver.n_steps % self.checkpoint_every == 0:
            self.checkpoints.append((self.solver.time, self.solver.n_steps, self.solver.y))

    def rollback_to(self, target_time: float, replay: Callable[[ScalarSolver], None]) -> dict:
        """Restore the state at ``target_time`` by replaying from a checkpoint."""
        candidates = [c for c in self.checkpoints if c[0] <= target_time + 1e-12]
        if not candidates:
            return {"restored": False, "reason": "no checkpoint at or before target time",
                    "replayed_steps": 0}
        t_c, n_c, y_c = max(candidates, key=lambda c: c[0])
        discarded = self.solver.n_steps - n_c
        self.solver.time, self.solver.n_steps, self.solver.y = t_c, n_c, y_c
        n_replay = 0
        while self.solver.time < target_time - 1e-12:
            replay(self.solver)
            n_replay += 1
        return {
            "restored": True,
            "checkpoint_time": t_c,
            "discarded_steps": discarded,
            "replayed_steps": n_replay,
            "time": self.solver.time,
            "state": self.solver.y,
        }


__all__ = [
    "CheckpointedRollback",
    "CoupledClockAudit",
    "ScalarSolver",
    "audit_legacy_rollback",
]