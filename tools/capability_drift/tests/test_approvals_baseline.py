"""审批与基线更新单元测试（A4 的判据自证层）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.capability_drift import approvals, baseline as baseline_mod

CAP = "synth.solver.energy_00"
VALUES = {"energy_00": 1.25}


def _signed(key: str, cap: str = CAP, values=None):
    values = VALUES if values is None else values
    return approvals.make_approval(
        "baseline_update", "owner-test", key,
        capability=cap, values_sha256=approvals.sha256_payload(values),
    )


def test_missing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(approvals.KEY_ENV, raising=False)
    record = _signed("some-key")
    with pytest.raises(approvals.ApprovalError):
        baseline_mod.update_baseline(tmp_path / "baseline.json", CAP, VALUES, record, key=None)


def test_unsigned_update_rejected_and_sha_unchanged(tmp_path, key_env):
    path = tmp_path / "baseline.json"
    approval = _signed(key_env)
    baseline_mod.update_baseline(path, CAP, VALUES, approval, key=key_env)
    sha_before = baseline_mod.baseline_sha256(path)
    unsigned = {k: v for k, v in approval.items() if k != "signature"}
    with pytest.raises(approvals.ApprovalError):
        baseline_mod.update_baseline(path, CAP, {"energy_00": 9.9}, unsigned, key=key_env)
    assert baseline_mod.baseline_sha256(path) == sha_before


def test_signed_update_changes_sha_and_writes_audit(tmp_path, key_env):
    path = tmp_path / "baseline.json"
    audit = tmp_path / "audit.jsonl"
    first = baseline_mod.update_baseline(path, CAP, VALUES, _signed(key_env), key=key_env,
                                        audit_path=audit)
    assert first["changed"] is True
    assert baseline_mod.baseline_sha256(path) == first["new_sha256"]
    # 数字变化 → 内容指纹必须变化（A4 判据用的就是这个）
    new_values = {"energy_00": 1.30}
    approval = approvals.make_approval(
        "baseline_update", "owner-test", key_env,
        capability=CAP, values_sha256=approvals.sha256_payload(new_values),
    )
    second = baseline_mod.update_baseline(path, CAP, new_values, approval, key=key_env,
                                          audit_path=audit)
    assert second["old_sha256"] == first["new_sha256"]
    assert second["new_sha256"] != second["old_sha256"]
    assert audit.exists() and len(audit.read_text().strip().splitlines()) == 2


def test_identical_values_same_second_keep_sha(tmp_path, key_env):
    """同一秒内写入相同数字 → 文件字节完全相同（sha 稳定，不虚报变化）。"""
    path = tmp_path / "baseline.json"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = baseline_mod.update_baseline(path, CAP, VALUES, _signed(key_env), key=key_env, now=now)
    second = baseline_mod.update_baseline(path, CAP, VALUES, _signed(key_env), key=key_env, now=now)
    assert first["new_sha256"] == second["new_sha256"]


def test_wrong_capability_rejected(tmp_path, key_env):
    with pytest.raises(approvals.ApprovalError):
        baseline_mod.update_baseline(
            tmp_path / "baseline.json", CAP, VALUES, _signed(key_env, cap="synth.other"),
            key=key_env,
        )


def test_values_tampered_after_signing_rejected(tmp_path, key_env):
    with pytest.raises(approvals.ApprovalError):
        baseline_mod.update_baseline(
            tmp_path / "baseline.json", CAP, {"energy_00": 7.7}, _signed(key_env), key=key_env,
        )


def test_forged_signature_rejected(tmp_path, key_env):
    approval = _signed(key_env)
    approval["signature"] = "f" * 64
    with pytest.raises(approvals.ApprovalError):
        baseline_mod.update_baseline(tmp_path / "baseline.json", CAP, VALUES, approval,
                                     key=key_env)


def test_expiry_detection():
    now = datetime.now(timezone.utc)
    assert approvals.approval_is_expired({"expires_at": (now - timedelta(days=1)).isoformat()})
    assert not approvals.approval_is_expired({"expires_at": (now + timedelta(days=1)).isoformat()})
    assert not approvals.approval_is_expired({})
    assert approvals.approval_is_expired({"expires_at": "not-a-date"})  # fail-closed


def test_canonical_payload_is_order_independent():
    assert approvals.sha256_payload({"a": 1, "b": 2}) == approvals.sha256_payload({"b": 2, "a": 1})