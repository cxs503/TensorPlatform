"""sediment_line 回归门禁：判定/幂等/模板/核验/配额。

设计要点：全部用**合成任务夹具**（假 repo 名、假 mission id），零真实平台依赖，
零领域语义——本套件证明的是「机制正确」，不是「这次跑出了什么数字」。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import sediment_line as S  # noqa: E402


def _mission(mid=101, repo="FakeRepo", *, headline="", checks="", goal="",
             files=(), report_id=None, pr_state=None, branch=None):
    result = {
        "headline": headline,
        "checks": checks,
        "model": "fake-model-v1",
        "evidence": {"branch": branch, "summary": {"headline": headline, "checks": checks}},
        "changed_files": list(files),
        "on_disk_changes": list(files),
    }
    if report_id:
        result["report_id"] = report_id
    if pr_state:
        result["pr_gate"] = {"pr": 42, "state": pr_state, "repo": repo}
    return {"id": mid, "repo": repo, "goal": goal or f"synthetic goal for m{mid}",
            "status": "succeeded", "finished_at": "2026-11-09T00:00:00+00:00",
            "result": result}


def _hit_mission(mid=101):
    """一条典型「可沉淀」单：定量误差 + 外部基准 + 验证工件。"""
    return _mission(
        mid,
        headline="解析锚对齐：恢复误差 max 1.06%（判据<3% 通过）",
        checks="pytest tests/test_anchor_verify.py -q → 26 passed；Ishigami 解析基准恢复误差 max 1.06%",
        files=[f"/root/tp-agent/work/m{mid}/tests/test_anchor_verify.py",
               f"/root/tp-agent/work/m{mid}/docs/anchor_verification_report.md"],
        pr_state="merged",
    )


# ───────────────────────── 判定：正向 ─────────────────────────
class TestClassifierPositive:
    def test_quantitative_validation_hits(self):
        d = S.classify(_hit_mission())
        assert d.hit, d.reason
        assert "physical_validation" in d.families
        assert d.exhibits is True

    def test_falsification_is_strong(self):
        m = _mission(102, headline="假设被否证：粗网格标定不可外推（反例见 artifacts/counter_example.json）",
                     checks="2 项 passed；falsification 记录已入库",
                     files=["/root/tp-agent/work/m102/tests/test_negative_result.py"])
        d = S.classify(m)
        assert d.hit and "falsification" in d.families
        assert "strong" in d.reason

    def test_verdict_family(self):
        m = _mission(103, headline="QR 定谳：C7 判据翻绿（独立席复核）",
                     checks="3 项 passed；仲裁结论=采纳",
                     files=["/root/tp-agent/work/m103/tests/test_c7_gate.py"])
        d = S.classify(m)
        assert d.hit and "verdict" in d.families

    def test_root_cause_family(self):
        m = _mission(104, headline="根因=推送门黑名单滤除 artifacts/，已加回归护栏",
                     checks="5 项 passed",
                     files=["/root/tp-agent/work/m104/tests/test_pusher_regression.py"])
        d = S.classify(m)
        assert d.hit and "root_cause" in d.families

    def test_benchmark_requires_artifact(self):
        # 只有「基准/baseline」字样 + 普通测试文件 → 不收；必须留下基准类工件或声明基准工装
        base = dict(headline="benchmark 套件跑完，baseline 稳定", checks="12 项 passed")
        with_art = _mission(105, files=["/root/tp-agent/work/m105/tests/benchmarks/bench_case.py"],
                            **base)
        without_art = _mission(106, files=["/root/tp-agent/work/m106/tests/test_plain.py"], **base)
        assert "benchmark_tooling" in S.classify(with_art).families
        assert "benchmark_tooling" not in S.classify(without_art).families


# ───────────────────────── 判定：负向（防噪） ─────────────────────────
class TestClassifierNegative:
    def test_infra_chore_skipped(self):
        m = _mission(201, goal="升级依赖并修 lint 与 pre-commit 配置",
                     headline="lint 修完，依赖升级到 2.3.1",
                     checks="12 项 passed；docker 镜像重建成功",
                     files=["/root/tp-agent/work/m201/requirements.txt"])
        d = S.classify(m)
        assert not d.hit
        assert d.reason.startswith("noise_dominant")

    def test_pure_prose_skipped(self):
        m = _mission(202, goal="重写 README 与文案排版",
                     headline="README 重写完成，措辞统一",
                     checks="", files=["/root/tp-agent/work/m202/README.md"])
        assert not S.classify(m).hit

    def test_no_artifact_skipped(self):
        m = _mission(203, headline="验证通过，误差 0.8%，基准一致", checks="",
                     files=[])
        d = S.classify(m)
        assert not d.hit
        assert d.reason.startswith("no_traceable_evidence")

    def test_self_loop_skipped(self):
        m = _hit_mission(204)
        m["goal"] = f"{S.MARKER} 被动进水口·知识沉淀提取单（源 mission m101）…"
        d = S.classify(m)
        assert not d.hit and d.reason.startswith("self_loop")

    def test_goal_boilerplate_alone_does_not_hit(self):
        """战役 goal 里的「验证/基准/判据」模板词不得单独触发派单（防噪核心）。"""
        m = _mission(205, goal="【战役 DAG 节点】物理目标：实现验证面；完成标准=误差<3%，"
                              "基准对标，判据机检，验证报告入账。",
                     headline="交付骨架文件", checks="", files=[])
        assert not S.classify(m).hit


# ───────────────────────── goal 模板 ─────────────────────────
class TestGoalTemplate:
    def test_template_carries_four_contracts_and_provenance(self):
        m = _hit_mission(301)
        d = S.classify(m)
        goal = S.build_goal(m, d)
        assert goal.startswith(S.MARKER)
        assert S.task_key_for(301) in goal
        assert "source_mission_id: 301" in goal
        assert "source_repo: FakeRepo" in goal
        for schema, _ in S.FOUR_CONTRACTS:
            assert schema in goal
        assert "epistemic_status=draft" in goal
        assert "生产者不得自审" in goal
        assert re.search(r"≥\s*3\s*条四契约知识", goal)
        # 引用逐字来自源单（不臆造）
        assert "[checks]" in goal
        assert "26 passed" in goal

    def test_missing_sha_is_pending_not_fabricated(self):
        m = _hit_mission(302)
        d = S.classify(m)
        goal = S.build_goal(m, d)
        sha_line = re.search(r"^- source_head_sha: (.+)$", goal, flags=re.MULTILINE).group(1)
        assert "PENDING" not in sha_line or re.search(r"[0-9a-f]{40}", sha_line) is None
        assert "rev-parse" in goal  # 给出解析命令
        assert not re.search(r"source_head_sha: [0-9a-f]{7,}", goal) or \
            re.search(r"source_head_sha: [0-9a-f]{40,64}", goal), "不许写短假 sha"

    def test_resolved_sha_is_used_verbatim(self):
        m = _hit_mission(303)
        sha = "a" * 40
        m["result"]["evidence"]["head_commit"] = sha
        goal = S.build_goal(m, S.classify(m))
        assert f"source_head_sha: {sha}" in goal

    def test_goal_length_within_api_limit(self):
        m = _hit_mission(304)
        m["result"]["changed_files"] = [f"/root/tp-agent/work/m304/f{i}.py" for i in range(60)]
        goal = S.build_goal(m, S.classify(m))
        assert len(goal) <= 12000


# ───────────────────────── 幂等 ─────────────────────────
class FakeAPI:
    def __init__(self):
        self.created: dict[str, dict] = {}
        self.posts: list[dict] = []
        self.next_id = 9000

    def post(self, path, body):
        self.posts.append(body)
        key = body.get("task_key")
        existing = self.created.get(key)
        if existing:
            identical = (existing["goal"] == body["goal"] and existing["repo"] == body["repo"])
            if identical:
                return 200, {"id": existing["id"], "idempotent": True}
            return 409, "task_key already identifies a different task"
        self.next_id += 1
        rec = {"id": self.next_id, **body}
        self.created[key] = rec
        return 200, {"id": rec["id"]}

    def get(self, path, params=None):
        raise AssertionError("not used")


class TestIdempotency:
    def test_rerun_derives_nothing_new(self, tmp_path):
        missions = [_hit_mission(401), _hit_mission(402)]
        api = FakeAPI()
        led = S.Ledger(tmp_path)
        first = S.run_round(api, missions=missions, ledger=led, log_file=None, dry_run=False,
                            max_per_run=10, daily_cap=10, per_repo_daily=10)
        assert first["derived"] == 2
        second = S.run_round(api, missions=missions, ledger=led, log_file=None, dry_run=False,
                             max_per_run=10, daily_cap=10, per_repo_daily=10)
        assert second["derived"] == 0
        assert second["already_derived"] == 2
        assert len(api.posts) == 2, "幂等：第二轮不得再 POST"

    def test_task_key_stable(self):
        assert S.task_key_for(777) == "tk-sediment-m777"
        assert re.match(r"^[a-zA-Z0-9_.:-]+$", S.task_key_for(777))

    def test_server_side_conflict_recorded(self, tmp_path):
        api = FakeAPI()
        api.created[S.task_key_for(403)] = {"id": 1, "goal": "different", "repo": "Other"}
        summary = S.run_round(api, missions=[_hit_mission(403)], ledger=S.Ledger(tmp_path),
                              log_file=None, dry_run=False, max_per_run=5,
                              daily_cap=5, per_repo_daily=5)
        assert summary["derived"] == 0 and summary["failed"] == 1
        assert summary["dispatched"][0]["status"] == "conflict"


# ───────────────────────── 配额 ─────────────────────────
class TestQuota:
    def test_daily_cap_blocks_and_defers(self, tmp_path):
        missions = [_hit_mission(500 + i) for i in range(6)]
        api = FakeAPI()
        summary = S.run_round(api, missions=missions, ledger=S.Ledger(tmp_path), log_file=None,
                              dry_run=False, max_per_run=10, daily_cap=2, per_repo_daily=10)
        assert summary["derived"] == 2
        assert summary["deferred_over_cap"] == 4
        assert {d["why"] for d in summary["deferred"]} == {"round_quota"}

    def test_per_repo_cap(self, tmp_path):
        missions = [_hit_mission(600 + i) for i in range(4)]
        api = FakeAPI()
        summary = S.run_round(api, missions=missions, ledger=S.Ledger(tmp_path), log_file=None,
                              dry_run=False, max_per_run=10, daily_cap=10, per_repo_daily=1)
        assert summary["derived"] == 1
        assert any(d["why"] == "repo_daily_quota" for d in summary["deferred"])

    def test_strong_families_dispatched_first(self, tmp_path):
        val = _hit_mission(700)
        fals = _mission(701, headline="假设被否证：反例已入库", checks="2 项 passed",
                        files=["/root/tp-agent/work/m701/tests/test_negative.py"])
        api = FakeAPI()
        summary = S.run_round(api, missions=[val, fals], ledger=S.Ledger(tmp_path), log_file=None,
                              dry_run=False, max_per_run=1, daily_cap=1, per_repo_daily=5)
        assert summary["dispatched"][0]["source_mission_id"] == 701

    def test_decisions_logged_only_for_new_missions(self, tmp_path):
        """高水位：5min 守护各轮窗口高度重叠，判定账不得每轮重写整个窗口。"""
        missions = [_hit_mission(850), _hit_mission(851), _hit_mission(852)]
        api = FakeAPI()
        led = S.Ledger(tmp_path)
        first = S.run_round(api, missions=missions, ledger=led, log_file=None, dry_run=False,
                            max_per_run=1, daily_cap=10, per_repo_daily=10)
        assert first["decisions_logged"] == 3
        second = S.run_round(api, missions=missions, ledger=led, log_file=None, dry_run=False,
                             max_per_run=1, daily_cap=10, per_repo_daily=10)
        assert second["decisions_logged"] == 0
        lines = (tmp_path / "decisions.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3

    def test_dry_run_writes_no_state(self, tmp_path):
        api = FakeAPI()
        led = S.Ledger(tmp_path)
        summary = S.run_round(api, missions=[_hit_mission(800)], ledger=led,
                              log_file=None, dry_run=True, max_per_run=5,
                              daily_cap=5, per_repo_daily=5)
        assert summary["dry_run_decisions"] == 1
        assert led.state["derived"] == {}
        assert api.posts == []


# ───────────────────────── 取窗 ─────────────────────────
class WindowAPI:
    def __init__(self, items):
        self.items = items

    def get(self, path, params=None):
        off = int((params or {}).get("offset", 0))
        lim = int((params or {}).get("limit", 200))
        return {"items": self.items[off:off + lim], "total": len(self.items)}


class TestWindow:
    def test_stops_at_cutoff(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
        items = [{"id": 1, "finished_at": "2026-11-09T00:00:00+00:00", "result": {}}] * 3 + \
                [{"id": 2, "finished_at": "2026-10-01T00:00:00+00:00", "result": {}}]
        got, meta = S.fetch_succeeded(WindowAPI(items), days=7, now=now, page=3)
        assert len(got) == 3
        assert meta["stopped"] == "cutoff"

    def test_paging_accumulates(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
        items = [{"id": i, "finished_at": "2026-11-09T00:00:00+00:00", "result": {}}
                 for i in range(7)]
        got, meta = S.fetch_succeeded(WindowAPI(items), days=7, now=now, page=3)
        assert len(got) == 7 and meta["stopped"] == "total"


# ───────────────────────── 抽样核验 ─────────────────────────
class VerifyAPI:
    """回放父/子 mission，用真实 build_goal 产物当子单 goal。"""

    def __init__(self, parent, child_goal=None):
        self.parent = parent
        self.child_goal = child_goal

    def get(self, path, params=None):
        if path.endswith(str(self.parent["id"])):
            return self.parent
        return {"id": 999, "goal": self.child_goal, "result": {}}


class TestVerify:
    def _ledger(self, tmp_path, parent):
        led = S.Ledger(tmp_path)
        goal = S.build_goal(parent, S.classify(parent))
        led.record(parent["id"], {"task_key": S.task_key_for(parent["id"]),
                                  "extraction_mission_id": 999,
                                  "derived_at": "2026-11-09T00:00:00+00:00",
                                  "source_repo": parent["repo"],
                                  "families": S.classify(parent).families})
        return led, goal

    def test_faithful_goal_passes(self, tmp_path):
        parent = _hit_mission(901)
        led, goal = self._ledger(tmp_path, parent)
        res = S.verify_derived(VerifyAPI(parent, goal), led, sample=1)
        assert res["all_pass"], res["items"]

    def test_tampered_quote_fails(self, tmp_path):
        parent = _hit_mission(902)
        led, goal = self._ledger(tmp_path, parent)
        tampered = goal.replace("26 passed", "999 passed")
        res = S.verify_derived(VerifyAPI(parent, tampered), led, sample=1)
        assert not res["all_pass"]
        assert "quotes_verbatim" in res["items"][0]["failures"]

    def test_fabricated_path_fails(self, tmp_path):
        parent = _hit_mission(903)
        led, goal = self._ledger(tmp_path, parent)
        fake = goal.replace("> [file]", "> [file] /nowhere/fabricated_file.md\n> [file]", 1)
        res = S.verify_derived(VerifyAPI(parent, fake), led, sample=1)
        assert "files_traceable" in res["items"][0]["failures"]

    def test_dropped_contract_fails(self, tmp_path):
        parent = _hit_mission(904)
        led, goal = self._ledger(tmp_path, parent)
        broken = goal.replace("schemas/provenance-record.schema.json", "nope.json")
        res = S.verify_derived(VerifyAPI(parent, broken), led, sample=1)
        assert "four_contracts_present" in res["items"][0]["failures"]

    def test_empty_ledger_reports_failure(self, tmp_path):
        res = S.verify_derived(VerifyAPI(_hit_mission(905), "x"), S.Ledger(tmp_path), sample=1)
        assert res["all_pass"] is False


# ───────────────────────── 账本 & 报告 ─────────────────────────
class TestLedgerAndReport:
    def test_ledger_is_append_only_jsonl(self, tmp_path):
        led = S.Ledger(tmp_path)
        led.append("decisions.jsonl", [{"mission_id": 1}, {"mission_id": 2}])
        led.append("decisions.jsonl", [{"mission_id": 3}])
        lines = (tmp_path / "decisions.jsonl").read_text().strip().splitlines()
        assert [json.loads(x)["mission_id"] for x in lines] == [1, 2, 3]

    def test_state_roundtrip(self, tmp_path):
        led = S.Ledger(tmp_path)
        led.record(11, {"task_key": "tk-sediment-m11", "extraction_mission_id": 5})
        again = S.Ledger(tmp_path)
        assert again.already_derived(11)["extraction_mission_id"] == 5
        assert again.already_derived(12) is None

    def test_report_contains_counts_and_skips(self, tmp_path):
        led = S.Ledger(tmp_path)
        summary = {"at": "2026-11-10T00:00:00+00:00", "days": 7, "dry_run": True,
                   "window": {"cutoff": "x", "stopped": "cutoff", "scanned_pages": 1},
                   "scanned": 10, "hits": 2, "new_hits": 2, "already_derived": 0,
                   "derived": 0, "dry_run_decisions": 2, "failed": 0, "deferred_over_cap": 0,
                   "family_counts": {"verdict": 1},
                   "skip_reason_counts": {"noise_dominant": 8},
                   "dispatched": [{"source_mission_id": 1, "source_repo": "FakeRepo",
                                   "families": ["verdict"], "mission_id": "dry_run",
                                   "task_key": "tk-sediment-m1"}],
                   "deferred": []}
        md = S.render_report(summary, {"sample": 1, "all_pass": True, "items": [
            {"source_mission_id": 1, "extraction_mission_id": 2,
             "task_key": "tk-sediment-m1", "failures": []}]}, led)
        assert "命中过滤器" in md and "noise_dominant" in md and "PASS" in md


# ───────────────────────── 平台铁律：零领域语义 ─────────────────────────
class TestZeroDomainSemantics:
    def test_tool_is_free_of_domain_vocabulary(self):
        """平台铁律：过滤器只准出现科研过程词。词表复用平台唯一真源，不在这里硬编码域词。"""
        from tools.evidence_domain_scan import load_lexicon, scan_paths

        lex = load_lexicon()
        assert lex.get("cjk_terms") or lex.get("ascii_terms"), "词表为空 → 本门是空转"
        report = scan_paths(["tools/sediment_line.py", "tools/test_sediment_line.py",
                             "ops/sediment_line_loop.sh"], lexicon=lex)
        hits = report["hits"]
        assert hits == [], f"sediment_line 出现域词：{hits[:5]}"

    def test_families_are_process_words(self):
        for fam, meta in S.FAMILIES.items():
            assert re.match(r"^[a-z_]+$", fam)
            assert meta["label"] and isinstance(meta["weight"], int)
            assert 0 < meta["weight"] <= 10
