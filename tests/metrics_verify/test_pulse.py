"""Pulse verification: latency recovery, loss detection, and the shipped freshness core."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tools.metrics_verify import pulse_latency, stats
from tools.metrics_verify.sim import EPOCH, FleetConfig, simulate_fleet


def test_latency_estimator_recovers_injected_lognormal_parameters():
    cfg = FleetConfig(n_agents=40, n_heartbeats=400, seed=4242)
    fleet = simulate_fleet(cfg)
    summary = pulse_latency.summarize_latency(fleet)
    # measured percentiles vs the closed-form quantiles of what was injected
    for key, q in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99)):
        reference = stats.lognormal_quantile(cfg.median_latency_s, cfg.sigma, q)
        assert summary["estimator_error"][f"{key}_rel"] == pytest.approx(
            stats.rel_error(summary["measured"][key], reference))
        assert stats.rel_error(summary["measured"][key], reference) < 0.05


def test_latency_ceiling_passes_on_healthy_fleet():
    fleet = simulate_fleet(FleetConfig(n_agents=20, n_heartbeats=200, seed=7))
    verdict = pulse_latency.evaluate_latency_ceiling(pulse_latency.summarize_latency(fleet))
    assert verdict["pass"] is True
    assert verdict["mean_s"] < 1.0 and verdict["p99_s"] < 5.0


def test_latency_ceiling_trips_when_stalls_are_injected():
    """Negative control: an 8 % stall population must break the P99 ceiling."""
    cfg = FleetConfig(n_agents=20, n_heartbeats=200, seed=7, stall_rate=0.08,
                      stall_latency_range_s=(6.0, 14.0))
    fleet = simulate_fleet(cfg)
    verdict = pulse_latency.evaluate_latency_ceiling(pulse_latency.summarize_latency(fleet))
    assert verdict["p99_pass"] is False
    assert verdict["p99_s"] > 5.0


def test_latency_undefined_for_empty_fleet():
    fleet = simulate_fleet(FleetConfig(n_agents=1, n_heartbeats=5, seed=1, loss_rate=0.999))
    if not fleet.delivered_latencies:
        with pytest.raises(pulse_latency.PulseVerifyError):
            pulse_latency.summarize_latency(fleet)


def test_heartbeat_loss_detection_above_threshold_at_five_percent():
    cfg = FleetConfig(n_agents=40, n_heartbeats=500, seed=99, loss_rate=0.05)
    fleet = simulate_fleet(cfg)
    result = pulse_latency.detect_heartbeat_loss(fleet)
    assert result["n_injected"] > 0
    assert result["detection_rate_strict"] > 0.95
    assert result["n_false_positive_gaps"] == 0
    # the only drops a gap detector cannot see are trailing ones
    assert result["n_undetectable_trailing"] <= result["n_injected"]


def test_loss_detector_is_silent_on_a_clean_stream():
    fleet = simulate_fleet(FleetConfig(n_agents=10, n_heartbeats=100, seed=5))
    result = pulse_latency.detect_heartbeat_loss(fleet)
    assert result["n_injected"] == 0
    assert result["n_false_positive_gaps"] == 0
    assert result["detection_rate_strict"] == 1.0


def test_snapshot_counts_match_simulator_truth():
    """The real ``saturation_pulse.compute`` must agree with who is alive/busy."""
    fleet = simulate_fleet(FleetConfig(n_agents=20, n_heartbeats=50, seed=11))
    now = max(fleet.last_heartbeats().values())
    core = pulse_latency.crosscheck_pulse_core(fleet, now=now)
    assert core["pass"] is True
    assert core["measured"] == {**core["truth"], "skewed_heartbeats": core["measured"]["skewed_heartbeats"]}
    assert core["predicate_mismatches"] == []
    assert core["truth"]["busy"] < core["truth"]["online"]  # both classes present


def test_freshness_predicate_matches_truth_under_a_tight_window():
    """Half the fleet must fall out of a 2 s freshness window -- and be reported so."""
    fleet = simulate_fleet(FleetConfig(n_agents=20, n_heartbeats=50, seed=13))
    now = max(fleet.last_heartbeats().values()) + timedelta(seconds=2.0)
    core = pulse_latency.crosscheck_pulse_core(fleet, now=now, window_s=2)
    assert core["predicate_mismatches"] == []
    assert core["predicate_checks"] == 20
    assert 0 < core["measured"]["online"] < 20


def test_future_heartbeat_is_flagged_as_skew_not_as_fresh_error():
    fleet = simulate_fleet(FleetConfig(n_agents=4, n_heartbeats=20, seed=17))
    latest = max(fleet.last_heartbeats().values())
    core = pulse_latency.crosscheck_pulse_core(fleet, now=latest - timedelta(seconds=120),
                                               window_s=60)
    assert core["measured"]["skewed_heartbeats"] == 4  # all four beat "in the future"
    assert core["predicate_mismatches"] == []