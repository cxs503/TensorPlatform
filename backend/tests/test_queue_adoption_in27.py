# -*- coding: utf-8 -*-
"""IN27 影子诊断单 a151356df79cb336 —— adoption_stall 根因与判据回归。

器官原判据：`min(created_at) FROM tensor_dev_missions WHERE status='queued'`
2026-11-07 读到 8820 min（阈值 240）。本测试把复算出的**两条独立根因**钉成
可机器裁的断言，并各配一条负控（negative control），防止把「测口径」当成「修」：

L1 口径：停挂单（suspended_at 非空）被算进「入池无人领」——它按设计不参与收养，
        且停在 status='queued' 时既不能 /resume 又能被领走（冻结洞）。
L2 引脚：被钉 model 的车道没有近期认领证据（会话/注册漂移），引脚单永久无人领。
L3 平台侧护栏：claim 查询必须排除停挂单；会话配置漂移的 409 必须带机器可读 code。
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


qa = _load("queue_adoption_in27", "backend/api/queue_adoption.py")

NOW = datetime(2026, 11, 7, 10, 30, 47, tzinfo=timezone.utc)


def _m(mid, minutes_ago, model=None, suspended=False, status="queued"):
    return {
        "id": mid,
        "status": status,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "suspended_at": (NOW - timedelta(minutes=minutes_ago)).isoformat() if suspended else None,
        "model": model,
    }


# ── L1 口径：parked 与 adoptable 分离，且只有 adoptable 参与越界判定 ──────────


def test_parked_mission_does_not_age_the_adoption_metric():
    """真实形态：11-01 冻结停挂单 + 一批 10 小时前的引脚单。"""
    rows = [_m(1572, 8820, model="glm-5.1", suspended=True)] + \
           [_m(5000 + i, 600, model="glm-5.1") for i in range(3)]
    split = qa.split_backlog(rows, NOW)
    assert split["parked_count"] == 1 and split["adoptable_count"] == 3
    assert split["oldest_any_minutes"] == pytest.approx(8820.0)   # 影子器官原口径
    assert split["oldest_adoptable_minutes"] == pytest.approx(600.0)  # 真实收养年龄
    assert split["oldest_parked_minutes"] == pytest.approx(8820.0)
    assert split["parked_but_queued_ids"] == [1572]
    assert split["violating"] is True  # 600 > 240：口径修正后仍越界 → 另有缺陷


def test_negative_control_parked_only_queue_is_not_a_stall():
    """负控：池子里只剩停挂单时不该报收养停滞（按设计无人认领）。"""
    split = qa.split_backlog([_m(1572, 8820, model="glm-5.1", suspended=True)], NOW)
    assert split["adoptable_count"] == 0
    assert split["violating"] is False


def test_negative_control_fresh_queue_is_not_a_stall():
    split = qa.split_backlog([_m(1, 30), _m(2, 10)], NOW)
    assert split["oldest_adoptable_minutes"] == pytest.approx(30.0)
    assert split["violating"] is False


def test_non_queued_rows_are_ignored():
    rows = [_m(1, 9000, status="suspended"), _m(2, 9000, status="running"), _m(3, 1)]
    split = qa.split_backlog(rows, NOW)
    assert split["queued_total"] == 1 and split["adoptable_ids"] == [3]


# ── L2 引脚：车道活性看认领证据，不看心跳 ──────────────────────────────────


RESIDENTS = [{"worker_id": "tp-gen-4", "model": "glm-5.1", "role": "generalist",
              "enabled": "t", "state": "idle"}]


def test_orphan_pin_has_registrant_but_no_claim_evidence():
    """tp-gen-4 真实形态：注册 glm-5.1、心跳新鲜 state=idle，却 6 天没领到过 glm-5.1。"""
    rows = [_m(5000 + i, 600, model="glm-5.1") for i in range(19)]
    claims = [{"claimed_by": "tp-gen-4", "model": "deepseek-v4-flash",
               "claimed_at": (NOW - timedelta(minutes=20)).isoformat()}]
    audit = qa.pin_audit(rows, RESIDENTS, claims, NOW)
    assert audit["orphan_pins"] == ["glm-5.1"]
    lane = audit["lanes"][0]
    assert lane["lane_alive"] is False and lane["registered_claimant_ids"] == ["tp-gen-4"]
    drift = audit["drift_suspects"][0]
    assert drift["drift"] is True and drift["last_claimed_model"] == "deepseek-v4-flash"


def test_negative_control_live_lane_is_not_an_orphan_pin():
    """负控：同一 model 近期真被领到过 → 车道活，不许报孤儿引脚。"""
    rows = [_m(5000 + i, 600, model="glm-5.1") for i in range(19)]
    claims = [{"claimed_by": "tp-gen-4", "model": "glm-5.1",
               "claimed_at": (NOW - timedelta(minutes=30)).isoformat()}]
    audit = qa.pin_audit(rows, RESIDENTS, claims, NOW)
    assert audit["orphan_pins"] == []
    assert audit["lanes"][0]["lane_alive"] is True


def test_negative_control_young_pin_below_threshold_is_not_orphan():
    rows = [_m(5000, 60, model="glm-5.1")]
    assert qa.pin_audit(rows, RESIDENTS, [], NOW)["orphan_pins"] == []


def test_disabled_registrant_does_not_hold_the_lane():
    residents = [dict(RESIDENTS[0], enabled="f")]
    audit = qa.pin_audit([_m(5000, 600, model="glm-5.1")], residents, [], NOW)
    assert audit["lanes"][0]["registered_claimant_ids"] == []
    assert audit["orphan_pins"] == ["glm-5.1"]


# ── 修复计划：只输出可逆动作，且不碰阈值 ─────────────────────────────────


def test_heal_plan_unparks_suspended_and_unpins_only_orphan_models():
    rows = [_m(1572, 8820, model="glm-5.1", suspended=True),
            _m(5000, 600, model="glm-5.1"),
            _m(5001, 600, model="deepseek-v4-flash")]
    claims = [{"claimed_by": "tp-gen-4", "model": "deepseek-v4-flash",
               "claimed_at": (NOW - timedelta(minutes=20)).isoformat()}]
    plan = qa.heal_plan(rows, RESIDENTS, claims, NOW)
    assert plan["unpark"] == [{"mission_id": 1572, "action": "restore_status_suspended"}]
    assert plan["unpin"] == [{"mission_id": 5000, "from_model": "glm-5.1"}]  # 不碰活车道
    assert qa.THRESHOLD_MINUTES == 240.0  # 修复不靠调阈值


# ── L3 平台护栏：源码级不变量 ─────────────────────────────────────────────


def _missions_src() -> str:
    return (BACKEND / "api" / "tensor_missions.py").read_text(encoding="utf-8")


def test_claim_query_never_adopts_parked_missions():
    src = _missions_src()
    assert "AND status = 'queued' AND suspended_at IS NULL" in src, (
        "claim 必须排除停挂单：suspended_at 非空的单按设计不可领")


def test_session_config_drift_is_machine_readable():
    src = _missions_src()
    assert '"code": "session_config_drift"' in src
    assert "presented_model" in src and "registered_model" in src