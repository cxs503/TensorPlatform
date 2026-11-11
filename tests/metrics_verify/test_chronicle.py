"""Chronicle verification: completeness, ordering, monotonicity and fault detection."""

from __future__ import annotations

import pytest

from tools.metrics_verify import chronicle_integrity as ci
from tools.metrics_verify.sim import simulate_event_stream


def test_honest_stream_is_complete_and_ordered():
    stream = simulate_event_stream(2000, seed=2026)
    report = ci.validate_event_stream(stream.events, stream.expected_per_session)
    assert report["n_missing_vs_truth"] == 0
    assert report["completeness"] == 1.0
    assert report["order_correct_rate"] == 1.0
    assert report["n_seq_regressions"] == 0 and report["n_ts_regressions"] == 0
    assert report["ok"] is True


def test_out_of_order_injection_is_detected_above_99_percent():
    stream = simulate_event_stream(4000, seed=2026, disorder_rate=0.01)
    score = ci.score_disorder_detection(stream)
    assert score["n_injected_transpositions"] > 0
    assert score["detection_rate"] > 0.99
    assert score["n_false_alarm_positions"] == 0


def test_dropped_events_are_visible_as_missing_against_emitter_truth():
    stream = simulate_event_stream(2000, seed=2026, drop_rate=0.01)
    report = ci.validate_event_stream(stream.events, stream.expected_per_session)
    assert report["n_missing_vs_truth"] == 20
    assert report["completeness"] == pytest.approx(0.99)
    assert report["ok"] is False


def test_duplicate_events_are_detected():
    stream = simulate_event_stream(1000, seed=2026, duplicate_rate=0.01)
    report = ci.validate_event_stream(stream.events, stream.expected_per_session)
    assert report["n_duplicates"] == 10
    assert report["ok"] is False


def test_sequence_hole_without_truth_counter_is_still_reported():
    """A missing seq inside the range is a hole even when no emitter truth is given."""
    events = [
        {"id": "e1", "session_id": "s", "seq": 1, "kind": "k", "created_at": "2026-11-11T06:00:00+00:00"},
        {"id": "e2", "session_id": "s", "seq": 3, "kind": "k", "created_at": "2026-11-11T06:00:01+00:00"},
    ]
    report = ci.validate_event_stream(events)
    assert report["seq_holes"] == {"s": [2]}
    assert report["completeness"] is None  # no fabricated 100 %
    assert report["ok"] is False


def test_time_regression_without_sequence_regression_is_flagged():
    events = [
        {"id": "e1", "session_id": "s", "seq": 1, "kind": "k", "created_at": "2026-11-11T06:00:05+00:00"},
        {"id": "e2", "session_id": "s", "seq": 2, "kind": "k", "created_at": "2026-11-11T06:00:01+00:00"},
    ]
    report = ci.validate_event_stream(events)
    assert report["n_seq_regressions"] == 0
    assert report["n_ts_regressions"] == 1
    assert report["order_correct_rate"] == 0.0


def test_missing_required_column_is_rejected():
    with pytest.raises(ci.ChronicleVerifyError):
        ci.validate_event_stream([{"id": "e1", "session_id": "s", "seq": 1}])


def test_full_acceptance_bundle_passes():
    result = ci.run_chronicle_verify(n_events=3000, seed=2026)
    assert result["honest"]["pass"] and result["disorder"]["pass"]
    assert result["drop_control"]["pass"] and result["duplicate_control"]["pass"]