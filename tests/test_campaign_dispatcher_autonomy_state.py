"""IN27-H1 回归测试：``metadata.autonomy`` 旧形状不得让守护告警流无限刷 ERROR。

病灶（shadowdiag-a242be993ae2d1d6，实测 plan 94）：
  persistent state = {"wave": 2, "arbiter": 5498, "proposers": [5209, 5210, 5211]}
  → ``aut["arbiter_attempts"] += 1`` KeyError；异常在 ``_set_plan_autonomy`` 之前抛出，
    坏状态写不回去 ⇒ 每 30s 重犯，campaign_dispatcher.log 每小时 55~64 行 ERROR
    ⇒ 器官 daemon_warns / error_total 越界（阈值 10）。

判据（脱库，全合成数据，纯函数 + 打桩）：
  ① ``_norm_autonomy`` 对旧形状补默认、对良形状零改动（幂等）；
  ② 旧形状进入 ``_resolve_arbiter_outcome`` 不再抛 KeyError，且按设计推进重试计数；
  ③ ``autonomy_phase`` 读到旧形状时**先补默认并回写**，本轮不抛异常（修的是坏状态本身）；
  ④ 修复脚本 ``stage`` 只挑需修复的计划（不误伤良状态，幂等）。
"""
from __future__ import annotations

import importlib.util
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


disp = _load("in27h1_dispatcher", TP / "campaign_dispatcher.py")
repair = _load("in27h1_repair", TP / "autonomy_state_repair.py")

# 生产实测形状（plan 94）
LEGACY = {"wave": 2, "arbiter": 5498, "proposers": [5209, 5210, 5211]}
HEALTHY = {"wave": 2, "arbiter": 5498, "proposers": [5209, 5210, 5211],
           "new_plan": None, "arbiter_attempts": 1, "exhausted": False}


# ───────────────── ① 规整器 ─────────────────
def test_norm_backfills_missing_keys_on_legacy_shape():
    fixed, repaired = disp._norm_autonomy(LEGACY)
    assert repaired is True
    assert fixed["arbiter_attempts"] == 0 and isinstance(fixed["arbiter_attempts"], int)
    assert fixed["new_plan"] is None
    assert fixed["exhausted"] is False
    assert fixed["wave"] == 2
    assert fixed["arbiter"] == 5498
    assert fixed["proposers"] == [5209, 5210, 5211]
    # 不改入参（持久状态不可原地污染）
    assert "arbiter_attempts" not in LEGACY


def test_norm_is_idempotent_on_healthy_state():
    fixed, repaired = disp._norm_autonomy(HEALTHY)
    assert repaired is False
    assert fixed == HEALTHY


def test_norm_repairs_bad_types_and_none():
    fixed, repaired = disp._norm_autonomy({"wave": "x", "arbiter_attempts": None,
                                           "proposers": {"a": 1}})
    assert repaired is True
    assert fixed["wave"] == 1 and fixed["arbiter_attempts"] == 0
    assert fixed["proposers"] == []
    defaults, None_repair = disp._norm_autonomy(None)
    assert None_repair is False and defaults["arbiter_attempts"] == 0
    garbage, garbage_repair = disp._norm_autonomy("not-a-dict")
    assert garbage_repair is True and garbage["exhausted"] is False


# ───────────────── ② 仲裁推进不再 KeyError ─────────────────
def test_resolve_arbiter_outcome_survives_legacy_state(monkeypatch):
    """旧形状 + 仲裁者终态成功但无继任 draft → 按设计计入 1 次尝试，不抛异常。"""
    calls = {}
    monkeypatch.setattr(disp, "_mission", lambda mid: {"id": mid, "status": "succeeded"})
    monkeypatch.setattr(disp, "_collect_new_plan", lambda pid, mid: None)
    monkeypatch.setattr(disp, "_arbiter_model", lambda: "stub-model")

    def fake_dispatch(token, goal, key, model=None):
        calls["key"] = key
        return None
    monkeypatch.setattr(disp, "_dispatch_mission", fake_dispatch)

    out = disp._resolve_arbiter_outcome("tok", 94, "plan-94", dict(LEGACY))
    assert out["arbiter_attempts"] == 1
    # 重派 key 里带的是「第 2 次尝试」（attempts+1），证明计数语义与健康状态一致
    assert calls["key"] == f"arbiter-p94-w2-a2"


