"""Append-only sha256-chained audit log tests (p262w1-N07, acceptance F4)."""
from __future__ import annotations

import json

import pytest

from tools.capability_auto_audit.append_only_log import (
    GENESIS_SHA256,
    AppendOnlyAuditLog,
    AuditLogCorrupted,
)


def _append(log, seq_marker: int, action: str = "AUTO_AUDIT") -> None:
    log.append(action=action, capability_id="syn.alpha", from_level="WITHHELD",
               to_level="REAL", reason_code="CHAIN_COMPLETE",
               evidence_digest=f"digest-{seq_marker}", payload={"i": seq_marker},
               ts=f"2026-11-10T00:00:{seq_marker:02d}Z")


def test_ten_operations_produce_ten_verifiable_entries(tmp_path):
    log = AppendOnlyAuditLog(tmp_path / "audit_log.jsonl")
    for i in range(1, 11):
        _append(log, i)
    assert len(log) == 10
    verify = log.verify()
    assert verify["ok"] is True and verify["entries"] == 10
    assert verify["head_sha256"] == log.entries[-1].sha256

    # every entry's sha256 is recomputable from its own payload
    for entry in log.entries:
        assert entry.recompute_sha256() == entry.sha256

    # and the chain links are intact
    prev = GENESIS_SHA256
    for entry in log.entries:
        assert entry.prev_sha256 == prev
        prev = entry.sha256
    assert [e.seq for e in log.entries] == list(range(1, 11))


def test_log_is_append_only_and_survives_reopen(tmp_path):
    path = tmp_path / "audit_log.jsonl"
    log = AppendOnlyAuditLog(path)
    for i in range(1, 4):
        _append(log, i)
    size_before = path.stat().st_size
    reopened = AppendOnlyAuditLog(path)
    assert len(reopened) == 3
    _append(reopened, 4)
    assert path.stat().st_size > size_before            # grew, never rewritten
    assert len(reopened.verify()["errors"]) == 0


def test_tampered_entry_is_detected(tmp_path):
    path = tmp_path / "audit_log.jsonl"
    log = AppendOnlyAuditLog(path)
    for i in range(1, 4):
        _append(log, i)
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["to_level"] = "WITHHELD"                    # pretend a promotion never happened
    lines[1] = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verify = AppendOnlyAuditLog(path).verify()
    assert verify["ok"] is False
    assert any("sha256 mismatch" in err for err in verify["errors"])


def test_deleted_entry_is_detected(tmp_path):
    path = tmp_path / "audit_log.jsonl"
    log = AppendOnlyAuditLog(path)
    for i in range(1, 5):
        _append(log, i)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
    verify = AppendOnlyAuditLog(path).verify()
    assert verify["ok"] is False
    assert any("seq" in err for err in verify["errors"])


def test_append_refuses_a_corrupted_log(tmp_path):
    path = tmp_path / "audit_log.jsonl"
    log = AppendOnlyAuditLog(path)
    _append(log, 1)
    path.write_text(path.read_text(encoding="utf-8").replace("REAL", "MOCK"),
                    encoding="utf-8")
    corrupted = AppendOnlyAuditLog(path)
    assert corrupted.integrity["ok"] is False
    with pytest.raises(AuditLogCorrupted):
        _append(corrupted, 2)


def test_digest_sidecar_records_head(tmp_path):
    log = AppendOnlyAuditLog(tmp_path / "audit_log.jsonl")
    _append(log, 1)
    sidecar = log.digest_sidecar(tmp_path / "audit_head.json")
    assert sidecar["entries"] == 1
    assert sidecar["head_sha256"] == log.head_sha256
    assert json.loads((tmp_path / "audit_head.json").read_text())["ok"] is True


def test_tail_returns_the_last_entries(tmp_path):
    log = AppendOnlyAuditLog(tmp_path / "audit_log.jsonl")
    for i in range(1, 6):
        _append(log, i)
    tail = log.tail(2)
    assert [t["payload"]["i"] for t in tail] == [4, 5]
    assert tail[-1]["sha256"] == log.head_sha256