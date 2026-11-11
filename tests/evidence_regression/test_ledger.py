"""Ledger integration: the write path must refuse RED edits and leave the ledger untouched."""
from __future__ import annotations

import json

import pytest

from tools.evidence_regression import ledger as ledger_mod


def _ledger(tmp_path, levels: dict) -> "object":
    path = tmp_path / "capability_ledger.json"
    path.write_text(json.dumps({
        "schema_version": "test",
        "capabilities": [{"id": cid, "claimed_level": lv, "domain": "synthetic"} for cid, lv in levels.items()],
    }), encoding="utf-8")
    return path


def test_red_edit_is_refused_and_nothing_is_written(tmp_path, registry):
    path = _ledger(tmp_path, {"cap.alpha": "REAL", "cap.beta": "REAL"})
    before = path.read_text(encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    with pytest.raises(ledger_mod.GateBlocked) as excinfo:
        ledger_mod.apply_changes(path, {"cap.alpha": "MOCK"}, registry, audit_path=audit)
    assert path.read_text(encoding="utf-8") == before  # untouched
    summary = ledger_mod.blocked_summary(excinfo.value)
    assert summary["verdict"] == "RED"
    assert summary["capabilities"] == ["cap.alpha"]
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["written"] is False
    assert records[-1]["verdict"] == "RED"


def test_ok_edit_is_written_with_audit_trail(tmp_path, registry):
    path = _ledger(tmp_path, {"cap.alpha": "REAL", "cap.delta": "MOCK"})
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "ledger_out.json"
    result = ledger_mod.apply_changes(path, {"cap.delta": "REAL"}, registry, out_path=out, audit_path=audit)
    assert result["gate"]["verdict"] == "OK"
    written = json.loads(out.read_text(encoding="utf-8"))
    levels = {cap["id"]: cap["claimed_level"] for cap in written["capabilities"]}
    assert levels["cap.delta"] == "REAL"
    assert written["last_gated_edit"]["verdict"] == "OK"
    log = next(cap for cap in written["capabilities"] if cap["id"] == "cap.delta")["level_change_log"]
    assert log[-1]["from"] == "MOCK" and log[-1]["to"] == "REAL"
    record = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert record["written"] is True and record["gate_digest"]


def test_acknowledged_degradation_can_be_written(tmp_path, registry):
    path = _ledger(tmp_path, {"cap.beta": "REAL"})
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "ledger_out.json"
    blocked = None
    with pytest.raises(ledger_mod.GateBlocked) as excinfo:
        ledger_mod.apply_changes(path, {"cap.beta": "MOCK"}, registry, out_path=out, audit_path=audit)
    blocked = excinfo.value
    digest = blocked.result.probe_reports[0].digest()
    result = ledger_mod.apply_changes(path, {"cap.beta": "MOCK"}, registry, out_path=out, audit_path=audit,
                                      ack={"capability_id": "cap.beta", "probe_digest": digest,
                                           "reason": "downstream evidence retracted", "by": "tester"})
    assert result["gate"]["verdict"] == "ACK"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {cap["id"]: cap["claimed_level"] for cap in written["capabilities"]}["cap.beta"] == "MOCK"


def test_missing_capability_is_appended(tmp_path, registry):
    path = _ledger(tmp_path, {"cap.alpha": "REAL"})
    out = tmp_path / "ledger_out.json"
    result = ledger_mod.apply_changes(path, {"cap.new": "REAL"}, registry, out_path=out,
                                      audit_path=tmp_path / "audit.jsonl")
    written = json.loads(out.read_text(encoding="utf-8"))
    ids = {cap["id"] for cap in written["capabilities"]}
    assert "cap.new" in ids  # appended: platform must record an uncovered capability rather than lose it
    assert result["gate"]["verdict"] == "ACK"