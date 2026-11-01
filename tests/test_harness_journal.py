#!/usr/bin/env python3
"""Harness v3 journal 单元测试（mission m1231）。

覆盖四条不可让步约束：
  1) append-only + seq/digest 链；
  2) 损坏/篡改 → degraded 且不猜；
  3) 续跑简报复核磁盘证据，缺证据的步骤不得算作续跑（不伪造）；
  4) 预算跨续跑累计，不追加。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness_journal as HJ  # noqa: E402


def test_append_only_chain(tmp_path):
    r = tmp_path / "j"
    a = HJ.append(r, 7, kind="checkpoint", agent_id="t", done_steps=[
        {"id": "s1", "what": "写 a", "evidence": "e/s1"}], calls_used=1, progress=0.5)
    b = HJ.append(r, 7, kind="checkpoint", agent_id="t", done_steps=[
        {"id": "s1", "what": "写 a", "evidence": "e/s1"},
        {"id": "s2", "what": "写 b", "evidence": "e/s2"}], calls_used=2, progress=1.0)
    assert (a["seq"], b["seq"]) == (1, 2)
    assert b["prev_digest"] == a["digest"]
    lines = HJ.journal_path(r, 7).read_text().splitlines()
    assert len(lines) == 2, "只追加，不重写"
    st = HJ.read(r, 7)
    assert len(st["entries"]) == 2 and not st["corrupt"] and not st["degraded"]
    m = HJ.merge(st["entries"])
    assert sorted(m["done_steps"]) == ["s1", "s2"]
    assert m["calls_used"] == 2 and m["progress"] == 1.0


def test_tamper_detected(tmp_path):
    r = tmp_path / "j"
    HJ.append(r, 8, kind="checkpoint", done_steps=[{"id": "s1", "what": "x"}], calls_used=1)
    p = HJ.journal_path(r, 8)
    rec = json.loads(p.read_text().splitlines()[0])
    rec["calls_used"] = 0                       # 篡改：谎报少用预算
    p.write_text(json.dumps(rec) + "\n")
    st = HJ.read(r, 8)
    assert st["degraded"] and not st["entries"]
    assert st["corrupt"][0]["why"] == "digest_mismatch"


def test_corruption_degrades_without_guessing(tmp_path):
    r = tmp_path / "j"
    HJ.append(r, 9, kind="checkpoint", done_steps=[{"id": "s1", "what": "x"}], calls_used=1)
    p = HJ.journal_path(r, 9)
    with open(p, "a") as f:
        f.write('{"schema":"harness.journal.v3","seq":2,"kind":"che')  # kill -9 半行
        f.write("\n@@garbage@@\n")
    st = HJ.read(r, 9)
    assert st["degraded"] and len(st["entries"]) == 1
    assert len(st["corrupt"]) >= 2
    b = HJ.resume_brief(r, 9)
    assert b["degraded"] is True
    assert "journal_corrupt" in b["text"] or "损坏" in b["text"]


def test_missing_journal_is_explicit_fresh(tmp_path):
    b = HJ.resume_brief(tmp_path / "empty", 10)
    assert b["mode"] == "fresh" and b["degraded"] is True
    assert b["reason"] == "journal_missing"
    assert "从零" in b["text"] and "不要声称续跑" in b["text"]


def test_resume_brief_lists_done_and_next(tmp_path):
    r = tmp_path / "j"
    HJ.append(r, 11, kind="checkpoint", goal="补齐阻力模块", plan=["读代码", "写测试"],
              done_steps=[{"id": "s1", "what": "读代码", "evidence": "e/s1"}],
              verified_facts=["API 返回 200"], unverified_assumptions=["还没跑 pytest"],
              next_intent="写测试", calls_used=3, progress=0.5,
              dirty_tree={"kind": "git", "dirty_count": 2, "dirty_paths": ["a.py", "b.py"],
                          "head": "deadbeef"})
    b = HJ.resume_brief(r, 11)
    assert b["mode"] == "resume" and b["resumed_steps"] == ["s1"]
    assert "你在续跑，不是从零开始" in b["text"]
    for frag in ("补齐阻力模块", "已验证事实", "未验证假设", "脏工作树", "下一步意图"):
        assert frag in b["text"]
    assert b["calls_used"] == 3


def test_evidence_check_blocks_fake_resume(tmp_path):
    r = tmp_path / "j"
    HJ.append(r, 12, kind="checkpoint", done_steps=[
        {"id": "s1", "what": "做完了", "evidence": "e/s1"}], calls_used=1, progress=1.0)
    # 日志说做完了，但磁盘上没证据 → 不得算续跑，必须重做
    b = HJ.resume_brief(r, 12, verified_evidence=lambda s: False)
    assert b["resumed_steps"] == [] and b["evidence_missing_steps"] == ["s1"]
    assert b["mode"] == "fresh"
    assert "必须重做" in b["text"]
    b2 = HJ.resume_brief(r, 12, verified_evidence=lambda s: s["id"] == "s1")
    assert b2["resumed_steps"] == ["s1"] and b2["mode"] == "resume"


def test_budget_is_cumulative_not_extended():
    m = {"calls_used": 37}
    rem = HJ.budget_remaining({"max_tool_calls": 100, "max_wall_seconds": 3600}, m)
    assert rem["remaining_tool_calls"] == 63 and rem["extended"] is False
    over = HJ.budget_remaining({"max_tool_calls": 100}, {"calls_used": 120})
    assert over["remaining_tool_calls"] == 0 and over["extended"] is False


def test_tree_snapshot_git_and_plain(tmp_path):
    plain = tmp_path / "w"
    (plain / "evidence").mkdir(parents=True)
    (plain / "evidence" / "s1.json").write_text("{}")
    snap = HJ.tree_snapshot(plain)
    assert snap["kind"] == "dir" and snap["file_count"] == 1
    assert HJ.tree_snapshot(tmp_path / "nope")["exists"] is False