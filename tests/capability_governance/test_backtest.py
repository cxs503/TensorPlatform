"""Frozen-set backtest: the mechanism must beat a single-gate check, measurably."""
from __future__ import annotations

import json

import pytest

from services.capability_governance import backtest as B
from services.capability_governance.ledger import CapabilityLedger


def _run(specs, test_report, tmp_path):
    ledger = CapabilityLedger(tmp_path / "ledger.json", tmp_path / "events.jsonl")
    return B.run_frozen_set(specs, test_report_path=test_report, mission_id=9811, ledger=ledger)


def test_subset_accuracy_and_false_positive_rate(small_frozen_specs, test_report, tmp_path):
    result = _run(small_frozen_specs, test_report, tmp_path)
    metrics = result["metrics"]
    assert metrics["n_cases"] == len(small_frozen_specs)
    assert metrics["n_positives"] >= 1 and metrics["n_negatives"] >= 1
    assert metrics["accuracy"] >= 0.90
    assert metrics["false_positive_rate"] < 0.05


def test_four_gate_mechanism_beats_single_gate_ablation(small_frozen_specs, test_report, tmp_path):
    result = _run(small_frozen_specs, test_report, tmp_path)
    pipeline = result["metrics"]
    ablation = result["single_gate_ablation"]
    assert ablation["false_positive_rate"] > pipeline["false_positive_rate"]
    assert ablation["accuracy"] < pipeline["accuracy"]


def test_every_case_has_a_sealed_chain(small_frozen_specs, test_report, tmp_path):
    result = _run(small_frozen_specs, test_report, tmp_path)
    assert len(result["chains"]) == len(small_frozen_specs)
    for chain in result["chains"]:
        assert len(chain["chain_digest"]) == 64
        assert chain["test_report"]["sha256"]


def test_ledger_level_matches_prediction(small_frozen_specs, test_report, tmp_path):
    result = _run(small_frozen_specs, test_report, tmp_path)
    for row in result["rows"]:
        if row["predicted_level"] == "REAL":
            assert row["ledger_level"] == "REAL"
        else:
            assert row["ledger_level"] == "WITHHELD"


def test_frozen_digest_is_stable_and_sensitive(small_frozen_specs):
    digest = B.freeze_digest(small_frozen_specs)
    assert digest == B.freeze_digest(small_frozen_specs)
    mutated = [dict(s) for s in small_frozen_specs]
    mutated[0] = dict(mutated[0], tol_pct=mutated[0]["tol_pct"] * 2)
    assert B.freeze_digest(mutated) != digest


def test_frozen_set_file_is_labelled_on_both_sides():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    probe_set = repo / "config" / "capability_governance" / "probe_set.json"
    specs = json.loads(probe_set.read_text(encoding="utf-8"))["endpoints"]
    labels = {s["expected_level"] for s in specs}
    assert labels == {"REAL", "MOCK", "WITHHELD"}
    assert sum(1 for s in specs if s["expected_level"] == "REAL") >= 6
    assert sum(1 for s in specs if s["expected_level"] != "REAL") >= 30