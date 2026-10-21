#!/usr/bin/env python3
"""回归：auto_approver v3——escalate 停车场 + checks 分母语义。

判据：
  T1 checks_capacity：机器摘录在=摘录数；无摘录+测试口径=pytest 容量；
     无摘录+非测试口径=None（不比数——锚点数 vs pytest 数是范畴错误）
  T2 停车场读写：tmp 路径经 TENSOR_ESCALATED_SEEN 覆盖，记录后可读回
  T3 静态锁：main 循环 escalate 分支含停车+入账；C3 用 checks_capacity

运行：python3 tools/agent/test_approver_escalate.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aa", HERE / "auto_approver.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

PASS: list[str] = []

# T1 分母语义
assert aa.checks_capacity(48, False, 14, 0, 0) == 48, "T1a"
assert aa.checks_capacity(0, True, 14, 0, 0) == 14, "T1b"
assert aa.checks_capacity(0, False, 14, 0, 0) is None, "T1c（非测试口径不比数）"
assert aa.checks_capacity(37, True, 14, 0, 0) == 37, "T1d（摘录优先）"
PASS.append("T1 checks_capacity 三口径分母正确（摘录/测试/不比）")

# T2 停车场（tmp 路径）
tmp = tempfile.mkdtemp(prefix="aa_v3_")
sf = Path(tmp) / "esc.json"
os.environ["TENSOR_ESCALATED_SEEN"] = str(sf)
spec2 = importlib.util.spec_from_file_location("aa2", HERE / "auto_approver.py")
aa2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(aa2)
assert aa2.escalated_seen() == {}, "T2a"
store = aa2.escalated_seen()
aa2.escalated_record(store, {"id": 104, "mission_id": 133}, "checks 25 口径")
assert "104" in aa2.escalated_seen(), "T2b（写后可读回）"
assert aa2.escalated_seen()["104"]["mission"] == 133, "T2c"
PASS.append("T2 escalate 停车场 env 覆盖+写读回环正确")

# T3 静态锁
src = (HERE / "auto_approver.py").read_text(encoding="utf-8")
assert "已 escalate 停车——跳过" in src, "T3a（停车跳过分支缺失）"
assert '_memory_append_decision(ap, "escalated")' in src, "T3b（escalate 未入账）"
assert "checks_capacity(_mc, _test_scope" in src, "T3c（C3 未接分母函数）"
m = re.search(r"声称 checks \{claimed_total\} 项超过其口径容量", src)
assert m, "T3d（C3 文案未更新）"
PASS.append("T3 静态锁：停车+入账+分母函数织入")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
