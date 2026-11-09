#!/usr/bin/env python3
"""写入边界门成熟化测试：R4 大文件拦截 + 事件账本 + 证据哈希。

覆盖
----
* R4 大文件/二进制判据（binary_gate 模块）
* 事件账本（ledger 模块）
* 违规证据哈希（evidence_sha256 字段）
* CLI 集成（--no-ledger, --ledger-path, --context）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.write_gate import (
    BinaryDecision, Change, Verdict, Violation,
    append_event, build_event, check_binary, check_binary_violations,
    evaluate_changes, load_policy, read_ledger, record_verdict,
)
from tools.write_gate.model import digest

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "data" / "write_gate" / "policy.json"


def load_test_policy(**overrides):
    """装载测试用策略（从文件 + 可选覆盖）。"""
    return load_policy(str(POLICY_PATH), overrides=overrides if overrides else None)


# ─────────────────────────── R4: 大文件/二进制判据 ───────────────────────────
class TestBinaryGate:
    """R4 大文件/二进制拦截测试。"""

    def test_small_file_allowed(self):
        """小文件在阈值内应放行。"""
        cfg = load_test_policy()
        decision = check_binary("src/main.py", cfg, file_size=1024)
        assert decision.allowed is True
        assert decision.code == "allow"

    def test_large_file_blocked(self):
        """超阈值文件应拦截。"""
        cfg = load_test_policy()
        decision = check_binary("src/large.dat", cfg, file_size=10 * 1024 * 1024)
        assert decision.allowed is False
        assert decision.code == "file_too_large"
        assert decision.rule_id == "binary.max_bytes"

    def test_large_file_in_artifacts_allowed(self):
        """artifacts/ 内的大文件应放行（豁免列表）。"""
        cfg = load_test_policy()
        decision = check_binary("artifacts/large.dat", cfg, file_size=10 * 1024 * 1024)
        assert decision.allowed is True
        assert decision.code == "binary_allow_glob"

    def test_binary_content_blocked(self):
        """二进制内容应拦截。"""
        cfg = load_test_policy()
        decision = check_binary("src/image.bin", cfg, is_binary=True)
        assert decision.allowed is False
        assert decision.code == "binary_content"

    def test_binary_content_in_data_allowed(self):
        """data/ 内的二进制应放行（豁免列表）。"""
        cfg = load_test_policy()
        decision = check_binary("data/image.bin", cfg, is_binary=True)
        assert decision.allowed is True

    def test_png_allowed(self):
        """PNG 文件应放行（扩展名豁免）。"""
        cfg = load_test_policy()
        decision = check_binary("docs/figure.png", cfg, is_binary=True, file_size=1024)
        assert decision.allowed is True

    def test_disabled_policy(self):
        """禁用 binary 组应全部放行。"""
        cfg = load_test_policy(binary={"enabled": False})
        decision = check_binary("src/large.bin", cfg, file_size=100 * 1024 * 1024, is_binary=True)
        assert decision.allowed is True
        assert decision.code == "binary_disabled"

    def test_violations_list(self):
        """check_binary_violations 应返回违规列表。"""
        cfg = load_test_policy()
        violations = check_binary_violations("src/large.bin", cfg, file_size=10 * 1024 * 1024)
        assert len(violations) == 1
        assert violations[0].category == "binary"
        assert violations[0].code == "file_too_large"

    def test_no_violations_for_small_file(self):
        """小文件应无违规。"""
        cfg = load_test_policy()
        violations = check_binary_violations("src/main.py", cfg, file_size=1024)
        assert len(violations) == 0


# ─────────────────────────── 事件账本 ───────────────────────────
class TestLedger:
    """事件账本测试。"""

    def test_build_event_structure(self):
        """事件结构应包含必要字段。"""
        cfg = load_test_policy()
        verdict = Verdict(policy_ref={"sha256": "abc123"})
        verdict.add(Violation("test_code", "whitelist", "test/path", rule_id="test_rule"))
        event = build_event(verdict, context={"mission_id": "m1234"})
        assert "timestamp" in event
        assert event["verdict"] == "FAIL"
        assert event["blocked"] == 1
        assert event["context"]["mission_id"] == "m1234"
        assert "policy_sha256" in event

    def test_append_and_read_ledger(self):
        """账本应可追加和读取。"""
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "events.jsonl"
            event1 = {"timestamp": "2024-01-01T00:00:00Z", "verdict": "FAIL", "digest": "abc"}
            event2 = {"timestamp": "2024-01-01T00:01:00Z", "verdict": "FAIL", "digest": "def"}
            assert append_event(event1, ledger_path) is True
            assert append_event(event2, ledger_path) is True
            events = read_ledger(ledger_path)
            assert len(events) == 2
            assert events[0]["digest"] == "abc"
            assert events[1]["digest"] == "def"

    def test_record_verdict_only_on_fail(self):
        """只有 FAIL 判决才记账。"""
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "events.jsonl"
            pass_verdict = Verdict()  # 默认 PASS
            fail_verdict = Verdict(policy_ref={"sha256": "test123"})
            fail_verdict.add(Violation("test", "whitelist", "path"))
            
            record_verdict(pass_verdict, ledger_path)
            assert not ledger_path.exists()  # PASS 不写账本
            
            record_verdict(fail_verdict, ledger_path)
            assert ledger_path.exists()
            events = read_ledger(ledger_path)
            assert len(events) == 1

    def test_ledger_failure_does_not_raise(self):
        """账本写入失败不应抛异常。"""
        # 使用不可写路径（/dev/null 是文件不是目录，parent 创建会失败）
        result = append_event({"test": True}, "/dev/null/impossible/events.jsonl")
        assert result is False  # 返回 False 而非抛异常

    def test_read_empty_ledger(self):
        """读不存在的账本应返回空列表。"""
        events = read_ledger("/nonexistent/ledger.jsonl")
        assert events == []


# ─────────────────────────── 证据哈希 ───────────────────────────
class TestEvidenceHash:
    """违规证据哈希测试。"""

    def test_violation_has_evidence_sha256(self):
        """违规应含 evidence_sha256 字段。"""
        cfg = load_test_policy()
        changes = [Change(".git/config")]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        assert verdict.verdict == "FAIL"
        assert len(verdict.violations) > 0
        for v in verdict.violations:
            assert v.evidence_sha256 is not None
            assert len(v.evidence_sha256) == 16  # 截断到 16 字符

    def test_evidence_hash_deterministic(self):
        """相同输入应得相同证据哈希。"""
        cfg = load_test_policy()
        changes = [Change(".git/config")]
        v1 = evaluate_changes(changes, cfg, root=REPO_ROOT)
        v2 = evaluate_changes(changes, cfg, root=REPO_ROOT)
        assert v1.violations[0].evidence_sha256 == v2.violations[0].evidence_sha256

    def test_evidence_hash_in_json(self):
        """证据哈希应出现在 JSON 输出中。"""
        cfg = load_test_policy()
        changes = [Change(".git/config")]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        payload = verdict.to_json()
        assert "violations" in payload
        assert len(payload["violations"]) > 0
        assert "evidence_sha256" in payload["violations"][0]

    def test_secret_violation_has_line_evidence(self):
        """凭证违规应有行级证据哈希。"""
        cfg = load_test_policy()
        # 使用 samples 模块生成的样例
        from tools.write_gate.samples import credential_assignment
        content = credential_assignment()
        changes = [Change("test.py", content=content)]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        secret_violations = [v for v in verdict.violations if v.category == "secret"]
        assert len(secret_violations) > 0, f"Expected secret violations, got: {[v.category for v in verdict.violations]}"
        for v in secret_violations:
            assert v.evidence_sha256 is not None


# ─────────────────────────── 集成测试 ───────────────────────────
class TestIntegration:
    """集成测试：四道判据协同工作。"""

    def test_four_rules_combined(self):
        """四道判据应同时生效。"""
        cfg = load_test_policy()
        from tools.write_gate.samples import credential_assignment
        changes = [
            Change(".git/config"),  # R2: 跨仓
            Change("test.py", content=credential_assignment()),  # R3: 凭证
            Change("large.bin", content="x" * (6 * 1024 * 1024)),  # R4: 大文件
        ]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        assert verdict.verdict == "FAIL"
        categories = {v.category for v in verdict.violations}
        assert "cross_repo" in categories  # R2
        assert "secret" in categories  # R3
        assert "binary" in categories  # R4

    def test_verdict_json_complete(self):
        """判决 JSON 应包含所有必要字段。"""
        cfg = load_test_policy()
        changes = [Change(".git/config")]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        payload = verdict.to_json()
        assert payload["schema"] == "tp.write_gate.verdict/v2"
        assert "verdict" in payload
        assert "violations" in payload
        assert "totals" in payload
        assert "policy" in payload
        assert "digest" in payload
        assert payload["totals"]["by_category"]["cross_repo"] > 0

    def test_policy_ref_in_verdict(self):
        """判决应含策略引用（审计链）。"""
        cfg = load_test_policy()
        changes = [Change("test.py")]
        verdict = evaluate_changes(changes, cfg, root=REPO_ROOT)
        payload = verdict.to_json()
        assert "policy" in payload
        assert "sha256" in payload["policy"]


# ─────────────────────────── CLI 集成 ───────────────────────────
class TestCLIIntegration:
    """CLI 集成测试。"""

    def test_cli_no_ledger_flag(self):
        """--no-ledger 应阻止账本写入。"""
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "events.jsonl"
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
            result = subprocess.run(
                [sys.executable, "-m", "tools.write_gate.cli",
                 "--no-ledger", "--ledger-path", str(ledger_path),
                 "paths", ".git/config"],
                capture_output=True, text=True, cwd=str(REPO_ROOT), env=env
            )
            assert result.returncode == 2  # BLOCK
            assert not ledger_path.exists()

    def test_cli_with_context(self):
        """--context 应传递到事件账本。"""
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "events.jsonl"
            context = json.dumps({"mission_id": "m9999", "branch": "agent/test"})
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
            result = subprocess.run(
                [sys.executable, "-m", "tools.write_gate.cli",
                 "--ledger-path", str(ledger_path),
                 "--context", context,
                 "paths", ".git/config"],
                capture_output=True, text=True, cwd=str(REPO_ROOT), env=env
            )
            assert result.returncode == 2  # BLOCK
            assert ledger_path.exists()
            events = read_ledger(ledger_path)
            assert len(events) == 1
            assert events[0]["context"]["mission_id"] == "m9999"
