"""pytest suite for the SR inheritance-readiness instrument (IN27-H7).

The suite is database-free: every test drives the metric code through
``FakeRunner`` so the divergence arithmetic, the look-ahead maturity rule, the
deterministic sampling, the red-line split and the chronicle s8 payload are all
pinned without a live Postgres.  A live-database smoke test runs only when
``TP_SR_LIVE=1`` is exported.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.autonomy import sr_readiness as SR  # noqa: E402
from tools.autonomy import sr_dashboard as D  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


class FakeRunner:
    """Rows keyed by a substring of the SQL; unmatched queries return the default."""

    def __init__(self, rows_by_token=None, default=None):
        self.rows_by_token = rows_by_token or {}
        self.default = default
        self.calls: list[tuple[str, dict | None]] = []
        self.writes: list[tuple[str, dict | None]] = []

    def fetchall(self, sql, params=None):
        self.calls.append((sql, params))
        for token, rows in self.rows_by_token.items():
            if token in sql:
                return rows(params) if callable(rows) else rows
        if self.default is not None:
            return self.default
        return [(0,)] if "count(" in sql else [(None,)]

    def execute(self, sql, params=None):
        self.writes.append((sql, params))
        return True


# ---------------------------------------------------------------------------
# SR1: parsing, probing, classification, arithmetic
# ---------------------------------------------------------------------------


def test_shadow_items_flattens_would_do_across_rounds():
    ts = datetime(2026, 11, 7, 9, 55, tzinfo=timezone.utc)
    rows = [
        (
            1,
            ts,
            {"urgent": True, "would_do": [
                {"action": "escalate", "target": "s5_outcomes", "reason": "coverage low"},
                {"action": "monitor", "target": "s2_campaign", "reason": "watch"},
            ]},
        ),
        (2, ts + timedelta(minutes=30), {"would_do": [{"action": "file_mission", "target": "queued_stuck"}]}),
        (3, ts, {"raw": "not json", "would_do": None}),
    ]
    items = SR.shadow_items(rows)
    assert [i.action for i in items] == ["escalate", "monitor", "file_mission"]
    assert items[0].round_id == 1 and items[2].target == "queued_stuck"
    assert all(isinstance(i.ts, datetime) for i in items)


def test_shadow_items_tolerates_json_string_payload():
    ts = datetime(2026, 11, 7, 9, 55, tzinfo=timezone.utc)
    items = SR.shadow_items([(7, ts, json.dumps({"would_do": [{"action": "ESCALATE", "target": "x_organ"}]}))])
    assert items[0].action == "escalate"


def test_normalise_action_and_target_guards():
    assert SR.normalise_action("Escalate") == "escalate"
    assert SR.normalise_action("file") == "file_mission"
    assert SR.normalise_action("teleport") is None
    assert SR.normalise_target("s5_outcomes") == "s5_outcomes"
    assert SR.normalise_target("x' OR 1=1 --") == "xOR11--"  # quotes/spaces stripped, safe chars kept
    assert SR.normalise_target("a b") is None  # too short after sanitising


def test_collect_dispositions_batches_the_feed_and_counts_hits_per_organ():
    since, until = NOW - timedelta(days=1), NOW
    feed = [
        (since + timedelta(hours=1), "fix s5_outcomes coverage"),
        (since + timedelta(hours=2), "s5_outcomes again"),
        (since + timedelta(hours=3), "unrelated organ"),
    ]
    runner = FakeRunner(
        {
            "FROM tensor_dev_missions": feed,
            "FROM campaign_events": [],
            "FROM alerts": [(since + timedelta(hours=2), "ALERT: S5_OUTCOMES low"),],
            "tensor_agent_session_events": [],
        }
    )
    dispositions = SR.collect_dispositions(runner, since, until)
    assert dispositions["status"] == "REAL"
    assert len(dispositions["rows"]) == 4
    evidence = SR.organ_evidence(dispositions, "s5_outcomes", since, until)
    assert evidence["status"] == "REAL"
    assert evidence["hits"] == 3  # case-insensitive match across sources
    assert evidence["sources"]["mission_goal"] == 2
    assert evidence["sources"]["alert"] == 1
    # one statement per source, not one per shadow item
    assert len(runner.calls) == 5


def test_organ_evidence_respects_the_horizon_and_guards_targets():
    since = NOW - timedelta(hours=10)
    rows = [("mission_goal", since + timedelta(hours=5), "organ_late detected")]
    dispositions = {"status": "REAL", "rows": rows}
    assert SR.organ_evidence(dispositions, "organ_late", since, since + timedelta(hours=2))["hits"] == 0
    assert SR.organ_evidence(dispositions, "organ_late", since, since + timedelta(hours=6))["hits"] == 1
    assert SR.organ_evidence(dispositions, "ab", since, NOW)["status"] == "unobservable"
    assert SR.organ_evidence({"status": "unobservable", "reason": "all-probes-failed"}, "organ_x", since, NOW)[
        "status"
    ] == "unobservable"


def test_classify_item_expects_work_for_action_and_monitor_inverse():
    item = SR.ShadowItem(1, NOW - timedelta(hours=30), "escalate", "organ_x", "")
    evidence = {"status": "REAL", "hits": 0, "sources": {"mission_goal": 0}}
    assert SR.classify_item(item, evidence, NOW, 24.0, 1.0)["state"] == "diverged"

    hit = {"status": "REAL", "hits": 1, "sources": {"mission_goal": 1}}
    assert SR.classify_item(item, hit, NOW, 24.0, 1.0)["state"] == "matched"

    watcher = SR.ShadowItem(1, NOW - timedelta(hours=30), "monitor", "organ_x", "")
    assert SR.classify_item(watcher, evidence, NOW, 24.0, 1.0)["state"] == "matched"
    assert SR.classify_item(watcher, hit, NOW, 24.0, 1.0)["state"] == "diverged"


def test_classify_item_pending_is_never_a_divergence():
    fresh = SR.ShadowItem(1, NOW - timedelta(minutes=10), "escalate", "organ_x", "")
    evidence = {"status": "REAL", "hits": 0, "sources": {}}
    entry = SR.classify_item(fresh, evidence, NOW, 24.0, 1.0)
    assert entry["state"] == "pending"
    assert entry["elapsed_hours"] < 1.0


def test_classify_item_early_evidence_decides_before_window_elapses():
    item = SR.ShadowItem(1, NOW - timedelta(hours=2), "file_mission", "organ_x", "")
    hit = {"status": "REAL", "hits": 2, "sources": {"mission_goal": 2}}
    entry = SR.classify_item(item, hit, NOW, 24.0, 1.0)
    assert entry["state"] == "matched" and entry["basis"] == "early-evidence"


def test_classify_item_unobservable_for_unknown_action_or_dead_probe():
    item = SR.ShadowItem(1, NOW - timedelta(hours=30), "teleport", "organ_x", "")
    assert SR.classify_item(item, {"status": "REAL", "hits": 0}, NOW, 24.0, 1.0)["state"] == "unobservable"
    dead = {"status": "unobservable", "reason": "target-too-short", "hits": 0}
    good = SR.ShadowItem(1, NOW - timedelta(hours=30), "escalate", "organ_x", "")
    assert SR.classify_item(good, dead, NOW, 24.0, 1.0)["state"] == "unobservable"


def test_measure_sr1_rate_and_counts():
    shadow_row = (
        1,
        NOW - timedelta(hours=30),
        {
            "would_do": [
                {"action": "escalate", "target": "organ_a"},
                {"action": "file_mission", "target": "organ_b"},
                {"action": "monitor", "target": "organ_c"},
            ]
        },
    )
    feed = [(NOW - timedelta(hours=28), "mission about organ_a")]
    runner = FakeRunner(
        {
            "FROM coordinator_shadow": [shadow_row],
            "FROM tensor_dev_missions": feed,
            "FROM campaign_events": [],
            "FROM alerts": [],
            "tensor_agent_session_events": [],
        }
    )
    sr1 = SR.measure_sr1(runner, NOW, window_days=30, lookahead_hours=24, provisional_hours=1)
    # organ_a: escalate + evidence -> matched; organ_b: file_mission + no evidence -> diverged;
    # organ_c: monitor + no evidence -> matched
    assert sr1["counts"] == {"matched": 2, "diverged": 1, "pending": 0, "unobservable": 0}
    assert sr1["value"] == pytest.approx(0.3333, abs=1e-4)
    assert sr1["status"] == "REAL"
    assert sr1["meets_target"] is False
    assert sr1["rounds"] == 1 and sr1["items"] == 3
    assert sr1["maturity"] == "settled"
    assert sr1["by_action"]["escalate"]["matched"] == 1
    # horizon table: 1 h and 24 h both settled here, 6 h too
    settled = [r["horizon_hours"] for r in sr1["horizon_reads"] if r["settled"]]
    assert settled == [0.5, 1.0, 6.0, 24.0]  # the shortest documented horizon wins the baseline
    assert sr1["baseline_horizon_hours"] == 0.5
    # at the 1 h horizon organ_a's disposition lands *after* the horizon (28 h > 30 h - 1 h),
    # so the short horizon reads 2/3 while the 24 h horizon reads 1/3 -- that is the point
    # of publishing the whole horizon table instead of a single number.
    assert sr1["baseline_value"] == pytest.approx(0.6667, abs=1e-4)


def test_fresh_round_does_not_block_the_baseline():
    """A live 30-min cadence always leaves the newest round pending: the baseline
    must still land on the shortest horizon that has a decided item."""
    old = (1, NOW - timedelta(hours=2), {"would_do": [{"action": "monitor", "target": "organ_a"}]})
    fresh = (2, NOW - timedelta(minutes=3), {"would_do": [{"action": "escalate", "target": "organ_b"}]})
    runner = FakeRunner(
        {
            "FROM coordinator_shadow": [old, fresh],
            "FROM tensor_dev_missions": [],
            "FROM campaign_events": [],
            "FROM alerts": [],
            "tensor_agent_session_events": [],
        }
    )
    sr1 = SR.measure_sr1(runner, NOW, window_days=30, lookahead_hours=24, provisional_hours=1)
    assert sr1["items"] == 2
    assert sr1["baseline_horizon_hours"] == 0.5
    assert sr1["baseline_value"] == 0.0  # the settled item is 'monitor with no disposition' = matched
    assert sr1["maturity"] == "provisional"  # main 24 h reading still waits, by design


def test_horizon_reads_expose_the_first_month_baseline():
    """A 1 h horizon matures long before the 24 h one: the baseline lands early."""
    shadow_row = (
        1,
        NOW - timedelta(hours=2),
        {"would_do": [{"action": "file_mission", "target": "organ_a"}]},
    )
    runner = FakeRunner(
        {
            "FROM coordinator_shadow": [shadow_row],
            "FROM tensor_dev_missions": [],
            "FROM campaign_events": [],
            "FROM alerts": [],
            "tensor_agent_session_events": [],
        }
    )
    sr1 = SR.measure_sr1(runner, NOW, window_days=30, lookahead_hours=24, provisional_hours=1)
    assert sr1["value"] is None and sr1["maturity"] == "provisional"
    assert sr1["baseline_horizon_hours"] == 0.5  # one shadow cycle (30 min) is already settled
    assert sr1["baseline_value"] == 1.0  # file_mission with no disposition -> diverged
    horizons = {r["horizon_hours"]: r for r in sr1["horizon_reads"]}
    assert horizons[0.5]["settled"] is True
    assert horizons[1.0]["settled"] is True
    assert horizons[24.0]["settled"] is False


def test_measure_sr1_withheld_when_no_item_is_matured():
    shadow_row = (
        3,
        NOW - timedelta(minutes=5),
        {"would_do": [{"action": "escalate", "target": "organ_a"}]},
    )
    runner = FakeRunner({"FROM coordinator_shadow": [shadow_row]})
    sr1 = SR.measure_sr1(runner, NOW, window_days=30, lookahead_hours=24, provisional_hours=1)
    assert sr1["value"] is None
    assert sr1["status"] == "WITHHELD"
    assert sr1["counts"]["pending"] == 1
    assert sr1["maturity"] == "provisional"
    assert sr1["meets_target"] is None


def test_measure_sr1_withheld_when_source_raises():
    class Boom(FakeRunner):
        def fetchall(self, sql, params=None):
            raise RuntimeError("db down")

    sr1 = SR.measure_sr1(Boom(), NOW)
    assert sr1["status"] == "WITHHELD" and sr1["value"] is None


# ---------------------------------------------------------------------------
# SR2 / SR4: hand-typed channel split
# ---------------------------------------------------------------------------


def _decision(ts, kind="dev_acceptance", decision="approved", note="owner batch"):
    return (ts, kind, decision, note)


def test_manual_decisions_reads_non_daemon_rows():
    rows = [_decision(NOW - timedelta(days=1)), _decision(NOW - timedelta(days=2), kind="stage_promotion")]
    runner = FakeRunner({"FROM tensor_agent_session_events": rows})
    events = SR.manual_decisions(runner, NOW - timedelta(days=7), NOW)
    assert len(events) == 2
    assert events[0]["kind"] == "dev_acceptance"
    sql = runner.calls[0][0]
    assert "[auto-approve]" in sql  # the daemon marker is part of the statement


def test_measure_sr2_streak_and_clean_days():
    rows = [_decision(NOW - timedelta(days=5, hours=2)), _decision(NOW - timedelta(days=6))]
    runner = FakeRunner(
        {
            "SELECT max(created_at)": [(NOW - timedelta(days=5, hours=2),)],
            "FROM tensor_agent_session_events": rows,
        }
    )
    sr2 = SR.measure_sr2(runner, NOW, window_days=30)
    assert sr2["value"] == pytest.approx(5.08, abs=0.02)
    assert sr2["status"] == "REAL"
    assert sr2["meets_target"] is False  # below the 7 d target
    assert sr2["anchor"] == "last-hand-typed-decision"
    assert sr2["hand_typed_decisions_in_window"] == 2
    assert sr2["clean_days_in_window"] == 28


def test_measure_sr2_falls_back_to_platform_epoch():
    runner = FakeRunner(
        {
            "SELECT max(created_at)": [(None,)],
            "SELECT min(created_at)": [(NOW - timedelta(days=9),)],
            "FROM tensor_agent_session_events": [],
        }
    )
    sr2 = SR.measure_sr2(runner, NOW)
    assert sr2["value"] == pytest.approx(9.0, abs=0.01)
    assert sr2["anchor"] == "platform-epoch"
    assert sr2["meets_target"] is True


def test_classify_redline_splits_kinds_and_lifecycle_actions():
    assert SR.classify_redline("stage_promotion") is True
    assert SR.classify_redline("stop_condition") is True
    assert SR.classify_redline("dev_acceptance") is False
    assert SR.classify_redline("", lifecycle_action="write_off") is True
    assert SR.classify_redline("", lifecycle_action="pr_gate") is False


def test_measure_sr4_normalises_per_week_and_excludes_red_lines():
    rows = [
        _decision(NOW - timedelta(days=1)),
        _decision(NOW - timedelta(days=2)),
        _decision(NOW - timedelta(days=3), kind="stage_promotion"),
    ]
    runner = FakeRunner(
        {
            "FROM tensor_agent_session_events": rows,
            "FROM mission_lifecycle_log": [(NOW - timedelta(days=1), "write_off", "eco_admin")],
        }
    )
    sr4 = SR.measure_sr4(runner, NOW, window_days=7)
    assert sr4["non_redline_decisions"] == 2
    assert sr4["redline_decisions"] == 1
    assert sr4["value"] == pytest.approx(2.0)  # 2 in 7 d == 2/week
    assert sr4["meets_target"] is False
    assert sr4["redline_kinds"] == ["stage_promotion"]
    assert len(sr4["redline_lifecycle_actions"]) == 1


def test_measure_sr4_zero_when_only_red_lines():
    runner = FakeRunner(
        {"FROM tensor_agent_session_events": [_decision(NOW - timedelta(days=1), kind="stop_condition")],
         "FROM mission_lifecycle_log": []}
    )
    sr4 = SR.measure_sr4(runner, NOW, window_days=7)
    assert sr4["value"] == 0.0 and sr4["meets_target"] is True


# ---------------------------------------------------------------------------
# SR3: sampling + pack audit
# ---------------------------------------------------------------------------


def _pack(
    pack_id,
    status="approved",
    mission_id=10,
    reason="x" * 40,
    evidence=None,
    note="owner batch review",
    mission_status="succeeded",
):
    summary = {
        "model": "glm-5.2",
        "headline": "## 审计完成：阻力算例与 ITS-90 基准一致，最大误差 1.2 %",
        "checks": "报告路径 docs/report.md（120 行）；误差 1.2 % < 3 % 目标；守恒残差 1e-9",
        "git_status": "M docs/report.md",
    }
    base = {"summary": summary, "finished_status": "succeeded"}
    base.update(evidence or {})
    return (
        pack_id,
        "dev_acceptance",
        status,
        mission_id,
        reason,
        json.dumps(base, ensure_ascii=False),
        note,
        NOW - timedelta(days=1),
        mission_status,
    )


def test_deterministic_sample_is_stable_and_order_independent():
    ids = list(range(500))
    first = SR.deterministic_sample(ids, 20, seed="IN27-H7")
    second = SR.deterministic_sample(list(reversed(ids)), 20, seed="IN27-H7")
    assert first == second
    assert len(first) == 20
    assert SR.deterministic_sample(ids, 20, seed="other") != first


def test_audit_pack_all_checks_pass_on_a_clean_pack():
    audit = SR.audit_pack(_pack(1))
    assert audit["passed"] is True
    assert all(audit["checks"].values())
    assert audit["failed_checks"] == []


def test_audit_pack_flags_thin_headline_and_no_quantitative_claim():
    summary = {"headline": "short", "checks": "looked at it"}
    evidence = {"summary": summary}
    audit = SR.audit_pack(_pack(2, evidence=evidence))
    assert audit["passed"] is False
    assert audit["checks"]["headline_ok"] is False
    assert audit["checks"]["verification_ok"] is False
    assert audit["checks"]["numbers_ok"] is False
    assert set(audit["failed_checks"]) >= {"headline_ok", "verification_ok", "numbers_ok", "deliverable_ok"}


def test_audit_pack_flags_untraceable_and_inconsistent():
    audit = SR.audit_pack(_pack(3, mission_id=None, note="", mission_status="failed"))
    assert audit["checks"]["traceable"] is False
    assert audit["checks"]["outcome_consistent"] is False


def test_audit_pack_accepts_commit_or_pr_as_deliverable_pointer():
    summary = {
        "headline": "## 交付：shape 函数按 Cb 目标重标定，残差 0.8 %",
        "checks": "独立复算 12 个测点，最差相对误差 0.8 %",
    }
    audit = SR.audit_pack(_pack(4, evidence={"summary": summary, "head_commit": "a" * 40}))
    assert audit["checks"]["deliverable_ok"] is True


def test_deliverable_pointer_ignores_hex_lookalikes():
    """`succeeded` and session UUIDs contain 7-8 char hex runs -- not commits."""
    summary = {
        "headline": "## 交付：阻力算例通过，误差 1.2 %",
        "checks": "独立复算 12 个测点，最差相对误差 1.2 %",
        "agent_session_id": "a8e12b86-59a4-4fa7-825e-9d35e6f2c111",
    }
    audit = SR.audit_pack(_pack(6, evidence={"summary": summary, "finished_status": "succeeded"}))
    assert audit["checks"]["deliverable_ok"] is False
    assert audit["checks"]["numbers_ok"] is True


def test_deliverable_pointer_accepts_report_path_and_head_commit():
    summary = {"headline": "## 交付：算例通过，误差 1.2 %", "checks": "复算 12 个测点，误差 1.2 %"}
    with_path = SR.audit_pack(_pack(7, evidence={"summary": {**summary, "checks": summary["checks"] + " 见 docs/x.md"}}))
    assert with_path["checks"]["deliverable_ok"] is True
    with_commit = SR.audit_pack(_pack(8, evidence={"summary": summary, "head_commit": "deadbeef12"}))
    assert with_commit["checks"]["deliverable_ok"] is True


def test_audit_pack_accepts_rejection_with_note():
    audit = SR.audit_pack(_pack(5, status="rejected", mission_status="failed", note="no deliverable produced"))
    assert audit["checks"]["outcome_consistent"] is True


def test_measure_sr3_rate_over_sample_with_undecided_excluded():
    packs = [_pack(i) for i in range(1, 8)] + [_pack(8, note="", mission_id=None)] + [
        (9, "dev_acceptance", "pending", 10, "x" * 40, "{}", "", NOW, "running")
    ]
    runner = FakeRunner({"FROM tensor_approval_requests": packs})
    sr3 = SR.measure_sr3(runner, NOW, window_days=30, sample_size=100)
    assert sr3["population"] == 8 and sr3["undecided"] == 1
    assert sr3["sample_size"] == 8
    assert sr3["value"] == pytest.approx(7 / 8, abs=1e-4)
    assert sr3["per_check_fail"]["traceable"] == 1
    assert set(sr3["per_check_pass"]) == {k for k, _ in SR.PACK_CHECKS}
    assert sr3["status"] == "REAL"
    assert sr3["meets_target"] is False


def test_measure_sr3_withheld_without_packs():
    sr3 = SR.measure_sr3(FakeRunner({"FROM tensor_approval_requests": []}), NOW)
    assert sr3["status"] == "WITHHELD" and sr3["value"] is None


# ---------------------------------------------------------------------------
# assembly / chronicle / report rendering
# ---------------------------------------------------------------------------


def _full_runner():
    shadow_row = (
        1,
        NOW - timedelta(hours=30),
        {"would_do": [
            {"action": "escalate", "target": "organ_a"},
            {"action": "monitor", "target": "organ_b"},
        ]},
    )
    runner = FakeRunner(
        {
            "FROM coordinator_shadow": [shadow_row],
            "FROM tensor_dev_missions": [(NOW - timedelta(hours=28), "fix organ_a drift")],
            "FROM campaign_events": [],
            "FROM alerts": [],
            "FROM tensor_agent_session_events": [_decision(NOW - timedelta(days=5))],
            "SELECT max(created_at)": [(NOW - timedelta(days=5),)],
            "FROM tensor_approval_requests": [_pack(1), _pack(2, note="")],
            "FROM mission_lifecycle_log": [],
            "FROM chronicle_snapshots": [(NOW.date(), 1)],
        }
    )
    return runner


def test_build_report_carries_all_four_metrics_and_sql_index():
    report = SR.build_report(_full_runner(), now=NOW, repo_commit="deadbeef")
    assert set(report["metrics"]) == {"SR1", "SR2", "SR3", "SR4"}
    assert report["grades"]["SR1"] == "REAL"
    assert report["domain_semantics"] == "none"
    assert report["provenance"]["commit"] == "deadbeef"
    metrics_in_sql = {item["metric"] for item in report["sql"]}
    assert metrics_in_sql == {"SR1", "SR2", "SR3", "SR4"}
    for item in report["sql"]:
        assert "%(" not in item["sql"], f"unsubstituted parameter in {item['key']}"


def test_chronicle_payload_lands_in_clock_slot_s8():
    report = SR.build_report(_full_runner(), now=NOW)
    payload = SR.chronicle_payload(report)
    assert payload["clock"] == {"slot": "s8", "name": "harness", "facet": SR.CLOCK_FACET}
    assert payload["schema"] == "chronicle-clock/1"
    block = payload["s8_harness"]["sr_inheritance"]
    assert set(block) == {"SR1", "SR2", "SR3", "SR4"}
    # organ_a (escalate, evidence present) matched; organ_b (monitor, no evidence) matched too
    assert block["SR1"]["counts"] == {"matched": 2, "diverged": 0, "pending": 0, "unobservable": 0}
    assert block["SR1"]["value"] == 0.0 and block["SR1"]["meets_target"] is True
    assert block["SR1"]["baseline_horizon_hours"] == 0.5
    assert json.dumps(payload, sort_keys=True)  # JSON-serialisable as stored in jsonb


def test_write_chronicle_reports_receipt_and_carries_payload():
    runner = _full_runner()
    report = SR.build_report(runner, now=NOW)
    receipt = SR.write_chronicle(runner, report, NOW)
    assert receipt["status"] == "written" and receipt["slot"] == "s8"
    sql, params = runner.writes[0]
    assert "chronicle_snapshots" in sql
    stored = json.loads(params["payload"])
    assert stored["s8_harness"]["sr_inheritance"]["SR2"]["value"] is not None


def test_read_trend_and_continuity():
    rows = [
        (NOW.replace(hour=0), {"SR1": {"value": 0.1}, "SR2": {"value": 5.0}}),
        (NOW - timedelta(days=1), {"SR1": {"value": None}, "SR2": {"value": 4.0}}),
    ]
    runner = FakeRunner(
        {
            "payload->'s8_harness'": rows,
            "ts::date": [(NOW.date(), 1), ((NOW - timedelta(days=1)).date(), 1)],
        }
    )
    trend = SR.read_trend(runner, limit=5)
    assert trend[0]["sr"]["SR2"]["value"] == 5.0
    cont = SR.continuity(runner, NOW, days=3)
    assert cont["status"] == "REAL"
    assert cont["current_run_streak"] == 2
    assert len(cont["missing_days"]) == 1


def test_render_markdown_contains_every_metric_and_sql_index():
    report = SR.build_report(_full_runner(), now=NOW)
    md = D.render_markdown(report, SR.continuity(_full_runner(), NOW, 3), [{"ts": "t", "sr": {"SR1": {"value": 0.5}}}])
    for token in ("SR1 影子分歧率", "SR2 协调官自主天数", "SR3 红线决策包完整正确率", "SR4 owner 非红线干预/周"):
        assert token in md
    assert "复算入口" in md and "psql" in md
    assert "WITHHELD" in md or "REAL" in md


def test_snapshot_cli_writes_artifact_report_and_chronicle(tmp_path, monkeypatch):
    runner = _full_runner()
    monkeypatch.setattr(D, "_runner_from_env", lambda: runner)
    out = tmp_path / "artifacts" / "sr.json"
    md = tmp_path / "docs" / "sr.md"
    fig = tmp_path / "artifacts" / "sr.png"
    code = D.main(
        [
            "snapshot",
            "--now",
            NOW.isoformat(),
            "--window-days",
            "30",
            "--sample-size",
            "10",
            "--out",
            str(out),
            "--markdown",
            str(md),
            "--figure",
            str(fig),
            "--chronicle",
        ]
    )
    assert code == 0
    payload = json.loads(out.read_text())
    assert payload["grades"]["SR1"] == "REAL"
    assert payload["chronicle"]["status"] == "written"
    assert payload["figure"]["status"] in {"written", "skipped"}
    assert "IN27-H7" in md.read_text()
    assert runner.writes, "chronicle write must be attempted"


def test_snapshot_cli_writes_acceptance_block(tmp_path, monkeypatch):
    runner = _full_runner()
    monkeypatch.setattr(D, "_runner_from_env", lambda: runner)
    out = tmp_path / "sr.json"
    md = tmp_path / "sr.md"
    assert D.main([
        "snapshot", "--now", NOW.isoformat(), "--sample-size", "5",
        "--out", str(out), "--markdown", str(md), "--chronicle",
    ]) == 0
    payload = json.loads(out.read_text())
    acc = payload["acceptance"]
    assert acc["all_passed"] is True
    ids = {c["id"] for c in acc["criteria"]}
    assert ids == {"A1_instrument_produces", "A2_sr1_first_month_baseline",
                   "A3_four_metrics_in_clock_slot_s8", "A4_weekly_report", "A5_pytest_suite"}
    assert acc["deliverables"]["tests"] == "tests/test_sr_readiness.py"
    assert isinstance(acc["criteria"][1]["observed"]["baseline_value"], float)
    # acceptance numbers and product paths must both live inside the artifact JSON
    assert payload["acceptance"]["targets"]["SR1"] == 0.2


def test_figure_function_is_explicit_when_matplotlib_is_missing(tmp_path, monkeypatch):
    report = SR.build_report(_full_runner(), now=NOW)

    def boom(*args, **kwargs):
        raise ImportError("no matplotlib")

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("no matplotlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = D.plot_figure(report, str(tmp_path / "x.png"), [])
    assert result["status"] == "skipped"


@pytest.mark.skipif(os.environ.get("TP_SR_LIVE") != "1", reason="set TP_SR_LIVE=1 for the live DB smoke test")
def test_live_database_smoke():  # pragma: no cover - opt-in
    from tools.autonomy.metrics import PgRunner

    report = SR.build_report(PgRunner(), window_days=30, sample_size=50)
    assert set(report["grades"]) == {"SR1", "SR2", "SR3", "SR4"}
    assert report["metrics"]["SR3"]["population"] >= 0
    payload = SR.chronicle_payload(report)
    assert payload["clock"]["slot"] == "s8"