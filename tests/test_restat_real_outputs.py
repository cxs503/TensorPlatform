#!/usr/bin/env python3
"""AUDIT-RESTAT 口径一致性测试：校验 runs/restat_20261111/ 产物内部自洽。

这些断言不依赖数据库连通性（沙箱 psycopg2 是 stub，DB 又是活库、随时新增任务），
只校验「同一份快照内的数字必须对得上」——口径漂移会立刻红。

跳过条件：产物目录不存在（未跑统计脚本）。
"""
from __future__ import annotations

import csv
import json
import os

import pytest

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "runs", "restat_20261111")

pytestmark = pytest.mark.skipif(not os.path.isdir(D), reason="restat artifacts absent")


@pytest.fixture(scope="module")
def summary():
    with open(os.path.join(D, "restat_summary.json")) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def pending():
    with open(os.path.join(D, "pending_acceptance_L0L1.json")) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def rows():
    with open(os.path.join(D, "missions_classified.csv")) as fh:
        return list(csv.DictReader(fh))


def test_status_split_sums_to_total(summary):
    assert sum(summary["status_split"].values()) == summary["missions_total"]


def test_tierB_levels_sum_to_succeeded(summary, rows):
    n = sum(summary["tierB_level_counts"].values())
    assert n == summary["succeeded_total"]
    assert sum(1 for r in rows if r["status"] == "succeeded") == summary["succeeded_total"]


def test_csv_rowcount_matches_total(summary, rows):
    assert len(rows) == summary["missions_total"]


def test_loose_dominates_strict_above_l1(summary):
    """L2+ 上宽松口径必须逐档 >= 严格口径（合并证据是实现的子集）。

    例外是 L1：严格口径把「实现未入库」回流到 L1，所以严格 L1 > 宽松 L1，
    这个例外本身也要被锁定（见 test_l1_absorbs_unmerged_implementations）。
    """
    for lvl in ("2", "3", "4", "5"):
        strict_n = summary["tierB_level_counts"].get(lvl, 0)
        loose_n = summary["tierB_level_counts_loose"].get(lvl, 0)
        assert loose_n >= strict_n, f"level {lvl} violates monotonicity"
    assert summary["tierB_level_counts"].get("0", 0) == summary["tierB_level_counts_loose"].get("0", 0)


def test_l1_absorbs_unmerged_implementations(summary):
    """严格 L1 = 纯验证脚本 + 实现未入库；两口径差必须等于回流单数。"""
    why = summary["tierB_why_counts"]
    unmerged = sum(v for k, v in why.items() if k.startswith("impl_without_merge_evidence"))
    assert summary["tierB_level_counts"]["1"] - why.get("tests_or_verify_only", 0) == unmerged


def test_verdict_crosstab_row_sums(summary):
    claim = summary["tierA_claimed_level_counts"]
    agg = {}
    for e in summary["tierA_crosstab"]:
        agg[str(e["claimed"])] = agg.get(str(e["claimed"]), 0) + e["n"]
    for k, v in claim.items():
        assert agg.get(k, 0) == v, f"claim L{k} crosstab mismatch"


def test_verdict_gate_totals(summary):
    """184 条 scope 判定：gate 分布合计 == claim 分布合计。"""
    assert sum(summary["tierA_claimed_level_counts"].values()) == \
           sum(summary["tierA_gate_level_counts"].values())


def test_pending_list_is_exactly_l0_l1(pending, summary):
    assert pending["count"] == len(pending["items"])
    assert pending["count"] == (summary["tierA_claimed_level_counts"].get("0", 0)
                                + summary["tierA_claimed_level_counts"].get("1", 0))
    for it in pending["items"]:
        assert it["claimed_level"] in (0, 1)
        assert it["verdict_id"] > 0


def test_pending_list_has_evidence_pointer(pending):
    """每条待完成项必须有 gap 或 evidence 摘录作证据指针（零手抄纪律）。"""
    missing = [it["verdict_id"] for it in pending["items"]
               if not it["gap"] and not it.get("evidence_excerpt")]
    assert missing == []


def test_proxy_pending_csv_consistent(rows):
    p = os.path.join(D, "pending_proxy_L0L1.csv")
    if not os.path.exists(p):
        pytest.skip("pending_proxy_L0L1.csv absent")
    with open(p) as fh:
        prow = list(csv.DictReader(fh))
    expect = [r for r in rows if r["status"] == "succeeded" and r["level"] in ("0", "1")]
    assert len(prow) == len(expect)


def test_figures_exist():
    figdir = os.path.join(D, "figures")
    if not os.path.isdir(figdir):
        pytest.skip("figures absent")
    figs = [f for f in os.listdir(figdir) if f.endswith(".png")]
    assert len(figs) >= 4
    for f in figs:
        assert os.path.getsize(os.path.join(figdir, f)) > 5000


def test_sql_registry_present():
    txt = open(os.path.join(D, "sql_registry.sql")).read()
    for key in ("tensor_dev_missions", "tensor_maturity_verdicts", "tensor_reports"):
        assert key in txt