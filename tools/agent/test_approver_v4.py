#!/usr/bin/env python3
"""回归：v4——导入隔离 + 收集容错归因 + 裁决思考流重试。

判据：
  T1 repo_pythonpath：src 布局→<wt>/src；平铺→wt 本身
  T2 _unparseable：只认「裁决输出不可解析」类 escalate，真 escalate 不算
  T3 llm_verdict 重试：首答思考流（无 JSON）→ 次答合法 JSON → 采用次答；
     两次都败 → escalate（不误判）
  T4 静态锁：三处 pytest 命令带 PYTHONPATH 前置与收集容错、无 -x；
     FAILED|ERROR 归因口径；max_tokens 12288（v6 头部空间）；worker 侧 do_run_tests 同口径

运行：python3 tools/agent/test_approver_v4.py
"""
from __future__ import annotations

import importlib.util
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aa", HERE / "auto_approver.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

PASS: list[str] = []

# T1 导入隔离
with tempfile.TemporaryDirectory() as td:
    flat = Path(td) / "flat"
    flat.mkdir()
    assert aa.repo_pythonpath(str(flat)) == str(flat), "T1a（平铺应=wt）"
    (flat / "src").mkdir()
    assert aa.repo_pythonpath(str(flat)) == str(flat / "src"), "T1b（src 布局优先）"
PASS.append("T1 repo_pythonpath：平铺=wt、src 布局=<wt>/src")

# T2 _unparseable 判别
assert aa._unparseable({"verdict": "escalate",
                        "reason": "裁决输出不可解析：xxx"}) is True, "T2a"
assert aa._unparseable({"verdict": "escalate",
                        "reason": "默认套件复跑超时"}) is False, "T2b"
assert aa._unparseable({"verdict": "approve", "reason": ""}) is False, "T2c"
PASS.append("T2 _unparseable：只认解析失败类，真 escalate 不触发重试")

# T3 重试逻辑（monkeypatch _stream_chat）
calls: list[str] = []


def fake_stream(base, key, messages):
    calls.append(messages[-1]["content"])
    if len(calls) == 1:
        return "Let me analyze the evidence carefully. " * 30   # 思考流无 JSON
    return '{"verdict": "approve", "reason": "重试后可解析"}'


aa._stream_chat = fake_stream
v = aa.llm_verdict("证据：锚全过")
assert v["verdict"] == "approve" and v["reason"] == "重试后可解析", "T3a"
assert len(calls) == 2, "T3b（必须恰好重试一次）"
assert "上一次输出没有裁决 JSON" in calls[1], "T3c（重试 prompt 须告知上次无 JSON）"

calls.clear()


def fake_stream_bad(base, key, messages):
    calls.append(1)
    return "仍然没有裁决标记的思考流……"


aa._stream_chat = fake_stream_bad
v2 = aa.llm_verdict("证据：锚全过")
assert v2["verdict"] == "escalate" and v2["reason"].startswith("裁决输出不可解析"), "T3d"
assert len(calls) == 2, "T3e（两败即止，不无限重试）"
PASS.append("T3 llm_verdict 重试：思考流→重试采用；两败→escalate 不误判")

# T4 静态锁（审批器 + worker 两侧）
src = (HERE / "auto_approver.py").read_text(encoding="utf-8")
assert "PYTHONPATH={repo_pythonpath(wt)}" in src, "T4a（主复跑无隔离）"
assert "PYTHONPATH={repo_pythonpath(bdir)}" in src, "T4b（基线复跑无隔离）"
assert "--continue-on-collection-errors" in src, "T4c（无收集容错）"
assert 'f"{python} -m pytest -x -q tests"' not in src, "T4d（旧复跑命令仍在）"
assert 'r"^(?:FAILED|ERROR) (\\S+)"' in src, "T4e（收集错未入归因口径）"
assert '"max_tokens": 12288' in src, "T4f（max_tokens 未提）"

ta_src = (HERE / "tensor_agent.py").read_text(encoding="utf-8")
assert "PYTHONPATH={repo_pythonpath(BRIDGE.worktree)}" in ta_src, "T4g"
assert "--continue-on-collection-errors" in ta_src, "T4h"
assert 'f"{default_python} -m pytest -x -q tests"' not in ta_src, "T4i"
PASS.append("T4 静态锁：双侧 PYTHONPATH 隔离+收集容错、-x 退场、归因含 ERROR")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
