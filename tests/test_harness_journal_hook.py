#!/usr/bin/env python3
"""Harness v3 集成钩子（tp_worker 侧）测试：证据驱动步骤 + 无日志降级声明。"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness_journal as HJ  # noqa: E402
import harness_journal_hook as HK  # noqa: E402


@pytest.fixture()
def jroot(tmp_path, monkeypatch):
    r = tmp_path / "journals"
    monkeypatch.setenv("HARNESS_JOURNAL_ROOT", str(r))
    monkeypatch.setenv("HARNESS_JOURNAL", "1")
    return r


def test_steps_come_from_disk_evidence(tmp_path, jroot):
    w = tmp_path / "wt"
    (w / "docs").mkdir(parents=True)
    (w / "docs" / "report.md").write_text("x" * 10)
    (w / "__pycache__").mkdir()
    (w / "__pycache__" / "a.pyc").write_text("junk")
    mission = {"id": 301, "goal": "写报告", "repo": "TensorPlatform"}
    inj = HK.claim_injection(mission, w)
    assert "你在续跑，不是从零开始" in inj or "从零" in inj      # 首次=无日志，显式声明
    HK.checkpoint(mission, w, tool_calls=3, next_intent="继续写图")
    steps = [s["id"] for s in HK.steps_from_evidence(w)]
    assert "docs/report.md" in steps
    assert all("__pycache__" not in s for s in steps), "噪声产物不得进日志"
    st = HJ.read(None, 301)
    m = HJ.merge(st["entries"])
    assert m["calls_used"] == 3 and m["next_intent"] == "继续写图"


def test_resume_after_restart_dedups_verified_products(tmp_path, jroot):
    w = tmp_path / "wt"
    (w / "docs").mkdir(parents=True)
    (w / "docs" / "a.md").write_text("a")
    mission = {"id": 302, "goal": "产出 a.md"}
    HK.claim_injection(mission, w)
    HK.checkpoint(mission, w, tool_calls=2, next_intent="产出 b.md")
    # 模拟席位重启后重领同一单
    inj = HK.claim_injection(mission, w)
    assert "你在续跑，不是从零开始" in inj
    assert "docs/a.md" in inj, "已落盘产物必须出现在续跑简报里"
    assert "已用工具调用 2" in inj, "预算用量跨尝试累计"


def test_verified_product_missing_is_not_resumed(tmp_path, jroot):
    w = tmp_path / "wt"
    (w / "docs").mkdir(parents=True)
    (w / "docs" / "a.md").write_text("a")
    mission = {"id": 303, "goal": "产出 a.md"}
    HK.claim_injection(mission, w)
    HK.checkpoint(mission, w, tool_calls=1)
    os.remove(w / "docs" / "a.md")             # 日志说做过，磁盘没了
    b = HJ.resume_brief(None, 303, verified_evidence=HK._evidence_verifier(w))
    assert b["resumed_steps"] == [] and b["evidence_missing_steps"] == ["docs/a.md"]
    assert "必须重做" in b["text"]


def test_hook_disabled_by_env(tmp_path, jroot, monkeypatch):
    monkeypatch.setenv("HARNESS_JOURNAL", "0")
    w = tmp_path / "wt"
    w.mkdir()
    assert HK.claim_injection({"id": 304}, w) == ""
    assert HK.checkpoint({"id": 304}, w, tool_calls=1) == {}
    assert not HJ.journal_path(None, 304).exists()


def test_hook_never_breaks_worker_on_bad_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_JOURNAL_ROOT", "/proc/definitely-not-writable")
    monkeypatch.setenv("HARNESS_JOURNAL", "1")
    w = tmp_path / "wt"
    w.mkdir()
    inj = HK.claim_injection({"id": 305}, w)
    assert "journal 不可用" in inj          # 降级成声明，不抛异常
    assert HK.checkpoint({"id": 305}, w) == {}