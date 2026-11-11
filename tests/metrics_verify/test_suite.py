"""End-to-end suite: the runner must emit a machine-checkable acceptance report."""

from __future__ import annotations

import json

import pytest

from tools.metrics_verify import suite
from tools.metrics_verify.sim import FleetConfig


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("metrics_verify")
    return suite.run_all(
        out_dir=out,
        pulse_cfg=FleetConfig(n_agents=20, n_heartbeats=200, seed=2026),
        chronicle_events=2000,
        portrait_agents=6,
        portrait_missions=80,
        make_figures=True,
        pytest_probe=True,
    ), out


def test_all_seven_criteria_are_present_and_pass(report):
    payload, _ = report
    assert set(payload["criteria"]) == {
        "a_pulse_latency", "b_pulse_loss_detection", "c_chronicle_integrity",
        "d_chronicle_disorder", "e_portrait_consistency", "f_portrait_drift",
        "g_harness_hygiene",
    }
    assert all(c["pass"] for c in payload["criteria"].values()), payload["criteria"]
    assert payload["overall_pass"] is True


def test_negative_controls_pass(report):
    payload, _ = report
    controls = payload["negative_controls"]
    assert controls["pulse_stall_sensitivity"]["detected_violation"] is True
    assert controls["pulse_loss_false_positive"]["pass"] is True
    assert controls["chronicle_drop"]["detected"] is True
    assert controls["chronicle_duplicate"]["detected"] is True
    assert controls["portrait_corruption"]["detected"] is True


def test_report_json_and_markdown_are_written_from_the_payload(report):
    payload, out = report
    disk = json.loads((out / "report.json").read_text())
    assert disk["overall_pass"] == payload["overall_pass"]
    assert disk["git_commit"] and disk["spec"]["portrait_error_max"] == 0.05
    markdown = (out / "report.md").read_text()
    assert "| a_pulse_latency |" in markdown
    assert "Negative controls" in markdown
    # every criterion id appears in the rendered table
    for cid in payload["criteria"]:
        assert cid in markdown


def test_figures_are_generated_and_hashed(report):
    payload, out = report
    figures = payload["evidence"]["figures"]
    assert len(figures) == 4
    for name, meta in figures.items():
        assert (out / f"{name}.png").exists(), name
        assert len(meta["sha256"]) == 64


def test_evidence_block_pins_the_modules_under_test(report):
    payload, _ = report
    hashes = payload["evidence"]["modules_under_test_sha256"]
    assert set(hashes) == {"tools/pulse/saturation_pulse.py",
                           "tools/worker_portrait/portrait.py"}
    assert all(len(h) == 64 for h in hashes.values())
    assert payload["evidence"]["under_test_versions"]["worker_portrait_schema"] == "worker_portrait/v1"