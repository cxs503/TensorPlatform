"""IN27-H4 战略质询机制测试。

覆盖：题目库（族覆盖/度量缺失不补零）、生成器（3-5 配额/去重/跨族/goal 判据）、
账本（题数不变量/同日去重/转化证据门/审计 append-only）、验收门（7 日连续 + 2 件转化，
含 backfill 不得冒充 live、证据被改写必须拦下）、daemon/cli 端到端。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.interrogator import acceptance, generator, question_bank  # noqa: E402
from src.interrogator.daemon import InterrogatorDaemon  # noqa: E402
from src.interrogator.ledger import (  # noqa: E402
    Ledger, LedgerError, MAX_PER_CYCLE, MIN_PER_CYCLE,
)
from src.interrogator.probes import MappingSignalSource, Signal  # noqa: E402


# ───────────────────────── fixtures ─────────────────────────

RICH_SIGNALS = {
    "missions_by_status": {"pending": 12, "running": 0, "succeeded": 40, "failed": 5},
    "missions_pending": 12,
    "missions_terminal_window": 18,
    "missions_succeeded_window": 12,
    "missions_failed_window": 4,
    "missions_honest_fail_window": 3,
    "mission_grade_counts": {"S1": 9, "S2": 31},
    "capability_grade_counts": {"S2": 21},
    "plans_budget": {"total": 6, "zero_actual": 5,
                     "examples": [{"plan_id": 42, "name": "继承者军",
                                   "planned": {"max_tokens": 0},
                                   "actual": {"max_tokens": 0, "max_tool_calls": 0}}]},
    "reports_window": 9,
    "reports_approved_window": 7,
    "playbook_entries": 0,
}


def rich_source(label: str = "fixture") -> MappingSignalSource:
    return MappingSignalSource(RICH_SIGNALS, label=label)


def make_question(family: str, key: str, severity: int = 3):
    return question_bank.Question(
        key=key, family=family, title=f"{family}-{key} 题面", text="问：为什么？",
        evidence=[{"signal": "x", "value": 1, "source": "t", "status": "ok"}],
        branches=["a) 甲", "b) 乙", "c) 丙"],
        criteria=["必须给数字"], convert_rule="→ 立修补单", severity=severity,
    )


def fill_cycle(ledger: Ledger, cycle_date: str, n: int = 3, mode: str = "live",
               offset: int = 0) -> str:
    """用互不相同的指纹填一个周期（测试账本层不变量）。"""
    qs = [make_question("ZERO_FLOW", f"T{offset + i}") for i in range(n)]
    return ledger.record_cycle(cycle_date=cycle_date, questions=qs, mode=mode)


# ───────────────────────── 题目库 ─────────────────────────

def test_question_bank_covers_required_families():
    cands = question_bank.candidates(rich_source().collect(), {"asked": 4, "converted": 0})
    families = {q.family for q in cands}
    assert {"ZERO_FLOW", "GRADE_CEIL", "EXP_BUDGET", "SN_TRACK", "CONVERSION"} <= families
    for q in cands:
        assert len(q.branches) >= 3, f"{q.fingerprint} 解释分支不足 3 个"
        assert q.criteria, f"{q.fingerprint} 缺机器判据"
        assert q.convert_rule, f"{q.fingerprint} 缺转化规则"


def test_unusable_signal_never_fabricates_zero():
    """探针失败的信号必须是 unavailable，且质询转成「度量缺失」题，而不是数值 0 的结论。"""
    sig = Signal("plans_budget", None, "http:/tensor/campaigns/plans", status="unavailable")
    cands = question_bank.candidates({"plans_budget": sig}, {"asked": 0, "converted": 0})
    eb = [q for q in cands if q.family == "EXP_BUDGET"]
    assert eb and "不可读" in eb[0].title
    assert eb[0].evidence[0]["status"] == "unavailable"


def test_budget_zero_actual_triggers_question_with_examples():
    cands = question_bank.candidates(rich_source().collect(), None)
    eb = [q for q in cands if q.family == "EXP_BUDGET"][0]
    assert "5/6" in eb[0].title if isinstance(eb, list) else "5/6" in eb.title
    assert any("budget_actual" in c for c in eb.criteria)


def test_grade_ceiling_reported_as_s2_when_no_s3():
    cands = question_bank.candidates(rich_source().collect(), None)
    gc = [q for q in cands if q.family == "GRADE_CEIL"][0]
    assert "S2" in gc.title
    assert len(gc.branches) == 4


# ───────────────────────── 生成器 ─────────────────────────

def test_cycle_target_count_always_within_3_5():
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    counts = {generator.cycle_target_count((base + timedelta(days=i)).date().isoformat())
              for i in range(60)}
    assert counts <= set(range(MIN_PER_CYCLE, MAX_PER_CYCLE + 1))
    assert len(counts) >= 2, "题数应有变化（按日确定，不是常量）"


def test_select_questions_respects_quota_and_families():
    signals = rich_source().collect()
    chosen = generator.select_questions(signals, {"asked": 0, "converted": 0},
                                        recent_fingerprints=set(), cycle_date="2026-11-07")
    assert MIN_PER_CYCLE <= len(chosen) <= MAX_PER_CYCLE
    assert len({q.family for q in chosen}) >= 2, "应跨族，不得同族刷量"
    assert len({q.fingerprint for q in chosen}) == len(chosen)


def test_dedupe_prefers_fresh_questions_then_reuses_oldest():
    signals = rich_source().collect()
    all_fps = {q.fingerprint for q in question_bank.candidates(signals, None)}
    fresh = generator.select_questions(signals, None, all_fps, "2026-11-07")
    assert MIN_PER_CYCLE <= len(fresh) <= MAX_PER_CYCLE, "池耗尽也必须落满下限（复用最旧题）"


def test_goal_carries_evidence_criteria_and_discipline():
    signals = rich_source().collect()
    q = question_bank.candidates(signals, None)[0]
    params = generator.to_mission_params(q, "2026-11-07", signals)
    goal = params["goal"]
    assert len(goal) >= 80
    assert "验收判据" in goal and "honest FAIL" in goal and "转化要求" in goal
    assert "IN27-H4" in goal
    for ev in q.evidence:
        assert str(ev["source"]) in goal, "证据必须带来源，质询才可回溯"
    assert params["origin"] == "agent"
    assert re.fullmatch(r"[a-zA-Z0-9_.:-]{2,120}", params["task_key"]), params["task_key"]


def test_build_cycle_emits_missions_for_every_question():
    signals = rich_source().collect()
    bundle = generator.build_cycle(signals, None, set(), "2026-11-08")
    assert MIN_PER_CYCLE <= len(bundle) <= MAX_PER_CYCLE
    for item in bundle:
        assert item["mission"]["task_key"].startswith("interrogator:")
        assert item["question"]["fingerprint"] == \
            f"{item['question']['family']}:{item['question']['key']}"


# ───────────────────────── 账本不变量 ─────────────────────────

def test_ledger_rejects_cycle_outside_quota(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        with pytest.raises(LedgerError):
            fill_cycle(ledger, "2026-11-01", n=2)
        with pytest.raises(LedgerError):
            fill_cycle(ledger, "2026-11-01", n=6)


def test_ledger_rejects_same_question_twice_in_one_day(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        fill_cycle(ledger, "2026-11-01", n=3)
        with pytest.raises(LedgerError, match="同一天"):
            fill_cycle(ledger, "2026-11-01", n=3)
        # 换一天可以复用同一题（证据会变，问题依然是有效质询）
        fill_cycle(ledger, "2026-11-02", n=3, offset=0)


def test_conversion_requires_existing_nonempty_evidence(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        fill_cycle(ledger, "2026-11-01", n=3)
        fp = "ZERO_FLOW:T0"
        with pytest.raises(LedgerError, match="不存在"):
            ledger.record_conversion(fp, "repair", str(tmp_path / "nope.md"), fix_mission_id=1)
        empty = tmp_path / "empty.md"
        empty.write_text("")
        with pytest.raises(LedgerError, match="为空"):
            ledger.record_conversion(fp, "repair", str(empty), fix_mission_id=1)
        ok = tmp_path / "ok.md"
        ok.write_text("缺陷证据：file::memory: 被跟踪\n")
        with pytest.raises(LedgerError, match="修补/改进单|修复单"):
            ledger.record_conversion(fp, "repair", str(ok))  # 没挂单号不许算转化
        cid = ledger.record_conversion(fp, "repair", str(ok), fix_mission_id=9001)
        assert cid >= 1
        assert ledger.interrogations(state="converted")


def test_audit_log_is_append_only_and_records_writes(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        fill_cycle(ledger, "2026-11-01", n=3)
        ev = tmp_path / "e.md"
        ev.write_text("x")
        ledger.record_conversion("ZERO_FLOW:T0", "improvement", str(ev), fix_task_key="k1")
        lines = (tmp_path / "l.db.audit.jsonl").read_text().strip().splitlines()
        events = [json.loads(l)["event"] for l in lines]
        assert events[0] == "cycle_recorded" and "conversion_recorded" in events
        assert all(json.loads(l)["at"] for l in lines)


# ───────────────────────── 验收门 ─────────────────────────

def _fill_days(ledger: Ledger, days: int, mode: str = "live", start="2026-11-01"):
    base = datetime.fromisoformat(start).date()
    for i in range(days):
        fill_cycle(ledger, (base + timedelta(days=i)).isoformat(), n=3, mode=mode,
                  offset=i * 10)


def _convert(ledger: Ledger, tmp_path, fp: str, mission_id: int, kind="repair"):
    ev = tmp_path / f"evidence_{mission_id}.md"
    ev.write_text(f"修补单 {mission_id} 证据：缺陷复现命令 + 修复 diff 路径\n")
    ledger.record_conversion(fp, kind, str(ev), fix_mission_id=mission_id)


def test_gate_pass_needs_seven_live_days_and_two_conversions(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        _fill_days(ledger, 7)
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        _convert(ledger, tmp_path, "ZERO_FLOW:T10", 9002, kind="improvement")
        verdict = acceptance.evaluate(ledger, as_of=datetime(2026, 11, 7, 12, tzinfo=timezone.utc))
        assert verdict.verdict == "PASS", verdict.to_dict()
        numbers = {c.name: c.numbers for c in verdict.criteria}
        assert numbers["A2_连续7天产出"]["live_days_in_window"] == 7
        assert numbers["A3_至少2件真实转化"]["unique_converted"] == 2


def test_gate_not_pass_with_only_six_days(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        _fill_days(ledger, 6)
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        _convert(ledger, tmp_path, "ZERO_FLOW:T10", 9002)
        verdict = acceptance.evaluate(ledger, as_of=datetime(2026, 11, 6, 12, tzinfo=timezone.utc))
        assert verdict.verdict == "PENDING", verdict.to_dict()
        a2 = [c for c in verdict.criteria if c.name.startswith("A2")][0]
        assert a2.ok is False and a2.numbers["live_days_in_window"] == 6


def test_gate_fails_with_single_conversion(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        _fill_days(ledger, 7)
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        verdict = acceptance.evaluate(ledger, as_of=datetime(2026, 11, 7, 12, tzinfo=timezone.utc))
        assert verdict.verdict == "FAIL"
        a3 = [c for c in verdict.criteria if c.name.startswith("A3")][0]
        assert a3.numbers["unique_converted"] == 1


def test_gate_rejects_backfill_days_disguised_as_live(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        _fill_days(ledger, 7, mode="backfill")
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        _convert(ledger, tmp_path, "ZERO_FLOW:T10", 9002)
        verdict = acceptance.evaluate(ledger, as_of=datetime(2026, 11, 7, 12, tzinfo=timezone.utc))
        assert verdict.verdict != "PASS"
        a2 = [c for c in verdict.criteria if c.name.startswith("A2")][0]
        assert a2.numbers["live_days_in_window"] == 0
        assert len(a2.numbers["backfill_only_days"]) == 7


def test_gate_detects_tampered_evidence(tmp_path):
    with Ledger(str(tmp_path / "l.db")) as ledger:
        _fill_days(ledger, 7)
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        _convert(ledger, tmp_path, "ZERO_FLOW:T10", 9002)
        (tmp_path / "evidence_9001.md").write_text("事后改写的证据\n")
        verdict = acceptance.evaluate(ledger, as_of=datetime(2026, 11, 7, 12, tzinfo=timezone.utc))
        assert verdict.verdict == "FAIL"
        a3 = [c for c in verdict.criteria if c.name.startswith("A3")][0]
        assert a3.numbers["tampered"], a3.numbers


# ───────────────────────── daemon / cli 端到端 ─────────────────────────

def test_daemon_run_once_writes_artifacts_and_ledger(tmp_path):
    out = tmp_path / "runs"
    daemon = InterrogatorDaemon(ledger_path=str(tmp_path / "l.db"), source=rich_source(),
                                api_base=None, out_dir=str(out))
    art = daemon.run_once(as_of=datetime(2026, 11, 7, 9, tzinfo=timezone.utc),
                          create_missions=True)  # 无 api_base → 不立单，但不得崩
    assert MIN_PER_CYCLE <= art["summary"]["count"] <= MAX_PER_CYCLE
    assert art["created_missions"] == 0
    assert art["source_status"] == "ok"
    assert os.path.isfile(art["artifact_path"])
    snap = out / "signals_2026-11-07.json"
    assert snap.is_file(), "live 周期必须落信号快照（质询引用的数字可回溯）"
    with Ledger(str(tmp_path / "l.db")) as ledger:
        assert ledger.stats()["asked"] == art["summary"]["count"]


def test_daemon_backfill_does_not_write_snapshot(tmp_path):
    out = tmp_path / "runs"
    daemon = InterrogatorDaemon(ledger_path=str(tmp_path / "l.db"), source=rich_source(),
                                out_dir=str(out))
    daemon.run_once(as_of=datetime(2026, 11, 6, 9, tzinfo=timezone.utc), mode="backfill")
    assert not (out / "signals_2026-11-06.json").exists()
    with Ledger(str(tmp_path / "l.db")) as ledger:
        days = ledger.cycle_days()
        assert days["2026-11-06"]["backfill"] == 1 and days["2026-11-06"]["live"] == 0


def test_cli_verify_exit_codes(tmp_path, capsys):
    from src.interrogator import cli

    ledger_path = str(tmp_path / "l.db")
    with Ledger(ledger_path) as ledger:
        _fill_days(ledger, 7)
        _convert(ledger, tmp_path, "ZERO_FLOW:T0", 9001)
        _convert(ledger, tmp_path, "ZERO_FLOW:T10", 9002)
    rc = cli.main(["--ledger", ledger_path, "--out", str(tmp_path),
                   "verify", "--as-of", "2026-11-07T12:00:00+00:00"])
    assert rc == 0
    verdict = json.loads((tmp_path / "verdict.json").read_text())
    assert verdict["verdict"] == "PASS"
    assert verdict["artifact_paths"]["ledger"].endswith("l.db")
    assert (tmp_path / "ledger_export.json").is_file()

# ───────────────────────── 现场修复回归（2026-11-07 实测发现） ─────────────────────────

def test_queue_depth_counts_queued_status():
    """平台队列态用 ``queued`` 而非 ``pending``；漏算会把 8 张排队单看成零积压。"""
    from src.interrogator.probes import HttpSignalSource

    rows = [{"status": "queued"}, {"status": "queued"}, {"status": "running"},
            {"status": "succeeded", "result": {"verdict": "PASS"}}]
    got = HttpSignalSource()._derive_missions(
        rows, datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 7, tzinfo=timezone.utc))
    assert got["missions_pending"].value == 2
    assert got["missions_by_status"].value["running"] == 1


def test_capability_coverage_signal_feeds_grade_question():
    signals = rich_source().collect()
    signals["capability_coverage"] = Signal(
        "capability_coverage",
        {"covered": 30, "succeeded": 153, "ratio": 0.1961, "total_deltas": 100,
         "substantive_deltas": 12, "no_substantive_deltas": 88, "by_category": {}},
        "http:/tensor/capability/summary")
    cands = question_bank.candidates(signals, None)
    gc = [q for q in cands if q.family == "GRADE_CEIL"][0]
    assert any(ev["signal"] == "capability_coverage" for ev in gc.evidence)


def test_zero_substantive_deltas_raises_structural_ceiling_question():
    """2026-11-07 实测：1366 条能力增量、实质增量 0 —— 必须成为一等质询，而非噪声。"""
    signals = rich_source().collect()
    signals["capability_coverage"] = Signal(
        "capability_coverage",
        {"covered": 1359, "succeeded": 4049, "ratio": 0.3356, "total_deltas": 1366,
         "substantive_deltas": 0, "no_substantive_deltas": 1366, "by_category": {}},
        "http:/tensor/capability/summary")
    cands = question_bank.candidates(signals, None)
    gc3 = [q for q in cands if q.key == "GC-03"]
    assert gc3, "实质增量为 0 时必须出结构性天花板题"
    assert "1366" in gc3[0].title and len(gc3[0].branches) == 3
    assert gc3[0].severity == 5
