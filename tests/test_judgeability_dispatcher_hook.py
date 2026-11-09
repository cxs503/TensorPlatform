"""E3 派发前门挂钩不破坏流程（P151W1-N10）。

判据先于实现，机器可裁：
1. 四档 fail-closed 语义：off/advisory 不改文本；enforce 只追加「证据槽位」块；block 缺 P1/P2 拒发；
2. 已收官 DAG（plan 22 十个节点）在 off/advisory/enforce 三模式下 refused==0（dry-run 全绿）；
3. enforce 对已交付单的漏放率 ≤ 5%：该拦的都被标注（un_flagged==0），且无假晋升；
4. 注入是纯追加（append-only）且幂等：剥离区块后任务书逐字节还原 → 不改任务语义；
5. 门自身异常 → fail-open（返回原 goal，不阻塞派发）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from backend.quality.judgeability import classifier as C
from backend.quality.judgeability import gate as G
from backend.quality.judgeability import schema as S

ROOT = Path(__file__).resolve().parents[1]
DAG = ROOT / "artifacts" / "quality" / "judgeability" / "dag_plan22.json"
SNAP = ROOT / "artifacts" / "quality" / "judgeability" / "missions_snapshot.json"

STRONG = ("【战役 DAG 节点|X1|示例】机器验收判据：合成集 F1 >= 0.95[F1]；"
          "相对误差 <= 5%[e]（对标实验标注集）。纪律：worktree 内提交。")
WEAK = "【战役 DAG 节点|X2|示例】机器验收判据：结果良好，效果明显。纪律：x"


def _dispatcher():
    spec = importlib.util.spec_from_file_location(
        "campaign_dispatcher_under_test",
        ROOT / "ops" / "taskplane" / "campaign_dispatcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mode_semantics_fail_closed():
    assert G.resolve_mode("off") == "off"
    assert G.resolve_mode("ENFORCE") == "enforce"
    assert G.resolve_mode("不存在的模式") == "advisory"   # 非法值回默认（fail-open）

    d = G.decide(WEAK, mode="off")
    assert d.action == "pass" and d.goal == WEAK and not d.injected

    d = G.decide(WEAK, mode="advisory")
    assert d.action == "pass_logged" and d.goal == WEAK and not d.injected
    assert set(d.hard_missing) == {"P1", "P2"}

    d = G.decide(WEAK, mode="enforce")
    assert d.action == "inject" and d.injected
    assert d.goal != WEAK and d.goal.startswith(WEAK)

    d = G.decide(WEAK, mode="block")
    assert d.action == "refuse" and d.blocked and d.goal == WEAK

    # 强判据单：四档都放行，且不注入
    for mode in S.GATE_MODES:
        d = G.decide(STRONG, mode=mode)
        assert not d.blocked and not d.injected and d.goal == STRONG, mode


def test_injection_is_append_only_and_idempotent():
    d = G.decide(WEAK, mode="enforce")
    assert G.assert_append_only(WEAK, d.goal)
    assert S.strip_slot_blocks(d.goal) == WEAK          # 剥离后逐字节还原
    assert G.inject_slots(d.goal) == d.goal             # 幂等：不重复追加
    # 注入不产生假合规：注入后仍是 weak
    assert C.classify_goal(d.goal).verdict == "weak"
    # 双保险①：注入区块内不存在任何 P1 形状（比较符+数字）→ 剥离失败也不会凭形状满足阈值
    block = d.goal[len(WEAK):]
    assert not any(pat.search(block) for _rid, pat in S.compile_p1()), block
    # 双保险②：区块内**确实**含 P2 词表项（建议里要列出可选项）→ 只能靠定界剥离避免假晋升，
    # 该风险由 leak_rate(marker=False) 负控实测（见 test_before_after_and_leak_controls）
    raw_block = block.replace(S.SLOT_BLOCK_BEGIN, "").replace(S.SLOT_BLOCK_END, "")
    assert C.classify_text(raw_block, scope="whole").dims["P2"], raw_block


def test_dag_dryrun_all_green_and_enforce_injects():
    if not DAG.is_file():
        pytest.skip(f"dag snapshot missing: {DAG}")
    dag = json.loads(DAG.read_text(encoding="utf-8"))
    assert dag["nodes"], "空 DAG 快照"
    for mode in ("off", "advisory", "enforce"):
        refuses = [n["ext_id"] for n in dag["nodes"]
                   if G.decide(n["goal"], mode=mode).blocked]
        assert refuses == [], f"{mode} 模式拦了已交付节点：{refuses}"
    injected = [n["ext_id"] for n in dag["nodes"]
                if G.decide(n["goal"], mode="enforce").injected]
    # 至少要有一批节点被判缺 P1/P2 并注入（否则挂钩形同虚设）
    assert len(injected) >= 1


def test_enforce_miss_rate_on_delivered_missions():
    if not SNAP.is_file():
        pytest.skip(f"snapshot missing: {SNAP}")
    missions = json.loads(SNAP.read_text(encoding="utf-8"))["missions"]
    delivered = [m["goal"] for m in missions if m.get("status") == "succeeded"]
    rep = G.replay_enforce(delivered)
    assert rep["n"] == len(delivered) and rep["n"] > 100
    assert rep["n_flagged"] > 0, "全部判为合规 → 门可能没生效"
    assert rep["un_flagged"] == 0
    assert rep["miss_rate"] <= 0.05
    assert rep["false_promotion"] == 0
    assert rep["false_promotion_rate"] <= 0.05


def test_hook_is_wired_inside_dispatch_one():
    """源码级：dispatch_one 必须在构造 missions payload **之前**过门，且拒发路径 return None。"""
    import inspect
    mod = _dispatcher()
    src = inspect.getsource(mod.dispatch_one)
    assert "judgeability_gate(" in src
    assert src.index("judgeability_gate(") < src.index("payload = {")
    # 拒发分支必须在构造 payload 之前返回 None（不建 mission、不烧预算）
    assert (src.index('"refuse"') < src.index("return None") < src.index("payload = {"))
    # 派发事件必须携带门读数（可审计）：detail 组装在 event() 之前
    assert src.index('detail["judgeability"]') < src.index('event("mission_dispatched"')


def test_dispatcher_hook_wired_and_fail_open(monkeypatch):
    mod = _dispatcher()
    assert hasattr(mod, "judgeability_gate")
    # advisory（默认）：不改 goal
    monkeypatch.setenv("TP_JUDGEABILITY_GATE", "advisory")
    goal, d = mod.judgeability_gate(WEAK)
    assert goal == WEAK and d["action"] == "pass_logged"
    # enforce：注入槽位块，append-only
    monkeypatch.setenv("TP_JUDGEABILITY_GATE", "enforce")
    goal, d = mod.judgeability_gate(WEAK)
    assert d["injected"] and S.SLOT_BLOCK_BEGIN in goal
    assert G.assert_append_only(WEAK, goal)
    # block：拒发（action=refuse）
    monkeypatch.setenv("TP_JUDGEABILITY_GATE", "block")
    goal, d = mod.judgeability_gate(WEAK)
    assert d["action"] == "refuse"
    # 强判据：任何模式都不注入
    monkeypatch.setenv("TP_JUDGEABILITY_GATE", "enforce")
    goal, d = mod.judgeability_gate(STRONG)
    assert goal == STRONG and not d["injected"]
    # fail-open：门内部抛异常 → 原样放行
    import backend.quality.judgeability.gate as _g
    monkeypatch.setattr(_g, "pre_dispatch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    goal, d = mod.judgeability_gate(WEAK)
    assert goal == WEAK and d is None