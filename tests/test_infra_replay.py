"""复活判定器 / 回退重播执行器 / 熔断 / KPI 的机读用例（判据 a–d、e、g 的单元层）。

纪律：
* 夹具用**合成** node/model 名，不含任何领域词（平台中性）；
* 纯函数用例不碰 DB；执行器用例用 :class:`InMemoryStore`（与 PG store 同语义）；
* 每个 reason_code 都有正向用例与**负向**用例（少一条就等于给误判开后门）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.validation import infra_replay as IR  # noqa: E402

PLAN = 4242


def mission(mid: int, *, status: str = "failed", fail_stage: str | None = None,
            stop_condition: str | None = None, tool_calls: int | None = 0,
            revoked_reason: str | None = None, accepted: bool | None = None,
            decision_note: str | None = None, gate: str | None = None,
            stage_in_checks: str | None = None, plan_id: int = PLAN,
            model: str = "modelA") -> dict:
    """造一条 mission（形状对齐 tensor_dev_missions.result）。"""
    checks: dict = {}
    if gate:
        checks["gate"] = gate
    if stage_in_checks:
        checks["fail_stage"] = stage_in_checks
    result: dict = {"model": model, "evidence": {"checks": checks}, "tool_calls": tool_calls}
    if fail_stage:
        result["fail_stage"] = fail_stage
    if stop_condition:
        result["stop_condition"] = stop_condition
    if revoked_reason:
        result["revoked"] = True
        result["revoked_reason"] = revoked_reason
    if accepted is not None:
        result["accepted"] = accepted
    if decision_note is not None:
        result["decision_note"] = decision_note
    return {"id": mid, "status": status, "repo": "synthetic-repo", "model": model,
            "result": result, "plan_id": plan_id, "goal": f"node_{mid}"}


# 7 条判据样本：5 条运行期丢失（零工具调用）+ 1 条声明证伪 + 1 条验收拒绝
SEVEN_CASES = [
    (mission(9001, fail_stage="llm_error", tool_calls=0), True, IR.REASON_OK),
    (mission(9002, stop_condition="agent_lost", tool_calls=0), True, IR.REASON_OK),
    (mission(9003, fail_stage="budget_exhausted+forced_finish", tool_calls=0), True, IR.REASON_OK),
    (mission(9004, fail_stage="wall_exhausted", tool_calls=0), True, IR.REASON_OK),
    (mission(9005, fail_stage="no_progress", tool_calls=0), True, IR.REASON_OK),
    (mission(9006, status="revoked", tool_calls=12,
             revoked_reason="声明被独立复核证伪"), False, IR.REASON_CLAIM_FALSIFIED),
    (mission(9007, status="failed", tool_calls=40, accepted=False), False,
     IR.REASON_ACCEPTANCE_REJECTED),
]


# ── 判据 a：混淆矩阵 ─────────────────────────────────────────────────────────
def test_confusion_matrix_7_of_7():
    results = [(IR.classify_replayable(m), expect, reason) for m, expect, reason in SEVEN_CASES]
    for verdict, expect, reason in results:
        assert verdict["replayable"] is expect, verdict
        assert verdict["reason_code"] == reason, verdict


def test_honest_fail_never_replayable():
    """honest FAIL（验收方明确写结论）一律 false——重播=掩盖失败。"""
    verdict = IR.classify_replayable(
        mission(9101, tool_calls=0, decision_note="C1–C5 判据未达成，诚实记 FAIL"))
    assert verdict["replayable"] is False
    assert verdict["reason_code"] == IR.REASON_ACCEPTANCE_REJECTED


def test_runtime_stage_inside_evidence_checks_is_seen():
    """终局标记可能只落在 evidence.checks.fail_stage（列 fail_stage 全空），必须能读到。"""
    verdict = IR.classify_replayable(
        mission(9102, tool_calls=0, gate="no-evidence", stage_in_checks="llm_error"))
    assert verdict["replayable"] is True
    assert "llm_error" in verdict["stage_tokens"]


def test_composite_stage_token_is_split():
    tokens = IR.stage_tokens(mission(9103, fail_stage="budget_exhausted+forced_finish"))
    assert "budget_exhausted" in tokens and "forced_finish" in tokens


def test_work_already_done_is_not_replayable():
    verdict = IR.classify_replayable(mission(9104, fail_stage="wall_exhausted", tool_calls=60))
    assert verdict["replayable"] is False
    assert verdict["reason_code"] == IR.REASON_WORK_ALREADY_DONE


def test_unknown_tool_calls_is_not_proven_zero_work():
    verdict = IR.classify_replayable(mission(9105, fail_stage="llm_error", tool_calls=None))
    assert verdict["replayable"] is False
    assert verdict["reason_code"] == IR.REASON_TOOL_CALLS_UNKNOWN


def test_stage_outside_whitelist_is_rejected():
    # timeout 命中了上层的运行期正则，但**不在**本闭环的白名单五类内 → 拒绝
    verdict = IR.classify_replayable(
        {"id": 9106, "status": "failed", "repo": "r", "model": "m",
         "result": {"fail_stage": "timeout", "tool_calls": 0}})
    assert verdict["replayable"] is False
    assert verdict["reason_code"] == IR.REASON_STAGE_NOT_WHITELISTED


def test_admin_dedupe_and_success_are_not_compensated():
    dedupe = IR.classify_replayable(
        mission(9107, status="revoked", tool_calls=0,
                revoked_reason="dup-dispatch-20261031 (owner 批量停重复)"))
    assert dedupe["reason_code"] == IR.REASON_ADMIN_DEDUPE
    ok = IR.classify_replayable(mission(9108, status="succeeded", tool_calls=99, gate="pass"))
    assert ok["replayable"] is False and ok["reason_code"] == IR.REASON_SUCCEEDED


def test_non_terminal_mission_is_not_judged():
    verdict = IR.classify_replayable(mission(9109, status="running", fail_stage="llm_error"))
    assert verdict["replayable"] is False
    assert verdict["reason_code"] == IR.REASON_NOT_TERMINAL


# ── 判据 g：kill-switch ──────────────────────────────────────────────────────
def test_kill_switch_turns_everything_off():
    verdicts = [IR.classify_replayable(m, kill_switch=True) for m, _, _ in SEVEN_CASES]
    assert all(v["replayable"] is False for v in verdicts)
    assert {v["reason_code"] for v in verdicts} == {IR.REASON_KILL_SWITCH}


# ── 回退链 ───────────────────────────────────────────────────────────────────
def test_fallback_chain_skips_blacklist_and_orders_by_yield():
    yields = {"low": 0.2, "best": 0.9, "mid_a": 0.5, "mid_b": 0.5}
    assert IR.select_fallback_model(yields, []) == "best"
    assert IR.select_fallback_model(yields, ["best"]) == "mid_a"      # 同 yield 名字定序
    assert IR.select_fallback_model(yields, ["best", "mid_a"]) == "mid_b"
    assert IR.select_fallback_model({"only": 0.1}, ["ONLY"]) is None  # 大小写不敏感
    assert IR.select_fallback_model({}, []) is None


# ── 判据 c：熔断 ────────────────────────────────────────────────────────────
def test_breaker_trips_only_on_three_consecutive():
    two = [{"model": "m1", "primary": "infra_runtime"}] * 2
    assert IR.breaker_tripped(two, "m1") is False
    three = two + [{"model": "m1", "primary": "infra_runtime"}]
    assert IR.breaker_tripped(three, "m1") is True


def test_breaker_streak_broken_by_a_success():
    events = [{"model": "m1", "primary": "infra_runtime"},
              {"model": "m1", "primary": "infra_runtime"},
              {"model": "m1", "primary": "succeeded_verified"},
              {"model": "m1", "primary": "infra_runtime"}]
    assert IR.breaker_tripped(events, "m1") is False


def test_breaker_does_not_mix_models():
    events = [{"model": "m1", "primary": "infra_runtime"},
              {"model": "m2", "primary": "infra_runtime"},
              {"model": "m1", "primary": "infra_runtime"}]
    assert IR.breaker_tripped(events, "m1") is False


def test_breaker_run_round_writes_blacklist_and_event():
    store = IR.InMemoryStore()
    events = [{"model": "mX", "primary": "infra_runtime"}] * 3
    report = IR.run_round([], store=store, events=events, model_yields={"mX": 0.9, "mY": 0.4})
    assert store.list_blacklist() == ["mX"]
    assert [e["event_type"] for e in store.events] == [IR.BREAKER_EVENT_TYPE]
    assert report["breaker"][0]["row_added"] is True
    assert IR.select_fallback_model({"mX": 0.9, "mY": 0.4}, store.list_blacklist()) == "mY"
    # 幂等：再跑一轮不重复加行
    IR.run_round([], store=store, events=events, model_yields={"mX": 0.9})
    assert store.list_blacklist() == ["mX"]


# ── 判据 b：幂等 ────────────────────────────────────────────────────────────
def test_three_calls_produce_one_mission():
    store = IR.InMemoryStore()
    target = mission(9201, fail_stage="llm_error", tool_calls=0)
    records = [IR.replay_one(target, store=store, model_yields={"mA": 1.0}) for _ in range(3)]
    assert records[0]["action"] == "replay"
    assert [r["reason_code"] for r in records[1:]] == [IR.REASON_ALREADY_REPLAYED] * 2
    assert len(store.missions) == 1
    created = [r for r in store.lifecycle if r["detail"].get("action") == "replay_created"]
    assert len(created) == 1
    replay = next(iter(store.missions.values()))
    assert replay["parent_mission_id"] == 9201
    assert IR.is_replay_mission(replay)
    assert replay["task_key"].startswith(IR.REPLAY_TASK_KEY_PREFIX)
    assert store.events[0]["event_type"] == IR.EVENT_TYPE


def test_replay_mission_carries_parent_and_attempt_in_task_key():
    store = IR.InMemoryStore()
    target = mission(9202, stop_condition="agent_lost", tool_calls=0)
    first = IR.replay_one(target, store=store, model_yields={"mA": 1.0}, plan_total_nodes=11)
    store.missions[first["replay_mission_id"]]["status"] = "failed"   # 重播也掉了
    second = IR.replay_one(target, store=store, model_yields={"mA": 1.0}, plan_total_nodes=11)
    assert second["attempt"] == 2
    assert store.missions[second["replay_mission_id"]]["task_key"].endswith(":2")


# ── 判据 d：预算 ────────────────────────────────────────────────────────────
def test_third_replay_denied_with_budget_cap():
    store = IR.InMemoryStore()
    target = mission(9301, fail_stage="no_progress", tool_calls=0)
    for _ in range(2):
        rec = IR.replay_one(target, store=store, model_yields={"mA": 1.0}, plan_total_nodes=11)
        assert rec["action"] == "replay"
        store.missions[rec["replay_mission_id"]]["status"] = "failed"
    third = IR.replay_one(target, store=store, model_yields={"mA": 1.0}, plan_total_nodes=11)
    assert third["action"] == "skip"
    assert third["reason_code"] == IR.REASON_BUDGET_CAP
    assert third["budget"]["scope"] == "node"
    denied = [r for r in store.lifecycle if r["detail"].get("action") == "replay_denied"]
    assert denied and denied[-1]["detail"]["reason"] == IR.REASON_BUDGET_CAP


def test_plan_share_caps_wide_node_sets():
    budget = IR.check_budget(node_replays=0, plan_replays=2, plan_total_nodes=11)
    assert budget["allowed"] is False and budget["scope"] == "plan"
    assert budget["raw_plan_cap"] == 2 and budget["effective_plan_cap"] == 2
    small = IR.check_budget(node_replays=0, plan_replays=0, plan_total_nodes=3)
    assert small["allowed"] is True and small["effective_plan_cap"] == 1
    unknown = IR.check_budget(node_replays=0, plan_replays=5, plan_total_nodes=0)
    assert unknown["allowed"] is True and unknown["plan_scope"] == "unknown"


def test_plan_budget_denial_writes_audit_row():
    store = IR.InMemoryStore()
    target = mission(9302, fail_stage="llm_error", tool_calls=0)
    # 先在同一个 plan 里铺满 20% 名额（11 节点 → 2 个）
    for other in (9303, 9304):
        IR.replay_one(mission(other, fail_stage="llm_error", tool_calls=0), store=store,
                      model_yields={"mA": 1.0}, plan_total_nodes=11)
    rec = IR.replay_one(target, store=store, model_yields={"mA": 1.0}, plan_total_nodes=11)
    assert rec["reason_code"] == IR.REASON_BUDGET_CAP
    assert rec["budget"]["scope"] == "plan"


def test_no_fallback_model_is_audited():
    store = IR.InMemoryStore()
    store.blacklist_add("only", "test")
    rec = IR.replay_one(mission(9305, fail_stage="llm_error", tool_calls=0), store=store,
                        model_yields={"only": 1.0}, plan_total_nodes=11)
    assert rec["reason_code"] == IR.REASON_NO_MODEL
    assert any(r["detail"].get("reason") == IR.REASON_NO_MODEL for r in store.lifecycle)


# ── KPI ────────────────────────────────────────────────────────────────────
def test_campaign_yield_6_of_11():
    nodes = ["done"] * 6 + ["blocked"] * 5
    kpi = IR.campaign_yield(nodes)
    assert (kpi["succeeded"], kpi["total"]) == (6, 11)
    assert kpi["yield"] == pytest.approx(6 / 11)
    assert kpi["yield_6dp"] == 0.545455


def test_mission_level_yield_diverges_from_node_yield():
    assert IR.campaign_yield(["done"] * 6 + ["blocked"] * 5)["yield"] == pytest.approx(6 / 11)
    assert IR.mission_level_yield(["succeeded"] * 11)["yield"] == 1.0


def test_infra_loss_and_forced_finish_rates():
    verdicts = [IR.classify_replayable(m) for m, _, _ in SEVEN_CASES]
    loss = IR.infra_loss_rate(verdicts)
    assert loss["terminal"] == 7 and loss["infra_runtime"] == 5
    assert loss["infra_loss_rate"] == pytest.approx(5 / 7)
    forced = IR.forced_finish_rate(verdicts)
    assert forced["forced_finish"] == 1              # 只有 budget_exhausted+forced_finish
    assert forced["forced_finish_rate"] == pytest.approx(1 / 7)


def test_run_round_totals_add_up():
    store = IR.InMemoryStore()
    missions = [m for m, _, _ in SEVEN_CASES]
    # plan 40 节点 → 份额帽 floor(40*0.2)=8，7 条样本不会被份额闸截断
    report = IR.run_round(missions, store=store, model_yields={"mA": 1.0, "mB": 0.5},
                          plan_total_nodes={PLAN: 40})
    totals = report["totals"]
    assert totals["scanned"] == 7
    assert totals["replayed"] == 5
    assert totals["skipped"] == 2
    assert totals["replayed"] + totals["skipped"] + totals["already_replayed"] \
        + totals["budget_cap"] == totals["scanned"]
    assert len(store.missions) == 5


def test_plan_share_truncates_a_round():
    """份额闸真的会截断：20 节点 plan 只给 4 个补偿名额，第 5 条必须被拒。"""
    store = IR.InMemoryStore()
    report = IR.run_round([m for m, _, _ in SEVEN_CASES], store=store,
                          model_yields={"mA": 1.0}, plan_total_nodes={PLAN: 20})
    assert report["totals"]["replayed"] == 4
    assert report["totals"]["budget_cap"] == 1
    assert len(store.missions) == 4


# ── 平台中性：交付文件不得含领域词 ─────────────────────────────────────────
def test_delivered_module_is_domain_neutral():
    from tools.evidence_domain_scan import load_lexicon, scan_paths
    result = scan_paths(["tools/validation/infra_replay.py",
                         "tools/validation/infra_replay_acceptance.py",
                         "tools/validation/infra_replay_report.py",
                         "tests/test_infra_replay.py"],
                        lexicon=load_lexicon(), root=ROOT)
    assert result["hits"] == [], result["hits"][:5]

# ── 报告生成器：工件必须可复算（零手抄的守门） ──────────────────────────────
def test_report_generator_consumes_committed_artifacts():
    acc_path = os.path.join(ROOT, "artifacts/infra_replay/acceptance.json")
    round_path = os.path.join(ROOT, "artifacts/infra_replay/round.json")
    if not (os.path.exists(acc_path) and os.path.exists(round_path)):
        pytest.skip("artifacts not present (fresh checkout before acceptance run)")
    from tools.validation import infra_replay_report as REP  # noqa: PLC0415
    acc = json.load(open(acc_path, encoding="utf-8"))
    round_rep = json.load(open(round_path, encoding="utf-8"))
    md = REP.build_report(acc, round_rep)
    assert "判据 a" in md and "误差判定" in md
    # 报告里的数必须来自工件：yield 字符串与工件同源
    assert f"{acc['kpi']['campaign.yield']['yield']:.6f}" in md
    assert acc["verdict"] in ("PASS", "FAIL")
