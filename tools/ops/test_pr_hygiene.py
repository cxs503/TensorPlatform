#!/usr/bin/env python3
"""pr_hygiene 回归：并集解 / 白名单机械解 / 推送前并 main 全链（本地裸仓）。

判据：
  T1 union_resolve_text：双侧行并集保序去重、零冲突标记、多块各解
  T2 merge_main_before_push：本地裸 origin 三场景——
     a) 落后 main 无重叠 → clean merge commit、main 内容进分支
     b) 双侧改同一台账 → 并集解、双侧条目全在、零标记
     c) 未知文件真冲突 → 拒解、HEAD 不动、无残留合并态
  T3 keep_open_prs：异常路径不抛（返回日志列表）
运行：python3 tools/ops/test_pr_hygiene.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ph", HERE / "pr_hygiene.py")
ph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ph)

PASS: list[str] = []


def sh(cmd: str, cwd: str):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                          text=True, timeout=120)


# T1 并集纯函数
txt = ("header\n"
       "<<<<<<< ours\n"
       "- A 案例闭环 m60\n"
       "- B 案例闭环 m61\n"
       "=======\n"
       "- B 案例闭环 m61\n"
       "- C 案例闭环 m62\n"
       ">>>>>>> theirs\n"
       "footer\n"
       "<<<<<<< ours\n"
       "x\n"
       "=======\n"
       "y\n"
       ">>>>>>> t\n")
out = ph.union_resolve_text(txt)
assert "- A 案例闭环 m60\n- B 案例闭环 m61\n- C 案例闭环 m62\n" in out, out
assert "x\ny\n" in out, out
for m in ph.CONFLICT_BALLAST:
    assert m not in out, f"标记残留 {m}"
assert out.startswith("header\n") and "footer\n" in out
assert ph.union_resolve_text("无冲突\n") == "无冲突\n"
PASS.append("T1 union_resolve_text：并集保序去重/零标记/多块各解")

# T2 本地裸仓三场景
with tempfile.TemporaryDirectory() as td:
    origin = os.path.join(td, "origin.git")
    sh(f"git init -q --bare {origin}", td)
    seed = os.path.join(td, "seed")
    sh(f"git clone -q {origin} {seed}", td)
    sh(f'git -C {seed} config user.email t@t && '
       f'git -C {seed} config user.name t && git -C {seed} checkout -qb main',
       td)
    LED = "benchmarks/pending/README.md"
    SOL = "solver.py"
    led = os.path.join(seed, LED)
    os.makedirs(os.path.dirname(led), exist_ok=True)
    open(led, "w").write("# pending\n- 旧条目\n")
    open(os.path.join(seed, SOL), "w").write("def f():\n    return 0\n")
    sh(f"git -C {seed} add -A && git -C {seed} commit -qm base", td)
    sh(f"git -C {seed} push -q origin main", td)
    base = sh(f"git -C {seed} rev-parse HEAD", td).stdout.strip()

    def branch(name: str, edits: dict) -> str:
        w = os.path.join(td, name)
        sh(f'git -C {seed} worktree add -q -b {name} "{w}" {base}', td)
        sh(f"git -C {w} config user.email t@t && git -C {w} config user.name t",
           td)
        sh(f"git -C {w} remote set-url origin {origin}", td)
        for rel, content in edits.items():
            fp = os.path.join(w, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            mode = "a" if rel.endswith("README.md") else "w"
            with open(fp, mode) as fh:
                fh.write(content)
        sh(f"git -C {w} add -A && git -C {w} commit -qm {name}", td)
        return w

    def advance_main(edits: dict, msg: str) -> None:
        for rel, content in edits.items():
            fp = os.path.join(seed, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            mode = "a" if rel.endswith("README.md") else "w"
            with open(fp, mode) as fh:
                fh.write(content)
        sh(f"git -C {seed} add -A && git -C {seed} commit -qm {msg} && "
           f"git -C {seed} push -q origin main", td)

    # a) 落后无重叠 → clean
    advance_main({"main_only.txt": "m\n", LED: "- main 侧新案 a\n"}, "m-a")
    wa = branch("br_a", {"a_only.txt": "a\n"})
    ok, how = ph.merge_main_before_push(wa)
    assert ok and how == "clean", (ok, how)
    assert "main 侧新案 a" in open(os.path.join(wa, LED)).read()
    assert open(os.path.join(wa, "main_only.txt")).read() == "m\n"
    assert "merge:" in sh(f"git -C {wa} log -1 --format=%s", td).stdout
    PASS.append("T2a 落后无重叠 → clean merge commit、main 内容进分支")

    # b) 双侧改台账 → 并集解
    advance_main({LED: "- main 侧新案 b\n"}, "m-b")
    wb = branch("br_b", {LED: "- 分支侧新案 b\n"})
    ok, how = ph.merge_main_before_push(wb)
    assert ok and "并集" in how and "pending" in how, (ok, how)
    body = open(os.path.join(wb, LED)).read()
    assert "main 侧新案 b" in body and "分支侧新案 b" in body, body
    for m in ph.CONFLICT_BALLAST:
        assert m not in body
    PASS.append("T2b 台账双侧改 → 并集解、双侧条目全在、零标记")

    # b2) operator_log 日报双侧追加 → 并集解；工作树残留 → 先 stash
    LOG = "operator_log/2026-10-21.md"
    advance_main({LOG: "- main 侧巡检行\n"}, "m-b2")
    wb2 = branch("br_b2", {LOG: "- 分支侧巡检行\n"})
    open(os.path.join(wb2, "residual_untracked.txt"), "w").write("代理残留\n")
    with open(os.path.join(wb2, LED), "a") as fh:
        fh.write("- 未提交残留行\n")  # 已跟踪文件的未提交改动
    ok, how = ph.merge_main_before_push(wb2)
    assert ok and "operator_log" in how, (ok, how)
    body = open(os.path.join(wb2, LOG)).read()
    assert "main 侧巡检行" in body and "分支侧巡检行" in body, body
    for m in ph.CONFLICT_BALLAST:
        assert m not in body
    PASS.append("T2b2 operator_log 日报并集 + 工作树残留先 stash 再 merge")

    # b3) 班次 .py / gates_verified.json 冲突 → 取分支侧（并集会坏语法/双案错配）
    VERI = "operator_log/verify_shift.py"
    GATE = "benchmarks/verified/permeability3d/gates_verified.json"
    advance_main({VERI: '"""m142 班次门：main 版"""\n', GATE: '{"main": 1}\n'},
                 "m-b3")
    wb3 = branch("br_b3", {VERI: '"""m199 班次门：分支版"""\n',
                           GATE: '{"branch": 2}\n'})
    ok, how = ph.merge_main_before_push(wb3)
    assert ok and VERI in how and GATE in how and "分支侧" in how, (ok, how)
    assert open(os.path.join(wb3, VERI)).read() == '"""m199 班次门：分支版"""\n'
    assert open(os.path.join(wb3, GATE)).read() == '{"branch": 2}\n'
    PASS.append("T2b3 班次 .py 与 gates_verified 冲突 → 取分支侧（PR diff 披露）")

    # b4) validation_report.json 双侧整体重写 → 顶层键并集、键冲突取分支侧
    import json as _json
    VR = "benchmarks/validation_report.json"
    advance_main({VR: '{"overall_pass": true, "task": "main 版"}\n'}, "m-b4")
    wb4 = branch("br_b4", {VR: '{"task": "m199 分支版", "criteria": [1, 2]}\n'})
    ok, how = ph.merge_main_before_push(wb4)
    assert ok and "顶层键并集" in how and "task" in how, (ok, how)
    vr = _json.loads(open(os.path.join(wb4, VR)).read())
    assert vr["task"] == "m199 分支版" and vr["overall_pass"] is True, vr
    assert vr["criteria"] == [1, 2]
    PASS.append("T2b4 validation_report 双侧重写 → 顶层键并集、键冲突取分支侧")

    # c) 未知文件真冲突 → 拒解、HEAD 不动
    advance_main({SOL: "def f():\n    return 1\n"}, "m-c")
    wc = branch("br_c", {SOL: "def f():\n    return 2\n"})
    head0 = sh(f"git -C {wc} rev-parse HEAD", td).stdout.strip()
    ok, how = ph.merge_main_before_push(wc)
    assert not ok and "未知冲突文件" in how, (ok, how)
    assert sh(f"git -C {wc} rev-parse HEAD", td).stdout.strip() == head0
    assert "UU" not in sh(f"git -C {wc} status --porcelain", td).stdout
    PASS.append("T2c 未知代码冲突 → 拒解、HEAD 不动、工作树无残留合并态")

# T3 异常路径不抛
ph_keep = ph.keep_open_prs([("TensorLBM", 999999, None)])
assert isinstance(ph_keep, list)
PASS.append("T3 keep_open_prs：异常路径不抛（返回日志列表）")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
