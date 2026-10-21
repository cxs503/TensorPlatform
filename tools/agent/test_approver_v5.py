#!/usr/bin/env python3
"""回归：v5——机器直读 JSON 数值注入 + 停车前置冻结。

判据：
  T1 machine_json_values：压平数值/嵌套点路径/非 json 跳过/坏文件跳过/
     相对+绝对路径/每文件上限
  T2 静态锁：values_block 进 context、循环顶冻结（verify 之前），
     else 分支停车记录仍在
运行：python3 tools/agent/test_approver_v5.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aa", HERE / "auto_approver.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

PASS: list[str] = []

# T1 数值直读
with tempfile.TemporaryDirectory() as td:
    wt = Path(td)
    (wt / "validation_report.json").write_text(json.dumps({
        "anchor1": {"rel_err": 0.0083, "verdict": "PASS"},
        "anchor2": {"rel_err": 0.021, "verdict": "FAIL"},
        "note": "x" * 40}), encoding="utf-8")
    (wt / "sub").mkdir()
    (wt / "sub" / "other.json").write_text(json.dumps({"k": 1}), encoding="utf-8")
    (wt / "bad.json").write_text("{not json", encoding="utf-8")
    (wt / "readme.md").write_text("skip me", encoding="utf-8")
    out = aa.machine_json_values(
        str(wt), ["sub/other.json", "readme.md", "absent.json"],
        [str(wt / "validation_report.json"), str(wt / "bad.json")])
    assert "anchor1.rel_err=0.0083" in out, "T1a"
    assert "anchor1.verdict=PASS" in out, "T1b"
    assert "anchor2.rel_err=0.021" in out, "T1c"
    assert "note=<40字>" in out, "T1d（长值以长度占位）"
    assert "sub/other.json: k=1" in out, "T1e（相对路径文件）"
    assert "absent" not in out and "readme" not in out, "T1f"
    assert "机器直读 JSON 数值" in out, "T1g（可信级声明头）"
    assert aa.machine_json_values(str(wt), [], []) == "", "T1h"
PASS.append("T1 machine_json_values：压平/嵌套/坏文件/相对绝对/上限全对")

# T2 静态锁
src = (HERE / "auto_approver.py").read_text(encoding="utf-8")
assert "values_block = machine_json_values(" in src, "T2a"
assert "{presence_block}{machine_block}{values_block}" in src, "T2b"
m = re.search(r"for ap in items:\n(.*?)if decided >=", src, re.S)
assert m and "in seen" in m.group(1), "T2c（冻结未前置到 verify 前）"
assert re.search(r'log\(f"approval \{ap\[.id.\]\} 已 escalate 停车', src), "T2d"
assert "escalated_record(seen, ap, note)" in src, "T2e（停车记录保留）"
PASS.append("T2 静态锁：数值进 context、冻结在 verify 前、记录路径保留")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
