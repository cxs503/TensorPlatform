"""FP15-BUDGET-ACCOUNTING ①：预算记账推导（合成夹具，零领域语义）。

为什么这些用例能红
------------------
修复前 ``budget_used`` 恒为 ``{}``：终局裁决（``budget_exhausted`` /
``no_progress``）的"花了多少"外部不可复核。这里的夹具全部是**合成 task 事实**
（没有 m 号、没有域词条），断言的是契约本身：

1. 四键 ``{tool_calls, tokens, wall_seconds, source}`` 必须存在；
2. ``tool_calls`` 必须等于 ``result.tool_calls``（可与外部 SQL 对拍的口径）；
3. 空 result / 缺字段 / 脏类型必须**降级不崩**（记账不能成为终态单点故障）；
4. 帽曲线聚合的分母口径（``rate`` = succ/n，``near_cap_rate`` 只对有记账的
   行取率）与 ``n>=100`` 门槛必须写在数据里，不是口头约定。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (REPO, os.path.join(REPO, "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from services.budget_accounting import (  # noqa: E402
    BUDGET_USED_KEYS,
    SOURCE_AGENT_DECLARED,
    SOURCE_HEARTBEAT_PROGRESS,
    SOURCE_RESULT_FACT,
    SOURCE_RUNNER_DETAIL,
    SOURCE_SERVER_CLOCK,
    SOURCE_UNAVAILABLE,
    budget_used_for_row,
    derive_budget_used,
    usage_is_recorded,
)

import importlib.util  # noqa: E402


def _load_script(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap_curve = _load_script("budget_cap_curve", "scripts/budget_cap_curve.py")


def _mission(status: str, cap: int | None, tool_calls: int | None, *,
             budget_used: dict | None = None) -> dict:
    """一条合成终态任务（字段名与 missions API 投影一致）。"""
    result: dict = {"headline": "synthetic", "task_key": "synth.budget.001"}
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    return {
        "id": 1,
        "status": status,
        "budget": {"max_tool_calls": cap} if cap is not None else {},
        "budget_used": budget_used if budget_used is not None else {},
        "result": result,
        "finished_at": "2026-01-01T00:00:00+00:00",
    }


# ── 1. 四键契约 + 与 result.tool_calls 对拍 ──────────────────────────────────

def test_four_keys_present_and_match_result_tool_calls():
    result = {"tool_calls": 73, "tokens": 1200, "wall_seconds": 900,
              "headline": "synthetic"}
    record = derive_budget_used(result=result)
    for key in BUDGET_USED_KEYS:
        assert key in record, f"missing contract key {key}"
    assert record["tool_calls"] == result["tool_calls"] == 73
    assert record["tokens"] == 1200
    assert record["wall_seconds"] == 900
    assert record["sources"]["tool_calls"] == SOURCE_RESULT_FACT
    assert "tool_calls=result_fact" in record["source"]


def test_legacy_mirror_keys_kept_for_resume_and_ledger():
    record = derive_budget_used(result={"tool_calls": 12, "tokens": 7,
                                        "wall_seconds": 30})
    # suspend/resume 与 credit_ledger 读的是历史键名，必须同步镜像
    assert record["tool_calls_used"] == record["tool_calls"] == 12
    assert record["tokens_used"] == record["tokens"] == 7
    assert record["wall_seconds_used"] == record["wall_seconds"] == 30


def test_row_helper_matches_result_tool_calls():
    row = {"result": {"tool_calls": 44}, "created_at": "2026-01-01T00:00:00+00:00"}
    record = budget_used_for_row(row, now=None)
    assert record["tool_calls"] == row["result"]["tool_calls"]
    assert usage_is_recorded(record)


# ── 2. 出处优先级 ────────────────────────────────────────────────────────────

def test_declared_usage_wins_and_is_labelled():
    record = derive_budget_used(result={"tool_calls": 5},
                                declared={"tool_calls": 9, "tokens": 42})
    assert record["tool_calls"] == 9
    assert record["sources"]["tool_calls"] == SOURCE_AGENT_DECLARED
    assert record["sources"]["tokens"] == SOURCE_AGENT_DECLARED


def test_heartbeat_progress_used_when_result_has_no_counts():
    result = {"progress": {"tool_calls": 31, "wall_seconds": 640}}
    record = derive_budget_used(result=result)
    assert record["tool_calls"] == 31
    assert record["sources"]["tool_calls"] == SOURCE_HEARTBEAT_PROGRESS
    assert record["wall_seconds"] == 640
    assert record["sources"]["wall_seconds"] == SOURCE_HEARTBEAT_PROGRESS


def test_runner_detail_string_is_parsed():
    record = derive_budget_used(result={"detail": "tool_calls=88"})
    assert record["tool_calls"] == 88
    assert record["sources"]["tool_calls"] == SOURCE_RUNNER_DETAIL


def test_server_clock_is_last_resort_for_wall_seconds():
    row = {"result": {}, "created_at": "2026-01-01T00:00:00+00:00"}
    from datetime import datetime, timezone
    record = budget_used_for_row(
        row, now=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert record["wall_seconds"] == 3600
    assert record["sources"]["wall_seconds"] == SOURCE_SERVER_CLOCK
    assert record["sources"]["tool_calls"] == SOURCE_UNAVAILABLE


# ── 3. 空/缺/脏 → 降级不崩 ───────────────────────────────────────────────────

@pytest.mark.parametrize("result", [
    None,
    {},
    {"tool_calls": None},
    {"detail": None},
    {"progress": "not-a-mapping"},
    [],
    "raw-string-result",
    {"tool_calls": -3},
    {"tool_calls": "abc"},
    {"tool_calls": True},
    {"tool_calls": float("nan")},
    {"tool_calls": 10 ** 9},
])
def test_degrade_without_crashing(result):
    record = derive_budget_used(result=result)
    for key in BUDGET_USED_KEYS:
        assert key in record
    assert isinstance(record["tool_calls"], int) and record["tool_calls"] >= 0
    assert isinstance(record["tokens"], int) and record["tokens"] >= 0
    assert isinstance(record["wall_seconds"], int) and record["wall_seconds"] >= 0
    assert isinstance(record["source"], str) and record["source"]


def test_empty_result_is_explicitly_unavailable_not_fake_zero():
    record = derive_budget_used(result={})
    assert record["tool_calls"] == 0
    assert record["sources"]["tool_calls"] == SOURCE_UNAVAILABLE
    assert record["source"] == (
        "tool_calls=unavailable;tokens=unavailable;wall_seconds=unavailable")


def test_jsonb_string_payload_is_accepted():
    record = derive_budget_used(result=json.dumps({"tool_calls": 17}))
    assert record["tool_calls"] == 17


def test_usage_is_recorded_rejects_legacy_blob():
    assert not usage_is_recorded({})
    assert not usage_is_recorded({"tool_calls_used": 12})
    assert usage_is_recorded({"tool_calls": 1, "tokens": 0,
                              "wall_seconds": 5, "source": "x"})


# ── 4. 帽曲线聚合：分母口径 + n 门槛 + 一致性自检 ────────────────────────────

def test_cap_buckets_and_rates():
    rows = [
        _mission("succeeded", 60, 59),
        _mission("succeeded", 60, 58),
        _mission("failed", 60, 60),
        _mission("failed", 60, 10),
        _mission("succeeded", 80, 75),
    ]
    agg = cap_curve.aggregate_missions(rows)
    buckets = {b["cap"]: b for b in agg["buckets"]}
    assert buckets[60]["n"] == 4 and buckets[60]["succ"] == 2
    assert buckets[60]["rate"] == 50.0
    # near_cap = tool_calls >= cap-5 → 59/58/60 三条
    assert buckets[60]["near_cap_rate"] == 75.0
    assert buckets[60]["at_cap_rate"] == 25.0
    assert buckets[60]["tool_calls_p50_succ"] == pytest.approx(58.5)
    assert buckets[60]["below_floor"] is True and buckets[80]["below_floor"] is True
    assert agg["totals"]["n"] == 5 and agg["totals"]["succ"] == 3


def test_near_cap_rate_is_none_without_usage_not_zero():
    agg = cap_curve.aggregate_missions([_mission("failed", 60, None)] * 3)
    bucket = agg["buckets"][0]
    assert bucket["usage_known"] == 0
    assert bucket["near_cap_rate"] is None      # 没数据 ≠ 0%
    assert bucket["rate"] == 0.0


def test_budget_used_record_is_preferred_source():
    recorded = {"tool_calls": 5, "tokens": 0, "wall_seconds": 60,
                "source": "tool_calls=result_fact;tokens=unavailable;"
                          "wall_seconds=server_clock_upper_bound"}
    mission = _mission("succeeded", 60, 59, budget_used=recorded)
    used, source = cap_curve.usage_of(mission)
    assert used == 5
    assert source.startswith("tool_calls=result_fact")


def test_build_report_consistency_check_is_zero_delta():
    rows = [_mission("succeeded", 60, 30), _mission("failed", 60, 60),
            _mission("succeeded", 80, 20)]
    report = cap_curve.build_report(rows, {"succeeded": 2, "failed": 1},
                                    hours=24.0)
    checks = report["checks"]
    assert checks["population_n"] == checks["api_total"] == 3
    assert checks["population_delta_vs_api"] == 0
    assert checks["all_zero_delta"] is True
    assert {"since", "until", "hours"} <= set(report["window"])
    for bucket in report["population"]["buckets"]:
        assert {"cap", "n", "succ", "fail", "rate", "near_cap_rate"} <= set(bucket)
    assert report["schema"] == "fp15.budget_cap_curve.v1"


def test_build_report_flags_truncated_population():
    rows = [_mission("succeeded", 60, 30)]
    report = cap_curve.build_report(rows, {"succeeded": 7, "failed": 3},
                                    hours=24.0)
    assert report["checks"]["population_delta_vs_api"] == -9
    assert report["checks"]["all_zero_delta"] is False