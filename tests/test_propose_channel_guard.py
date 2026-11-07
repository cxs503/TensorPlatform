"""IN20-R4 自主提案通道根因修复的回归测试（脱库，全部合成数据）。

覆盖三层：
  ① propose_channel_guard 的判定/审计/回滚纯逻辑（真实 9 张积压的镜像数据集）
  ② campaign_dispatcher 的模型活体钉选 + task_key 去重纪元（幂等死单不再被当新点火）
  ③ wave_cycle 的 session 无关兜底派发（无议事会也要有活可派，且 task_key 幂等去重）

负例纪律：活体模型上的 queued 单必须 keep（不误伤）；刚入池的单必须在宽限期内 keep。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TP = REPO / "ops" / "taskplane"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("pcg_guard", TP / "propose_channel_guard.py")
disp = _load("pcg_dispatcher", TP / "campaign_dispatcher.py")
wave = _load("pcg_wave_cycle", TP / "wave_cycle.py")

NOW = 1793750000.0          # 2026-11-07 附近（固定，测试确定性）
T0 = NOW - 3600             # 一小时前入池


# ─────────────────────── ① guard：活体名册 ───────────────────────
def test_live_models_filters_stale_disabled_and_stopped():
    rows = [
        {"model": "qwen3.7-plus", "enabled": "t", "heartbeat_at": str(NOW - 30), "state": "idle"},
        {"model": "deepseek-v4-flash", "enabled": "t", "heartbeat_at": str(NOW - 120), "state": "busy"},
        # 在册但心跳停了 2h = glm-5.1 的真实状态（进程没了，行还 enabled）
        {"model": "glm-5.1", "enabled": "t", "heartbeat_at": str(NOW - 7200), "state": "idle"},
        {"model": "glm-5.2", "enabled": "f", "heartbeat_at": str(NOW - 10), "state": "idle"},
        {"model": "Qwen3.8-27B", "enabled": "t", "heartbeat_at": str(NOW - 10), "state": "stopped"},
    ]
    assert guard.live_models(rows, NOW) == {"qwen3.7-plus", "deepseek-v4-flash"}


def test_parse_ts_handles_epoch_and_postgres_text():
    assert guard.parse_ts("1793750000") == 1793750000.0
    assert guard.parse_ts(1793750000) == 1793750000.0
    a = guard.parse_ts("2026-11-07 00:35:24.715368+00")
    b = guard.parse_ts("2026-11-07T00:35:24.715368Z")
    assert a == b and a is not None
    assert guard.parse_ts(None) is None


# ─────────────────────── ① guard：分类（镜像真实积压） ───────────────────────
def _backlog():
    """真实积压镜像：9 张 glm-5.1 槽位（p11..p20，缺 p14）+ 同波两个活体槽位已终态。"""
    ms = []
    for i, pid in enumerate([11, 12, 13, 15, 16, 17, 18, 19, 20], start=4966):
        ms.append({"id": i, "task_key": f"propose-p{pid}-w1-2", "model": "glm-5.1",
                   "status": "queued", "created_at": str(T0)})
    ms.append({"id": 9999, "task_key": "propose-p11-w1-1", "model": "qwen3.7-plus",
               "status": "succeeded", "created_at": str(T0)})
    return ms


def test_classify_withdraws_every_unclaimable_slot():
    live = {"qwen3.7-plus", "deepseek-v4-flash"}
    acts = guard.classify(_backlog(), live, NOW)
    todo = [a for a in acts if a["verb"] == "withdraw"]
    assert len(todo) == 9, acts
    assert {a["mission_id"] for a in todo} == set(range(4966, 4975))
    assert all("unclaimable_model" in a["reason"] for a in todo)
    assert all(a["claimable"] is False for a in todo)


def test_classify_keeps_claimable_slot_no_false_positive():
    live = {"qwen3.7-plus", "deepseek-v4-flash"}
    ms = [{"id": 1, "task_key": "propose-p30-w1-1", "model": "qwen3.7-plus",
           "status": "queued", "created_at": str(T0)}]
    acts = guard.classify(ms, live, NOW)
    assert acts[0]["verb"] == "keep" and acts[0]["claimable"] is True


def test_classify_keeps_within_grace_window():
    """刚入池（<stall_secs）的单不许碰——否则与正常 claim 抢跑。"""
    live = {"qwen3.7-plus"}
    ms = [{"id": 2, "task_key": "propose-p30-w1-2", "model": "glm-5.1",
           "status": "queued", "created_at": str(NOW - 60)}]
    acts = guard.classify(ms, live, NOW)
    assert acts[0]["verb"] == "keep" and "grace" in acts[0]["reason"]


def test_classify_unpinned_and_non_channel_are_untouched():
    live = set()
    ms = [{"id": 3, "task_key": "propose-p30-w1-3", "model": "", "status": "queued",
           "created_at": str(T0)},
          {"id": 4, "task_key": "IN20-R4", "model": "glm-5.1", "status": "queued",
           "created_at": str(T0)}]
    acts = guard.classify(ms, live, NOW)
    assert [a["verb"] for a in acts] == ["keep"]      # 非提案通道的单不在本守护范围
    assert acts[0]["claimable"] is True               # 未钉模型 = 任何 worker 可领


def test_classify_repin_policy_targets_live_model():
    live = {"qwen3.7-plus", "deepseek-v4-flash"}
    acts = guard.classify(_backlog(), live, NOW, repin_target="qwen3.7-plus", policy="repin")
    todo = [a for a in acts if a["verb"] == "repin"]
    assert len(todo) == 9 and all(a["repin_target"] == "qwen3.7-plus" for a in todo)
    assert "glm-5.1" in todo[0]["reason"]


# ─────────────────────── ① guard：仲裁推进诊断 ───────────────────────
def test_plan_diagnostics_pinpoints_blocking_proposer():
    plans = [{"id": 11, "status": "active", "proposers": [1, 2, 3], "arbiter": None}]
    by_id = {1: {"status": "succeeded"}, 2: {"status": "queued", "model": "glm-5.1"},
             3: {"status": "succeeded"}}
    d = guard.plan_diagnostics(plans, by_id, NOW)[0]
    assert d["succeeded"] == 2 and len(d["blocking"]) == 1
    assert d["blocking"][0]["id"] == 2 and d["advance_ready"] is False
    # 堵点终态化（撤销后）→ 够 2 份成功提案即就绪仲裁
    by_id[2]["status"] = "stopped"
    d2 = guard.plan_diagnostics(plans, by_id, NOW)[0]
    assert d2["blocking"] == [] and d2["advance_ready"] is True


# ─────────────────────── ① guard：审计 + 回滚 ───────────────────────
def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry-run 不许碰 DB")
    monkeypatch.setattr(guard, "psql", boom)
    acts = guard.classify(_backlog(), {"qwen3.7-plus", "deepseek-v4-flash"}, NOW)
    changed, shift = guard.apply_actions(acts, tmp_path, dry_run=True)
    assert len(changed) == 9 and all(c["applied"] is False for c in changed)
    assert not shift.exists() and not list(tmp_path.glob("audit-*.jsonl"))


def test_apply_writes_audit_and_rollback_then_restores(tmp_path, monkeypatch):
    seen = []

    def fake_psql(sql, timeout=60):
        seen.append(sql)
        return [["1"]] if "RETURNING id" in sql else []
    monkeypatch.setattr(guard, "psql", fake_psql)
    acts = guard.classify(_backlog(), {"qwen3.7-plus"}, NOW)
    changed, shift = guard.apply_actions(acts, tmp_path, dry_run=False)
    assert len([c for c in changed if c["applied"]]) == 9
    audit = list(tmp_path.glob("audit-*.jsonl"))
    assert len(audit) == 1
    lines = [json.loads(x) for x in audit[0].read_text().splitlines()]
    assert len(lines) == 9 and all(l["verb"] == "withdraw" for l in lines)
    recs = json.loads(shift.read_text())["records"]
    assert len(recs) == 9 and recs[0]["model"] == "glm-5.1"
    # 撤销只对 queued 生效 + 带独立 result.guard 键（可回滚）
    assert all("status IN ('queued','suspended')" in s for s in seen if "UPDATE" in s)
    assert all("'guard'" in s for s in seen if "UPDATE" in s)
    # 回滚 SQL 恰好恢复 queued 并摘掉 guard 键
    rb = guard.rollback_sql(recs[0])
    assert "status='queued'" in rb and "- 'guard'" in rb
    done = guard.apply_rollback(recs[:1], tmp_path)
    assert done[0]["restored"] is True


# ─────────────────────── ② dispatcher：模型活体钉选 ───────────────────────
def test_pick_proposer_models_drops_dead_model():
    picked = disp.pick_proposer_models({"qwen3.7-plus", "deepseek-v4-flash"})
    assert picked == ["qwen3.7-plus", "deepseek-v4-flash"]      # glm-5.1 无活体被剔除
    assert "glm-5.1" not in picked


def test_pick_proposer_models_falls_back_to_unpinned():
    """一个活体都没有 → 不钉模型（claim_null_model 通道兜底），绝不造永远领不走的单。"""
    assert disp.pick_proposer_models(set()) == [None]
    # 候选全灭但舰队有别的活体模型：让位于可领取性
    assert disp.pick_proposer_models({"Qwen3.8-27B"}) == ["Qwen3.8-27B"]


def test_dispatcher_accepts_suspended_as_terminal():
    """p9-w1-2 自 2026-11-01 停在 suspended，旧 TERMINAL 不认 → plan 9 堵 6 天。"""
    assert "suspended" in disp.TERMINAL and "orphan" in disp.TERMINAL


# ─────────────────────── ② dispatcher：task_key 去重纪元 ───────────────────────
def test_dispatch_mission_reissues_epoch_when_idempotent_hit_is_terminal(monkeypatch):
    calls = []

    def fake_api(path, payload=None, token=None):   # dispatcher.api(path, payload, token)
        calls.append(payload["task_key"])
        return 200, {"id": 100 + len(calls), "idempotent": True}

    status = {"100": "failed"}
    monkeypatch.setattr(disp, "api", fake_api)
    monkeypatch.setattr(disp, "q", lambda sql: [[status.get("100", "queued")]])
    mid = disp._dispatch_mission("tok", "goal", "propose-p30-w1-2", model="glm-5.1")
    assert calls == ["propose-p30-w1-2", "propose-p30-w1-2-r2"], calls
    assert mid == 102          # 拿到的是新纪元单，不是死单 101


def test_dispatch_mission_reuses_live_idempotent_mission(monkeypatch):
    calls = []

    def fake_api(path, payload=None, token=None):
        calls.append(payload["task_key"])
        return 200, {"id": 777, "idempotent": True}

    monkeypatch.setattr(disp, "api", fake_api)
    monkeypatch.setattr(disp, "q", lambda sql: [["running"]])
    assert disp._dispatch_mission("tok", "goal", "propose-p30-w1-1", model="qwen3.7-plus") == 777
    assert calls == ["propose-p30-w1-1"]      # 在飞 → 幂等复用，不重派


# ─────────────────────── ③ wave_cycle：session 无关兜底 ───────────────────────
NODE_ROWS = [
    [186, 21, "IN20-R1", "TensorPlatform", "目标" * 60, '["判据1","判据2"]', "active"],
    [193, 21, "IN20-D1", "TensorLBM", "目标" * 60, "[]", "active"],
    [194, 21, "IN20-D2", "ship-platform", "目标" * 60, "[]", "active"],   # 别名归一
    [195, 21, "IN20-D3", "TensorFEM", "目标" * 60, "[]", "active"],        # 别名归一
    [197, 21, "IN20-G21", "TensorPaper", "目标" * 60, "[]", "active"],     # 白名单外 → 跳过
    [198, 21, "IN20-G22", "TensorPlatform", "   ", "[]", "active"],        # 空 goal → 跳过
]


def test_fallback_rows_builds_idempotent_payloads():
    payloads, skipped = wave.fallback_rows(NODE_ROWS, max_wave=10)
    keys = [p["task_key"] for p in payloads]
    assert keys == ["cnode-186", "cnode-193", "cnode-194", "cnode-195"]
    assert all(p["repo"] in wave.REPOS for p in payloads)
    assert [p["repo"] for p in payloads][2:3] == ["ship-performance-platform"]
    assert [p["repo"] for p in payloads][3:4] == ["TensorPhysics"]
    assert payloads[0]["goal"].startswith("目标")
    assert "机器验收判据" in payloads[0]["goal"] and "机器验收判据" not in payloads[1]["goal"]
    assert any("TensorPaper" in s for s in skipped) and any("空 goal" in s for s in skipped)


def test_fallback_rows_respects_budget_cap():
    rows = [[i, 21, f"N{i}", "TensorPlatform", "目标" * 60, "[]", "active"] for i in range(20)]
    payloads, skipped = wave.fallback_rows(rows, max_wave=3)
    assert len(payloads) == 3 and any("超帽" in s for s in skipped)


def test_ready_nodes_sql_is_idempotent_by_task_key():
    sql = wave.ready_nodes_sql()
    assert "NOT EXISTS" in sql and "'cnode-'" in sql
    assert "n.status IN ('in-flight','blocked')" in sql and "p.status" in sql


def test_dispatch_fallback_wave_dispatches_without_any_session():
    posted = []

    def fake_api(method, path, payload=None, token=None):
        posted.append(payload)
        return 201, {"id": 600 + len(posted)}

    ids = wave.dispatch_fallback_wave(9, api_fn=fake_api, q_fn=lambda sql: NODE_ROWS,
                                      login_fn=lambda: "tok", logf=lambda m: None)
    assert ids == [601, 602, 603, 604]
    assert all(p["origin"] == "agent" and p["runner"] == "native" for p in posted)
    assert [p["task_key"] for p in posted] == ["cnode-186", "cnode-193", "cnode-194", "cnode-195"]


def test_dispatch_fallback_wave_degrades_when_pool_empty():
    assert wave.dispatch_fallback_wave(9, api_fn=lambda *a, **k: (500, {}),
                                       q_fn=lambda sql: [], login_fn=lambda: "t",
                                       logf=lambda m: None) == []


def test_no_human_halt_after_consecutive_session_failures():
    """渐退令：连败只转兜底模式，不许再写 halt 等人（旧代码 = 静默空转的根因）。"""
    src = (TP / "wave_cycle.py").read_text()
    assert 'st["fallback_mode"] = True' in src
    assert 'st["halt"] = f"连续' not in src           # 自动熔断等人已移除
    assert "dispatch_fallback_wave(n)" in src         # 无计划 → 兜底派发接入主循环
    assert "HEARTBEAT_SECS" in src                    # 空转心跳（日志不再黑箱）

# ─────────────────────── ① guard：行为级活体证据（名册会撒谎） ───────────────────────
def test_activity_models_detects_dead_model_from_claim_history():
    rows = [{"model": "deepseek-v4-flash", "last_hb": str(NOW - 120)},
            # 超 1h 窗口 → 无活动
            {"model": "qwen3.7-plus", "last_hb": str(NOW - 3700)},
            # glm-5.1 名册行 enabled+心跳紧跟，但名下最近一次 claim 心跳停在 5 天前
            {"model": "glm-5.1", "last_hb": str(NOW - 5 * 86400)},
            {"model": "", "last_hb": str(NOW)}]
    assert guard.activity_models(rows, NOW) == {"deepseek-v4-flash"}


def test_classify_catches_roster_lie_no_claim_activity():
    """名册说 glm-5.1 enabled+idle，实际零 claim 活动 → 仍须带理由撤销（不许误判为 keep）。"""
    live = {"qwen3.7-plus", "deepseek-v4-flash", "glm-5.1"}     # 名册包含 glm-5.1
    active = {"qwen3.7-plus", "deepseek-v4-flash"}              # 行为证据里没有
    acts = guard.classify(_backlog(), live, NOW, active=active)
    todo = [a for a in acts if a["verb"] == "withdraw"]
    assert len(todo) == 9
    assert all("roster_says_live_but_no_claim_activity" in a["reason"] for a in todo)


def test_classify_keeps_when_roster_and_activity_agree():
    live = {"qwen3.7-plus", "glm-5.1"}
    acts = guard.classify(_backlog()[:1], live, NOW, active=live)
    assert acts[0]["verb"] == "keep"


def test_classify_withdraws_suspended_slot_and_rolls_back_to_suspended():
    """p9-w1-2 停在 suspended（旧 TERMINAL 不认）→ 同样按龄撤销，且回滚回 suspended 而非 queued。"""
    ms = [{"id": 1572, "task_key": "propose-p9-w1-2", "model": "glm-5.1",
           "status": "suspended", "created_at": str(NOW - 6 * 86400)}]
    acts = guard.classify(ms, {"qwen3.7-plus"}, NOW)
    assert acts[0]["verb"] == "withdraw" and acts[0]["prev_status"] == "suspended"
    assert "blocks_arbitration" in acts[0]["reason"]
    sql = guard.withdraw_sql(acts[0], "2026-11-07T00:00:00Z")
    assert "status IN ('queued','suspended')" in sql and "arbiter_starved" in sql
    rb = guard.rollback_sql({"mission_id": 1572, "verb": "withdraw", "prev_status": "suspended"})
    assert "'suspended'" in rb
