#!/usr/bin/env python3
"""coordinator_shadow v1 —— 账本与 72h 验收裁决测试（IN27-H1）。

判据：
1. 账本幂等：同 ``beat_id`` 写两次只留一行；v0 老表（无 beat_id/schema）能原地迁移，
   老列 ``verdict/pulse_digest`` 内容保留（IN27-H7 的分歧率评估仍可读老行）。
2. 账本写边界：只放行本表 DDL 与 append-only INSERT。
3. 覆盖率：144 拍 @30min = 72h → ``coverage_ratio == 1.0``；缺口与最长连续链算得准。
4. 错拍自愈：error 拍后 2 个 interval 内有成功拍 → self_heal_ratio 计对。
5. 人工修扫描：命中「手工修/人工干预/hotfix」等即入账。
6. 误报率：分母只含已处置；``unclaimed_expired`` 单列；边界 0.30 不通过（要求 <30%）。
7. 验收状态机：窗口未走完 → ``PENDING_WALL_CLOCK``（绝不自己升格 PASS）。
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.coordinator_shadow import acceptance as acc  # noqa: E402
from tools.coordinator_shadow import ledger as lg  # noqa: E402
from tools.coordinator_shadow import pulse as pu  # noqa: E402

T0 = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
IV = 1800


def _beat(i: int, error: str | None = None, filings=None) -> lg.Beat:
    ts = T0 + timedelta(seconds=i * IV)
    return lg.Beat(beat_id=str(int(ts.timestamp()) // IV), ts=pu.iso(ts), model="rule/1",
                   urgent=False, verdict={"schema": "x", "findings": []},
                   pulse_digest={"as_of": pu.iso(ts)}, filings=filings or [], error=error)


# ─────────────────────────── 1) 账本 ───────────────────────────


def test_ledger_append_idempotent():
    led = lg.SqliteLedger(":memory:")
    led.migrate()
    assert led.append(_beat(0)) is True
    assert led.append(_beat(0)) is False          # 同槽重复跑 → 不重复记
    assert led.append(_beat(1)) is True
    rows = led.read()
    assert len(rows) == 2 and rows[0]["beat_id"] != rows[1]["beat_id"]


def test_ledger_migrates_legacy_v0_table(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE coordinator_shadow (id INTEGER PRIMARY KEY, ts TEXT, model TEXT, "
                "urgent INTEGER, verdict TEXT, pulse_digest TEXT)")
    con.execute("INSERT INTO coordinator_shadow (id, ts, model, urgent, verdict, pulse_digest) "
                "VALUES (1,'2026-11-07T09:55:34+00:00','qwen3.7-plus',1,?,?)",
                (json.dumps({"urgent": True, "findings": [{"organ": "credit_backlog"}]}),
                 json.dumps({"credit_backlog": 37})))
    con.commit()
    con.close()

    led = lg.SqliteLedger(path)
    led.migrate()                                  # 原地加列 + 唯一索引
    rows = led.read()
    assert len(rows) == 1
    assert rows[0]["verdict"]["findings"][0]["organ"] == "credit_backlog"   # 老行仍可读
    assert rows[0]["beat_id"] is None
    assert led.append(_beat(0)) is True            # 迁移后能写新行
    assert len(led.read()) == 2


def test_ledger_guard_blocks_foreign_writes():
    led = lg.SqliteLedger(":memory:")
    with pytest.raises(lg.LedgerBoundaryViolation):
        led.guard.execute("DELETE FROM coordinator_shadow")
    with pytest.raises(lg.LedgerBoundaryViolation):
        led.guard.execute("UPDATE tensor_dev_missions SET status='done'")


def test_runtime_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    st = lg.RuntimeState(streaks={"credit_backlog:higher_worse": 3},
                         last_filed={"credit_backlog:higher_worse": "2026-11-06T01:00:00+00:00"},
                         last_filed_organ={"credit_backlog": "2026-11-06T01:00:00+00:00"},
                         beats_total=7, filings_total=2)
    st.save(path)
    loaded = lg.RuntimeState.load(path)
    assert loaded.streaks == st.streaks and loaded.beats_total == 7
    assert loaded.last_filed_organ == st.last_filed_organ
    assert lg.RuntimeState.load(tmp_path / "missing.json").beats_total == 0


# ─────────────────────────── 2) 覆盖率 / 自愈 ───────────────────────────


def test_coverage_full_window():
    beats = [b.to_dict() for b in (_beat(i) for i in range(144))]
    cov = acc.coverage(beats, T0, T0 + timedelta(hours=72), IV)
    assert cov["expected_beats"] == 144
    assert cov["present_beats"] == 144
    assert cov["coverage_ratio"] == 1.0
    assert cov["missing_beats"] == 0
    assert cov["gaps"] == []
    assert cov["longest_run_hours"] == pytest.approx(71.5, abs=0.01)


def test_coverage_detects_gaps_and_longest_run():
    idx = [i for i in range(144) if not (20 <= i < 30)]      # 中间缺 10 拍（5 小时）
    beats = [_beat(i).to_dict() for i in idx]
    cov = acc.coverage(beats, T0, T0 + timedelta(hours=72), IV)
    assert cov["present_beats"] == 134 and cov["missing_beats"] == 10
    assert cov["coverage_ratio"] == pytest.approx(134 / 144, abs=1e-4)
    assert len(cov["gaps"]) == 1
    # 缺口在 [20,30) 槽：前段 0..19（9.5h），后段 30..143（56.5h）→ 最长链 56.5h
    assert cov["longest_run_hours"] == pytest.approx(56.5, abs=0.01)


def test_self_healing_counts():
    beats = [_beat(0), _beat(1, error="boom"), _beat(2), _beat(3, error="boom"), _beat(5)]
    heal = acc.self_healing([b.to_dict() for b in beats], IV)
    assert heal["error_beats"] == 2 and heal["self_healed"] == 2
    assert heal["self_heal_ratio"] == 1.0
    beats2 = [_beat(0), _beat(1, error="boom"), _beat(9)]        # 4h 后才恢复 → 不算自愈
    heal2 = acc.self_healing([b.to_dict() for b in beats2], IV)
    assert heal2["self_heal_ratio"] == 0.0


def test_human_repair_detection():
    text = "\n".join([
        "2026-11-06 01:00:00 拍 123 ok",
        "2026-11-06 02:00:00 手工修改 state.json 后重启",
        "2026-11-06 03:00:00 hotfix applied to judge prompt",
        "2026-11-06 04:00:00 判读正常",
    ])
    hits = acc.count_human_repairs(text)
    assert len(hits) == 2
    assert acc.count_human_repairs("纯自动运行，无干预") == []


# ─────────────────────────── 3) 误报率 ───────────────────────────


def _filing(**kw):
    base = {"task_key": "shadowdiag-x", "mission_id": 1, "status": "succeeded",
            "claimed_by": "w1", "session_id": 1, "created_at": pu.iso(T0)}
    base.update(kw)
    return base


def test_fpr_excludes_unclaimed_and_pending():
    filings = [
        _filing(task_key="a", disposition="confirmed"),
        _filing(task_key="b", disposition="confirmed"),
        _filing(task_key="c", disposition="false_positive"),
        _filing(task_key="d", disposition="unclaimed_expired", claimed_by=None, session_id=None),
        _filing(task_key="e", disposition="pending", claimed_by=None, session_id=None, status="queued"),
    ]
    rep = acc.fpr_report(filings, now=T0 + timedelta(hours=1))
    assert rep["adjudicated"] == 3
    assert rep["fpr"] == pytest.approx(1 / 3, abs=1e-4)
    assert rep["unclaimed_expired"] == 1
    assert rep["fpr_including_unclaimed"] == pytest.approx(1 / 5, abs=1e-4)


def test_fpr_boundary_must_be_strictly_below_30pct():
    three_of_ten = [_filing(task_key=f"k{i}", disposition="confirmed" if i < 7 else "false_positive")
                    for i in range(10)]
    rep = acc.fpr_report(three_of_ten, now=T0)
    assert rep["fpr"] == pytest.approx(0.3, abs=1e-6)
    crit = acc.Criteria()
    result = acc.evaluate(beats=[b.to_dict() for b in (_beat(i) for i in range(144))],
                          filings=three_of_ten, criteria=crit,
                          start=T0, end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72))
    assert result["checks"]["C6_fpr"] is False           # == 30% 不算通过（要求 <30%）
    assert result["status"] == "FAIL"


# ─────────────────────────── 4) 验收状态机 ───────────────────────────


def _good_filings(n=3):
    return [_filing(task_key=f"shadowdiag-{i}", mission_id=i, disposition="confirmed") for i in range(n)]


def test_acceptance_pass_when_all_criteria_met():
    beats = [b.to_dict() for b in (_beat(i) for i in range(144))]
    result = acc.evaluate(beats=beats, filings=_good_filings(3),
                          start=T0, end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72))
    assert result["status"] == "PASS", result["reason"]
    assert result["numbers"]["claimed_executed"] == 3
    assert result["numbers"]["fpr"] == 0.0
    assert result["numbers"]["coverage_ratio"] == 1.0
    for key in ("coverage_ratio", "human_repair_hits", "self_heal_ratio", "fpr",
                "claimed_executed", "longest_run_hours"):
        assert key in result["numbers"]


def test_acceptance_pending_wall_clock_before_72h():
    beats = [b.to_dict() for b in (_beat(i) for i in range(48))]
    now = T0 + timedelta(hours=24)
    result = acc.evaluate(beats=beats, filings=_good_filings(1),
                          start=T0, end=now, now=now)
    assert result["status"] == "PENDING_WALL_CLOCK"
    assert "24.0h" in result["reason"]


def test_acceptance_pending_adjudication_when_missions_unclaimed():
    beats = [b.to_dict() for b in (_beat(i) for i in range(144))]
    filings = _good_filings(3) + [_filing(task_key="z", disposition="unclaimed_expired",
                                          claimed_by=None, session_id=None, status="queued")]
    result = acc.evaluate(beats=beats, filings=filings,
                          start=T0, end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72))
    assert result["status"] == "PENDING_ADJUDICATION"


def test_acceptance_fail_on_human_repair():
    beats = [b.to_dict() for b in (_beat(i) for i in range(144))]
    result = acc.evaluate(beats=beats, filings=_good_filings(3), log_text="手工修 state.json 后重启",
                          start=T0, end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72))
    assert result["checks"]["C3_no_human_repair"] is False
    assert result["status"] == "FAIL"


def test_acceptance_fail_on_too_few_executed_missions():
    beats = [b.to_dict() for b in (_beat(i) for i in range(144))]
    result = acc.evaluate(beats=beats, filings=_good_filings(2),
                          start=T0, end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72))
    assert result["checks"]["C5_claimed_executed"] is False