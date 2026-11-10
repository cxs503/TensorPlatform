"""Coupling clock: master/sub clocks, coupling points and sub-cycling (domain-neutral).

The clock problem
-----------------
Two solvers that advance with different time steps must agree on *when* they
exchange data.  If the fast solver simply takes ``ceil(Δ/dt)`` full steps it
lands **after** the coupling point; the skew then accumulates and the exchanged
fields are read at the wrong time.  The mechanism here is:

1. the schedule is a list of coupling points ``T_k = k·Δ`` computed by
   multiplication (not by accumulation), so the points themselves do not drift;
2. every solver advances to ``T_k`` with its own step, and the **final substep of
   each window is truncated** so the solver lands exactly on ``T_k``;
3. the residual of that truncation is recorded per solver per coupling point, so
   the synchronisation claim is audited rather than asserted.

Therefore ``skew = t_solver(T_k) − T_k`` is zero to round-off with alignment on,
and grows like ``ceil(Δ/dt)·dt − Δ`` per window with alignment off — which is
what the negative control shows.

Only numbers of seconds are involved.  Which solver produces what field is
decided by the caller through the ``exchange`` callback.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

__all__ = [
    "ClockError",
    "ClockSpec",
    "CouplingPointRecord",
    "CouplingTrace",
    "CouplingClock",
]


class ClockError(ValueError):
    """Raised when a clock configuration cannot be scheduled."""


@dataclass(frozen=True)
class ClockSpec:
    """One solver's local clock.

    ``role`` is ``"master"`` for the solver that sets the coupling interval (its
    own step must divide it) or ``"sub"`` for a solver with an independent,
    usually smaller step.
    """

    name: str
    dt: float
    role: str = "sub"
    max_time: float = 1.0
    tag: str = ""

    def __post_init__(self) -> None:
        if self.role not in ("master", "sub"):
            raise ClockError(f"clock role must be 'master' or 'sub', got {self.role!r}")
        if not (np.isfinite(self.dt) and self.dt > 0.0):
            raise ClockError(f"clock {self.name!r} needs a positive finite dt")
        if not (np.isfinite(self.max_time) and self.max_time > 0.0):
            raise ClockError(f"clock {self.name!r} needs a positive finite max_time")


@dataclass
class CouplingPointRecord:
    """What actually happened at one coupling point."""

    index: int
    time: float
    solver_time: Dict[str, float]
    skew: Dict[str, float]
    substeps: Dict[str, int]
    truncated: Dict[str, int]
    exchange: Dict[str, float] = _dc_field(default_factory=dict)

    @property
    def max_skew(self) -> float:
        return max((abs(v) for v in self.skew.values()), default=0.0)

    def to_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "time": self.time,
            "solver_time": dict(self.solver_time),
            "skew": dict(self.skew),
            "substeps": dict(self.substeps),
            "truncated": dict(self.truncated),
            "max_skew": self.max_skew,
            "exchange": dict(self.exchange),
        }


@dataclass
class CouplingTrace:
    """Full schedule outcome: every coupling point plus the aggregates."""

    coupling_interval: float
    align_last_substep: bool
    sync_tolerance: float
    points: List[CouplingPointRecord] = _dc_field(default_factory=list)
    solver_dt: Dict[str, float] = _dc_field(default_factory=dict)
    roles: Dict[str, str] = _dc_field(default_factory=dict)

    # ------------------------------------------------------------------ metrics
    @property
    def max_skew(self) -> float:
        return max((p.max_skew for p in self.points), default=0.0)

    @property
    def skew_series(self) -> np.ndarray:
        return np.array([p.max_skew for p in self.points], dtype=float)

    @property
    def coupling_times(self) -> np.ndarray:
        return np.array([p.time for p in self.points], dtype=float)

    @property
    def total_truncations(self) -> int:
        return int(sum(sum(p.truncated.values()) for p in self.points))

    @property
    def subcycle_ratios(self) -> Dict[str, float]:
        master = [d for n, d in self.solver_dt.items() if self.roles.get(n) == "master"]
        base = master[0] if master else max(self.solver_dt.values(), default=1.0)
        return {n: float(d / base) for n, d in self.solver_dt.items()}

    def within(self, tolerance: Optional[float] = None) -> bool:
        tol = self.sync_tolerance if tolerance is None else float(tolerance)
        return self.max_skew <= tol

    def summary(self) -> Dict[str, object]:
        return {
            "coupling_interval_s": self.coupling_interval,
            "align_last_substep": self.align_last_substep,
            "sync_tolerance_s": self.sync_tolerance,
            "n_coupling_points": len(self.points),
            "max_skew_s": self.max_skew,
            "solver_dt": dict(self.solver_dt),
            "roles": dict(self.roles),
            "subcycle_ratios": self.subcycle_ratios,
            "total_truncations": self.total_truncations,
            "within_tolerance": self.within(),
        }


class CouplingClock:
    """Deterministic sub-cycling schedule shared by a set of solver clocks.

    Parameters
    ----------
    specs
        One :class:`ClockSpec` per solver.  Exactly one ``"master"`` clock is
        required (the solver whose step defines the coupling interval).
    coupling_interval
        ``Δ`` — the exchange period in seconds.
    align_last_substep
        Truncate the last substep of each window so every solver lands on the
        coupling point (True) or take full steps and accept the overshoot
        (False — the negative control).
    sync_tolerance
        The admissible ``max|skew|`` for :meth:`CouplingTrace.within`.
    """

    def __init__(
        self,
        specs: Sequence[ClockSpec],
        coupling_interval: float,
        *,
        align_last_substep: bool = True,
        sync_tolerance: float = 1e-10,
    ):
        specs = list(specs)
        if not specs:
            raise ClockError("at least one clock is required")
        if not (np.isfinite(coupling_interval) and coupling_interval > 0.0):
            raise ClockError("coupling_interval must be positive and finite")
        masters = [s for s in specs if s.role == "master"]
        if len(masters) != 1:
            raise ClockError(f"exactly one master clock is required, got {len(masters)}")
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ClockError("clock names must be unique")
        if coupling_interval < masters[0].dt - 1e-15:
            raise ClockError("coupling_interval must not be shorter than the master step")
        self.specs = specs
        self.coupling_interval = float(coupling_interval)
        self.align_last_substep = bool(align_last_substep)
        self.sync_tolerance = float(sync_tolerance)
        self.master = masters[0]

    # ------------------------------------------------------------- scheduling
    def coupling_point(self, index: int) -> float:
        """``T_k = k·Δ`` — by multiplication, so the schedule cannot drift."""
        if index < 0:
            raise ClockError("coupling point index must be non-negative")
        return float(index) * self.coupling_interval

    def n_coupling_points(self, t_end: Optional[float] = None) -> int:
        end = self._end_time(t_end)
        n = int(np.floor(end / self.coupling_interval + 1e-12))
        return max(n, 0)

    def _end_time(self, t_end: Optional[float]) -> float:
        if t_end is not None:
            return float(t_end)
        return min(s.max_time for s in self.specs)

    def window_plan(self, dt: float, span: float, align: Optional[bool] = None) -> Dict[str, float]:
        """Substep plan for one solver over one coupling window.

        Returns ``n_steps``, ``n_full`` (full ``dt`` steps), ``dt_last`` and
        ``truncated`` (1 when the final substep had to be shortened).
        """
        align = self.align_last_substep if align is None else bool(align)
        if dt <= 0.0 or span <= 0.0:
            raise ClockError("dt and span must be positive")
        if align:
            n_full = int(np.floor(span / dt + 1e-12))
            remainder = span - n_full * dt
            if remainder <= 1e-12 * max(span, dt):
                return {"n_steps": n_full, "n_full": n_full, "dt_last": dt, "truncated": 0}
            return {"n_steps": n_full + 1, "n_full": n_full, "dt_last": remainder, "truncated": 1}
        n_steps = int(np.ceil(span / dt - 1e-12))
        return {"n_steps": n_steps, "n_full": n_steps, "dt_last": dt, "truncated": 0}

    # ------------------------------------------------------------------ run
    def run(
        self,
        exchange: Optional[Callable[[CouplingPointRecord], Optional[Dict[str, float]]]] = None,
        t_end: Optional[float] = None,
    ) -> CouplingTrace:
        """Walk the schedule and (optionally) fire ``exchange`` at every point.

        ``exchange`` receives the record of the coupling point being closed and
        may return a dict of scalars (energies, fluxes, ...) that is stored on
        the record — the clock never interprets them.
        """
        end = self._end_time(t_end)
        trace = CouplingTrace(
            coupling_interval=self.coupling_interval,
            align_last_substep=self.align_last_substep,
            sync_tolerance=self.sync_tolerance,
            solver_dt={s.name: float(s.dt) for s in self.specs},
            roles={s.name: s.role for s in self.specs},
        )
        solver_time = {s.name: 0.0 for s in self.specs}
        n_points = self.n_coupling_points(end)
        for k in range(1, n_points + 1):
            target = self.coupling_point(k)
            record = CouplingPointRecord(
                index=k,
                time=target,
                solver_time={},
                skew={},
                substeps={},
                truncated={},
            )
            for spec in self.specs:
                span = target - solver_time[spec.name]
                plan = self.window_plan(spec.dt, span)
                if self.align_last_substep:
                    new_time = target  # lands exactly on the coupling point
                else:
                    new_time = solver_time[spec.name] + plan["n_steps"] * plan["dt_last"]
                solver_time[spec.name] = new_time
                record.solver_time[spec.name] = new_time
                record.skew[spec.name] = new_time - target
                record.substeps[spec.name] = int(plan["n_steps"])
                record.truncated[spec.name] = int(plan["truncated"])
            if exchange is not None:
                extra = exchange(record)
                if extra:
                    record.exchange = {str(k2): float(v) for k2, v in extra.items()}
            trace.points.append(record)
        return trace