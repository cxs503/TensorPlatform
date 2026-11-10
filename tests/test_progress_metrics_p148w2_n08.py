"""P148-W2-N08 进展度量与停滞处置成熟化 —— 验收测试。

判据映射（与 ``ops/taskplane/progress_metrics.py`` 模块头 C1–C8 一一对应）：

* C1/C2/C3 停滞检测与正负控：``TestStagnationDetection`` / ``TestControlExperiments``
* C4 信息增益：``TestInformationGain``
* C5 DAG 端到端：``TestCampaignNodeStore``（假执行器）与 ``TestRealPostgresWriteBack``（真实 PG，无库则 skip）
* C6 早停报告：``TestEarlyStopReport``
* C7 零领域语义：``TestDomainNeutrality``
* C8 上游适配层：``TestExternalAdapter``
* 不变式：warmup 不参与判定、有符号斜率、日志哈希链、续跑重放。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ops.taskplane import progress_metrics as pm  # noqa: E402
from ops.taskplane.progress_metrics import (  # noqa: E402
    ACCEPTED_SCHEMAS,
    ACTION_EARLY_STOP,
    ACTION_OBSERVE,
    ArtifactScanner,
    CampaignNodeStore,
    DAGError,
    EarlyStopHandler,
    ExternalSignalAdapter,
    InformationGainEstimator,
    ProgressJournal,
    ProgressOrchestrator,
    ProgressSnapshot,
    ProgressThresholds,
    ProgressTracker,
    PytestRunner,
    RULE_NO_PROGRESS_CYCLES,
    StagnationDetector,
)

UTC = timezone.utc
TH = ProgressThresholds()


# ── 夹具 ────────────────────────────────────────────────────────────────────
@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "doc.md").write_text("# doc\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return root


def _snap(cycle: int, *, composite: float, hard_no_progress: bool, warmup: bool = False) -> ProgressSnapshot:
    return ProgressSnapshot(ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=cycle),
                            node_id=1, cycle=cycle, warmup=warmup, composite_score=composite,
                            signals=[], hard_no_progress=hard_no_progress)


def _pytest(total: int = 10, passed: int = 8) -> dict:
    return {"total": total, "passed": passed, "pass_rate": passed / total if total else 0.0}


# ── 工件扫描 ────────────────────────────────────────────────────────────────
class TestArtifactScanner:
    def test_scans_content_files(self, worktree):
        sc = ArtifactScanner(worktree)
        rels = {str(p.relative_to(worktree)) for p in sc.scan_files()}
        assert {"src/core.py", "src/doc.md", "tests/test_ok.py"} <= rels

    def test_excludes_cache_dirs(self, worktree):
        cache = worktree / "__pycache__"
        cache.mkdir()
        (cache / "junk.py").write_text("x=1\n", encoding="utf-8")
        rels = {str(p.relative_to(worktree)) for p in ArtifactScanner(worktree).scan_files()}
        assert not any("__pycache__" in r for r in rels)

    def test_excludes_non_source_suffixes(self, tmp_path):
        root = tmp_path / "wt2"
        root.mkdir()
        (root / "a.bin").write_bytes(b"\x00\x01")
        (root / "b.py").write_text("x=1\n", encoding="utf-8")
        rels = [p.name for p in ArtifactScanner(root).scan_files()]
        assert rels == ["b.py"]

    def test_aggregate_hash_is_deterministic(self, worktree):
        a = ArtifactScanner(worktree).snapshot_state()
        b = ArtifactScanner(worktree).snapshot_state()
        assert a["artifact_hash"] == b["artifact_hash"]
        assert a["file_count"] == b["file_count"] == 3
        assert a["line_count"] >= 3

    def test_hash_changes_on_content_edit(self, worktree):
        before = ArtifactScanner(worktree).snapshot_state()["artifact_hash"]
        (worktree / "src" / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
        after = ArtifactScanner(worktree).snapshot_state()["artifact_hash"]
        assert before != after

    def test_mtime_touch_does_not_change_hash(self, worktree):
        """防假活核心不变式：只碰 mtime 不算内容进展。"""
        state = ArtifactScanner(worktree).snapshot_state()
        for rel in ("src/core.py", "src/doc.md"):
            p = worktree / rel
            st = p.stat()
            pm.os.utime(p, (st.st_atime, st.st_mtime + 600))
        after = ArtifactScanner(worktree).snapshot_state()
        assert after["artifact_hash"] == state["artifact_hash"]
        assert ArtifactScanner.change_fraction(state["file_hashes"], after["file_hashes"]) == 0.0

    def test_change_fraction_counts_add_change_remove(self):
        prev = {"a.py": "1", "b.py": "2"}
        assert ArtifactScanner.change_fraction(None, prev) == 0.0  # 无基线不定义变化
        assert ArtifactScanner.change_fraction(prev, dict(prev)) == 0.0
        assert ArtifactScanner.change_fraction(prev, {"a.py": "1", "b.py": "9"}) == pytest.approx(0.5)
        assert ArtifactScanner.change_fraction(prev, {"a.py": "1", "c.py": "3"}) == pytest.approx(1.0)

    def test_nonexistent_worktree_is_empty_not_crash(self, tmp_path):
        sc = ArtifactScanner(tmp_path / "missing")
        assert sc.scan_files() == []
        assert sc.snapshot_state()["file_count"] == 0


# ── 信息增益（C4） ─────────────────────────────────────────────────────────
class TestInformationGain:
    def test_warmup_undefined_and_zero(self, worktree):
        info = InformationGainEstimator()
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert out["warmup"] is True and out["gain"] == 0.0

    def test_new_artifact_yields_positive_gain(self, worktree):
        info = InformationGainEstimator()
        info.estimate(ArtifactScanner(worktree).snapshot_state())
        (worktree / "src" / "new_unit.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert out["warmup"] is False
        assert out["novel_files"] >= 1 and out["gain"] > 0

    def test_no_change_yields_zero_gain(self, worktree):
        info = InformationGainEstimator()
        info.estimate(ArtifactScanner(worktree).snapshot_state())
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert out["gain"] == 0.0

    def test_mtime_touch_yields_zero_gain(self, worktree):
        info = InformationGainEstimator()
        info.estimate(ArtifactScanner(worktree).snapshot_state())
        p = worktree / "src" / "core.py"
        st = p.stat()
        pm.os.utime(p, (st.st_atime, st.st_mtime + 3600))
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert out["gain"] == 0.0

    def test_edit_yields_positive_gain(self, worktree):
        info = InformationGainEstimator()
        info.estimate(ArtifactScanner(worktree).snapshot_state())
        p = worktree / "src" / "core.py"
        p.write_text(p.read_text(encoding="utf-8") + "EXTRA = 3\n", encoding="utf-8")
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert out["gain"] > 0 and out["novel_files"] >= 1

    def test_gain_bounded(self, worktree):
        info = InformationGainEstimator()
        info.estimate(ArtifactScanner(worktree).snapshot_state())
        for i in range(5):
            (worktree / "src" / f"u{i}.py").write_text("x = %d\n" % i + "y = 1\n" * 50, encoding="utf-8")
        out = info.estimate(ArtifactScanner(worktree).snapshot_state())
        assert 0.0 < out["gain"] <= 1.0


# ── pytest 通过率 ──────────────────────────────────────────────────────────
class TestPytestRunner:
    def test_parse_summary(self):
        out = PytestRunner.parse_output("3 passed, 2 failed, 1 error in 0.5s", 1)
        assert out["total"] == 6 and out["passed"] == 3 and out["failed"] == 2
        assert out["pass_rate"] == pytest.approx(0.5)

    def test_parse_empty_is_zero_not_optimistic(self):
        out = PytestRunner.parse_output("collection error", 2)
        assert out["total"] == 0 and out["pass_rate"] == 0.0

    def test_missing_target_is_reported(self, tmp_path):
        out = PytestRunner(tmp_path, target="tests").run()
        assert out["total"] == 0 and "target missing" in out["note"]

    def test_runs_real_pytest(self, worktree):
        out = PytestRunner(worktree, target="tests").run()
        assert out["total"] == 1 and out["passed"] == 1 and out["pass_rate"] == 1.0


# ── 采集层 ──────────────────────────────────────────────────────────────────
class TestProgressTracker:
    def test_first_snapshot_is_warmup(self, worktree):
        snap = ProgressTracker(worktree).take_snapshot()
        assert snap.warmup is True and snap.hard_no_progress is False
        assert any("warmup" in n for n in snap.notes)

    def test_hard_no_progress_after_identical_cycles(self, worktree):
        tr = ProgressTracker(worktree)
        tr.take_snapshot(pytest_result=_pytest(), criteria={"c1": True})
        for _ in range(3):
            snap = tr.take_snapshot(pytest_result=_pytest(), criteria={"c1": True})
            assert snap.hard_no_progress is True
        assert [s.cycle for s in tr.history] == [0, 1, 2, 3]

    def test_content_change_clears_no_progress(self, worktree):
        tr = ProgressTracker(worktree)
        tr.take_snapshot(pytest_result=_pytest())
        tr.take_snapshot(pytest_result=_pytest())
        (worktree / "src" / "core.py").write_text("VALUE = 42\n", encoding="utf-8")
        snap = tr.take_snapshot(pytest_result=_pytest())
        assert snap.hard_no_progress is False and snap.change_fraction > 0

    def test_criteria_improvement_counts_as_progress(self, worktree):
        tr = ProgressTracker(worktree)
        tr.take_snapshot(pytest_result=_pytest(), criteria={"c1": False, "c2": False})
        snap = tr.take_snapshot(pytest_result=_pytest(), criteria={"c1": True, "c2": False})
        assert snap.criteria_delta > 0 and snap.hard_no_progress is False

    def test_pytest_not_run_is_marked_stale(self, worktree):
        tr = ProgressTracker(worktree)
        tr.take_snapshot(pytest_result=_pytest())
        snap = tr.take_snapshot()  # 本拍不跑测试
        assert snap.pytest_stale is True
        assert any("stale" in n for n in snap.notes)

    def test_composite_weighting(self, worktree):
        """composite = Σ(w_i·signal_i)，权重和恒为 1（无隐藏归一化失真）。"""
        tr = ProgressTracker(worktree)
        snap = tr.take_snapshot(pytest_result=_pytest(10, 10), criteria={"c": True})
        total_w = sum(pm.SIGNAL_WEIGHTS.values())
        assert total_w == pytest.approx(1.0)
        expected = sum(pm.SIGNAL_WEIGHTS[s.signal_type] * s.value
                       for s in snap.signals if s.signal_type in pm.SIGNAL_WEIGHTS)
        assert snap.composite_score == pytest.approx(expected)


# ── 停滞检测（C1/C2/C3 的单元层） ──────────────────────────────────────────
class TestStagnationDetection:
    def test_insufficient_samples_observes(self):
        v = StagnationDetector().analyze([_snap(0, composite=0.3, hard_no_progress=False, warmup=True),
                                          _snap(1, composite=0.3, hard_no_progress=True)])
        assert v.recommendation == ACTION_OBSERVE and v.is_stalled is False

    def test_three_no_progress_cycles_trigger_early_stop(self):
        snaps = [_snap(0, composite=0.3, hard_no_progress=False, warmup=True)] + [
            _snap(i, composite=0.3, hard_no_progress=True) for i in range(1, 4)]
        v = StagnationDetector().analyze(snaps)
        assert v.is_stalled and v.rule_fired == RULE_NO_PROGRESS_CYCLES
        assert v.recommendation == ACTION_EARLY_STOP
        assert v.consecutive_no_progress_cycles == 3

    def test_two_no_progress_cycles_only_watch(self):
        snaps = [_snap(0, composite=0.1, hard_no_progress=False, warmup=True),
                 _snap(1, composite=0.5, hard_no_progress=False)]
        snaps += [_snap(i, composite=0.5, hard_no_progress=True) for i in range(2, 4)]
        v = StagnationDetector().analyze(snaps)
        assert v.consecutive_no_progress_cycles == 2
        assert v.is_stalled is False and v.recommendation == "watch"

    def test_recovery_resets_counter(self):
        snaps = [_snap(0, composite=0.3, hard_no_progress=False, warmup=True)]
        snaps += [_snap(i, composite=0.3, hard_no_progress=True) for i in range(1, 4)]
        snaps += [_snap(4, composite=0.9, hard_no_progress=False)]
        v = StagnationDetector().analyze(snaps)
        assert v.consecutive_no_progress_cycles == 0 and v.is_stalled is False

    def test_warmup_excluded_from_window(self):
        """首拍不算「无进展」，也不污染斜率（回归：warmup 增益=1.0 的旧实现缺陷）。"""
        snaps = [_snap(0, composite=0.9, hard_no_progress=False, warmup=True)] + [
            _snap(i, composite=0.2, hard_no_progress=True) for i in range(1, 4)]
        v = StagnationDetector().analyze(snaps)
        assert v.consecutive_no_progress_cycles == 3
        assert v.detail["warmup_excluded"] == 1
        assert all(x == pytest.approx(0.2) for x in v.window_values)

    def test_negative_slope_counts_as_stalled_not_progress(self):
        """回归：旧实现用 abs(slope)，把断崖下滑也算「有进展」。"""
        values = [0.9, 0.6, 0.3, 0.1, 0.05]
        snaps = [_snap(0, composite=0.99, hard_no_progress=False, warmup=True)] + [
            _snap(i + 1, composite=v, hard_no_progress=(i == len(values) - 1))
            for i, v in enumerate(values)]
        verdict = StagnationDetector().analyze(snaps)
        assert verdict.slope < 0
        # 负斜率不得当作「有进展」（回归 abs(slope) 缺陷）：窗满 + 有硬无进展 → 趋势告警
        assert verdict.trend_flat is True
        assert verdict.is_stalled is False  # 未满 N 拍，不升格为停滞标记
        assert verdict.recommendation == "investigate"

    def test_converging_progress_not_flagged(self):
        """收敛型进展（novelty 饱和导致 composite 自然衰减）不得误报停滞。"""
        snaps = [_snap(0, composite=0.30, hard_no_progress=False, warmup=True)]
        snaps += [_snap(i, composite=v, hard_no_progress=False)
                  for i, v in enumerate([0.70, 0.55, 0.45, 0.38, 0.34, 0.31], start=1)]
        v = StagnationDetector().analyze(snaps)
        assert v.is_stalled is False and v.trend_flat is False
        assert v.slope < TH.stagnation_slope_threshold

    def test_slope_exact_value(self):
        assert StagnationDetector.slope([0, 1, 2, 3]) == pytest.approx(1.0)
        assert StagnationDetector.slope([5, 5, 5]) == pytest.approx(0.0)
        assert StagnationDetector.slope([7]) == 0.0

    def test_runtime_floor_is_honoured_when_configured(self):
        th = ProgressThresholds(min_runtime_before_early_stop_s=600)
        det = StagnationDetector(th)
        snaps = [_snap(0, composite=0.3, hard_no_progress=False, warmup=True)] + [
            _snap(i, composite=0.3, hard_no_progress=True) for i in range(1, 4)]
        v = det.analyze(snaps)
        assert v.recommendation == ACTION_EARLY_STOP
        assert det.should_early_stop(v, runtime_s=10) is False
        assert det.should_early_stop(v, runtime_s=601) is True

    def test_analysis_is_pure(self):
        snaps = [_snap(0, composite=0.3, hard_no_progress=False, warmup=True)] + [
            _snap(i, composite=0.3, hard_no_progress=True) for i in range(1, 4)]
        det = StagnationDetector()
        when = datetime(2026, 1, 1, tzinfo=UTC)
        a, b = det.analyze(snaps, ts=when), det.analyze(snaps, ts=when)
        assert a.as_dict() == b.as_dict()


# ── 早停报告（C6） ─────────────────────────────────────────────────────────
class TestEarlyStopReport:
    def _history(self, worktree, n: int = 4):
        tr = ProgressTracker(worktree)
        tr.take_snapshot(pytest_result=_pytest(10, 5), criteria={"a": False, "b": False})
        for _ in range(n - 1):
            tr.take_snapshot(pytest_result=_pytest(10, 5), criteria={"a": False, "b": False})
        return tr.history

    def test_report_fields_and_honest_fail(self, worktree, tmp_path):
        hist = self._history(worktree)
        verdict = StagnationDetector().analyze(hist)
        rep = EarlyStopHandler(worktree, output_dir=tmp_path / "es").generate_report(
            node_id=42, history=hist, verdict=verdict)
        d = rep.as_dict()
        assert d["honest_fail"] is True
        assert d["schema"] == "tp.progress.early_stop.v1"
        assert len(d["progress_curve"]) == len(hist)
        assert len(d["alternative_paths"]) >= 2
        assert len(d["report_sha256"]) == 64
        assert Path(d["report_path"]).exists()

    def test_report_file_content_matches_sha(self, worktree, tmp_path):
        hist = self._history(worktree)
        verdict = StagnationDetector().analyze(hist)
        rep = EarlyStopHandler(worktree, output_dir=tmp_path / "es").generate_report(
            node_id=7, history=hist, verdict=verdict)
        text = Path(rep.report_path).read_text(encoding="utf-8")
        header, body = text.split("\n", 1)
        assert header == f"# honest_fail_report sha256={rep.report_sha256}"
        assert pm._sha256(body.rstrip("\n")) == rep.report_sha256

    def test_existing_report_is_never_overwritten(self, worktree, tmp_path):
        hist = self._history(worktree)
        verdict = StagnationDetector().analyze(hist)
        handler = EarlyStopHandler(worktree, output_dir=tmp_path / "es")
        first = handler.generate_report(node_id=1, history=hist, verdict=verdict)
        first_text = Path(first.report_path).read_text(encoding="utf-8")
        second = handler.generate_report(node_id=1, history=hist, verdict=verdict)
        assert second.report_path != first.report_path
        assert Path(first.report_path).read_text(encoding="utf-8") == first_text

    def test_alternatives_react_to_signals(self, worktree):
        hist = self._history(worktree)
        alts = EarlyStopHandler(worktree).recommend(hist)
        strategies = [a["strategy"] for a in alts]
        assert "criteria_gap_squeeze" in strategies
        assert "test_first" in strategies  # pass rate 0.5 < 1
        assert strategies[0] and alts[0]["confidence"] >= alts[-1]["confidence"]


# ── 日志与续跑 ──────────────────────────────────────────────────────────────
class TestJournal:
    def test_append_load_and_chain(self, tmp_path):
        j = ProgressJournal(tmp_path / "snap.jsonl")
        j.append({"a": 1})
        j.append({"b": 2})
        ok, problems = j.verify()
        assert ok and problems == []
        records = j.load()
        assert records == [{"a": 1}, {"b": 2}]

    def test_tamper_detected(self, tmp_path):
        path = tmp_path / "snap.jsonl"
        j = ProgressJournal(path)
        j.append({"a": 1})
        j.append({"b": 2})
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"a": 1', '"a": 99')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = ProgressJournal(path).verify()
        assert ok is False and problems
        with pytest.raises(pm.JournalError):
            ProgressJournal(path).load()

    def test_missing_journal_is_not_ok(self, tmp_path):
        ok, problems = ProgressJournal(tmp_path / "nope.jsonl").verify()
        assert ok is False and "missing" in problems[0]


class TestResume:
    def test_resume_replays_history_and_rebaselines(self, worktree, tmp_path):
        out = tmp_path / "runs"
        orch = ProgressOrchestrator(worktree, node_id=3, output_dir=out)
        for _ in range(3):
            orch.tick(pytest_result=_pytest())
        resumed = ProgressOrchestrator(worktree, node_id=3, output_dir=out, resume=True)
        assert len(resumed.tracker.history) == 3
        snap = resumed.tick(pytest_result=_pytest())  # 续跑首拍=重新基线化
        assert snap["snapshot"]["warmup"] is True
        assert snap["snapshot"]["cycle"] == 3
        assert resumed.journal.verify()[0] is True

    def test_verdict_reproducible_after_replay(self, worktree, tmp_path):
        out = tmp_path / "runs"
        orch = ProgressOrchestrator(worktree, node_id=3, output_dir=out)
        orch.tick(pytest_result=_pytest())
        for _ in range(3):
            orch.tick(pytest_result=_pytest())
        live = orch.summary()
        replay = ProgressOrchestrator(worktree, node_id=3, output_dir=out, resume=True)
        rep = replay.detector.analyze(replay.tracker.history)
        assert rep.as_dict()["consecutive_no_progress_cycles"] == live["consecutive_no_progress_cycles"]
        assert rep.as_dict()["recommendation"] == live["recommendation"]


# ── 外部适配层（C8） ───────────────────────────────────────────────────────
class TestExternalAdapter:
    def test_accepts_whitelisted_and_rejects_unknown(self, tmp_path):
        path = tmp_path / "ext.jsonl"
        path.write_text(
            json.dumps({"schema": ACCEPTED_SCHEMAS[0], "ts": "2026-11-10T08:00:00+00:00",
                        "kind": "resume"}) + "\n"
            + json.dumps({"schema": "other.v1", "ts": "2026-11-10T08:00:00+00:00", "kind": "x"}) + "\n",
            encoding="utf-8")
        res = ExternalSignalAdapter().load(path)
        assert res.accepted == 1 and res.rejected == 1 and res.degraded is True
        assert res.kinds == ["resume"]

    def test_missing_file_degrades_without_crash(self, tmp_path):
        res = ExternalSignalAdapter().load(tmp_path / "nope.jsonl")
        assert res.degraded is True and res.records == [] and "missing" in res.reason

    def test_no_path_configured_degrades(self):
        res = ExternalSignalAdapter().load(None)
        assert res.degraded is True and res.records == []

    def test_bad_ts_rejected(self, tmp_path):
        path = tmp_path / "ext.jsonl"
        path.write_text(json.dumps({"schema": ACCEPTED_SCHEMAS[0], "ts": "not-a-time",
                                    "kind": "crash"}) + "\n", encoding="utf-8")
        res = ExternalSignalAdapter().load(path)
        assert res.accepted == 0 and res.rejected == 1

    def test_external_signal_attached_to_snapshot(self, worktree, tmp_path):
        path = tmp_path / "ext.jsonl"
        path.write_text(json.dumps({"schema": ACCEPTED_SCHEMAS[0], "ts": "2026-11-10T08:00:00+00:00",
                                    "kind": "resume"}) + "\n", encoding="utf-8")
        orch = ProgressOrchestrator(worktree, output_dir=tmp_path / "runs")
        res = orch.tick(pytest_result=_pytest(), external_signals=path)
        types = {s["signal_type"] for s in res["snapshot"]["signals"]}
        assert pm.SIGNAL_EXTERNAL in types


# ── DAG 回写（C5，假执行器层） ─────────────────────────────────────────────
class FakeExec:
    def __init__(self, script):
        self.script = script  # list of (predicate, rows)
        self.calls = []

    def __call__(self, sql, variables):
        self.calls.append((sql, dict(variables)))
        for pred, rows in self.script:
            if pred(sql):
                return rows
        return []


class TestCampaignNodeStore:
    def test_illegal_table_name_rejected(self):
        with pytest.raises(ValueError):
            CampaignNodeStore(table="campaign_nodes; DROP TABLE x")

    def test_mark_blocked_reports_affected_rows(self):
        exec_fn = FakeExec([(lambda s: "UPDATE" in s, [["pending", "1"]])])
        store = CampaignNodeStore(exec_fn=exec_fn)
        res = store.mark_node_blocked(42, "no progress")
        assert res.blocked is True and res.affected == 1 and res.previous_status == "pending"
        assert res.reason.startswith("stagnation: ")
        sql, variables = exec_fn.calls[-1]
        assert "NOT IN ('done','failed','blocked')" in sql
        assert variables["node_id"] == "42"

    def test_mark_blocked_terminal_node_reports_zero(self):
        exec_fn = FakeExec([(lambda s: "UPDATE" in s, [["done", "0"]])])
        res = CampaignNodeStore(exec_fn=exec_fn).mark_node_blocked(7, "x")
        assert res.affected == 0 and res.blocked is False and res.new_status == "done"

    def test_mark_blocked_dry_run_wraps_transaction(self):
        exec_fn = FakeExec([(lambda s: "UPDATE" in s, [["pending", "1"]])])
        res = CampaignNodeStore(exec_fn=exec_fn).mark_node_blocked(7, "x", dry_run=True)
        sql, _ = exec_fn.calls[-1]
        assert sql.startswith("BEGIN;") and "ROLLBACK;" in sql
        assert res.dry_run is True and res.blocked is False  # 演练不算真落库

    def test_mark_blocked_errors_are_not_swallowed(self):
        def boom(sql, variables):
            raise DAGError("psql 退出码 1")
        with pytest.raises(DAGError):
            CampaignNodeStore(exec_fn=boom).mark_node_blocked(1, "x")

    def test_event_insert_rejects_missing_plan(self):
        exec_fn = FakeExec([(lambda s: "INSERT INTO campaign_events" in s, [])])
        with pytest.raises(DAGError):
            CampaignNodeStore(exec_fn=exec_fn).record_block_event(1, {"a": 1}, plan_id=None)

    def test_event_insert_uses_allowed_event_type(self):
        exec_fn = FakeExec([(lambda s: "INSERT INTO campaign_events" in s, [["123"]])])
        event_id = CampaignNodeStore(exec_fn=exec_fn).record_block_event(
            1, {"source": "test"}, plan_id=5)
        sql, variables = exec_fn.calls[-1]
        assert event_id == 123
        assert "'block_detected'" in sql
        assert variables["detail"].startswith("{")

    def test_get_node_absent_returns_none(self):
        assert CampaignNodeStore(exec_fn=lambda s, v: []).get_node(9) is None

    def test_dispatch_eligible_uses_dispatcher_predicate(self):
        exec_fn = FakeExec([(lambda s: "FILTER" in s, [["0", "1"]])])
        assert CampaignNodeStore(exec_fn=exec_fn).is_dispatch_eligible(3) is False


class TestRealPostgresWriteBack:
    """真实 PG 端到端（探针表）：无库/无权限则 skip，绝不写生产行。"""

    PROBE = "p148w2_n08_test_probe"

    @pytest.fixture()
    def store(self):
        st = CampaignNodeStore()
        try:
            st._exec("SELECT 1;", {})
        except (DAGError, OSError) as exc:
            pytest.skip(f"postgres unavailable: {exc}")
        st._exec(f"DROP TABLE IF EXISTS {self.PROBE};", {})
        st._exec(f"CREATE TABLE {self.PROBE} AS SELECT id, plan_id, mission_id, name, status, "
                 f"block_reason, consecutive_fails, actual_end, updated_at FROM {st.table} WITH NO DATA;", {})
        st._exec(f"INSERT INTO {self.PROBE} (id, plan_id, name, status, consecutive_fails) "
                 f"VALUES (910000001, 1, 'probe-pending', 'pending', 0), "
                 f"(910000002, 1, 'probe-done', 'done', 0);", {})
        yield CampaignNodeStore(table=self.PROBE)
        st._exec(f"DROP TABLE IF EXISTS {self.PROBE};", {})

    def test_block_pending_and_spare_terminal(self, store):
        assert store.is_dispatch_eligible(910000001) is True
        res = store.mark_node_blocked(910000001, "unit test")
        assert res.blocked is True and res.previous_status == "pending"
        row = store.get_node(910000001)
        assert row["status"] == "blocked" and row["block_reason"].startswith("stagnation: ")
        assert store.is_dispatch_eligible(910000001) is False

        done = store.mark_node_blocked(910000002, "unit test")
        assert done.affected == 0 and done.blocked is False
        assert store.get_node(910000002)["status"] == "done"

    def test_event_write_back_rolls_back(self, store):
        rows = store._exec("SELECT COALESCE(min(id),1) FROM campaign_plans;", {})
        plan_id = int(rows[0][0])
        event_id = store.record_block_event(910000001, {"source": "p148n08_test"},
                                            plan_id=plan_id, dry_run=True)
        assert isinstance(event_id, int) and event_id > 0
        left = store._exec("SELECT count(*) FROM campaign_events "
                           "WHERE detail->>'source' = 'p148n08_test';", {})
        assert int(left[0][0]) == 0  # 回滚，不留痕


# ── 编排器端到端（C1–C3 的整链路） ─────────────────────────────────────────
class TestOrchestratorEndToEnd:
    def test_healthy_progress_never_early_stops(self, worktree, tmp_path):
        orch = ProgressOrchestrator(worktree, node_id=1, output_dir=tmp_path / "runs")
        for i in range(8):
            (worktree / "src" / f"u{i}.py").write_text("x = %d\n" % i, encoding="utf-8")
            res = orch.tick(pytest_result=_pytest(10, 9), criteria={"a": True})
            assert res.get("early_stop_report") is None
        assert res["action"] != ACTION_EARLY_STOP

    def test_stagnation_early_stops_on_third_cycle(self, worktree, tmp_path):
        orch = ProgressOrchestrator(worktree, node_id=2, output_dir=tmp_path / "runs")
        orch.tick(pytest_result=_pytest(), criteria={"a": False})
        res = None
        for _ in range(3):
            res = orch.tick(pytest_result=_pytest(), criteria={"a": False})
        assert res is not None and res["verdict"]["recommendation"] == ACTION_EARLY_STOP
        assert res.get("early_stop_report") is not None
        assert res["snapshot"]["cycle"] == 3

    def test_mtime_only_activity_is_not_progress(self, worktree, tmp_path):
        orch = ProgressOrchestrator(worktree, node_id=4, output_dir=tmp_path / "runs")
        orch.tick(pytest_result=_pytest())
        res = None
        for _ in range(3):
            st = (worktree / "src" / "core.py").stat()
            pm.os.utime(worktree / "src" / "core.py", (st.st_atime, st.st_mtime + 900))
            res = orch.tick(pytest_result=_pytest())
        assert res["snapshot"]["hard_no_progress"] is True
        assert res.get("early_stop_report") is not None

    def test_dag_push_records_block_and_event(self, worktree, tmp_path):
        calls = []

        class FakeStore:
            def mark_node_blocked(self, node_id, reason):
                calls.append(("block", node_id, reason))
                return type("B", (), {"as_dict": lambda self: {"affected": 1, "blocked": True}})()

            def record_block_event(self, node_id, detail, plan_id=None):
                calls.append(("event", node_id, detail["source"]))
                return 555

            def is_dispatch_eligible(self, node_id):
                return False

        orch = ProgressOrchestrator(worktree, node_id=9, output_dir=tmp_path / "runs",
                                    dag_store=FakeStore(), block_on_early_stop=True)
        orch.tick(pytest_result=_pytest())
        res = None
        for _ in range(3):
            res = orch.tick(pytest_result=_pytest())
        assert res["dag"]["event_id"] == 555
        assert res["dag"]["dispatch_eligible_after"] is False
        assert calls[0][0] == "block" and calls[1][0] == "event"

    def test_dag_error_is_reported_not_swallowed(self, worktree, tmp_path):
        class BrokenStore:
            def mark_node_blocked(self, node_id, reason):
                raise DAGError("psql 退出码 1: syntax error")

            def record_block_event(self, *a, **k):  # pragma: no cover
                raise AssertionError("must not be reached")

            def is_dispatch_eligible(self, node_id):  # pragma: no cover
                return None

        orch = ProgressOrchestrator(worktree, node_id=9, output_dir=tmp_path / "runs",
                                    dag_store=BrokenStore(), block_on_early_stop=True)
        orch.tick(pytest_result=_pytest())
        res = None
        for _ in range(3):
            res = orch.tick(pytest_result=_pytest())
        assert res["dag"]["error"].startswith("DAGError")
        assert res["early_stop_report"]["honest_fail"] is True  # 早停证据不因回写失败丢失


# ── CLI ────────────────────────────────────────────────────────────────────
class TestCLI:
    def test_tick_cli_ok(self, worktree, tmp_path, capsys):
        rc = pm.main(["tick", "--worktree", str(worktree), "--out", str(tmp_path / "runs"),
                      "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["snapshot"]["warmup"] is True

    def test_replay_cli_detects_tamper(self, worktree, tmp_path, capsys):
        out = tmp_path / "runs"
        assert pm.main(["tick", "--worktree", str(worktree), "--out", str(out)]) == 0
        journal = out / "snapshots.jsonl"
        text = journal.read_text(encoding="utf-8").replace('"cycle": 0', '"cycle": 5')
        journal.write_text(text, encoding="utf-8")
        assert pm.main(["replay", "--journal", str(journal)]) == 1
        assert "chain_ok=False" in capsys.readouterr().out

    def test_watch_cli_dry_run_interval(self, worktree, tmp_path):
        rc = pm.main(["watch", "--worktree", str(worktree), "--out", str(tmp_path / "runs"),
                      "--max-cycles", "2", "--dry-run-interval", "--pytest-target", "tests"])
        assert rc in (0, 2)


# ── 零领域语义（C7） ───────────────────────────────────────────────────────
class TestDomainNeutrality:
    DENYLIST = REPO_ROOT / "tests/fixtures/domain_term_denylist_p148w2_n08.json"

    @pytest.mark.parametrize("rel", ["ops/taskplane/progress_metrics.py",
                                     "tools/progress_metrics_eval.py",
                                     "tests/test_progress_metrics_p148w2_n08.py"])
    def test_no_domain_terms(self, rel):
        terms = json.loads(self.DENYLIST.read_text(encoding="utf-8"))["terms"]
        text = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
        hits = [t for t in terms if t.lower() in text]
        assert hits == [], f"领域词条泄漏到平台层：{hits}"

    def test_thresholds_are_domain_free_config(self):
        th = ProgressThresholds()
        assert set(th.as_dict()) >= {"no_progress_cycles", "stagnation_window_size",
                                     "stagnation_slope_threshold", "info_gain_min_delta"}

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TP_PROGRESS_NO_PROGRESS_CYCLES", "5")
        assert ProgressThresholds.from_env().no_progress_cycles == 5

    def test_bad_env_raises(self, monkeypatch):
        monkeypatch.setenv("TP_PROGRESS_NO_PROGRESS_CYCLES", "abc")
        with pytest.raises(ValueError):
            ProgressThresholds.from_env()