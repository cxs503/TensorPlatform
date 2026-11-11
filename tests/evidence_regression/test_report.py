"""Acceptance harness pieces: zero false positives, latency, and a fast end-to-end smoke run."""
from __future__ import annotations

import json

from tools.evidence_regression import report


def test_false_positive_suite_stays_ok(registry, claims):
    fp = report.false_positive_suite(registry, claims, runs=25, seed=11)
    assert fp["false_positives"] == 0
    assert fp["false_positive_rate"] == 0.0
    assert fp["claims_track_fp"] == 0 and fp["changes_track_fp"] == 0


def test_latency_suite_within_budget(registry, claims):
    latency = report.latency_suite(registry, claims)
    assert latency["within_budget"] is True
    assert latency["max_ms"] < report.LATENCY_BUDGET_MS
    assert latency["per_change"]


def test_acceptance_smoke_writes_artifacts(tmp_path, workspace_root):
    result = report.run_acceptance(
        registry_path="tests/evidence_regression/_acceptance_registry.json",
        ledger_path="tests/evidence_regression/_acceptance_ledger.json",
        out_dir=tmp_path / "artifacts",
        injections=20,
        fp_runs=20,
        with_pytest=False,
        with_figures=False,
        workspace=workspace_root,
    )
    assert result["criteria"]["C1_registered_entries"]["pass"] is True
    assert result["criteria"]["C2_degradation_detection"]["detection_rate"] == 1.0
    assert result["criteria"]["C3_zero_false_positive"]["false_positives"] == 0
    assert result["criteria"]["C4_gate_latency"]["pass"] is True
    assert result["negative_controls"]["stale_ack_rejected"]["pass"] is True
    written = json.loads((tmp_path / "artifacts" / "acceptance.json").read_text(encoding="utf-8"))
    assert written["all_pass"] is False  # C5 skipped on purpose in this smoke test
    assert written["criteria"]["C5_pytest"]["pass"] is False
    assert (tmp_path / "artifacts" / "drill.json").exists()
    assert (tmp_path / "artifacts" / "false_positive.json").exists()