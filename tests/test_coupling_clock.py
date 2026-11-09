"""Tests for the coupling clock contract (tools/coupling/clock.py).

Acceptance mapping (node IN383-PLAT01):
  C6 out-of-contract exchanges are fail-closed (no silent interpolation/extrapolation)
  subcycling contract dt_master = k·dt_sub is enforced at construction and at request time
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.coupling import (
    ClockContract,
    ClockContractViolation,
    ClockMode,
    TimeSeriesExchange,
    canonical_digest,
)


def make_contract(k=2, dt_sub=0.01, mode=ClockMode.SYNC, **kwargs) -> ClockContract:
    return ClockContract(dt_master=k * dt_sub, dt_sub=dt_sub, mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# Contract construction
# ---------------------------------------------------------------------------

def test_contract_ratio_and_substep_times():
    contract = make_contract(k=3, dt_sub=0.02)
    assert contract.ratio == 3
    np.testing.assert_allclose(contract.substep_times(0), [0.0, 0.02, 0.04])
    np.testing.assert_allclose(contract.substep_times(2), [0.12, 0.14, 0.16])
    assert contract.master_time(2) == pytest.approx(0.12, rel=0, abs=1e-15)


def test_non_integer_subcycling_ratio_is_rejected():
    with pytest.raises(ClockContractViolation):
        ClockContract(dt_master=0.1, dt_sub=0.03)


def test_sub_step_larger_than_master_step_is_rejected():
    with pytest.raises(ClockContractViolation):
        ClockContract(dt_master=0.01, dt_sub=0.02)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_non_positive_and_non_finite_steps_are_rejected(bad):
    with pytest.raises(ClockContractViolation):
        ClockContract(dt_master=bad, dt_sub=0.01)


def test_unknown_mode_is_rejected():
    with pytest.raises(ClockContractViolation):
        make_contract(mode="adaptive_magic")


# ---------------------------------------------------------------------------
# Sample publication
# ---------------------------------------------------------------------------

def test_store_rejects_times_off_the_sub_grid():
    exchange = TimeSeriesExchange(make_contract())
    with pytest.raises(ClockContractViolation):
        exchange.store(0.003, [1.0])


def test_store_rejects_non_monotonic_and_duplicate_times():
    exchange = TimeSeriesExchange(make_contract())
    exchange.store(0.01, [1.0])
    with pytest.raises(ClockContractViolation):
        exchange.store(0.01, [2.0])
    with pytest.raises(ClockContractViolation):
        exchange.store(0.0, [2.0])


def test_store_rejects_non_finite_values():
    exchange = TimeSeriesExchange(make_contract())
    with pytest.raises(ClockContractViolation):
        exchange.store(0.0, [float("nan")])


# ---------------------------------------------------------------------------
# SYNC
# ---------------------------------------------------------------------------

def test_sync_answers_only_at_stored_times():
    exchange = TimeSeriesExchange(make_contract(k=2, mode=ClockMode.SYNC))
    exchange.store(0.0, [1.0, 2.0])
    exchange.store(0.01, [2.0, 4.0])
    exchange.store(0.02, [3.0, 6.0])
    sample = exchange.request(0.02)
    np.testing.assert_allclose(sample.values, [3.0, 6.0])
    assert sample.mode == "sync" and not sample.interpolated and not sample.extrapolated


def test_sync_between_samples_fails_closed():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.SYNC))
    exchange.store(0.0, [1.0])
    exchange.store(0.02, [2.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.01)


def test_sync_request_without_samples_fails_closed():
    exchange = TimeSeriesExchange(make_contract())
    with pytest.raises(ClockContractViolation):
        exchange.request(0.0)


# ---------------------------------------------------------------------------
# LINEAR_INTERPOLATION
# ---------------------------------------------------------------------------

def test_linear_interpolation_is_exact_at_the_midpoint():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.LINEAR_INTERPOLATION))
    exchange.store(0.0, [0.0, 10.0])
    exchange.store(0.02, [2.0, 12.0])
    sample = exchange.request(0.01)
    np.testing.assert_allclose(sample.values, [1.0, 11.0], rtol=0, atol=1e-14)
    assert sample.interpolated and not sample.extrapolated


def test_linear_interpolation_does_not_extrapolate():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.LINEAR_INTERPOLATION))
    exchange.store(0.0, [0.0])
    exchange.store(0.01, [1.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.02)


def test_interpolation_reproduces_a_linear_signal_exactly():
    exchange = TimeSeriesExchange(make_contract(k=4, mode=ClockMode.LINEAR_INTERPOLATION))
    for i in range(9):
        exchange.store(i * 0.01, [3.0 + 2.5 * i * 0.01])
    for i in range(1, 8):
        t = i * 0.01
        assert exchange.request(t).values[0] == pytest.approx(3.0 + 2.5 * t, abs=1e-14)


# ---------------------------------------------------------------------------
# ONE_STEP_EXTRAPOLATION
# ---------------------------------------------------------------------------

def test_one_step_extrapolation_is_allowed_and_flagged():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.ONE_STEP_EXTRAPOLATION))
    exchange.store(0.0, [0.0])
    exchange.store(0.01, [1.0])
    sample = exchange.request(0.02)
    assert sample.extrapolated and sample.mode == "one_step_extrapolation"
    np.testing.assert_allclose(sample.values, [2.0], atol=1e-14)


def test_extrapolation_beyond_the_declared_horizon_fails_closed():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.ONE_STEP_EXTRAPOLATION))
    exchange.store(0.0, [0.0])
    exchange.store(0.01, [1.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.05)


def test_extrapolation_needs_two_samples():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.ONE_STEP_EXTRAPOLATION))
    exchange.store(0.0, [0.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.01)


def test_zero_horizon_extrapolation_is_effectively_sync():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.ONE_STEP_EXTRAPOLATION,
                                                max_extrapolation_steps=0))
    exchange.store(0.0, [0.0])
    exchange.store(0.01, [1.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.02)


# ---------------------------------------------------------------------------
# EVENT_TRIGGERED
# ---------------------------------------------------------------------------

def test_event_value_is_marked_stale_between_events():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.EVENT_TRIGGERED,
                                                max_staleness_steps=2))
    exchange.publish_event(0.0, [5.0])
    sample = exchange.request(0.01)
    assert sample.stale is True
    np.testing.assert_allclose(sample.values, [5.0])


def test_event_staleness_beyond_the_window_fails_closed():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.EVENT_TRIGGERED,
                                                max_staleness_steps=1))
    exchange.publish_event(0.0, [5.0])
    with pytest.raises(ClockContractViolation):
        exchange.request(0.05)


def test_event_request_before_first_event_fails_closed_and_publish_needs_event_mode():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.EVENT_TRIGGERED))
    with pytest.raises(ClockContractViolation):
        exchange.request(0.0)
    sync_exchange = TimeSeriesExchange(make_contract(mode=ClockMode.SYNC))
    with pytest.raises(ClockContractViolation):
        sync_exchange.publish_event(0.0, [1.0])


# ---------------------------------------------------------------------------
# Audit chain and subcycle alignment
# ---------------------------------------------------------------------------

def test_master_tick_requests_hit_samples_for_integer_ratios():
    for k in (1, 2, 3, 5):
        exchange = TimeSeriesExchange(make_contract(k=k, dt_sub=0.005, mode=ClockMode.SYNC))
        contract = exchange.contract
        for step in range(4):
            for t in contract.substep_times(step):
                exchange.store(float(t), [float(t)])
        for step in range(4):
            sample = exchange.request(contract.master_time(step))
            assert sample.values[0] == pytest.approx(contract.master_time(step), abs=1e-15)


def test_ledger_chain_is_valid_and_tamper_evident():
    exchange = TimeSeriesExchange(make_contract(mode=ClockMode.LINEAR_INTERPOLATION))
    exchange.store(0.0, [0.0])
    exchange.store(0.02, [2.0])
    exchange.request(0.01)
    chain = exchange.ledger.to_dict()
    assert chain["chain_valid"] is True
    assert chain["n_entries"] == 4
    exchange.ledger.entries[1]["values_digest"] = "0" * 64
    assert exchange.ledger.verify_chain() is False
    assert exchange.ledger.digest != chain["chain_digest"]


def test_contract_digest_distinguishes_modes():
    a = make_contract(mode=ClockMode.SYNC).digest()
    b = make_contract(mode=ClockMode.LINEAR_INTERPOLATION).digest()
    assert a != b and len(a) == 64
    assert a == make_contract(mode=ClockMode.SYNC).digest()
    assert canonical_digest({"k": 2}) == canonical_digest({"k": 2})