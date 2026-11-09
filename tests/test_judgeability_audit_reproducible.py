"""E2 基线审计可复现（P151W1-N10）。

判据先于实现，机器可裁：
1. 同批 300 单两次运行 → ``digest`` 逐位一致（sha256 相同）；
2. 与输入顺序无关、与进程无关（``PYTHONHASHSEED`` 不影响 dict/list 结构）；
3. 三维切片（repo/campaign/is_maturity）各自单数求和 == 总单数；
4. 逐条记录带三维标签与证据片段（可人工抽查复核，不是黑箱分）；
5. 前后对照(``before_after``)字段完备且假晋升为 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.quality.judgeability import audit as A
from backend.quality.judgeability import gate as G

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "artifacts" / "quality" / "judgeability" / "missions_snapshot.json"


@pytest.fixture(scope="module")
def missions():
    if not SNAP.is_file():  # 快照缺失 → 生成器可重建（不静默 skip）
        pytest.skip(f"snapshot missing: {SNAP}（用 tools/judgeability/export_missions.py 重建）")
    return json.loads(SNAP.read_text(encoding="utf-8"))["missions"]


def test_digest_identical_across_runs(missions):
    rep = A.reproducibility(missions, runs=2)
    assert rep["runs"] == 2
    assert rep["identical"] is True
    assert len(set(rep["digests"])) == 1
    assert len(rep["sha256"]) == 64


def test_digest_identical_across_processes(missions):
    """跨进程复算（不同 PYTHONHASHSEED）：证明 digest 不依赖哈希随机化。"""
    code = (
        "import json,sys;"
        "sys.path.insert(0,%r);"
        "from backend.quality.judgeability import audit as A;"
        "m=json.load(open(%r,encoding='utf-8'))['missions'];"
        "print(A.audit_records(m,batch_label='terminal_missions_300')['digest'])"
        % (str(ROOT), str(SNAP))
    )
    outs = []
    for seed in ("0", "12345"):
        env = {"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=str(ROOT), env=env)
        assert r.returncode == 0, r.stderr[-800:]
        outs.append(r.stdout.strip())
    assert outs[0] == outs[1] == A.audit_records(
        missions, batch_label="terminal_missions_300")["digest"]


def test_order_independent(missions):
    a = A.audit_records(missions)["digest"]
    b = A.audit_records(list(reversed(missions)))["digest"]
    assert a == b


def test_slices_partition_records(missions):
    rep = A.audit_records(missions)
    n = rep["n_records"]
    assert n == len(missions)
    for dim in ("repo", "campaign", "is_maturity"):
        assert sum(v["n"] for v in rep["slices"][dim].values()) == n, dim
    # overall 与 pairs 四格自洽
    pairs = rep["overall"]["pairs"]
    assert sum(pairs.values()) == n
    # 结构完备（P1&P2）与「判为强判据」在任务书级 fail-closed 下不等价：
    # 无显式判据段的单虽维度齐全，仍判 weak —— 差额必须被显式记账
    assert pairs["P1&P2"] == rep["overall"]["strong"] + rep["overall"]["dims_complete_but_weak"]
    assert set(rep["slices"]["is_maturity"]) <= {"True", "False"}


def test_per_record_has_reviewable_labels(missions):
    rep = A.audit_records(missions)
    row = rep["per_record"][0]
    for k in ("id", "repo", "campaign", "is_maturity", "verdict", "dims", "missing",
              "evidence", "scope", "goal_chars"):
        assert k in row
    # 每条命中都带原文片段（可逐例复核）；未命中的维为空列表
    assert isinstance(row["evidence"]["P1"], list)
    assert all(isinstance(x, str) and x for x in row["evidence"]["P1"])
    # verdict 与 dims 必须自洽
    for r in rep["per_record"]:
        gate_ok = r["dims"]["P1"] and r["dims"]["P2"] and "no_criteria_section" not in r["flags"]
        assert r["verdict"] == ("strong" if gate_ok else "weak"), r["id"]


def test_before_after_and_leak_controls(missions):
    goals = [m["goal"] for m in missions]
    ba = A.before_after(missions)
    assert ba["n"] == len(goals)
    assert set(ba["before"]) == {"P1", "P2", "P3", "strong"}
    assert ba["after"]["slot_coverage"] == pytest.approx(
        G.replay_enforce(goals)["n_flagged"] / len(goals), abs=1e-6)
    # 正规注入不假晋升；朴素注入（负控）必须能假晋升 → 指标有鉴别力
    assert G.leak_rate(goals, marker=True)["leaked"] == 0
    assert G.leak_rate(goals, marker=False)["leaked"] > 0
    assert ba["false_promotion"] == 0.0