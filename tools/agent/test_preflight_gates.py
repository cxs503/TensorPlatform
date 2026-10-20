#!/usr/bin/env python3
"""P1a 补测：acceptance 前 gates 门禁预检（打回-自修环，2026-10-20）。

判据：
  P1 无 gates.json → None（非本机制任务不受影响，不额外烧预算）
  P2 复算全过 → None（放行提请）
  P3 有案不过 → 打回消息含案例名 + 失败细节 + 常见修法
  P4 环境坏（脚本缺失/超时）→ 放行 None（fail-open 刻意：派发器 push 侧
     同一道门兜底，预检不是单点）
  P5 集成：do_request_acceptance 在 gates 不过时拒绝（ok=False），且
     未走到 POST（BRIDGE.tool_calls 不增）

运行：/srv/tensor/shared/venvs/tensor_agent/bin/python test_preflight_gates.py
（tensor_agent 导入 openhands，须用 tensor_agent venv）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tensor_agent as ta  # noqa: E402

PASS: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="pf_gates_"))
ta.GATES_CHECK = Path(
    "/srv/tensor/shared/tensor_eco/agent_work/benchmark_closure/gates_check.py")
ta.GATES_PY = "/srv/tensor/shared/venvs/tensor_eco/bin/python"


def _mk_wt(name: str, bad: bool = False) -> Path:
    """最小可复算 worktree：单案 benchmarks/verified/c1 四门 classic。"""
    wt = TMP / name
    case = wt / "benchmarks" / "verified" / "c1"
    case.mkdir(parents=True)
    (case / "results.json").write_text(json.dumps({
        "run_date": "2026-10-19", "locked": "2026-10-18",
        "grid": [{"rel_err_pct": 2.9}, {"rel_err_pct": 1.1}]}),
        encoding="utf-8")
    (case / "run.py").write_text("from tensorlbm import solver\n",
                                 encoding="utf-8")
    rj, rp = "benchmarks/verified/c1/results.json", "benchmarks/verified/c1/run.py"
    dobs = 9.9 if bad else 2.9       # bad=True：手抄错值 → direct_observable 复算不过
    (case / "gates.json").write_text(json.dumps({
        "case": "c1", "verdict": "promote", "gates": [
            {"gate": "library_entry", "pass": True,
             "evidence": [{"v": "from tensorlbm", "f": rp,
                           "k": "grep:from tensorlbm"}]},
            {"gate": "direct_observable", "pass": True,
             "evidence": [{"v": dobs, "f": rj, "k": "grid[0].rel_err_pct"},
                          {"v": 1.1, "f": rj, "k": "grid[1].rel_err_pct"}]},
            {"gate": "monotone_convergence", "pass": True, "mode": "abs_decrease",
             "evidence": [{"v": 2.9, "f": rj, "k": "grid[0].rel_err_pct"},
                          {"v": 1.1, "f": rj, "k": "grid[1].rel_err_pct"}]},
            {"gate": "reference_locked_first", "pass": True,
             "evidence": [{"v": "2026-10-18", "f": rj, "k": "locked"},
                          {"v": "2026-10-19", "f": rj, "k": "run_date"}]},
        ]}), encoding="utf-8")
    return wt


# P1 无 gates.json → None
bare = TMP / "bare"
bare.mkdir()
assert ta._gates_preflight(str(bare)) is None
PASS.append("P1 无 gates.json → None（非本机制任务不受影响）")

# P2 全过 → None
wt_ok = _mk_wt("ok")
assert ta._gates_preflight(str(wt_ok)) is None, "全过应放行"
PASS.append("P2 复算全过 → None（放行提请）")

# P3 手抄错值 → 打回，消息含案例名/细节/修法
wt_bad = _mk_wt("bad", bad=True)
note = ta._gates_preflight(str(wt_bad))
assert isinstance(note, str), "复算不过应打回"
assert "c1" in note and "拒绝提请" in note and "常见修法" in note, note[:300]
PASS.append(f"P3 复算不过 → 打回（含案例名/失败细节/常见修法；前 120 字：{note[:120]}…）")

# P4a 脚本缺失 → 放行（push 侧兜底）
saved = ta.GATES_CHECK
ta.GATES_CHECK = Path("/nonexistent/gates_check.py")
assert ta._gates_preflight(str(wt_bad)) is None
ta.GATES_CHECK = saved
# P4b 超时 → 放行
real_run = subprocess.run


def slow_run(*a, **k):
    raise subprocess.TimeoutExpired(cmd=a[0], timeout=300)


ta.subprocess.run = slow_run
assert ta._gates_preflight(str(wt_bad)) is None
ta.subprocess.run = real_run
PASS.append("P4 环境坏（脚本缺失/超时）→ 放行 None（fail-open 刻意，push 侧同门兜底）")

# P5 集成：do_request_acceptance 打回，未走到 POST
saved_bridge = ta.BRIDGE
saved_memroot = ta.MEMORY_ROOT
ta.MEMORY_ROOT = TMP / "agent_memory"      # 隔离：绝不碰真常驻记忆根
ta.BRIDGE = SimpleNamespace(worktree=str(wt_bad), mission_id=990500,
                            mission=None, tool_calls=0)
act = ta.RequestAcceptanceAction(
    headline="晋级完成", summary="四门全过", checks_passed=10, checks_total=10,
    evidence_files=[])
obs = ta.do_request_acceptance(act)
assert obs is not None and obs.ok is False, obs
assert "gates 门禁预检不过" in obs.message
assert ta.BRIDGE.tool_calls == 0, "打回路径不得增 tool_calls（未 POST）"
ta.BRIDGE = saved_bridge
ta.MEMORY_ROOT = saved_memroot
PASS.append("P5 集成：do_request_acceptance 预检打回（ok=False、消息可诊断、未 POST）")

print("\n".join(f"PASS {x}" for x in PASS))
print(f"OK {len(PASS)}/5")
