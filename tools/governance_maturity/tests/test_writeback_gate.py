"""Tests for the metric write-back gate (synthetic metrics only)."""

from __future__ import annotations

import json

import pytest

from tools.governance_maturity.writeback_gate import (
    GateConfig,
    Identity,
    MetricWrite,
    MetricWritebackGate,
    WritebackRejected,
    evaluate_expression,
)

NOW = "2026-11-10T12:00:00Z"
DIGEST = "sha256:" + "a" * 64


def config(**overrides):
    doc = {
        "recompute_rtol": 1e-9,
        "regression_rtol": 1e-6,
        "freshness_slo_s": 86400.0,
        "allowed_units": ["ratio", "count"],
        "direction": {"repro_rate": "up"},
        "identities": [
            {"name": "share", "expression": "numerator / denominator",
             "fields": ["numerator", "denominator"], "target": "value"},
        ],
    }
    doc.update(overrides)
    return GateConfig.from_dict(doc)


def write(**overrides):
    payload = {
        "metric": "repro_rate",
        "window": "2026-W45",
        "value": 0.95,
        "unit": "ratio",
        "expression": "reproduced / total",
        "inputs": {"reproduced": 19.0, "total": 20.0},
        "source_digest": DIGEST,
        "evidence_count": 20,
        "written_at": NOW,
    }
    payload.update(overrides)
    return MetricWrite.from_dict(payload)


@pytest.fixture()
def gate(tmp_path):
    return MetricWritebackGate(config(), tmp_path / "ledger.jsonl")


def test_accepts_valid_write(gate):
    receipt = gate.write(write(), now=NOW)
    assert receipt["status"] == "accepted"
    assert receipt["ledger_length"] == 1
    assert gate.verify_chain()["ok"]


def test_recompute_mismatch_rejected(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(value=0.5), now=NOW)
    assert exc.value.code == "recompute_mismatch"


def test_expression_cannot_call_arbitrary_code():
    with pytest.raises(WritebackRejected):
        evaluate_expression("__import__('os').system('true')", {})


def test_expression_nonfinite_rejected():
    with pytest.raises(WritebackRejected):
        evaluate_expression("1.0 / 0.0", {})


def test_unit_not_allowed(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(unit="widgets"), now=NOW)
    assert exc.value.code == "unit_not_allowed"


def test_malformed_provenance(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(source_digest="deadbeef"), now=NOW)
    assert exc.value.code == "provenance_malformed"


def test_stale_write_rejected(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(written_at="2026-11-01T00:00:00Z"), now=NOW)
    assert exc.value.code == "stale_write"


def test_future_write_rejected(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(written_at="2026-11-12T00:00:00Z"), now=NOW)
    assert exc.value.code == "future_write"


def test_idempotent_duplicate(gate):
    first = gate.write(write(), now=NOW)
    second = gate.write(write(), now=NOW)
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["chain"] == first["chain"]
    assert gate.verify_chain()["length"] == 1


def test_duplicate_survives_reload(tmp_path):
    path = tmp_path / "ledger.jsonl"
    gate_a = MetricWritebackGate(config(), path)
    gate_a.write(write(), now=NOW)
    gate_b = MetricWritebackGate(config(), path)
    again = gate_b.write(write(), now=NOW)
    assert again["status"] == "duplicate"
    assert gate_b.verify_chain()["length"] == 1


def test_regression_without_new_evidence_detected(gate):
    gate.write(write(), now=NOW)
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(value=0.80, inputs={"reproduced": 16.0, "total": 20.0}), now=NOW)
    assert exc.value.code == "regression_without_new_evidence"


def test_rollback_to_historical_value_detected(gate):
    gate.write(write(value=0.80, evidence_count=10,
                    inputs={"reproduced": 16.0, "total": 20.0}), now=NOW)
    gate.write(write(value=0.95, evidence_count=20), now=NOW)
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(value=0.80, evidence_count=25,
                        inputs={"reproduced": 16.0, "total": 20.0}), now=NOW)
    assert exc.value.code == "rollback_to_historical_value"


def test_legitimate_update_with_evidence_accepted(gate):
    gate.write(write(), now=NOW)
    receipt = gate.write(write(value=0.96, evidence_count=25,
                               inputs={"reproduced": 24.0, "total": 25.0}), now=NOW)
    assert receipt["status"] == "accepted"
    assert gate.verify_chain()["length"] == 2


def test_direction_violation_detected(tmp_path):
    g = MetricWritebackGate(config(direction={"repro_rate": "up"}), tmp_path / "dir.jsonl")
    g.write(write(), now=NOW)
    with pytest.raises(WritebackRejected) as exc:
        g.write(write(value=0.90, evidence_count=21,
                      inputs={"reproduced": 18.0, "total": 20.0}), now=NOW)
    assert exc.value.code == "direction_violation"


def test_identity_violation_detected(tmp_path):
    cfg = config(identities=[{"name": "share", "expression": "numerator / denominator",
                              "fields": ["numerator", "denominator"], "target": "value"}])
    g = MetricWritebackGate(cfg, tmp_path / "identity.jsonl")
    w = write(value=0.5, expression="numerator * 2",
              inputs={"numerator": 0.25, "denominator": 1.0})
    with pytest.raises(WritebackRejected) as exc:
        g.write(w, now=NOW)
    assert exc.value.code == "identity_violation"


def test_identity_target_field_checked(tmp_path):
    cfg = config(identities=[{"name": "total", "expression": "left + right",
                              "fields": ["left", "right"], "target": "declared_total"}])
    g = MetricWritebackGate(cfg, tmp_path / "identity2.jsonl")
    w = write(value=2.0, expression="left + right",
              inputs={"left": 1.0, "right": 1.0, "declared_total": 3.0})
    with pytest.raises(WritebackRejected) as exc:
        g.write(w, now=NOW)
    assert exc.value.code == "identity_violation"


def test_chain_detects_history_edit(tmp_path):
    path = tmp_path / "ledger.jsonl"
    g = MetricWritebackGate(config(), path)
    g.write(write(), now=NOW)
    g.write(write(value=0.96, evidence_count=25,
                  inputs={"reproduced": 24.0, "total": 25.0}), now=NOW)
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["value"] = 0.0
    path.write_text(json.dumps(tampered) + "\n" + lines[1] + "\n")
    g2 = MetricWritebackGate(config(), path)
    verdict = g2.verify_chain()
    assert verdict["ok"] is False
    assert verdict["reason"] in ("digest", "previous_link")


def test_negative_evidence_count_rejected(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(evidence_count=-1), now=NOW)
    assert exc.value.code == "evidence_negative"


def test_non_finite_value_rejected(gate):
    with pytest.raises(WritebackRejected):
        gate.write(write(value=float("nan")), now=NOW)


def test_written_at_malformed(gate):
    with pytest.raises(WritebackRejected) as exc:
        gate.write(write(written_at="not-a-time"), now=NOW)
    assert exc.value.code == "written_at_malformed"