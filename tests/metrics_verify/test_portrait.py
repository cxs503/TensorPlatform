"""Portrait verification: profile consistency against truth, corruption control, drift latency."""

from __future__ import annotations

import pytest

from tools.metrics_verify import portrait_consistency as pc
from tools.metrics_verify.sim import simulate_portrait


def test_portrait_reproduces_simulator_truth_within_five_percent():
    sim = simulate_portrait(n_agents=6, missions_per_agent=150, seed=31)
    result = pc.compare_portrait(sim)
    assert result["pass"] is True
    assert result["max_success_rate_abs_error"] <= 0.05
    assert result["max_mean_wall_rel_error"] <= 0.05
    assert result["max_domain_share_abs_error"] <= 0.05
    assert result["traceability"]["ok"] is True
    assert result["traceability"]["checked"] == 6 * 150


def test_portrait_detects_injected_status_corruption():
    """Negative control: 15 % of successes relabelled as failures must exceed 5 %."""
    clean = simulate_portrait(n_agents=4, missions_per_agent=100, seed=31)
    corrupt = simulate_portrait(n_agents=4, missions_per_agent=100, seed=31,
                                injected_status_flips=60)
    clean_result = pc.compare_portrait(clean)
    corrupt_result = pc.compare_portrait(corrupt)
    assert clean_result["pass"] is True
    assert corrupt_result["pass"] is False
    assert corrupt_result["max_success_rate_abs_error"] > 0.05


def test_portrait_traceability_flags_a_row_that_does_not_exist_in_the_table():
    sim = simulate_portrait(n_agents=4, missions_per_agent=50, seed=31)
    truncated = sim.rows[:-5]
    report = __import__("tools.worker_portrait.portrait", fromlist=["aggregate"]).aggregate(
        sim.rows, min_missions=3)
    trace = __import__("tools.worker_portrait.portrait", fromlist=["verify_against_missions"]
                       ).verify_against_missions(report, truncated)
    assert trace["ok"] is False
    assert trace["n_mismatches"] > 0


def test_drift_detected_within_ten_events():
    result = pc.evaluate_drift()
    assert result["n_missed"] == 0
    assert result["latency_max"] is not None and result["latency_max"] < 10
    assert result["arl0_events_measured"] is None or result["arl0_events_measured"] > 1000
    assert result["pass"] is True


def test_drift_detector_stays_quiet_on_a_stationary_stream():
    """Deterministic in-control stream (5 % failures evenly spread) must not alarm."""
    outcomes = [0 if i % 20 == 0 else 1 for i in range(250)]
    out = pc.detect_success_drift(outcomes, change_index=None)
    assert out["alarmed"] is False
    assert out["applicable"] is True


def test_drift_power_curve_reports_its_own_limits():
    result = pc.evaluate_drift()
    curve = {row["p_after"]: row for row in result["power_curve"]}
    assert curve[0.2]["within_ceiling"] is True
    # honest limitation: a mild change is outside the 10-event design point
    assert curve[0.5]["within_ceiling"] is False


def test_domain_drift_detects_capability_shift():
    stream = ["domain_alpha"] * 60 + ["domain_gamma"] * 60
    out = pc.detect_domain_drift(stream, change_index=60)
    assert out["alarmed"] is True
    assert out["latency_events"] is not None and out["latency_events"] < 20


def test_portrait_requires_at_least_three_workers():
    with pytest.raises(ValueError):
        simulate_portrait(n_agents=2, missions_per_agent=10, seed=1)