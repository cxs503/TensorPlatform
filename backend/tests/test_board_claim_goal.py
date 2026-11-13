"""FEAT-BOARD-CLAIM 的 goal 组装判据（纯函数，无需数据库）。

为什么单测不连库也要有
----------------------
造单的正确性有一半在 goal 文本里：锚 slug、验收判据原文、ref 出处零手抄、
报告六段、cluster 路由、记忆回路、台账等级纪律。这些一旦漏项，端点「绿」而
派出去的单不可验收。此文件用合成锚（不含任何真实域内容）把这些判据逐条钉住，
并对拍报告台账门（tools.audit.report_gate_ledger.ledger_gate）：**服务端组装的
goal 不得触发 REPORT_OVERCLAIMS_LEDGER 误判**（goal 是任务描述，不是报告）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
for p in (str(REPO_ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.tensor_board import anchor_task_key, build_goal  # noqa: E402

SYNTH_ANCHOR = {
    "id": 9001,
    "slug": "FEAT-SYNTH-ANCHOR",
    "title": "合成锚·服务端组装 goal",
    "domain": "SynthRepo",
    "acceptance": "【判据】concurrent_claims == 1；ref: Synthetic Manual §3 Table 2",
    "status": "open",
    "source": "owner",
    "ledger_group": None,
    "mission_id": None,
}


def _goal(**over):
    anchor = dict(SYNTH_ANCHOR, **over)
    return build_goal(anchor, repo=over.get("repo", "SynthRepo"),
                      holder="user:tester", lease_ttl_seconds=600)


def test_goal_carries_anchor_identity_and_verbatim_acceptance():
    g = _goal()
    assert g.startswith("【锚点=FEAT-SYNTH-ANCHOR】")
    # 验收判据原文逐字进 goal（零手抄的反面：不许改写判据）
    assert SYNTH_ANCHOR["acceptance"] in g
    assert "feature_ledger#9001" in g
    assert "domain=SynthRepo" in g and "source=owner" in g
    assert "目标仓：SynthRepo" in g


@pytest.mark.parametrize("needle", [
    "开工三查",          # 知识面（capability / negative / oss）
    "cluster_queue",     # cluster 路由（禁指定节点）
    "报告六段",          # 报告结构
    "①", "②", "③", "④", "⑤", "⑥",
    "记忆回路",          # 记忆回路
    "reconciled_level",  # 台账等级纪律（与报告 gate 同源）
    "honest-FAIL",
    "/tensor/board/leases/FEAT-SYNTH-ANCHOR/release",  # 完单释放租约
])
def test_goal_required_sections_present(needle):
    assert needle in _goal()


def test_goal_records_lease_holder_and_ttl():
    g = _goal()
    assert "user:tester" in g and "600s" in g


def test_task_key_is_anchor_scoped_and_matches_dispatcher_convention():
    assert anchor_task_key("FEAT-SYNTH-ANCHOR") == "anchor-feat-synth-anchor"
    # 与 dispatcher 侧既有约定同源（小写、anchor- 前缀），防双供
    assert anchor_task_key("  MixEd-Slug ") == "anchor-mixed-slug"
    assert len(anchor_task_key("X" * 200)) == 120


def test_goal_is_deterministic():
    assert _goal() == _goal()


def test_goal_does_not_trip_report_ledger_gate():
    """LEDGER-GATE 兼容：goal 里不得出现「能力 id + 证据等级」式断言。

    gate 的 ``ok`` 还受台账状态影响（台账 DEGRADED 时 ok=False），那是台账自身的
    事实，与本端点无关；这里钉的是**本端点不制造违规/不制造误捕**。
    """
    # 以子进程跑门（仓库根 tools/ 与 backend/tools/ 同名，同进程 import 会串味）
    import json
    import subprocess

    script = (
        "import json,sys;"
        "sys.path.insert(0, %r);"
        "from tools.audit.report_gate_ledger import ledger_gate;"
        "print(json.dumps(ledger_gate(sys.stdin.read()), ensure_ascii=False))"
    ) % str(REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", script], input=_goal(),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["violations"] == []
    assert res["unregistered_ids"] == []