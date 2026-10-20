#!/usr/bin/env python3
"""回归：常驻记忆注入块字节硬顶（2026-10-21 班次线 422 全卡死事故）。

事故：compose_resident_context 只封行数不封字节，context.md 涨到 14KB →
goal = 模板 1055 + 记忆 11773 > 12000 → POST /tensor/missions 422
string_too_long，operator_shift 每班必炸，平台 5 小时无新 mission。

判据：
  C1 _clip 未超限 → 原样返回（恒等）
  C2 _clip 超限 → 总长受控、头尾内容保留、显式裁剪标记含裁掉字符数
  C3 compose_resident_context 面对 20KB context.md（行数不超 200，绕开行顶）
     → 注入块 ≤ MEMORY_MAX_CHARS，且首尾内容仍在
  C4 MEMORY_MAX_CHARS 环境变量覆写生效（小顶场景）

运行：python3 test_memory_byte_cap.py（不联网；TENSOR_MEMORY_ROOT 指临时目录）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mem_cap_"))
os.environ["TENSOR_MEMORY_ROOT"] = str(TMP / "agent_memory")
os.environ["MEMORY_MAX_CHARS"] = "8500"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_helper as dh  # noqa: E402

PASS: list[str] = []

# C1 恒等
short = "x" * 100
assert dh._clip(short, 8500, "常驻记忆") == short
PASS.append("C1 未超限恒等返回")

# C2 超限裁剪
long_text = "HEAD_MARKER" + "y" * 30000 + "TAIL_MARKER"
out = dh._clip(long_text, 8500, "常驻记忆")
assert len(out) <= 8500 + 200, f"C2 FAIL: 总长 {len(out)} 未受控"
assert out.startswith("HEAD_MARKER"), "C2 FAIL: 头部丢失"
assert out.rstrip().endswith("TAIL_MARKER"), "C2 FAIL: 尾部丢失"
assert "中段裁剪" in out and str(len(long_text) - 8500) in out, "C2 FAIL: 裁剪标记缺失/无字符数"
PASS.append(f"C2 超限裁剪：总长 {len(out)}，头尾保留，显式标记含裁掉 {len(long_text) - 8500} 字符")

# C3 20KB context.md（150 行 × ~140 字符，行数 150 < 200 行顶不触发——正是事故形态）
d = TMP / "agent_memory" / "shift-key"
d.mkdir(parents=True)
(d / "identity.md").write_text(
    "身份：operator 班次agent，负责巡检。\n" * 3, encoding="utf-8")
big_ctx = "\n".join(f"L{i:03d}：" + "交接内容" * 40 for i in range(150))
assert len(big_ctx) > 19000, f"C3 FAIL: 铺垫失败，context 仅 {len(big_ctx)} 字符"
(d / "context.md").write_text(big_ctx, encoding="utf-8")
block = dh.compose_resident_context("shift-key")
assert len(block) <= 8500 + 200, f"C3 FAIL: 注入块 {len(block)} 超顶（事故复现）"
assert "L000" in block, "C3 FAIL: 头部内容丢失"
assert "L149" in block, "C3 FAIL: 尾部内容丢失"
assert "memory_key=shift-key" in block
PASS.append(f"C3 20KB context.md（150 行不触行顶）→ 注入块 {len(block)} ≤ 8500，事故形态不再复现")

# C4 环境覆写
dh.MEMORY_MAX_CHARS = 500
out2 = dh.compose_resident_context("shift-key")
assert len(out2) <= 700, f"C4 FAIL: 覆写后 {len(out2)} 未受控"
assert "L000" in out2 and "L149" in out2, "C4 FAIL: 小顶下头尾丢失"
PASS.append(f"C4 MEMORY_MAX_CHARS=500 覆写生效：注入块 {len(out2)}，头尾仍保留")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
