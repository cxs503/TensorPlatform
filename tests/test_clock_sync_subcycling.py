"""Sub-cycling, interface energy bookkeeping and the zero-coupling negative control."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tensorphysics.clock_sync import (  # noqa: E402
    CoupledOscillatorConfig,
    InterfaceEnergyLedger,
    compare_series,
    simulate_coupled_oscillators,
    substep_split,
)


def _config(**kwargs) -> CoupledOscillatorConfig:
    base = dict(coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=1.0, partner_model="linear")
    base.update(kwargs)
    return CoupledOscillatorConfig(**base)


def test_substep_split_lands_exactly_on_the_window_end():
    sizes = substep_split(0.0031, 0.05)
    assert sum(sizes) == pytest.approx(0.05, abs=1e-15)
    assert all(s <= 0.0031 + 1e-15 for s in sizes)
    exact = substep_split(0.005, 0.05)
    assert len(exact) == 10


def test_ten_to_one_subcycling_matches_the_refined_reference():
    cfg = _config()
    coarse = simulate_coupled_oscillators(cfg)
    reference = simulate_coupled_oscillators(cfg, refine=8.0)
    comparison = compare_series(coarse, reference)
    assert comparison["state_rel_l2"] <= 5e-2
    assert comparison["state_rel_l2"] < 1e-3  # measured, not just inside the gate


def test_error_shrinks_when_the_coarse_run_is_refined():
    cfg = _config()
    reference = simulate_coupled_oscillators(cfg, refine=8.0)
    errors = [
        compare_series(simulate_coupled_oscillators(cfg, refine=float(r)), reference)["state_rel_l2"]
        for r in (1, 2, 4)
    ]
    assert errors[0] > errors[1] > errors[2]


def test_interface_energy_budget_closes_and_hold_is_worse_than_linear():
    linear = simulate_coupled_oscillators(_config())
    hold = simulate_coupled_oscillators(_config(partner_model="hold"))
    assert linear.energy_report.closure_rel < 1e-2
    assert linear.energy_report.closure_rel < hold.energy_report.closure_rel
    assert hold.energy_report.closure_rel > 1e-2  # the negative control is genuinely wrong
    assert linear.energy_report.mapping_loss == pytest.approx(0.0, abs=1e-15)


def test_interface_defect_is_first_order_for_hold_and_second_order_for_linear():
    def closure(model: str, interval: float) -> float:
        run = simulate_coupled_oscillators(
            _config(coupling_interval=interval, t_end=1.0, partner_model=model)
        )
        return max(run.energy_report.closure_rel, 1e-18)

    hold_ratio = closure("hold", 0.05) / closure("hold", 0.025)
    linear_ratio = closure("linear", 0.05) / closure("linear", 0.025)
    assert 1.5 < hold_ratio < 2.6          # ~2  -> O(dt)
    assert 3.0 < linear_ratio < 6.0        # ~4  -> O(dt^2)


def test_zero_coupling_exchanges_nothing_at_all():
    run = simulate_coupled_oscillators(_config(interface_stiffness=0.0, t_end=0.5))
    report = run.energy_report
    assert report.total_in == 0.0
    assert report.total_out == 0.0
    assert report.stored_delta == 0.0
    assert report.closure_residual == 0.0
    assert report.mapping_loss == 0.0
    assert report.n_exchanges == 0
    assert report.ok


def test_ledger_detects_a_non_conservative_mapping():
    ledger = InterfaceEnergyLedger(tolerance=1e-6, energy_scale=1.0)
    ledger.exchange("fast", "slow", emitted=1.0, received=0.9)
    report = ledger.report()
    assert report.mapping_loss == pytest.approx(0.1)
    assert report.participants["fast"] == pytest.approx(-1.0)
    assert report.participants["slow"] == pytest.approx(0.9)
    assert not report.ok


def test_ledger_closure_identity_sums_to_zero():
    """``Σ net_s + ΔE_stored = 0``: interface work and interface storage balance."""
    ledger = InterfaceEnergyLedger(tolerance=1e-12)
    ledger.record_net("a", -2.0)   # a loses 2.0 to the interface
    ledger.record_net("b", 1.0)    # b gains 1.0 from it
    ledger.record_storage(1.0)     # the remaining 1.0 is stored by the coupling
    report = ledger.report()
    assert report.closure_residual == pytest.approx(0.0, abs=1e-15)
    assert report.ok
    ledger.record_storage(0.1)     # perturbing the balance must break the gate
    assert not ledger.report().ok


def test_compare_series_rejects_incomparable_schedules():
    a = simulate_coupled_oscillators(_config())
    b = simulate_coupled_oscillators(_config(), refine=8.0, coupling_interval_scale=0.5)
    with pytest.raises(ValueError):
        # b's schedule is finer than a's but not a superset of it
        compare_series(b, a)
    # ... while a coarse run can be compared against a finer *superset* schedule
    assert compare_series(a, b)["n_points"] > 1