#!/usr/bin/env python3
"""回归：auto_approver v2——m126 类误杀消灭（交付=base..HEAD 累积）。

判据：
  T1 branch_diff_block：双提交分支（.md 先、JSON 后）的画像必须含两个
     文件名与"共 2 个"——末提交 stat 只见 JSON 正是 m126 误杀根因
  T2 claimed_deliverables：产出/新增/交付 + 含 / 路径提取；注入记忆段不算
  T3 presence_split：在场/缺席二分与 base..HEAD 变更集一致
  T4 guard_false_kill：reject+未见+在场→escalate；其余组合原样透传
  T5 静态锁：verify_dev_acceptance 不再含 `git show --stat --oneline {head}`

运行：python3 tools/agent/test_approver_branchdiff.py
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aa", HERE / "auto_approver.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

PASS: list[str] = []


def sh(cmd: str, cwd: str):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                          text=True)


# 构造 m126 形状的仓库：base 提交 → .md 交付 → 验收 JSON 补丁
tmp = tempfile.mkdtemp(prefix="aa_v2_")
MD = "scenarios/suboff_icebreaking/audits/SI-0B-TensorFVM.md"
JS = "scenarios/suboff_icebreaking/audits/validation_report.json"
sh("git init -q -b main .", tmp)
sh('git config user.email t@t && git config user.name t', tmp)
sh("mkdir -p scenarios/suboff_icebreaking/audits && echo base > README.md", tmp)
sh("git add -A && git commit -qm base", tmp)
# 交付发生在开发分支上（真实流=agent/m<id>），否则 main..HEAD 恒空
sh("git checkout -q -b agent/t1", tmp)
sh(f"mkdir -p scenarios/suboff_icebreaking/audits && echo '# 审计' > '{MD}'", tmp)
sh(f"git add -A && git commit -qm '审计: 新增审计报告'", tmp)
sh(f"echo '{{}}' > '{JS}' && git add -A && git commit -qm '审计: 验收工件'", tmp)

# T1 分支全量画像
block = aa.branch_diff_block(tmp, "main")
assert "SI-0B-TensorFVM.md" in block, "T1 FAIL: .md 不在画像（m126 误杀回归）"
assert "validation_report.json" in block, "T1 FAIL: JSON 不在画像"
assert "共 2 个" in block, "T1 FAIL: commit 计数缺失"
assert "main..HEAD" in block, "T1 FAIL: 未标注全量口径"
PASS.append("T1 branch_diff_block 含先 .md 后 JSON 双提交（末提交 stat 盲区消灭）")

# T2 交付文件提取
goal = (f"1. 产出 `{MD}` 五节审计文档。"
        "2. 识别 7 项差距。3. 新增 src/foo.py 辅助。"
        "往次任务记忆（平台注入）：产出 scenarios/old/旧文件.md 不算本案")
files = aa.claimed_deliverables(goal)
assert MD in files, f"T2 FAIL: 主交付未提取 {files}"
assert any(f.endswith("src/foo.py") for f in files), f"T2 FAIL: 新增未提取 {files}"
assert not any("old/旧文件" in f for f in files), "T2 FAIL: 注入记忆混入"
assert aa.claimed_deliverables("无交付路径的 goal") == [], "T2 FAIL: 空例"
PASS.append("T2 claimed_deliverables 提取正确，注入记忆隔离")

# T3 在场性
present, absent = aa.presence_split(tmp, "main", [MD, JS, "docs/不存在的.md"])
assert present == [MD, JS], f"T3 FAIL: 在场集 {present}"
assert absent == ["docs/不存在的.md"], f"T3 FAIL: 缺席集 {absent}"
assert aa.presence_split(tmp, "main", []) == ([], []), "T3 FAIL: 空例"
PASS.append("T3 presence_split 在场/缺席二分正确")

# T4 误杀护栏
v, r = aa.guard_false_kill(
    "reject", "机器工件仅含 JSON，未见 .md 文件", [MD])
assert v == "escalate" and "误杀嫌疑" in r and MD in r, f"T4 FAIL: {v} {r}"
v2, _ = aa.guard_false_kill("reject", "测试套件新增红 3 项", [MD])
assert v2 == "reject", "T4 FAIL: 非未见类 reject 被误翻"
v3, _ = aa.guard_false_kill("approve", "证据自洽", [MD])
assert v3 == "approve", "T4 FAIL: approve 被误动"
v4, _ = aa.guard_false_kill("reject", "未见文件", [])
assert v4 == "reject", "T4 FAIL: 无在场证据时不应翻案"
PASS.append("T4 guard_false_kill 仅翻'未见文件+机器在场'类，余透传")

# T5 静态锁
src = (HERE / "auto_approver.py").read_text(encoding="utf-8")
assert "git show --stat --oneline {head}" not in src, \
    "T5 FAIL: 末提交 stat 仍在（误杀根因未除）"
assert "branch_diff_block(wt, base)" in src and "guard_false_kill(" in src, \
    "T5 FAIL: v2 织入点缺失"
# presence_block 必须在 context 内（LLM 可见）
m_ctx = re.search(r"presence_block\}\{machine_block\}", src)
assert m_ctx, "T5 FAIL: 在场性事实未注入裁决上下文"
PASS.append("T5 静态锁：末提交 stat 已除，v2 三件套织入验收主径")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
