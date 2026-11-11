"""Boundary exchange: conservation accounting, protocols, negative control."""

from __future__ import annotations

import numpy as np
import pytest

from src.multifidelity.clock_manager import ClockContractViolation, GlobalClockManager, Strategy
from src.multifidelity.exchange import (
    Channel,
    exchange_free_running,
    exchange_synced,
    interpolation_audit,
    summarise,
)

DTS = {"fast": 1 / 960, "mid": 1 / 480, "slow": 1 / 60}
HORIZON = 0.5
CHANNELS = (
    Channel("mass_flux", base=1.0, amp=0.50, freq=8.0, phase=0.0),
    Channel("momentum_flux", base=0.8, amp=0.35, freq=8.0, phase=0.7, amp2=0.10, phase2=0.3),
    Channel("energy_flux", base=1.2, amp=0.60, freq=8.0, phase=1.9),
)


def _manager(strategy=Strategy.GCD):
    manager = GlobalClockManager(strategy)
    for name, dt in DTS.items():
        manager.register(name, dt)
    return manager


def test_channel_integral_matches_quadrature():
    ch = CHANNELS[0]
    t = np.linspace(0.0, 0.5, 200001)
    numeric = float(np.trapezoid(ch.rate(t), t))
    assert abs(numeric - ch.integral(0.0, 0.5)) / abs(ch.integral(0.0, 0.5)) < 1e-6


def test_synced_conservation_is_structural():
    manager = _manager()
    grid = manager.build(HORIZON)
    ledger = exchange_synced(manager, grid, CHANNELS, "fast", "slow")
    for name, row in ledger.items():
        assert row.net_defect < 1e-12, name            # accounts agree to round-off
        assert row.frame_l1_defect < 1e-3, name        # and per frame to 1e-3
        assert row.consumed_error < 1e-12, name
        assert row.n_frames == grid.n_steps // 16


def test_synced_protocol_works_on_the_macro_grid_too():
    manager = _manager(Strategy.LCM)
    grid = manager.build(HORIZON)
    ledger = exchange_synced(manager, grid, CHANNELS, "fast", "slow")
    worst = summarise(ledger)
    assert worst["max_frame_l1_defect"] < 1e-3
    assert ledger["mass_flux"].n_frames == grid.n_steps


def test_nesting_violation_is_refused():
    """The barrier protocol must refuse a frame lattice that is not nested."""
    manager = _manager()
    grid = manager.build(HORIZON)
    with pytest.raises(ClockContractViolation):
        exchange_synced(manager, grid, CHANNELS, "slow", "fast")


def test_negative_no_clock_breaks_frame_wise_conservation():
    """Negative control: disable the shared clock => frame-wise defect > 5%."""
    manager = _manager()
    grid = manager.build(HORIZON)
    worst = 0.0
    for offset in (0.0, 0.25, 0.5, 0.75):
        ledger = exchange_free_running(
            manager.clocks, CHANNELS, "fast", "slow", float(grid.horizon),
            mode="hold", consumer_offset=offset)
        worst = max(worst, summarise(ledger)["max_frame_l1_defect"])
    assert worst > 0.05
    synced = summarise(exchange_synced(manager, grid, CHANNELS, "fast", "slow"))
    assert worst > 100 * synced["max_frame_l1_defect"]


def test_negative_net_balance_alone_cannot_see_a_missing_clock():
    """The net defect stays small while the frame defect is large: net is not a gate."""
    manager = _manager()
    grid = manager.build(HORIZON)
    ledger = exchange_free_running(
        manager.clocks, CHANNELS, "fast", "slow", float(grid.horizon),
        mode="hold", consumer_offset=0.0)
    summary = summarise(ledger)
    assert summary["max_net_defect"] < 0.01          # passes a 1% net gate ...
    assert summary["max_frame_l1_defect"] > 0.05     # ... while every frame is wrong


def test_extrapolating_consumer_is_better_but_still_not_a_clock():
    manager = _manager()
    grid = manager.build(HORIZON)
    hold = summarise(exchange_free_running(
        manager.clocks, CHANNELS, "fast", "slow", float(grid.horizon),
        mode="hold", consumer_offset=0.5))
    extrap = summarise(exchange_free_running(
        manager.clocks, CHANNELS, "fast", "slow", float(grid.horizon),
        mode="extrapolate", consumer_offset=0.5))
    assert extrap["max_frame_l1_defect"] < hold["max_frame_l1_defect"]
    assert extrap["max_frame_l1_defect"] > 0.01


def test_interpolation_error_on_the_grid_is_below_0p1_percent():
    report = interpolation_audit(CHANNELS[0], DTS["fast"], n_query=1500, horizon=HORIZON)
    assert report["linear"]["max_rel_error"] < 1e-3
    assert report["hermite"]["max_rel_error"] < report["linear"]["max_rel_error"]
    # the consumer's own lattice cannot resolve the signal: that is why the
    # interface carries interval integrals instead of samples
    coarse = interpolation_audit(CHANNELS[0], DTS["slow"], n_query=1500, horizon=HORIZON)
    assert coarse["linear"]["max_rel_error"] > 0.01


def test_unknown_mode_is_rejected():
    manager = _manager()
    with pytest.raises(ValueError):
        exchange_free_running(manager.clocks, CHANNELS, "fast", "slow", HORIZON, mode="magic")