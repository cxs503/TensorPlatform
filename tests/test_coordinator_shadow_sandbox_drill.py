#!/usr/bin/env python3
"""IN28-N06 沙箱 fail-closed + 部署门 + 回滚演练用例。

判据 (d)(e) 的机器面：
- (e) 沙箱外生产写 = 0：三种逃逸（``..`` / 绝对路径 / 符号链接）必须全部被拒；
- (d) 坏部署被拦、回滚 < 5 min、状态逐字节一致，且"校验器能发现篡改"（负控）。
"""
from __future__ import annotations

import json
import os

import pytest

from tools.coordinator_shadow import drill as D
from tools.coordinator_shadow import sandbox as S


# ---------------------------------------------------------------------------
# fail-closed 沙箱
# ---------------------------------------------------------------------------


def test_sandbox_blocks_dotdot_absolute_and_symlink(tmp_path):
    root = tmp_path / "sandbox"
    guard = S.SandboxGuard(str(root))
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(S.SandboxViolation):
        guard.write_json(str(root / ".." / "outside" / "x.json"), {"a": 1})
    with pytest.raises(S.SandboxViolation):
        guard.write_json(str(outside / "x.json"), {"a": 1})

    link = root / "link"
    os.symlink(outside, link)
    with pytest.raises(S.SandboxViolation):
        guard.write_json(str(link / "x.json"), {"a": 1})

    assert not (outside / "x.json").exists()
    summary = guard.summary()
    assert summary["committed_outside"] == 0
    assert summary["fail_closed"] is True
    assert summary["rejected"] >= 3


def test_sandbox_allows_declared_extra_root_only(tmp_path):
    root = tmp_path / "sandbox"
    art = tmp_path / "artifacts"
    guard = S.SandboxGuard(str(root), extra_roots=(str(art),))
    guard.write_json(str(art / "ok.json"), {"ok": True})
    with pytest.raises(S.SandboxViolation):
        guard.write_json(str(tmp_path / "elsewhere" / "no.json"), {"no": True})
    assert (art / "ok.json").exists()


def test_self_check_reports_zero_production_writes(tmp_path):
    report = S.self_check(str(tmp_path / "sbx"))
    assert report["escape_cases"] >= 2
    assert report["escape_blocked"] == report["escape_cases"]
    assert report["outside_committed"] == 0
    assert report["production_writes"] == 0
    assert report["fail_closed"] is True


# ---------------------------------------------------------------------------
# 部署门
# ---------------------------------------------------------------------------


def test_gate_accepts_good_and_rejects_three_bad_cases(tmp_path):
    guard = S.SandboxGuard(str(tmp_path))
    state = tmp_path / "state"
    D._write_state(guard, str(state), 1, {"mode": "live"}, {"policy.json": "{}\n"})
    assert D.DeployGate(current_version=0).check(str(state)).ok is True
    assert D.DeployGate(current_version=1).check(str(state)).ok is False  # 版本不单调

    bad = tmp_path / "bad"
    D._write_state(guard, str(bad), 2, {"mode": "live"}, {"policy.json": "{}\n"},
                   schema="wrong/0")
    verdict = D.DeployGate(current_version=1).check(str(bad))
    assert verdict.ok is False and any("bad-schema" in r for r in verdict.reasons)

    tampered = tmp_path / "tampered"
    D._write_state(guard, str(tampered), 2, {"mode": "live"}, {"policy.json": "{}\n"})
    with open(tampered / "shadow_state.json", "a") as fh:
        fh.write(" ")
    verdict = D.DeployGate(current_version=1).check(str(tampered))
    assert verdict.ok is False and any("hash-mismatch" in r for r in verdict.reasons)


def test_verify_identical_is_not_vacuous(tmp_path):
    guard = S.SandboxGuard(str(tmp_path))
    a = tmp_path / "a"
    b = tmp_path / "b"
    D._write_state(guard, str(a), 1, {"mode": "live"}, {"policy.json": "{}\n"})
    D._copy_tree(str(a), str(b), guard)   # 同一份状态的副本：必须逐字节一致
    ma, mb = D.manifest(str(a)), D.manifest(str(b))
    assert D.verify_identical(ma, mb) is True
    with open(b / "policy.json", "a") as fh:      # 改 1 个字节
        fh.write(" ")
    assert D.verify_identical(ma, D.manifest(str(b))) is False


# ---------------------------------------------------------------------------
# 回滚演练
# ---------------------------------------------------------------------------


def test_rollback_drill_passes_all_criteria(tmp_path):
    report = D.rollback_drill(str(tmp_path / "drill"))
    crit = report["criteria"]
    assert crit["bad_deploys_blocked"] is True
    assert crit["state_untouched_by_bad"] is True
    assert crit["good_deploy_accepted"] is True
    assert crit["state_changed_by_good_deploy"] is True
    assert crit["rollback_within_budget"] is True
    assert crit["rollback_seconds"] < D.ROLLBACK_BUDGET_SECONDS
    assert crit["state_identical_after_rollback"] is True
    assert crit["negative_control_detects_tamper"] is True
    assert crit["pass"] is True
    assert report["sandbox"]["committed_outside"] == 0


def test_rollback_drill_detects_a_broken_gate(tmp_path, monkeypatch):
    """负控的负控：把校验器弄坏（永远 True），演练必须 FAIL 而非假 PASS。"""
    monkeypatch.setattr(D, "verify_identical", lambda a, b: True)
    report = D.rollback_drill(str(tmp_path / "drill2"))
    assert report["criteria"]["negative_control_detects_tamper"] is False
    assert report["criteria"]["pass"] is False


def test_drill_artifacts_are_written_inside_root(tmp_path):
    root = tmp_path / "drill3"
    D.rollback_drill(str(root))
    for rel in ("state/manifest.json", "snapshots/v1/shadow_state.json"):
        assert (root / rel).exists(), rel
    saved = json.loads((root / "state" / "manifest.json").read_text())
    assert saved["schema"] == D.STATE_SCHEMA
    assert saved["version"] == 1  # 回滚后回到基线版本