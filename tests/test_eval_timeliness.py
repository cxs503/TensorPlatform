"""评价时效性契约（IN35-N07）验收测试：cohort 窗口无关 · 事件驱动增量评价 · 积压排空 SLO。

每类判据至少两个测试（正例 + 负控/边界），覆盖 J1–J6：

- J1 窗口无关性：同一 cohort 在 2/7/30/90 天视图下逐字段相等；且**查询时刻推移后**
  已冻结 cohort 的覆盖率不变（这是「任意查询时间点不变」的直接检验）。
- J2 事件驱动增量评价：单 mission 终态 → 一行事件 + 就地判定；重复事件幂等
  （不得把已判定记录降级回 pending）；失败单只入队不判定。
- J3 积压排空：注入 100 行 pending + 阈值 50 → 自动触发、排空 ≥90 行；
  证据不足的成果保持 pending（排空率天花板如实存在，不许写 rejected 凑数）。
- J4 向后兼容：``summarize()`` 输出 / ``stats_queries`` 查询名 / 评价 API 路由表
  与动手前冻结的基线 ``artifacts/IN35-N07/baseline_contract.json`` 一致。
- J5 测试数量：由 ``acceptance_timeliness.py`` 统计（本文件 ≥ 15 条）。
- J6 面板一致性：抽 10 个面板单元 ↔ 独立 SQL 重算，零不一致。

夹具是确定性合成的（``tools/evaluation_ops/fixtures.py``，固定种子），不依赖线上库。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from sqlalchemy import text

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import cohorts as ch  # noqa: E402
from tools.evaluation_ops import contract_baseline as cb  # noqa: E402
from tools.evaluation_ops import fixtures as fx  # noqa: E402
from tools.evaluation_ops import panel as pn  # noqa: E402
from tools.evaluation_ops import triggers as tg  # noqa: E402
from tools.evaluation_ops import views as vw  # noqa: E402

AS_OF = fx.DEFAULT_AS_OF


# ─────────────────────────── 夹具 ───────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "timeliness.db"
    info = fx.build(str(path))
    conn = fx.connect(str(path))
    tg.ensure_tables(conn)
    yield conn, info
    conn.close()


def _pending(conn) -> int:
    return int(conn.execute(
        text("SELECT count(*) FROM outcome_evaluations WHERE status='pending'")).scalar())


def _history(conn, mid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        text("SELECT status, decided_at FROM outcome_evaluations WHERE mission_id=:m "
             "ORDER BY decided_at"), {"m": mid}).mappings().all()]


# ─────────────────── J1 cohort 窗口/时刻无关性 ───────────────────

def test_fixture_covers_three_iso_weeks(db):
    conn, info = db
    keys = {ch.cohort_key(m["finished_at"]) for m in ch.fetch_missions(conn)}
    assert {"2026-W43", "2026-W44", "2026-W45"} <= keys
    assert info["counts"]["pending"] == 100


def test_window_invariance_same_cohort_all_views(db):
    """2/7/30/90 天视图下，同一 cohort 的逐字段数值必须完全一致。"""
    conn, _ = db
    cohorts = ch.compute_cohorts(ch.fetch_missions(conn), ch.fetch_records(conn), as_of=AS_OF)
    res = vw.window_independence(cohorts, as_of=AS_OF, days=(2, 7, 30, 90))
    assert len(res["diffs"]) == 0, res["diffs"][:3]
    assert res["max_abs_diff"] == 0
    # 负控：视图确实筛掉了 cohort（否则「相同」是空的）
    assert res["cohorts_per_view"]["2"] != res["cohorts_per_view"]["90"]


def test_frozen_cohort_coverage_invariant_across_query_times(db):
    """判据 J1 的直接检验：查询时刻推移，**已冻结** cohort 的覆盖率一分不变。"""
    conn, _ = db
    missions, records = ch.fetch_missions(conn), ch.fetch_records(conn)
    base = ch.compute_cohorts(missions, records, as_of=AS_OF)
    frozen = {k: v for k, v in base.items() if ch.is_frozen(v, as_of=AS_OF)}
    assert frozen, "夹具必须包含至少一个已冻结 cohort"
    for later in ("2026-11-07T15:00:00+00:00", "2026-11-09T00:00:00+00:00",
                  "2026-11-21T00:00:00+00:00"):
        again = ch.compute_cohorts(missions, records, as_of=later)
        for key, row in frozen.items():
            assert again[key]["coverage"] == row["coverage"]
            assert again[key]["missions_total"] == row["missions_total"]
            assert again[key]["missions_decided"] == row["missions_decided"]


def test_current_cohort_status_differs_from_frozen(db):
    """负控：未冻结 cohort 不算「冻结不变」的对象（当前周可继续涨）。"""
    conn, _ = db
    cohorts = ch.compute_cohorts(ch.fetch_missions(conn), ch.fetch_records(conn), as_of=AS_OF)
    assert ch.is_frozen(cohorts["2026-W45"], as_of=AS_OF) is False
    assert ch.is_frozen(cohorts["2026-W44"], as_of=AS_OF) is True


# ─────────────── J2 事件驱动增量评价 ───────────────

def test_terminal_event_decides_single_mission(db):
    conn, _ = db
    before = _pending(conn)
    ev = tg.on_mission_terminal(conn, 221, status="succeeded", commit=True)
    assert ev["outcome"] == "decided"
    assert ev["evaluation_status"] == "confirmed"
    assert ev["grade"] == "S1" and ev["e_level"] == "E1"
    assert ev["latency_seconds"] is not None and ev["latency_seconds"] < 60.0
    assert _pending(conn) == before  # 未留下新的 pending 行
    assert len(_history(conn, 221)) == 1  # 每个 mission 至多一行有效记录


def test_repeat_event_is_idempotent_and_never_downgrades(db):
    """重复事件（含状态改判 failed）不得把已判定记录降级回 pending。"""
    conn, _ = db
    tg.on_mission_terminal(conn, 222, status="succeeded", commit=True)
    first = _history(conn, 222)
    ev = tg.on_mission_terminal(conn, 222, status="failed", commit=True)
    assert ev["outcome"] == "decided"
    assert json.loads(ev["detail"])["reason"] == "already_decided"
    assert _history(conn, 222) == first


def test_failed_mission_is_queued_not_graded(db):
    """失败单：只入队 pending，机器扫描不替它写等级。"""
    conn, _ = db
    ev = tg.on_mission_terminal(conn, 123, status="failed", commit=True)
    assert ev["outcome"] == "queued"
    assert ev["evaluation_status"] == "pending"
    assert ev["grade"] is None and ev["e_level"] is None
    assert json.loads(ev["detail"])["reason"] == "failed_mission_no_deliverable"


def test_event_log_is_append_only_and_records_latency(db):
    conn, _ = db
    for mid in (221, 222, 223):
        tg.on_mission_terminal(conn, mid, status="succeeded", commit=True)
    evs = tg.events(conn)
    assert len(evs) == 3
    assert all(e["latency_seconds"] is not None for e in evs)
    slo = tg.inflight_slo(conn)
    assert slo["state"] == "ok" and slo["met"] is True
    assert slo["latency_seconds"]["p95"] < 60.0


def test_catch_up_picks_missed_terminal_events(db):
    """补偿扫描：未进过事件表的终态 mission 被拾取；重跑不再重复入队。"""
    conn, _ = db
    res = tg.catch_up(conn, limit=10, commit=True)
    assert res["scanned"] == 10 and res["events"] == 10
    second = tg.catch_up(conn, limit=10, commit=True)
    assert second["scanned"] == 10  # 另取 10 张（有界通道，不一次刷库）
    seen = {e["mission_id"] for e in tg.events(conn)}
    assert len(seen) == 20  # 不重复入队
    tg.catch_up(conn, limit=1000, commit=True)
    assert tg.catch_up(conn, limit=10, commit=True)["scanned"] == 0  # 扫干净后归零


# ─────────────── J3 积压排空 SLO ───────────────

def test_backlog_trigger_threshold_and_drain(db):
    conn, _ = db
    state = tg.should_drain(conn, threshold=tg.DEFAULT_BACKLOG_THRESHOLD)
    assert state["pending_rows"] == 100 and state["trigger"] is True
    run = tg.drain_backlog(conn, commit=True, batch=50)
    assert run["triggered"] is True and run["trigger_kind"] == "threshold"
    assert run["pending_before"] == 100
    assert run["drained"] >= 90, f"排空不足：{run['drained']}/100"
    assert run["drain_share"] >= tg.DRAIN_TARGET_SHARE
    assert run["met"] is True and run["elapsed_seconds"] < tg.DRAIN_WINDOW_HOURS * 3600
    assert run["pending_after"] == 8  # 8 张证据不足 → 如实保持 pending
    assert run["undecidable"] == 8


def test_drain_respects_below_threshold_noop(db):
    """负控：pending 未越阈值时不自动排空（避免无意义刷库）。"""
    conn, _ = db
    tg.drain_backlog(conn, commit=True, threshold=500)  # 阈值远高于积压
    run = tg.drain_backlog(conn, commit=True, threshold=500)
    assert run["triggered"] is False and run["trigger_kind"] == "noop"
    assert run["pending_before"] == run["pending_after"] == 100


def test_drain_never_writes_rejected_to_fake_progress(db):
    """排空率天花板必须来自证据不足，而不是把成果写成 rejected 凑数。"""
    conn, _ = db
    run = tg.drain_backlog(conn, commit=True, batch=100)
    assert run["rejected"] == 0
    rejected = int(conn.execute(
        text("SELECT count(*) FROM outcome_evaluations WHERE status='rejected'")).scalar())
    assert rejected == 0
    assert run["drained"] + run["undecidable"] >= run["pending_before"]


def test_drain_slo_states(db):
    """三态：无排空记录 → undefined；排空后 → ok；目标抬高后 → breach。"""
    conn, _ = db
    assert tg.drain_slo(conn)["state"] == "undefined"
    tg.drain_backlog(conn, commit=True)
    assert tg.drain_slo(conn)["state"] == "ok"
    strict = tg.drain_slo(conn, target_share=0.999)
    assert strict["state"] == "breach" and strict["met"] is False


# ─────────────── J4 向后兼容 ───────────────

def test_summarize_contract_matches_frozen_baseline():
    """``summarize()`` / ``stats_queries`` / 路由表 vs 动手前基线，零差异。"""
    baseline_path = REPO / "artifacts/IN35-N07/baseline_contract.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    diffs = cb.diff(baseline, cb.capture())
    assert diffs == [], f"评价 API 契约被破坏：{diffs}"


def test_evaluation_api_routes_unchanged():
    base = json.loads((REPO / "artifacts/IN35-N07/baseline_contract.json").read_text())
    routes = cb.api_routes()
    assert routes == base["api_routes"]
    assert any(r["path"] == "/tensor/outcome-evaluations/stats" for r in routes)


# ─────────────── J6 面板与库一致 ───────────────

def test_panel_trend_and_backlog_consistent_with_db(db):
    conn, info = db
    tg.drain_backlog(conn, commit=True)
    for mid in (221, 222, 223, 224):
        tg.on_mission_terminal(conn, mid, status="succeeded", commit=True)
    panel = pn.build_panel(conn, as_of=AS_OF)
    assert panel["coverage_trend"], "面板必须有 cohort 趋势"
    chk = pn.panel_consistency(conn, panel, sample=10)
    assert chk["mismatch_count"] == 0, chk["mismatches"]
    assert chk["cells_checked"] >= 10 and chk["ok"] is True
    # 积压面板数 = 库里真实 pending 行数
    assert panel["backlog"]["pending_rows"] == _pending(conn)
    # 排空速率与最近一次排空的 drained/elapsed 自洽
    last = panel["backlog"]["last_run"]
    if last and last["elapsed_seconds"]:
        assert panel["backlog"]["drain_rate_rows_per_hour"] == pytest.approx(
            last["drained"] / (last["elapsed_seconds"] / 3600.0))


def test_panel_latency_slo_tracks_events(db):
    conn, _ = db
    panel = pn.build_panel(conn, as_of=AS_OF)
    assert panel["inflight_latency"]["slo"] == "undefined"  # 无事件 → 不折成 ok
    tg.catch_up(conn, limit=5, commit=True)
    panel = pn.build_panel(conn, as_of=AS_OF)
    assert panel["inflight_latency"]["events"] == 5
    assert panel["inflight_latency"]["slo"] == "ok"
    assert sum(b["n"] for b in panel["inflight_latency"]["histogram"]) == 5


def test_panel_markdown_uses_same_numbers(db):
    conn, _ = db
    tg.catch_up(conn, limit=3, commit=True)
    panel = pn.build_panel(conn, as_of=AS_OF)
    md = pn.render_markdown(panel)
    for row in panel["coverage_trend"]:
        assert row["cohort"] in md
    assert "in-flight" in md