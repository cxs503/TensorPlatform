"""tools/campaign_throughput.py 的单测（不需要数据库）。

覆盖：百分位与 SQL `percentile_cont` 同构性、plan 归属解析、成功率/回流率口径、
分母为 0 返回 NULL、markdown 渲染、Python↔SQL 对拍判定、CLI dry-run 不落盘。
所有数据集为**合成**样本（数值可手算），真实报表在 artifacts/ 里。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools import campaign_throughput as ct  # noqa: E402


# ─────────────────────────── 合成数据集（数值可手算） ───────────────────────────
def _m(mid, key, status, created, claimed=None, finished=None, requeued=0, sid=None):
    return {"id": mid, "task_key": key, "status": status, "created_at": created,
            "claimed_at": claimed, "finished_at": finished, "requeued": requeued,
            "session_id": sid or mid, "repo": "TensorPlatform", "claimed_by": "w"}


MISSIONS = [
    # p11：4 单，3 终态（2 成功 / 1 失败），1 在飞；失败单有 a2 重试 → 回流率 100%
    _m(1, "p11-N1-a1", "succeeded", "2026-11-06T10:00:00+00:00",
       "2026-11-06T10:00:10+00:00", "2026-11-06T10:01:00+00:00"),
    _m(2, "p11-N2-a1", "succeeded", "2026-11-06T10:00:00+00:00",
       "2026-11-06T10:00:20+00:00", "2026-11-06T10:02:00+00:00"),
    _m(3, "p11-N3-a1", "failed", "2026-11-06T10:00:00+00:00",
       "2026-11-06T10:00:30+00:00", "2026-11-06T10:03:00+00:00", requeued=1),
    _m(4, "p11-N3-a2", "running", "2026-11-06T10:05:00+00:00",
       "2026-11-06T10:05:40+00:00"),
    # p12：2 单，1 终态成功，1 在飞；无 failed → 回流率必须 NULL（不是 0）
    _m(5, "p12-N1-a1", "succeeded", "2026-11-06T11:00:00+00:00",
       "2026-11-06T11:00:05+00:00", "2026-11-06T11:10:00+00:00"),
    _m(6, "p12-N2-a1", "queued", "2026-11-06T11:00:00+00:00"),
    # 无 plan 归属（必须被排除，不能瞎猜）
    _m(7, "selfstart-tp-a-1", "failed", "2026-11-06T10:00:00+00:00",
       None, "2026-11-06T10:05:00+00:00"),
    _m(8, None, "failed", "2026-11-06T10:00:00+00:00", None, "2026-11-06T10:05:00+00:00"),
    # 有终态但缺领取证据 → 墙钟回落 + wall_fallback_n 计数
    _m(9, "p13-N1-a1", "succeeded", "2026-11-06T12:00:00+00:00",
       None, "2026-11-06T12:04:00+00:00"),
]


@pytest.fixture()
def report():
    return ct.aggregate(MISSIONS, [11, 12, 13], ct.load_contract(), source="fixture:test")


def _plan(report, pid):
    return next(p for p in report["plan_reports"] if p["plan_id"] == pid)


# ─────────────────────────── 1. 百分位与 SQL 同构 ───────────────────────────
def test_percentile_linear_matches_percentile_cont():
    """线性插值：p50/p95 与 PostgreSQL percentile_cont 的取值逐一相符（手算）。"""
    assert ct.percentile_linear([], 0.5) is None
    assert ct.percentile_linear([7.0], 0.95) == 7.0
    assert ct.percentile_linear([10.0, 20.0], 0.5) == 15.0          # h=0.5 → 中点
    assert ct.percentile_linear([10.0, 20.0], 0.95) == pytest.approx(19.5)  # h=0.95
    # n=5：h=(5−1)*0.5=2 → 第 3 个值；h=(5−1)*0.95=3.8 → 0.2*x[3]+0.8*x[4]
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ct.percentile_linear(xs, 0.50) == 3.0
    assert ct.percentile_linear(xs, 0.95) == pytest.approx(4.8)
    s = ct.summarize([1.0, None, 3.0])
    assert (s["n"], s["p50"], s["min"], s["max"]) == (2, 2.0, 1.0, 3.0)
    assert ct.summarize([])["p50"] is None


# ─────────────────────────── 2. plan 归属/规整 ───────────────────────────
def test_parse_task_key_and_normalize():
    assert ct.parse_task_key("p11-IN10-L1-a1") == {"plan_id": 11, "node": "IN10-L1", "attempt": 1}
    assert ct.parse_task_key("p17-IN16-K3-a2")["attempt"] == 2
    for bad in (None, "", "selfstart-tp-gen-2-1102-0225", "p11-IN10-L1", "propose-p17-w1-1"):
        assert ct.parse_task_key(bad) is None
    # 墙钟 = finish − claim；领取时延 = claim − created
    row = ct.normalize_mission(MISSIONS[0])
    assert row["claim_latency_s"] == 10.0 and row["wall_clock_s"] == 50.0
    assert row["cycle_time_s"] == 60.0 and row["wall_fallback"] is False
    # 缺领取证据 → 回落 created→finished 并打标
    fb = ct.normalize_mission(MISSIONS[8])
    assert fb["wall_fallback"] is True and fb["wall_clock_s"] == 240.0
    assert fb["claim_latency_s"] is None


# ─────────────────────────── 3. 口径：成功率/终态/在飞 ───────────────────────────
def test_aggregate_success_rate_uses_terminal_denominator(report):
    p11 = _plan(report, 11)
    assert (p11["n_missions"], p11["n_terminal"], p11["n_active"]) == (4, 3, 1)
    assert p11["success_rate"] == pytest.approx(2 / 3, abs=1e-6)   # 在飞单不进分母
    assert p11["status_counts"] == {"failed": 1, "running": 1, "succeeded": 2}
    # 领取时延 p50：4 单的 [10,20,30,40] → h=1.5 → 25.0
    assert p11["claim_latency_s"]["n"] == 4
    assert p11["claim_latency_s"]["p50"] == pytest.approx(25.0)
    assert report["diagnostics"]["n_missions_with_plan_linkage"] == 7
    assert report["diagnostics"]["claim_attribution_1to1"] is True   # 合成数据一 session 一单
    assert report["diagnostics"]["n_missions_unlinked"] == 2      # selfstart + task_key NULL
    assert 99 not in report["plans_with_data"]


# ─────────────────────────── 4. 回流率 & 分母为 0 ───────────────────────────
def test_failed_return_rate_and_null_denominator(report):
    p11 = _plan(report, 11)["retry"]
    assert (p11["n_failed"], p11["n_failed_returned"], p11["n_failed_return_rate"]
            if "n_failed_return_rate" in p11 else p11["failed_return_rate"]) == (1, 1, 1.0)
    assert p11["retry_share"] == pytest.approx(0.25)             # a2 1 单 / 4 单
    assert p11["custody_requeue_events"] == 1
    p12 = _plan(report, 12)["retry"]
    assert p12["n_failed"] == 0 and p12["failed_return_rate"] is None   # 不写 0%
    # 通用：ratio 分母 0/None → None
    assert ct.ratio(0, 0) is None and ct.ratio(3, None) is None and ct.ratio(3, 4) == 0.75


# ─────────────────────────── 5. 增长序列 / 吞吐 ───────────────────────────
def test_growth_series_and_throughput(report):
    p11 = _plan(report, 11)
    assert [pt["mission_id"] for pt in p11["growth"]["points"]] == [1, 2, 3]
    assert [pt["cum_terminal"] for pt in p11["growth"]["points"]] == [1, 2, 3]
    tp = p11["throughput"]
    assert tp["n_terminal"] == 3
    assert tp["span_seconds"] == 120.0                            # 10:01 → 10:03
    assert tp["per_hour"] == pytest.approx(90.0)                  # 3 / (120/3600)
    assert tp["day_buckets"] == [{"date": "2026-11-06", "terminal": 3}]
    # 只有 1 张终态单 → 跨度无法定义，吞吐为 NULL
    p13 = _plan(report, 13)
    assert p13["throughput"]["span_seconds"] is None and p13["throughput"]["per_hour"] is None
    assert p13["wall_fallback_n"] == 1


# ─────────────────────────── 6. markdown 渲染 ───────────────────────────
def test_render_markdown_sections_and_null_rendering(report):
    md = ct.render_markdown(report)
    for head in ("## 1. 主表", "## 2. 失败回流率", "## 3. 状态分布",
                 "## 4. 增长曲线数据源", "## 6. 诊断与局限", "## 7. 口径定义"):
        assert head in md
    assert "p11" in md and "p12" in md and "p13" in md
    # 无失败样本的 p12 回流率渲染为破折号，绝不渲染 0.0%（§2 表：plan|重试单|占比|failed|已回流|回流率）
    sec2 = md.split("## 2. 失败回流率", 1)[1].split("## 3.", 1)[0]
    p12_line = next(l for l in sec2.splitlines() if l.startswith("| p12 |"))
    cells = [c.strip() for c in p12_line.strip("|").split("|")]
    assert cells[5] == "—"
    assert "66.7%" in md                                          # p11 成功率 2/3


# ─────────────────────────── 7. 对拍判定 ───────────────────────────
def test_compare_crosscheck_pass_and_fail(report):
    sql_rows = [
        {"plan_id": 11, "n_missions": 4, "n_terminal": 3, "n_succeeded": 2, "n_failed": 1,
         "n_attempt_gt1": 1, "n_claim_evidence": 4, "claim_p50": 25.0, "claim_p95": 38.5,
         "wall_p50": 100.0, "wall_p95": 145.0, "n_failed_returned": 1,
         "custody_requeue_events": 1,
         "first_terminal": "2026-11-06T10:01:00+00:00",
         "last_terminal": "2026-11-06T10:03:00+00:00"},
        {"plan_id": 12, "n_missions": 2, "n_terminal": 1, "n_succeeded": 1, "n_failed": 0,
         "n_attempt_gt1": 0, "n_claim_evidence": 1, "claim_p50": 5.0, "claim_p95": 5.0,
         "wall_p50": 595.0, "wall_p95": 595.0, "n_failed_returned": 0,
         "custody_requeue_events": 0,
         "first_terminal": "2026-11-06T11:10:00+00:00",
         "last_terminal": "2026-11-06T11:10:00+00:00"},
        # p13：无领取证据 → 领取时延两侧均为 NULL（both_null），墙钟走回落
        {"plan_id": 13, "n_missions": 1, "n_terminal": 1, "n_succeeded": 1, "n_failed": 0,
         "n_attempt_gt1": 0, "n_claim_evidence": 0, "claim_p50": None, "claim_p95": None,
         "wall_p50": 240.0, "wall_p95": 240.0, "n_failed_returned": 0,
         "custody_requeue_events": 0,
         "first_terminal": "2026-11-06T12:04:00+00:00",
         "last_terminal": "2026-11-06T12:04:00+00:00"},
    ]
    rows = ct.compare_crosscheck(report, sql_rows)
    verdict = ct.crosscheck_verdict(rows)
    assert verdict["ok"], verdict
    assert verdict["failures"] == 0
    assert verdict["both_null"] >= 3        # p12 回流率 + p13 领取时延 P50/P95 双空
    # 注入一处漂移（wall p95 偏 1 s）必须被抓住
    bad = [dict(r) for r in sql_rows]
    bad[0]["wall_p95"] = 150.0
    assert not ct.crosscheck_verdict(ct.compare_crosscheck(report, bad))["ok"]


# ─────────────────────────── 8. CLI dry-run 不落盘 ───────────────────────────
def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    fx = tmp_path / "missions.json"
    fx.write_text(json.dumps({"missions": MISSIONS}), encoding="utf-8")
    out_json = tmp_path / "out" / "r.json"
    out_md = tmp_path / "out" / "r.md"
    rc = ct.main(["--source", "fixture", "--fixture", str(fx), "--plans", "11,12",
                  "--dry-run", "--out-json", str(out_json), "--out-md", str(out_md)])
    assert rc == 0
    assert not out_json.exists() and not out_md.exists()
    assert "战役波次吞吐报表" in capsys.readouterr().out


def test_cli_persist_and_contract_consistency(tmp_path):
    fx = tmp_path / "missions.json"
    fx.write_text(json.dumps({"missions": MISSIONS}), encoding="utf-8")
    out_json = tmp_path / "r.json"
    out_md = tmp_path / "r.md"
    assert ct.main(["--source", "fixture", "--fixture", str(fx),
                    "--out-json", str(out_json), "--out-md", str(out_md)]) == 0
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["schema_version"] == ct.SCHEMA_VERSION
    assert [p["plan_id"] for p in saved["plan_reports"]] == [11, 12, 13]
    assert out_md.read_text(encoding="utf-8").startswith("# 战役波次吞吐报表")
    # 兜底词表不得与契约漂移（单一真相）
    ctr = ct.load_contract()
    assert ctr["source"] != "fallback"
    assert tuple(ct.FALLBACK_TERMINAL) == ctr["terminal"]
    assert "succeeded" in ctr["terminal"] and "running" not in ctr["terminal"]

def test_shipped_fixture_is_usable(tmp_path, capsys):
    """仓内离线 fixture 可被 CLI 直接消费（CI/评审无 DB 也能复现报表形状）。"""
    fx = REPO / "tests/fixtures/campaign_throughput_sample.json"
    assert fx.exists()
    rc = ct.main(["--source", "fixture", "--fixture", str(fx), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "p11" in out and "p12" in out and "p13" in out
    assert "SYNTHETIC" in json.loads(fx.read_text(encoding="utf-8"))["_note"]
