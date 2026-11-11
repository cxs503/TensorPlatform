"""Global clock manager for heterogeneous multi-solver co-simulation.

The manager answers one question in exact arithmetic: *given the natural time
step of every participant, which instants can all participants agree on?*

Two classical strategies are supported and both are exact (rational, not
floating point):

``gcd``
    ``dt_global = gcd(dt_i)`` — the finest common grid.  Every solver step is an
    integer number of global steps (``dt_i / dt_global = k_i in N``), so a
    solver publishes at every k_i-th grid node and a coarse solver consumes
    k_i nested sub-samples per own step.

``lcm``
    ``dt_global = lcm(dt_i)`` — the coarse macro step.  Every solver takes an
    integer number of its own steps per macro window
    (``dt_global / dt_i = k_i in N``), which is the classical macro-step /
    sub-cycling arrangement.

Why rational arithmetic matters: ``lcm`` of floating point time steps is not
representable.  A naive shortcut (round every step to integers, take ``lcm``,
divide by the scale) lands on a step that is *not* a common instant of the
original clocks; the clocks then drift by a fraction of a step that never
cancels.  Keep the steps as :class:`fractions.Fraction` and the common instant
is exact — the residual skew is floating point conversion error (~1e-16), which
is what :meth:`GlobalClockManager.skew_audit` measures and what the naive
shortcut is compared against.

Time is dimensionless here: the manager never sees units or physics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from math import gcd
from typing import Dict, List, Optional, Sequence

import numpy as np

#: Denominator cap used when a float time step is converted to a rational.
#: 1e9 resolves one part in a billion of the step, far below any integrator
#: tolerance, while keeping the denominators small enough for exact gcd/lcm.
RATIONAL_LIMIT = 10 ** 9

#: Refuse to build a sync grid whose subdivisions per window exceed this: an
#: lcm strategy over incommensurate clocks is not a schedule, it is a stall.
MAX_SUBDIVISION = 10 ** 7

#: A sync grid with more than this many steps over the horizon is not a schedule
#: either: two rationalised clocks with no common structure produce a gcd of
#: the order of the rounding limit, i.e. a step nobody wants to take.
MAX_STEPS = 10 ** 7


class ClockContractViolation(RuntimeError):
    """A registered clock cannot join the requested global sync grid."""


class Strategy(str, Enum):
    """How the global time step is derived from the participants' steps."""

    GCD = "gcd"
    LCM = "lcm"


def as_fraction(dt) -> Fraction:
    """Convert a time step to an exact positive rational."""
    if isinstance(dt, Fraction):
        frac = dt
    elif isinstance(dt, int):
        frac = Fraction(dt)
    else:
        value = float(dt)
        if not np.isfinite(value):
            raise ClockContractViolation(f"time step must be finite, got {dt!r}")
        frac = Fraction(value).limit_denominator(RATIONAL_LIMIT)
    if frac <= 0:
        raise ClockContractViolation(f"time step must be positive, got {dt!r}")
    return frac


