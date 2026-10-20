#!/usr/bin/env python3
"""P0a 补测：auto_approver 基线感知 + 裁决解析容错（2026-10-20，approval 73 误杀教训）。

判据：
  T1 _parse_verdict 四级容错：完整 JSON / 带噪声 JSON / 裸 verdict 键 /
     裸单词行；垃圾输出 → escalate（绝不误判 approve/reject）
  T2 verdict_prompt 锁定两条新规则：基线已有红不得据此 reject；拿不准一律 escalate
  T3 集成（真 git+pytest 临时仓）：基线红 test_base 与新增红 test_new 正确分离
  T4 归因机制故障（源仓缺失）→ None（调用方 escalate，绝不因此 approve/reject）

运行：/srv/tensor/shared/hermes-agent/.venv/bin/python test_approver_baseline.py
（须带 pytest 的解释器；auto_approver 本体无重依赖）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_approver as aa  # noqa: E402

PASS: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="appr_base_"))
PY = sys.executable

# T1 解析容错
assert aa._parse_verdict('{"verdict": "approve", "reason": "证据齐"}') == \
    {"verdict": "approve", "reason": "证据齐"}
assert aa._parse_verdict(
    '前置噪声\n{"verdict": "reject", "reason": "矛盾"}\n后置噪声')["verdict"] == "reject"
assert aa._parse_verdict('部分输出 "verdict": "escalate" 无完整 JSON')["verdict"] == \
    "escalate"
assert aa._parse_verdict("思考中...\napprove\n")["verdict"] == "approve"
bad = aa._parse_verdict("我认为应该批准这个验收因为证据充分")
assert bad["verdict"] == "escalate" and "不可解析" in bad["reason"]
PASS.append("T1 _parse_verdict 四级容错全过；垃圾输出 escalate 不误判")

# T2 prompt 规则锁定
p = aa.verdict_prompt("测试上下文")
assert "基线已有红" in p and "不得据此 reject" in p
assert "拿不准时一律 escalate" in p
assert "第一行必须且只能是" in p
PASS.append("T2 verdict_prompt 含基线红豁免/拿不准 escalate/首行 JSON 三规则")

# T3 集成：真 git 临时仓
have_pytest = subprocess.run([PY, "-m", "pytest", "--version"],
                             capture_output=True).returncode == 0
assert have_pytest, "解释器缺 pytest（用 hermes .venv 跑）"

repo = TMP / "repo"
(repo / "tests").mkdir(parents=True)
def git(*a, cwd=repo):
    r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                       cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()

git("init", "-q", "-b", "main")
(repo / "tests" / "test_base.py").write_text(
    "def test_baseline_red():\n    assert False, 'base red'\n\n"
    "def test_green():\n    assert True\n", encoding="utf-8")
git("add", "-A")
git("commit", "-q", "-m", "base")
BASE = git("rev-parse", "HEAD")
git("checkout", "-q", "-b", "agent/m9")
(repo / "tests" / "test_new.py").write_text(
    "def test_new_red():\n    assert False, 'new red'\n", encoding="utf-8")
git("add", "-A")
git("commit", "-q", "-m", "mission")

head_ids = aa.enumerate_failures(str(repo), PY)
assert head_ids is not None and len(head_ids) == 2, head_ids
assert any("test_baseline_red" in t for t in head_ids)
assert any("test_new_red" in t for t in head_ids)

# 源仓（ECO_ROOT 克隆）+ 缓存根指到 tmp
eco = TMP / "eco"
eco.mkdir()
subprocess.run(["git", "clone", "-q", str(repo), str(eco / "repoX")], check=True)
aa.ECO_ROOT = str(eco)
aa.BASE_WT_ROOT = str(TMP / "base_wt")

base_reds = aa.baseline_failures("repoX", BASE, PY, head_ids)
assert base_reds is not None, "归因不应故障"
assert any("test_baseline_red" in t for t in base_reds), base_reds
assert not any("test_new_red" in t for t in base_reds), base_reds   # 新增文件=新增红
new_reds = [t for t in head_ids if t not in base_reds]
assert len(new_reds) == 1 and "test_new_red" in new_reds[0]
PASS.append(f"T3 集成：基线红/新增红正确分离（head {len(head_ids)}、"
            f"基线 {len(base_reds)}、新增 {len(new_reds)}）")

# 缓存复用：同 base 二次调用不再新建 worktree（目录数不变）
n_before = len(os.listdir(aa.BASE_WT_ROOT))
aa.baseline_failures("repoX", BASE, PY, head_ids)
assert len(os.listdir(aa.BASE_WT_ROOT)) == n_before
PASS.append("T4 基线 worktree 按 repo@base 缓存复用（二次调用零新建）")

# T5 机制故障 → None
aa.ECO_ROOT = str(TMP / "nonexistent")
assert aa.baseline_failures("repoX", BASE, PY, head_ids) is None
PASS.append("T5 源仓缺失 → None（调用方 escalate，绝不因此 approve/reject）")

print("\n".join(f"PASS {x}" for x in PASS))
print(f"OK {len(PASS)}/5")
