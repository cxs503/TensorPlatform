#!/usr/bin/env python3
"""补测③④+第 5 点（PR #22 初审）：memory_key 越界防护、任务快速 claim
时 route 不丢（goal 标记自愈）、事实账本 UTC/flock/幂等。

判据：
  T1 validate_memory_key 拒绝 ../ 越界/绝对路径/多段/首字符非法/超长/空格
  T2 validate_memory_key 放行合法键
  T3 无路由文件 + goal 常驻记忆标记 → _memory_route 自愈重建（self_healed）
  T4 无路由 + 无标记 → None（临时任务行为不变）；标记被污染(../) → None 不落盘
  T5 append_memory 幂等：同一事件重复追加只入账一次
  T6 多进程并发 append：行不撕裂（逐行可解析）、内容不丢、重复事件去重
  T7 时间戳为 UTC Z 后缀格式

运行：tensor_agent venv python（tensor_agent 导入 openhands）。
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mem_guard_"))
MEMROOT = TMP / "agent_memory"
os.environ["TENSOR_MEMORY_ROOT"] = str(MEMROOT)
# dispatch_helper 只读 TENSOR_API_BASE（惰性），不会在导入时联网

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_helper as dh  # noqa: E402
import tensor_agent as ta  # noqa: E402

PASS: list[str] = []

# T1 非法键
BAD = ["../evil", "../../etc", "/abs/path", "a/b", "a//b", ".hidden",
       "-lead-dash", "_lead-us", "has space", "x" * 65, "", "键"]
for key in BAD:
    try:
        dh.validate_memory_key(key)
        raise AssertionError(f"T1 FAIL: 非法键被放行 {key!r}")
    except SystemExit:
        pass
PASS.append(f"T1 非法 memory_key 全拒（{len(BAD)} 例：越界/绝对/多段/首字符/超长/空格/非 ASCII）")

# T2 合法键
for key in ["wedge-icebreaking", "Team.foo-1", "a", "A9._-z"]:
    assert dh.validate_memory_key(key) == key, f"T2 FAIL: {key}"
PASS.append("T2 合法 memory_key 放行（含 . _ - 混排）")

MID = 990001
GOAL_OK = f"把 S3 腿跑完\n\n## 常驻记忆 memory_key=wedge-icebreaking（你是一次班次）\n后续内容"
GOAL_BAD = "目标\n\n## 常驻记忆 memory_key=../../escape（污染标记）\n"

# T3 route 自愈
ta.MEMORY_ROOT = MEMROOT          # 测试根（模块常量覆写）
route = ta._memory_route(MID, GOAL_OK)
assert route and route["memory_key"] == "wedge-icebreaking" and route.get("self_healed"), \
    f"T3 FAIL: {route}"
assert (MEMROOT / "_routes" / f"m{MID}.json").exists(), "T3 FAIL: 自愈路由未落盘"
PASS.append("T3 无路由+goal 标记→路由自愈重建并落盘（快速 claim 竞窗闭合）")

# T4 无标记/污染标记
MID2 = 990002
assert ta._memory_route(MID2, "普通 goal 无常驻标记") is None, "T4a FAIL"
assert ta._memory_route(MID2, GOAL_BAD) is None, "T4b FAIL: 污染标记未被拒"
assert not (MEMROOT / "_routes" / f"m{MID2}.json").exists(), "T4c FAIL: 污染路由落盘了"
PASS.append("T4 无标记→None（临时任务不变）；../污染标记→拒绝且不落盘")

# T5 幂等 + T7 UTC 格式
rec = {"status": "succeeded", "stop": "goal_met", "headline": "S3 绿"}
ta.append_memory(MID, dict(rec), goal=GOAL_OK)
ta.append_memory(MID, dict(rec), goal=GOAL_OK)      # 同事件重复（终态+审批双路径模拟）
lines = [json.loads(x) for x in
         (MEMROOT / "wedge-icebreaking" / "memory.jsonl").read_text().splitlines() if x.strip()]
same = [l for l in lines if l.get("mission") == MID and l.get("status") == "succeeded"]
assert len(same) == 1, f"T5 FAIL: 重复入账 {len(same)} 次"
assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", lines[0]["ts"]), \
    f"T7 FAIL: ts 非 UTC Z 格式 {lines[0]['ts']!r}"
PASS.append("T5 同事件重复追加幂等（fingerprint 去重）")
PASS.append("T7 账本时间戳 UTC Z 后缀")

# T6 多进程并发（fork 继承已导入模块与测试根）
def worker(mid: int, status: str, headline: str) -> None:
    ta.append_memory(mid, {"status": status, "headline": headline}, goal=GOAL_OK)

if __name__ == "__main__":
    jobs = []
    for i in range(8):        # 8 个不同事件
        jobs.append(mp.Process(target=worker, args=(990100 + i, "succeeded", f"h{i}")))
    for _ in range(6):        # 6 个并发重复同一事件（去重竞争）
        jobs.append(mp.Process(target=worker, args=(990200, "failed", "dup")))
    for j in jobs:
        j.start()
    for j in jobs:
        j.join(60)
    assert all(j.exitcode == 0 for j in jobs), "T6 FAIL: worker 异常退出"
    raw = (MEMROOT / "wedge-icebreaking" / "memory.jsonl").read_text()
    parsed = []
    for ln in raw.splitlines():          # 逐行解析 = 撕裂检测
        if ln.strip():
            parsed.append(json.loads(ln))
    uniq = [p for p in parsed if 990100 <= p["mission"] < 990200]
    assert len(uniq) == 8, f"T6 FAIL: 不同事件丢失 {len(uniq)}/8"
    dup = [p for p in parsed if p["mission"] == 990200]
    assert len(dup) == 1, f"T6 FAIL: 重复事件入账 {len(dup)} 次"
    PASS.append("T6 14 进程并发 append：8/8 事件无丢失、逐行可解析（无撕裂）、重复事件恰入账 1 次")

    print("\n".join(f"PASS {x}" for x in PASS))
    print(f"OK {len(PASS)}/7")
