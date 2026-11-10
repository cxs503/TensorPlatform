"""Sub-cycling: fast solver multi-steps per slow step, with an auditable reference.

The schedule comes from :class:`tensorphysics.clock_sync.clock.CouplingClock`; this
module is the *plant* that proves the schedule is usable — a two-subsystem coupled
oscillator integrated with velocity-Verlet, each subsystem seeing the partner's
state through a zero-order hold that is refreshed at the coupling points.

Why an abstract oscillator
--------------------------
The mechanism under test is *time integration and data exchange*, not any
particular physics: two linear subsystems with their own frequencies, coupled by a
linear interface stiffness, already contain the stiff-vs-soft and fast-vs-slow
structure that makes sub-cycling necessary.  Every parameter is dimensionless in
the sense that the model has no physical identity.

Reference solution
------------------
Re-running the *same* coupling scheme with both time steps divided by ``refine``
gives a reference in which the step-size error is below the tolerance; the
difference between the coarse run and that reference is therefore the
sub-cycling/time-integration error, which is what the acceptance criterion
measures.  A second, harsher reference (finer step **and** finer coupling
interval) is also reported so the scheme error cannot be hidden inside the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from .clock import ClockSpec, CouplingClock, CouplingTrace
from .energy import EnergyReport, InterfaceEnergyLedger

__all__ = [
    "CoupledOscillatorConfig",
    "CoupledRun",
    "substep_split",
    "simulate_coupled_oscillators",
    "compare_series",
    "coupled_window_map",
]


@dataclass(frozen=True)
class CoupledOscillatorConfig:
    """Dimensionless two-subsystem coupling test case."""

    omega_fast: float = 4.0
    omega_slow: float = 1.0
    interface_stiffness: float = 1.0
    coupling_interval: float = 0.05
    ratio: float = 10.0
    slow_substeps: int = 1
    t_end: float = 2.0
    align: bool = True
    partner_model: str = "hold"
    damping: float = 0.0
    dt_fast_override: float = 0.0
    fast_init: Tuple[float, float] = (1.0, 0.0)
    slow_init: Tuple[float, float] = (0.0, 0.0)

    @property
    def dt_slow(self) -> float:
        """Slow-solver step: the coupling interval split into ``slow_substeps``."""
        return self.coupling_interval / max(1, int(self.slow_substeps))

    @property
    def dt_fast(self) -> float:
        """Fast-solver step: ``ratio`` times smaller, unless overridden.

        ``dt_fast_override`` is how an *incommensurate* clock is expressed: a real
        solver brings its own step, which generally does not divide the coupling
        interval — that is the case in which landing on the coupling point
        requires truncating the last substep.
        """
        if self.dt_fast_override > 0.0:
            return float(self.dt_fast_override)
        return self.dt_slow / self.ratio


@dataclass
class CoupledRun:
    """Outcome of one coupled run."""

    config: CoupledOscillatorConfig
    trace: CouplingTrace
    times: np.ndarray
    fast: np.ndarray
    slow: np.ndarray
    energy_series: np.ndarray
    ledger: InterfaceEnergyLedger
    refine: float = 1.0

    @property
    def energy_report(self) -> EnergyReport:
        return self.ledger.report()

    def state_matrix(self) -> np.ndarray:
        """``(n_points, 4)`` of ``(q_fast, p_fast, q_slow, p_slow)``."""
        return np.concatenate([self.fast, self.slow], axis=1)

    def summary(self) -> Dict[str, object]:
        rep = self.energy_report
        return {
            "ratio": self.config.ratio,
            "align": self.config.align,
            "coupling_interval_s": self.config.coupling_interval,
            "dt_fast": self.config.dt_fast,
            "dt_slow": self.config.dt_slow,
            "refine": self.refine,
            "n_coupling_points": int(len(self.times)),
            "clock": self.trace.summary(),
            "energy": rep.to_dict(),
            "final_energy": float(self.energy_series[-1]) if self.energy_series.size else 0.0,
        }


def substep_split(dt: float, span: float) -> List[float]:
    """Substep sizes covering ``span`` with steps ``<= dt`` and an exact landing.

    The last entry may be shorter than ``dt``; that truncation is the price of
    landing on the coupling point and is reported by the clock.
    """
    if dt <= 0.0 or span <= 0.0:
        raise ValueError("dt and span must be positive")
    n_full = int(np.floor(span / dt + 1e-12))
    sizes = [dt] * n_full
    remainder = span - n_full * dt
    if remainder > 1e-12 * max(span, dt):
        sizes.append(remainder)
    return sizes


def _force(q: float, v: float, omega: float, stiffness: float, partner: float, damping: float = 0.0) -> float:
    """Synthetic subsystem force: own oscillator + linear interface coupling.

    ``damping > 0`` dissipates (``-2ζω·v``), which is what makes the coupled map
    strictly stable; ``damping < 0`` is the unstable negative control.
    """
    return -((omega * omega) * q) - (2.0 * damping * omega) * v - stiffness * (q - partner)


def _integrate(
    q: float,
    v: float,
    omega: float,
    stiffness: float,
    partner,
    sizes: List[float],
    t0: float = 0.0,
    damping: float = 0.0,
) -> Tuple[float, float, float]:
    """Velocity-Verlet over one coupling window with a prescribed partner profile.

    ``partner`` is either a constant or a callable ``t -> q_partner(t)`` with ``t``
    measured from the start of the window.  The work integral uses the *same*
    partner values as the force, so the ledger receives a consistent bookkeeping.

    Returns ``(q, v, work)`` where ``work`` is the work done on this subsystem by
    the interface force (the quantity the energy ledger needs).
    """
    profile = partner if callable(partner) else (lambda _t, _p=float(partner): _p)
    t = 0.0
    work = 0.0
    a = _force(q, v, omega, stiffness, profile(t), damping)
    for h in sizes:
        q_old = q
        q = q + v * h + 0.5 * a * h * h
        t_new = t + h
        a_new = _force(q, v, omega, stiffness, profile(t_new), damping)
        v = v + 0.5 * (a + a_new) * h
        f_old = -stiffness * (q_old - profile(t))
        f_new = -stiffness * (q - profile(t_new))
        work += 0.5 * (f_old + f_new) * (q - q_old)
        a = a_new
        t = t_new
    return q, v, work


def _partner_profile(model: str, value: float, previous: float, span: float):
    """Interface data treatment inside a coupling window.

    ``hold``    zero-order hold: the value received at the coupling point is kept
                for the whole window.  Standard for explicit partitioned
                coupling; the interface force then differs from the true
                gradient by ``O(Δ)``, which is exactly the energy defect the
                ledger measures.
    ``linear``  linear extrapolation from the last two exchanged values
                (second-order interface data); reduces the defect to ``O(Δ²)``.
    """
    if model == "hold":
        return float(value)
    if model == "linear":
        if span <= 0.0:
            return float(value)
        slope = (float(value) - float(previous)) / span
        return lambda t, _v=float(value), _s=slope: _v + _s * t
    raise ValueError(f"unknown partner_model {model!r}; use 'hold' or 'linear'")


def _energy(q: float, v: float, omega: float) -> float:
    return 0.5 * v * v + 0.5 * omega * omega * q * q


def simulate_coupled_oscillators(
    config: CoupledOscillatorConfig,
    *,
    refine: float = 1.0,
    coupling_interval_scale: float = 1.0,
) -> CoupledRun:
    """Run the coupled oscillator through the coupling clock.

    ``refine`` divides both solver steps (the sub-cycling error is what remains);
    ``coupling_interval_scale`` refines the exchange period as well (scheme error).
    """
    if refine <= 0.0 or coupling_interval_scale <= 0.0:
        raise ValueError("refine factors must be positive")
    interval = config.coupling_interval * coupling_interval_scale
    dt_slow = config.dt_slow * coupling_interval_scale / refine
    dt_fast = config.dt_fast * coupling_interval_scale / refine
    clock = CouplingClock(
        [
            ClockSpec("slow", dt=dt_slow, role="master", max_time=config.t_end),
            ClockSpec("fast", dt=dt_fast, role="sub", max_time=config.t_end),
        ],
        interval,
        align_last_substep=config.align,
        sync_tolerance=1e-10,
    )

    stiffness = config.interface_stiffness
    q_fast, p_fast = float(config.fast_init[0]), float(config.fast_init[1])
    q_slow, p_slow = float(config.slow_init[0]), float(config.slow_init[1])
    ledger = InterfaceEnergyLedger(tolerance=1e-6, label="two-subsystem interface")

    times: List[float] = [0.0]
    fast_series: List[Tuple[float, float]] = [(q_fast, p_fast)]
    slow_series: List[Tuple[float, float]] = [(q_slow, p_slow)]
    energy_series: List[float] = [
        _energy(q_fast, p_fast, config.omega_fast)
        + _energy(q_slow, p_slow, config.omega_slow)
        + 0.5 * stiffness * (q_fast - q_slow) ** 2
    ]

    u_prev = 0.5 * stiffness * (q_fast - q_slow) ** 2
    q_fast_prev, q_slow_prev = q_fast, q_slow

    def advance(record):
        nonlocal q_fast, p_fast, q_slow, p_slow, u_prev, q_fast_prev, q_slow_prev
        t_target = record.time
        t_prev = times[-1]
        span_fast = t_target - t_prev
        span_slow = t_target - t_prev
        plan_fast = clock.window_plan(dt_fast, span_fast)
        plan_slow = clock.window_plan(dt_slow, span_slow)
        sizes_fast = [dt_fast] * plan_fast["n_full"] + (
            [plan_fast["dt_last"]] if plan_fast["truncated"] else []
        )
        sizes_slow = [dt_slow] * plan_slow["n_full"] + (
            [plan_slow["dt_last"]] if plan_slow["truncated"] else []
        )
        q_fast_old, q_slow_old = q_fast, q_slow
        span = t_target - t_prev
        partner_for_fast = _partner_profile(config.partner_model, q_slow_old, q_slow_prev, span)
        partner_for_slow = _partner_profile(config.partner_model, q_fast_old, q_fast_prev, span)
        q_fast, p_fast, w_fast = _integrate(
            q_fast, p_fast, config.omega_fast, stiffness, partner_for_fast, sizes_fast,
            damping=config.damping,
        )
        q_slow, p_slow, w_slow = _integrate(
            q_slow, p_slow, config.omega_slow, stiffness, partner_for_slow, sizes_slow,
            damping=config.damping,
        )
        q_fast_prev, q_slow_prev = q_fast_old, q_slow_old
        u_now = 0.5 * stiffness * (q_fast - q_slow) ** 2
        ledger.record_net("fast", w_fast, time=t_target)
        ledger.record_net("slow", w_slow, time=t_target)
        ledger.record_storage(u_now - u_prev)
        u_prev = u_now
        times.append(t_target)
        fast_series.append((q_fast, p_fast))
        slow_series.append((q_slow, p_slow))
        energy_series.append(
            _energy(q_fast, p_fast, config.omega_fast)
            + _energy(q_slow, p_slow, config.omega_slow)
            + u_now
        )
        return {"work_fast": w_fast, "work_slow": w_slow, "interface_energy": u_now}

    trace = clock.run(exchange=advance, t_end=config.t_end)
    return CoupledRun(
        config=config,
        trace=trace,
        times=np.asarray(times, dtype=float),
        fast=np.asarray(fast_series, dtype=float),
        slow=np.asarray(slow_series, dtype=float),
        energy_series=np.asarray(energy_series, dtype=float),
        ledger=ledger,
        refine=float(refine),
    )


def compare_series(a: CoupledRun, b: CoupledRun) -> Dict[str, float]:
    """Relative L2 difference of two runs, sampled where their schedules coincide.

    When the two runs share a coupling interval the comparison is point by point.
    A *finer* reference (smaller coupling interval) is supported as well: its
    coupling times contain the coarse ones, so the coarse schedule is evaluated on
    the reference's matching samples — the comparison stays on the coarse clock,
    which is the schedule whose accuracy is being measured.
    """
    ta, tb = a.times, b.times
    if ta.size < 2 or tb.size < 2:
        raise ValueError("need at least two coupling points to compare")
    if ta.size == tb.size and np.allclose(ta, tb, rtol=0.0, atol=1e-12):
        idx_a, idx_b = np.arange(ta.size), np.arange(tb.size)
    else:
        idx_b = []
        for t in ta:
            j = int(np.argmin(np.abs(tb - t)))
            if abs(tb[j] - t) > 1e-12:
                raise ValueError("runs do not share a common coupling schedule")
            idx_b.append(j)
        idx_a = np.arange(ta.size)
    sa = a.state_matrix()[idx_a]
    sb = b.state_matrix()[idx_b]
    diff = float(np.sqrt(np.sum((sa - sb) ** 2)))
    denom = float(np.sqrt(np.sum(sb**2)))
    energy_ref = float(np.max(np.abs(b.energy_series[idx_b])))
    energy_err = float(np.max(np.abs(a.energy_series[idx_a] - b.energy_series[idx_b]))) / max(energy_ref, 1e-30)
    return {
        "n_points": float(len(idx_a)),
        "state_rel_l2": diff / denom if denom > 0.0 else 0.0,
        "max_energy_rel_diff": energy_err,
        "final_state_rel": float(np.linalg.norm(sa[-1] - sb[-1]) / max(np.linalg.norm(sb[-1]), 1e-30)),
    }


def coupled_window_map(config: CoupledOscillatorConfig):
    """One coupling window as an **autonomous** map on ``(q_f, p_f, q_s, p_s)``.

    The partner model is fixed to ``hold`` so the state needs no history — a
    requirement of the Lyapunov estimator, which propagates perturbations of the
    state only.  The time unit of the resulting exponent is the coupling interval.
    """
    interval = config.coupling_interval
    sizes_fast = substep_split(config.dt_fast, interval)
    sizes_slow = substep_split(config.dt_slow, interval)

    def step(x):
        q_f, p_f, q_s, p_s = (float(v) for v in x)
        q_f, p_f, _ = _integrate(
            q_f, p_f, config.omega_fast, config.interface_stiffness, q_s, sizes_fast,
            damping=config.damping,
        )
        q_s, p_s, _ = _integrate(
            q_s, p_s, config.omega_slow, config.interface_stiffness, q_f, sizes_slow,
            damping=config.damping,
        )
        return np.array([q_f, p_f, q_s, p_s], dtype=float)

    return step
