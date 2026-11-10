"""Tests for corruption-injection gate-power measurement (synthetic criteria)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools.governance_maturity.gate_power import (
    CORRUPTIONS,
    CriterionGate,
    GatePolicy,
    always_true,
    build_records,
    inject,
    load_registry_criteria,
    measure_gate_power,
)


def _criterion(**overrides):
    crit = {
        "criterion_id": "synthetic/ratio/bound",
        "comparator": "<=",
        "threshold": 1e-3,
        "source_type": "analytic",
        "source_ref": "data/source.py#sha256:" + "0" * 64 + "#probe:synthetic",
        "decidable": True,
        "review_date": "2026-11-10",
        "depends_on": [{"param": "h", "exponent": 2.0}],
        "derivation": {
            "expression": "C*h**2",
            "coefficient": 0.064,
            "reference_h": 0.125,
        },
    }
    crit.update(overrides)
    return crit


@pytest.fixture()
def record(tmp_path):
    # coefficient 0.064 * 0.125**2 = 1e-3, so the derivation recomputes exactly
    src = tmp_path / "data" / "source.py"
    src.parent.mkdir(parents=True)
    src.write_text("PROBE = 1\n", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(src.read_bytes()).hexdigest()
    crit = _criterion(source_ref=f"data/source.py#{digest}#probe:synthetic")
    [rec] = build_records([crit], tmp_path, reference_date="2026-11-10")
    return rec


def test_clean_record_accepted(record):
    decision = CriterionGate().evaluate(record)
    assert decision["accepted"], decision["failures"]
    assert decision["checks"]["derivation_recompute"] == "pass"
    assert decision["checks"]["verdict_recompute"] == "pass"


def test_derivation_mismatch_detected(record):
    bad = copy.deepcopy(record)
    bad["derivation"]["coefficient"] *= 1.1
    decision = CriterionGate().evaluate(bad)
    assert "derivation_mismatch" in decision["failures"]


def test_verdict_mismatch_detected(record):
    bad = copy.deepcopy(record)
    bad["observation"]["observed_value"] *= 1e3
    decision = CriterionGate().evaluate(bad)
    assert "verdict_mismatch" in decision["failures"]


def test_unit_scale_violation_detected(record):
    bad = copy.deepcopy(record)
    bad["observation"]["unit"]["scale"] = 1000.0
    decision = CriterionGate().evaluate(bad)
    assert "unit_scale_violation" in decision["failures"]


def test_stale_review_detected(record):
    bad = copy.deepcopy(record)
    bad["review_date"] = "2020-01-01"
    decision = CriterionGate().evaluate(bad)
    assert "review_stale" in decision["failures"]


def test_future_review_detected(record):
    bad = copy.deepcopy(record)
    bad["review_date"] = "2030-01-01"
    decision = CriterionGate().evaluate(bad)
    assert "review_in_future" in decision["failures"]


def test_provenance_digest_mismatch_detected(record):
    bad = copy.deepcopy(record)
    bad["provenance"]["digest_ok"] = False
    decision = CriterionGate().evaluate(bad)
    assert "provenance_digest_mismatch" in decision["failures"]


def test_nonfinite_threshold_detected(record):
    bad = copy.deepcopy(record)
    bad["threshold"] = float("inf")
    decision = CriterionGate().evaluate(bad)
    assert "threshold_insane" in decision["failures"]


def test_unknown_source_type_detected(record):
    bad = copy.deepcopy(record)
    bad["source_type"] = "vibes"
    decision = CriterionGate().evaluate(bad)
    assert "source_type_unknown" in decision["failures"]


def test_gate_power_perfect_on_clean_corpus_with_controls(record):
    ledger = measure_gate_power([record], CriterionGate())
    assert ledger["false_positive_rate"] == 0.0
    assert ledger["gate_power"] == 1.0
    assert ledger["injected_total"] == len(
        [c for c in CORRUPTIONS if inject(record, c) is not None]
    )


def test_gate_power_detects_each_class(record):
    ledger = measure_gate_power([record], CriterionGate())
    for name, slot in ledger["per_class"].items():
        assert slot["gate_power"] == 1.0, name


def test_missing_derivation_limits_corruption_classes(record):
    bare = copy.deepcopy(record)
    bare["derivation"] = None
    bare["depends_on"] = []
    ledger = measure_gate_power([bare], CriterionGate())
    assert ledger["per_class"]["tolerance_widening"]["injected"] == 0
    assert ledger["per_class"]["residual_amplification"]["injected"] == 1


def test_stealth_perturbation_is_a_documented_blind_spot(record):
    ledger = measure_gate_power([record], CriterionGate())
    assert ledger["stealth"]["injected"] == 1
    assert ledger["stealth"]["gate_power"] == 0.0


def test_always_true_detector_catches_degenerate_predicate(record):
    tautology = copy.deepcopy(record)
    tautology["comparator"] = ">="
    tautology["threshold"] = 0.0
    assert always_true(tautology) is True
    assert always_true(record) is False


def test_degenerate_tautology_listed_in_ledger(record):
    tautology = copy.deepcopy(record)
    tautology["criterion_id"] = "synthetic/tautology"
    tautology["comparator"] = ">="
    tautology["threshold"] = 0.0
    ledger = measure_gate_power([record, tautology], CriterionGate())
    assert ledger["always_true_criteria"] == ["synthetic/tautology"]


def test_upstream_non_derivable_listed(record):
    nd = copy.deepcopy(record)
    nd["criterion_id"] = "synthetic/non_derivable"
    nd["upstream_decidable"] = False
    ledger = measure_gate_power([record, nd], CriterionGate())
    assert ledger["upstream_non_derivable_criteria"] == ["synthetic/non_derivable"]


def test_equality_comparator_supported(record):
    eq = copy.deepcopy(record)
    eq["comparator"] = "=="
    eq["observation"]["observed_value"] = float(eq["threshold"])
    assert CriterionGate().evaluate(eq)["accepted"]


def test_build_records_skips_unknown_comparator(tmp_path):
    crit = _criterion(comparator="~=")
    assert build_records([crit], tmp_path) == []


def test_load_registry_requires_criteria(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"criteria": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_registry_criteria(path)


def test_policy_controls_freshness_window(record):
    bad = copy.deepcopy(record)
    bad["review_date"] = "2025-06-01"
    assert CriterionGate(GatePolicy(max_review_age_days=30)).evaluate(bad)["accepted"] is False
    assert CriterionGate(GatePolicy(max_review_age_days=4000)).evaluate(bad)["accepted"] is True