def test_healthy_state_still_counts_from_its_own_value(monkeypatch):
    """补默认不得覆盖已有真值：attempts=0 的良状态推进到 1（而不是被重置/跳过）。"""
    monkeypatch.setattr(disp, "_mission", lambda mid: {"id": mid, "status": "succeeded"})
    monkeypatch.setattr(disp, "_collect_new_plan", lambda pid, mid: None)
    monkeypatch.setattr(disp, "_arbiter_model", lambda: "stub-model")
    monkeypatch.setattr(disp, "_dispatch_mission", lambda *a, **k: 7777)

    out = disp._resolve_arbiter_outcome("tok", 94, "plan-94", dict(HEALTHY, arbiter_attempts=0))
    assert out["arbiter_attempts"] == 1      # 0 → 1（补默认不覆盖已有真值）
    assert out["arbiter"] == 7777


def test_attempts_at_cap_advances_wave_instead_of_dispatching(monkeypatch):
    """既有语义护栏：attempts 触顶 → 开下一波、attempts 归零（本修复不得改变）。"""
    monkeypatch.setattr(disp, "_mission", lambda mid: {"id": mid, "status": "succeeded"})
    monkeypatch.setattr(disp, "_collect_new_plan", lambda pid, mid: None)
    monkeypatch.setattr(disp, "_dispatch_mission", lambda *a, **k: 7777)

    out = disp._resolve_arbiter_outcome("tok", 94, "plan-94", dict(HEALTHY))   # attempts=1
    assert out["arbiter_attempts"] == 0 and out["wave"] == 3 and out["arbiter"] is None


# ───────────────── ③ autonomy_phase 读入即修 ─────────────────
def test_autonomy_phase_repairs_and_persists_without_raising(monkeypatch):
    persisted = []
    monkeypatch.setattr(disp, "q", lambda sql: [[94, "plan-94"]]
                        if "campaign_plans p" in sql else [])
    monkeypatch.setattr(disp, "_plan_meta", lambda pid: {"autonomy": dict(LEGACY)})
    monkeypatch.setattr(disp, "_set_plan_autonomy",
                        lambda pid, aut: persisted.append((pid, dict(aut))))
    monkeypatch.setattr(disp, "_mission", lambda mid: {"id": mid, "status": "succeeded"})
    monkeypatch.setattr(disp, "_collect_new_plan", lambda pid, mid: None)
    monkeypatch.setattr(disp, "_arbiter_model", lambda: "stub-model")
    monkeypatch.setattr(disp, "_dispatch_mission", lambda *a, **k: None)
    monkeypatch.setattr(disp, "_idle_free_wave", lambda token: None)
    monkeypatch.setattr(disp, "event", lambda *a, **k: None)

    disp.autonomy_phase("tok")      # 旧代码在此抛 KeyError: 'arbiter_attempts'

    # 首次回写=规整补默认（attempts 0），末次回写=本轮推进后的状态（attempts 1）
    assert [pid for pid, _ in persisted] == [94, 94]
    assert persisted[0][1]["arbiter_attempts"] == 0
    state = persisted[-1][1]
    assert state["arbiter_attempts"] == 1   # 先补 0 再由本轮推进到 1
    assert state["new_plan"] is None and state["exhausted"] is False


# ───────────────── ④ 数据修复脚本 ─────────────────
def test_repair_stage_selects_only_broken_states():
    todo = repair.stage({94: dict(LEGACY), 95: dict(HEALTHY)})
    assert [t["plan_id"] for t in todo] == [94]
    assert "arbiter_attempts" in todo[0]["missing"]
    assert repair.stage({95: dict(HEALTHY)}) == []      # 幂等：良状态不动


def test_repair_after_apply_needs_no_second_pass():
    first = repair.stage({94: dict(LEGACY)})[0]["after"]
    assert repair.stage({94: first}) == []