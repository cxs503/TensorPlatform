#!/usr/bin/env python3
"""coordinator_shadow v1 —— 72h 加速演练与负控测试（IN27-H1）。

演练不是墙钟，这一点必须在测试名与断言里都说清楚：``run_soak`` 是**合成平台状态**下的
时序压力演练（144 拍 / 72h 等效），用来验证「纪律在 72h 尺度上自洽」：

1. 144 拍零 error、零人工干预 → 覆盖 1.0、最长链 ≥ 71h；
2. 三个真故障（信用积压 / 告警流簇 / 收养停滞）各出 1 单，共 3 单；
3. 单拍尖峰被持久性门槛挡掉（不立单）；良性告警流簇被 LLM 否决（不立单）；
4. 同一剧本两次跑 → 逐字节确定性（可复现，不是随机）；
5. 无 LLM（rule 模式）时多出 1 张「良性越界」单 → 误报率 25% < 30%，仍过门槛——
   但高于有 LLM 的 0%，说明判读层的价值是被测出来的，不是自封的；
6. 负控 ``drill`` 全过：只读门/健康脉搏/尖峰/坏 JSON/幻觉器官/立单幂等/账本幂等。
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
from tools.coordinator_shadow import cli  # noqa: E402
from tools.coordinator_shadow import soak as sk  # noqa: E402

T0 = datetime(2026, 11, 6, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def soak_llm() -> sk.SoakResult:
    return sk.run_soak(hours=72, t0=T0, judge_mode="scripted")


@pytest.fixture(scope="module")
def soak_rule() -> sk.SoakResult:
    return sk.run_soak(hours=72, t0=T0, judge_mode="rule")


def test_soak_runs_144_beats_without_error(soak_llm):
    assert len(soak_llm.beats) == 144
    assert all(not b.get("error") for b in soak_llm.beats)


def test_soak_files_exactly_the_three_real_faults(soak_llm):
    metrics = sorted(f["metric"] for f in soak_llm.filings)
    assert metrics == ["credit_backlog", "error_total", "oldest_queued_minutes"]
    assert all(f["status"] == "succeeded" for f in soak_llm.filings)


def test_soak_single_beat_spike_not_filed(soak_llm):
    """60.0–60.5h 的单拍 credit_backlog 尖峰不得立单。"""
    spike_beats = [b for b in soak_llm.timeline if 60.0 <= b["t_hours"] < 60.5]
    assert spike_beats and spike_beats[0]["truth"] == "noise"
    assert spike_beats[0]["findings"] == 1      # 规则层看到了越界（credit_backlog 26）
    assert spike_beats[0]["filings"] == 0       # 但不立单（consecutive=1 < 2）


def test_soak_benign_warn_burst_vetoed_by_llm(soak_llm):
    """64.0–65.5h 良性告警流簇：规则层越界，LLM 否决 → 不立单。"""
    benign = [b for b in soak_llm.timeline if b["truth"] == "benign"]
    assert benign and all(b["findings"] >= 1 for b in benign)
    assert all(b["filings"] == 0 for b in benign)
    assert "warn_total" not in {f["metric"] for f in soak_llm.filings}


def test_soak_is_deterministic(soak_llm):
    again = sk.run_soak(hours=72, t0=T0, judge_mode="scripted")
    assert [f["task_key"] for f in again.filings] == [f["task_key"] for f in soak_llm.filings]
    assert again.timeline == soak_llm.timeline


def test_soak_acceptance_pass_and_fpr_zero(soak_llm):
    evaluation = acc.evaluate(
        beats=soak_llm.beats, filings=soak_llm.filings,
        criteria=acc.Criteria(window_hours=72), start=T0,
        end=T0 + timedelta(hours=72), now=T0 + timedelta(hours=72), extra={},
    )
    assert evaluation["numbers"]["coverage_ratio"] == 1.0
    assert evaluation["numbers"]["claimed_executed"] == 3
    assert evaluation["numbers"]["fpr"] == 0.0


def test_rule_mode_has_one_false_positive_proving_llm_value(soak_rule):
    iron = sk.iron_law_check(soak_rule)
    assert iron["filings"] == 4
    assert iron["false_positive_filings"] == 1
    iron_llm = sk.iron_law_check(sk.run_soak(hours=72, t0=T0, judge_mode="scripted"))
    assert iron_llm["filings"] == 3 and iron_llm["false_positive_filings"] == 0


def test_rule_mode_fpr_below_threshold(soak_rule):
    end = T0 + timedelta(hours=72)
    evaluation = acc.evaluate(beats=soak_rule.beats, filings=soak_rule.filings,
                              criteria=acc.Criteria(window_hours=72), start=T0, end=end, now=end)
    assert evaluation["numbers"]["fpr"] == pytest.approx(0.25, abs=1e-4)
    assert evaluation["checks"]["C6_fpr"] is True


def test_cli_soak_and_drill_exit_zero(tmp_path):
    out = tmp_path / "soak.json"
    assert cli.main(["soak", "--hours", "72", "--json-out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["iron_law"]["filings"] == 3
    assert payload["acceptance_simulated"]["status"] == "PASS"

    drill = tmp_path / "drill.json"
    assert cli.main(["drill", "--json-out", str(drill)]) == 0
    checks = json.loads(drill.read_text())["checks"]
    assert checks["all_pass"] is True
    assert checks["N1_read_only_guard"] is True
    assert checks["N7_ledger_idempotent"] is True


def test_soak_report_declares_it_is_not_wall_clock(tmp_path):
    out = tmp_path / "soak.json"
    cli.main(["soak", "--hours", "72", "--json-out", str(out)])
    payload = json.loads(out.read_text())
    extra = payload["acceptance_simulated"]["extra"]
    assert extra["source"] == "synthetic-72h"
    assert "不等于生产" in extra["note"]