def rational_gcd(a: Fraction, b: Fraction) -> Fraction:
    """Greatest common divisor of two positive rationals: ``gcd(num)/lcm(den)``."""
    a, b = as_fraction(a), as_fraction(b)
    return Fraction(gcd(a.numerator, b.numerator),
                    a.denominator * b.denominator // gcd(a.denominator, b.denominator))


def rational_lcm(a: Fraction, b: Fraction) -> Fraction:
    """Least common multiple of two positive rationals: ``lcm(num)/gcd(den)``."""
    a, b = as_fraction(a), as_fraction(b)
    return Fraction(a.numerator * b.numerator // gcd(a.numerator, b.numerator),
                    gcd(a.denominator, b.denominator))


def _fold(fn, values: Sequence[Fraction]) -> Fraction:
    acc = as_fraction(values[0])
    for value in values[1:]:
        acc = fn(acc, as_fraction(value))
    return acc


@dataclass(frozen=True)
class SolverClock:
    """A participant's natural clock.  ``dt`` is exact, ``max_time`` optional."""

    solver_id: str
    dt: Fraction
    max_time: Optional[Fraction] = None

    @property
    def dt_float(self) -> float:
        return float(self.dt)

    def to_dict(self) -> Dict[str, object]:
        return {
            "solver_id": self.solver_id,
            "dt": str(self.dt),
            "dt_float": self.dt_float,
            "max_time": None if self.max_time is None else str(self.max_time),
        }


@dataclass(frozen=True)
class SyncGrid:
    """The instants every participant agrees on, plus the sub-stepping plan."""

    strategy: Strategy
    dt_global: Fraction
    horizon: Fraction
    ratios: Dict[str, Fraction]   # dt_i / dt_global
    n_steps: int                  # global steps in [0, horizon]

    # -- grid -------------------------------------------------------------
    def time(self, k: int) -> Fraction:
        return k * self.dt_global

    def grid(self) -> np.ndarray:
        return np.array([float(self.time(k)) for k in range(self.n_steps + 1)])

    # -- per solver plan ---------------------------------------------------
    def solver_steps_per_global(self, solver_id: str) -> int:
        """Own steps per global step (>=1) — non-zero for sub-cycled solvers."""
        ratio = self.ratios[solver_id]
        return 1 if ratio >= 1 else int(Fraction(1, 1) / ratio)

    def global_steps_per_own_step(self, solver_id: str) -> int:
        """Global steps spanned by one of this solver's own steps (>=1)."""
        ratio = self.ratios[solver_id]
        return 1 if ratio < 1 else int(ratio)

    def publication_nodes(self, solver_id: str) -> List[int]:
        """Global grid indices at which this solver produces a fresh sample."""
        if self.ratios[solver_id] < 1:
            return list(range(self.n_steps + 1))   # sub-cycled: steps every node
        span = self.global_steps_per_own_step(solver_id)
        return list(range(0, self.n_steps + 1, span))

    def consumer_windows(self, solver_id: str) -> List[int]:
        """Global grid indices bounding one of this solver's own steps."""
        return self.publication_nodes(solver_id)

    def nesting_ok(self, producer: str, consumer: str) -> bool:
        """Can the consumer's frame edges be producer publication nodes?

        Requires ``dt_consumer / dt_producer`` to be a positive integer: only
        then does every consumer frame boundary fall on a producer sample, which
        is what makes the frame total exactly computable instead of held.
        """
        rp = self.ratios[producer]
        rc = self.ratios[consumer]
        ratio = rc / rp
        return ratio.denominator == 1 and ratio >= 1

    def digest(self) -> str:
        payload = "|".join(
            [self.strategy.value, str(self.dt_global), str(self.horizon), str(self.n_steps)]
            + [f"{k}:{v}" for k, v in sorted(self.ratios.items())]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> Dict[str, object]:
        return {
            "strategy": self.strategy.value,
            "dt_global": str(self.dt_global),
            "dt_global_float": float(self.dt_global),
            "horizon": str(self.horizon),
            "n_steps": self.n_steps,
            "ratios": {k: str(v) for k, v in self.ratios.items()},
            "solver_steps_per_global": {k: self.solver_steps_per_global(k) for k in self.ratios},
            "global_steps_per_own": {k: self.global_steps_per_own_step(k) for k in self.ratios},
            "grid_digest": self.digest(),
        }


class GlobalClockManager:
    """Registry of participant clocks plus derivation of the shared grid."""

    def __init__(self, strategy: Strategy | str = Strategy.GCD):
        self.strategy = Strategy(strategy)
        self._clocks: Dict[str, SolverClock] = {}

    # -- registration ------------------------------------------------------
    def register(self, solver_id: str, dt, max_time=None) -> SolverClock:
        if not solver_id:
            raise ClockContractViolation("solver_id must be non-empty")
        clock = SolverClock(
            solver_id=solver_id,
            dt=as_fraction(dt),
            max_time=None if max_time is None else as_fraction(max_time),
        )
        self._clocks[solver_id] = clock
        return clock

    def unregister(self, solver_id: str) -> None:
        self._clocks.pop(solver_id, None)

    @property
    def clocks(self) -> Dict[str, SolverClock]:
        return dict(self._clocks)

    @property
    def solver_ids(self) -> List[str]:
        return list(self._clocks)

    # -- derivation --------------------------------------------------------
    def dt_global(self) -> Fraction:
        """Exact global step: ``gcd`` or ``lcm`` of the registered steps."""
        if not self._clocks:
            raise ClockContractViolation("no solver registered")
        dts = [c.dt for c in self._clocks.values()]
        return _fold(rational_gcd if self.strategy is Strategy.GCD else rational_lcm, dts)

    def ratios(self) -> Dict[str, Fraction]:
        dt_global = self.dt_global()
        return {sid: c.dt / dt_global for sid, c in self._clocks.items()}

    def build(self, horizon) -> SyncGrid:
        """Build the sync grid covering ``horizon`` (snapped up to a common instant)."""
        if not self._clocks:
            raise ClockContractViolation("no solver registered")
        dt_global = self.dt_global()
        ratios = self.ratios()
        for sid, ratio in ratios.items():
            if self.strategy is Strategy.GCD:
                if ratio.denominator != 1:
                    raise ClockContractViolation(
                        f"{sid}: dt/dt_global = {ratio} is not an integer")
            else:
                reciprocal = 1 / ratio
                if reciprocal.denominator != 1:
                    raise ClockContractViolation(
                        f"{sid}: dt_global/dt = {reciprocal} is not an integer")
                if reciprocal > MAX_SUBDIVISION:
                    raise ClockContractViolation(
                        f"{sid}: {reciprocal} sub-steps per macro window exceeds "
                        f"MAX_SUBDIVISION={MAX_SUBDIVISION}")
        horizon_frac = as_fraction(horizon)
        n_steps = int(np.ceil(horizon_frac / dt_global))
        if n_steps > MAX_STEPS:
            raise ClockContractViolation(
                f"{self.strategy.value} grid needs {n_steps} steps over the horizon "
                f"(dt_global={dt_global}); the registered steps share no usable "
                f"common structure (MAX_STEPS={MAX_STEPS})")
        return SyncGrid(
            strategy=self.strategy,
            dt_global=dt_global,
            horizon=n_steps * dt_global,
            ratios=ratios,
            n_steps=n_steps,
        )

    # -- audits ------------------------------------------------------------
    def skew_audit(self, grid: SyncGrid) -> Dict[str, object]:
        """Compare the exact grid against the two floating point shortcuts.

        ``float accumulation``
            the loop a solver actually runs, ``t += dt_global``; its deviation
            from ``float(k*dt_global)`` is pure round-off drift.
        ``integer rounding shortcut``
            ``lcm`` taken on time steps rounded to 1e-6; this is what one gets by
            treating the steps as integers, and it misses the common instant
            whenever the steps are not commensurate on that rounding grid.
        """
        naive_drift = 0.0
        t = 0.0
        for k in range(grid.n_steps + 1):
            naive_drift = max(naive_drift, abs(t - float(grid.time(k))))
            t += float(grid.dt_global)

        own_step_drift = {}
        for sid, clock in self._clocks.items():
            n_own = int(np.ceil(float(grid.horizon) / clock.dt_float))
            worst = 0.0
            for n in range(n_own + 1):
                exact = n * clock.dt                       # exact rational own time
                nearest_global = round(exact / grid.dt_global)
                worst = max(worst, abs(float(exact) - float(nearest_global * grid.dt_global)))
            own_step_drift[sid] = worst

        integer_shortcut = None
        shortcut_dt = None
        # the engineering shortcut: quantise every step to 1 ms and take the lcm
        scale = 10 ** 3
        ints = [max(1, int(round(c.dt_float * scale))) for c in self._clocks.values()]
        if len(ints) >= 2:
            shortcut = ints[0]
            for value in ints[1:]:
                shortcut = shortcut * value // gcd(shortcut, value)
            shortcut_dt = shortcut / scale
            integer_shortcut = abs(shortcut_dt - float(grid.dt_global)) / float(grid.dt_global)

        return {
            "strategy": self.strategy.value,
            "dt_global": float(grid.dt_global),
            "dt_global_exact": str(grid.dt_global),
            "max_skew_float_accumulation": naive_drift,
            "max_offgrid_distance_own_steps": max(own_step_drift.values()) if own_step_drift else 0.0,
            "integer_rounding_shortcut_rel_error": integer_shortcut,
            "integer_rounding_shortcut_dt": shortcut_dt,
            "integer_rounding_shortcut_usable": (
                None if shortcut_dt is None
                else bool(shortcut_dt <= 4.0 * float(grid.horizon))),
            "n_steps": grid.n_steps,
            "ratio_integrality": {
                sid: bool(r.denominator == 1 or (1 / r).denominator == 1)
                for sid, r in grid.ratios.items()
            },
            "grid_digest": grid.digest(),
        }