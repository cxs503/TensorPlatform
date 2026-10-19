#!/usr/bin/env python3
"""native_harness 做厚回归：交付保留（A1）+ 上下文压缩（A2）。

  T1 交付保留：触界→单次通知→诊断工具锁→交付面放行→幂等
  T2 续跑恢复：检查点含【交付保留】→ 新进程 _delivery_mode=True
  T3 压缩计划纯函数：头/尾边界不拆散 assistant(tool_calls)/tool 配对；middle<8 拒压
  T4 压缩集成：超阈值→中段替换为摘要消息、头尾逐字、检查点 v2
  T5 token 估计：ascii 密集按 2 char/token

运行：/srv/tensor/shared/venvs/tensor_agent/bin/python test_harness_thicken.py（需
litellm/tensor_agent 环境）；退出码 0=全过。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_harness as nh  # noqa: E402


class _B:
    action_count = 0

    def emit(self, *a, **k):
        pass


class _R:
    model = "qwen3.7-plus"
    stop_reason = None
    max_tools = 20
    t0 = time.monotonic()
    bridge = _B()


def mk_harness(max_tools=20):
    r = _R()
    r.max_tools = max_tools
    d = tempfile.mkdtemp(prefix="nh_thicken_")
    h = nh.NativeHarness(r, d, d)
    return h


def t1_delivery_reserve():
    h = mk_harness(max_tools=20)          # reserve=max(6,2)=6，边界=14
    h.r.bridge.action_count = 13
    h._check_delivery_reserve()
    assert not h._delivery_mode, "13<14 不应触发"
    h.r.bridge.action_count = 14
    h._check_delivery_reserve()
    assert h._delivery_mode, "14≥14 应触发交付模式"
    assert any(str(m.get("content")).startswith("【交付保留】") for m in h.messages)
    n = len(h.messages)
    h._check_delivery_reserve()           # 幂等：只通知一次
    assert len(h.messages) == n
    out = h._exec("terminal", {"command": "ls"})
    assert out.startswith("【交付模式】"), f"诊断工具应被锁：{out[:40]}"
    out = h._exec("web_search", {"query": "x"})
    assert out.startswith("【交付模式】")
    out = h._exec("str_replace_editor", {"command": "view", "path": "不存在.md"})
    assert not out.startswith("【交付模式】"), "交付面工具应放行"
    # 边界前不锁
    h2 = mk_harness(max_tools=20)
    h2.r.bridge.action_count = 5
    out = h2._exec("terminal", {"command": "echo hi"})
    assert "exit=0" in out, f"未触界时终端应正常：{out[:60]}"
    return True


def t2_resume_restores_delivery_mode():
    h = mk_harness()
    h.messages.append({"role": "user", "content": "【交付保留】工具预算已用…"})
    h._save_state()
    h2 = nh.NativeHarness(h.r, h.workspace, os.path.dirname(h.state_path))
    assert h2._delivery_mode, "续跑应从检查点重算交付模式"
    return True


def _pairs(n, start=2):
    msgs = [{"role": "user", "content": "goal"}, {"role": "user", "content": "补充"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "terminal",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": f"out{i}"})
    return msgs


def t3_plan_purity():
    msgs = _pairs(20)                       # 42 条
    plan = nh._plan_compaction(msgs, keep_recent=6)
    assert plan is not None
    head, middle, tail = plan
    assert len(head) == 2 and msgs[0] in head
    assert tail[0].get("role") != "tool", "tail 不得以孤儿 tool 开头"
    assert not middle[-1].get("tool_calls"), "middle 不得以无结果的 tool_calls 结尾"
    # 配对完整性：middle 与 tail 各自内部 id 集合封闭
    ids_in = {tc["id"] for m in middle for tc in m.get("tool_calls") or []}
    res_in = {m.get("tool_call_id") for m in middle if m.get("role") == "tool"}
    assert ids_in == res_in, f"middle 配对破损：{ids_in ^ res_in}"
    # 边界落在 tool 上：walk-back 生效
    plan2 = nh._plan_compaction(msgs, keep_recent=5)   # cut=37 → messages[37]=tool
    _, _, tail2 = plan2
    assert tail2[0].get("role") != "tool"
    # head 末条带 tool_calls → head 缩为 1
    msgs3 = _pairs(20)
    msgs3[1] = {"role": "assistant", "content": "",
                "tool_calls": [{"id": "cX", "type": "function",
                                "function": {"name": "t", "arguments": "{}"}}]}
    plan3 = nh._plan_compaction(msgs3, keep_recent=6)
    assert plan3 is not None and len(plan3[0]) == 1
    # middle<8 → None（不值得压）
    assert nh._plan_compaction(_pairs(15), keep_recent=24) is None
    return True


def t4_compaction_integration(monkey_summary="·摘要fixture·"):
    os.environ["NATIVE_COMPACT_TOKENS"] = "10"      # 强制触阈
    os.environ["NATIVE_COMPACT_KEEP_RECENT"] = "6"
    try:
        h = mk_harness()
        h.messages = _pairs(20)
        h._summarize = lambda middle: monkey_summary   # 不打真实 LLM
        before = list(h.messages)
        h._compact_if_needed()
        after = h.messages
        assert after is not before and len(after) < len(before)
        assert after[0] == before[0], "头逐字保留"
        assert after[-6:] == before[-6:], "尾逐字保留"
        note = after[len(after) - 7] if "【上下文压缩】" in str(after[-7].get("content")) else None
        assert note and monkey_summary in note["content"], "摘要消息在头尾之间"
        # 孤儿检查
        seen_calls, seen_res = set(), set()
        for m in after:
            for tc in m.get("tool_calls") or []:
                seen_calls.add(tc["id"])
            if m.get("role") == "tool":
                seen_res.add(m["tool_call_id"])
        assert seen_calls == seen_res, f"压缩后配对破损：{seen_calls ^ seen_res}"
        with open(h.state_path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["version"] == 2
    finally:
        os.environ.pop("NATIVE_COMPACT_TOKENS", None)
        os.environ.pop("NATIVE_COMPACT_KEEP_RECENT", None)
    return True


def t5_est_tokens():
    assert nh._est_tokens([{"content": "a" * 100}]) == 50
    assert nh._est_tokens([]) == 0
    m = [{"content": "", "tool_calls": [
        {"id": "x", "type": "function",
         "function": {"name": "t", "arguments": "b" * 40}}]}]
    assert nh._est_tokens(m) == 20
    return True


def main() -> int:
    tests = [("T1 交付保留", t1_delivery_reserve),
             ("T2 续跑恢复", t2_resume_restores_delivery_mode),
             ("T3 压缩计划纯函数", t3_plan_purity),
             ("T4 压缩集成", t4_compaction_integration),
             ("T5 token 估计", t5_est_tokens)]
    npass = 0
    for name, fn in tests:
        try:
            ok = fn()
        except AssertionError as exc:
            ok = False
            print(f"FAIL  {name}: {exc}")
        print(("PASS  " if ok else "FAIL  ") + name)
        npass += bool(ok)
    print(f"{npass}/{len(tests)}")
    return 0 if npass == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
