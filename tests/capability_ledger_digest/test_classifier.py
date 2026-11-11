"""Classifier tests: decision table, boundaries, overclaim precedence, validation."""
from __future__ import annotations

import pytest

from capability_ledger_digest.spec import EvidenceRecord, SpecError, Thresholds
from capability_ledger_digest.classifier import (
    RULE_IDS,
    classify,
    classify_all,
    known_label_score,
    level_counts,
)

THR = Thresholds()


def rec(**kw) -> EvidenceRecord:
    base = dict(
        capability_id="cap.x",
        impl_present=True,
        test_pass_rate=0.95,
        benchmark_error_pct=2.0,
        review_verdict="pass",
        mock_markers=0,
        current_level="WITHHELD",
    )
    base.update(kw)
    return EvidenceRecord(**base).validate()


# ---------------------------------------------------------------- criterion (a)
def test_known_label_accuracy_is_exact(known_spec):
    results = classify_all(known_spec.capabilities, known_spec.thresholds)
    score = known_label_score(results)
    assert score["judged"] >= 10
    assert score["accuracy"] == 1.0, score["mismatches"]
    assert score["per_level"]["REAL"]["matched"] == 5
    mock_stub = score["per_level"]["MOCK"]["matched"] + score["per_level"]["STUB"]["matched"]
    assert mock_stub == 5
    assert score["mismatches"] == []


def test_level_counts_cover_all_rows(known_spec):
    results = classify_all(known_spec.capabilities, known_spec.thresholds)
    counts = level_counts(results)
    assert sum(counts.values()) == len(known_spec.capabilities)
    assert counts["REAL"] == 5 and counts["MOCK"] == 3 and counts["STUB"] == 2
    assert counts["WITHHELD"] == 2 and counts["NOT_SUPPORTED"] == 1


def test_rule_ids_are_stable_vocabulary():
    assert RULE_IDS == ("R0", "R1", "R2", "R3", "R4", "R5", "R6")


# ------------------------------------------------------------- decision table
def test_impl_absent_is_not_supported():
    res = classify(rec(impl_present=False, test_pass_rate=None, benchmark_error_pct=None,
                       review_verdict="missing"), THR)
    assert (res.level, res.rule_id) == ("NOT_SUPPORTED", "R0")


def test_no_test_evidence_is_stub():
    res = classify(rec(test_pass_rate=None, benchmark_error_pct=1.0, review_verdict="missing"), THR)
    assert (res.level, res.rule_id) == ("STUB", "R1")


def test_mock_marker_outranks_green_numbers():
    res = classify(rec(mock_markers=2, test_pass_rate=1.0, benchmark_error_pct=0.01), THR)
    assert (res.level, res.rule_id) == ("MOCK", "R2")
    assert res.chain.chain_complete is True  # numbers are green, marker still vetoes


def test_error_over_line_is_mock():
    res = classify(rec(benchmark_error_pct=22.0), THR)
    assert (res.level, res.rule_id) == ("MOCK", "R3")


def test_low_pass_rate_withholds():
    res = classify(rec(test_pass_rate=0.75), THR)
    assert (res.level, res.rule_id) == ("WITHHELD", "R4")


def test_review_failure_withholds():
    res = classify(rec(review_verdict="fail"), THR)
    assert (res.level, res.rule_id) == ("WITHHELD", "R5")


def test_all_items_green_is_real():
    res = classify(rec(), THR)
    assert (res.level, res.rule_id) == ("REAL", "R6")
    assert res.chain.chain_complete is True


# ------------------------------------------------------------------ boundaries
def test_pass_rate_exactly_at_threshold_passes():
    res = classify(rec(test_pass_rate=0.8, benchmark_error_pct=1.0), THR)
    assert res.chain.detail("test_pass_rate").passed is True
    assert res.level == "REAL"


def test_error_exactly_at_threshold_fails():
    res = classify(rec(benchmark_error_pct=15.0), THR)
    assert res.chain.detail("benchmark_error_pct").passed is False
    assert res.level == "MOCK"


def test_review_not_required_by_thresholds():
    thr = Thresholds(require_independent_review=False)
    res = classify(rec(review_verdict="missing"), thr)
    assert res.chain.detail("independent_review").passed is True
    assert res.level == "REAL"


# ----------------------------------------------------------------- transitions
def test_transition_rank_monotone():
    assert classify(rec(current_level="WITHHELD"), THR).transition == "UPGRADE"
    assert classify(rec(current_level="REAL"), THR).transition == "HOLD"
    assert classify(rec(current_level="REAL", benchmark_error_pct=40.0), THR).transition == "DOWNGRADE"


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "kwargs",
    [
        {"test_pass_rate": 1.5},
        {"test_pass_rate": -0.1},
        {"benchmark_error_pct": -0.01},
        {"review_verdict": "maybe"},
        {"mock_markers": -1},
        {"capability_id": "cap|pipe"},
        {"expected_level": "PROBABLY"},
    ],
)
def test_invalid_evidence_rejected(kwargs):
    with pytest.raises(SpecError):
        rec(**kwargs)


def test_unknown_key_rejected():
    with pytest.raises(SpecError):
        EvidenceRecord.from_dict({"capability_id": "cap.x", "surprise": 1})


def test_thresholds_validation_and_fingerprint():
    assert Thresholds().sha256() == Thresholds().sha256()
    assert Thresholds().sha256() != Thresholds(benchmark_error_max_pct=10.0).sha256()
    with pytest.raises(SpecError):
        Thresholds(test_pass_rate_min=0.0).validate()