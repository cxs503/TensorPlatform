"""Coupling clock: scheduling, exact landing, skew audit and fail-closed config."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tensorphysics.clock_sync import ClockError, ClockSpec, CouplingClock  # noqa: E402


def _clocks(fast_dt: float, slow_dt: float = 0.0125, t_end: float = 1.0):
    return [
        ClockSpec("slow", dt=slow_dt, role="master", max_time=t_end),
        ClockSpec("fast", dt=fast_dt, role="sub", max_time=t_end),
    ]


def test_coupling_points_are_multiplication_not_accumulation():
    clock = CouplingClock(_clocks(0.0031), 0.05)
    times = np.array([clock.coupling_point(k) for k in range(1, clock.n_coupling_points() + 1)])
    np.testing.assert_array_equal(times, 0.05 * np.arange(1, times.size + 1))
    trace = clock.run()
    np.testing.assert_allclose(trace.coupling_times, times, rtol=0.0, atol=0.0)


def test_alignment_lands_exactly_on_every_coupling_point():
    trace = CouplingClock(_clocks(0.0031), 0.05, align_last_substep=True).run()
    assert trace.max_skew <= 1e-12
    assert trace.within()
    # the fast clock cannot divide the interval, so every window truncates once
    assert trace.total_truncations == len(trace.points)
    for record in trace.points:
        assert record.substeps["fast"] == 17


def test_unaligned_control_overshoots_by_the_substep_remainder():
    """Without truncation the fast clock lands one partial substep past the point.

    The residual is the remainder ``ceil(Δ/dt)·dt − Δ`` of the *first* window; later
    windows start from the overshot time, so the skew stays inside ``(0, dt]``
    instead of accumulating without bound — still many orders above the 1e-10
    tolerance, which is what the negative control has to demonstrate.
    """
    dt_fast = 0.0031
    trace = CouplingClock(_clocks(dt_fast), 0.05, align_last_substep=False).run()
    first_remainder = 17 * dt_fast - 0.05
    assert first_remainder <= trace.max_skew <= dt_fast
    assert trace.max_skew > 100 * 1e-10
    assert not trace.within()


def test_commensurate_clock_needs_no_truncation():
    trace = CouplingClock(_clocks(0.005), 0.05).run()
    assert trace.total_truncations == 0
    assert trace.max_skew <= 1e-15
    assert trace.subcycle_ratios["fast"] == pytest.approx(0.4)


def test_window_plan_truncates_the_last_substep_only():
    clock = CouplingClock(_clocks(0.0031), 0.05)
    plan = clock.window_plan(0.0031, 0.05)
    assert plan["n_full"] == 16 and plan["truncated"] == 1
    assert plan["n_steps"] * 1.0 >= 0
    assert 16 * 0.0031 + plan["dt_last"] == pytest.approx(0.05, abs=1e-15)
    exact = clock.window_plan(0.005, 0.05)
    assert exact["truncated"] == 0 and exact["n_steps"] == 10


def test_exchange_callback_receives_the_closed_coupling_point():
    seen = []

    def exchange(record):
        seen.append((record.index, record.time, record.max_skew))
        return {"marker": float(record.index)}

    trace = CouplingClock(_clocks(0.005), 0.05).run(exchange=exchange)
    assert len(seen) == len(trace.points)
    assert [idx for idx, _, _ in seen] == list(range(1, len(seen) + 1))
    assert trace.points[0].exchange["marker"] == 1.0


def test_configuration_errors_fail_closed():
    with pytest.raises(ClockError):
        CouplingClock([ClockSpec("a", dt=0.01), ClockSpec("b", dt=0.01)], 0.05)  # no master
    with pytest.raises(ClockError):
        CouplingClock([ClockSpec("a", dt=0.01, role="master"), ClockSpec("a", dt=0.02)], 0.05)
    with pytest.raises(ClockError):
        CouplingClock([ClockSpec("a", dt=0.01, role="master")], 0.0)
    with pytest.raises(ClockError):
        CouplingClock([ClockSpec("a", dt=0.1, role="master")], 0.05)  # interval < master step
    with pytest.raises(ClockError):
        ClockSpec("a", dt=-1.0)


def test_trace_summary_carries_the_manifest_numbers():
    trace = CouplingClock(_clocks(0.0031), 0.05).run()
    summary = trace.summary()
    for key in ("coupling_interval_s", "sync_tolerance_s", "max_skew_s", "subcycle_ratios",
                "n_coupling_points", "total_truncations", "within_tolerance"):
        assert key in summary
    assert summary["max_skew_s"] == trace.max_skew
    assert summary["within_tolerance"] is True