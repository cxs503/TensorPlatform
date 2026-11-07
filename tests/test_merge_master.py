"""merge_master 判据机测试（零真实网络：全部 provider 用假件）。

覆盖任务书四条判据 + 机制约束：
  * 清算 N 张 awaiting PR：合并或留因，且「留因必有理由码」
  * 边界门负例：构造越界 PR MUST 拒并（词条从词表运行时读取，源码零域词）
  * 合并后任务状态回写正确（merged → 任务 succeeded）
  * 预算闸门、幂等（同 SHA 不重复合并）、崩溃恢复（回写失败重试）
  * 审计链可校验 + 篡改必被发现；回滚规格可执行
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.merge_master import gates as G  # noqa: E402
from tools.merge_master import rollback as RB  # noqa: E402
from tools.merge_master.audit import AuditLog, verify  # noqa: E402
from tools.merge_master.engine import Budget, Reconciler  # noqa: E402
from tools.merge_master.model import (ACTION_LEAVE, ACTION_MERGE,  # noqa: E402
                                      ACTION_SKIP, MissionRecord, PullRequest,
                                      Reason)

REPO = "TensorPlatform"
LEXICON = REPO_ROOT / "data" / "boundary" / "pr_boundary_lexicon.json"


# --------------------------------------------------------------------------- #
# 假 provider
# --------------------------------------------------------------------------- #
class FakeLedger:
    def __init__(self, missions=None, writeback_ok=True):
        self.missions = dict(missions or {})
        self.writeback_ok = writeback_ok
        self.writes = []

    def find(self, repo, pr):
        return self.missions.get("%s#%d" % (repo, int(pr)))

    def writeback(self, mission_id, state, repo, pr, sha, note):
        self.writes.append({"mission_id": mission_id, "state": state,
                            "repo": repo, "pr": pr, "sha": sha, "note": note})
        if not self.writeback_ok:
            return {"ok": False, "status": 500, "error": "boom"}
        m = self.missions.get("%s#%d" % (repo, int(pr)))
        if m is not None and m.status != "succeeded":
            m.status = "succeeded" if state == "merged" else m.status
        return {"ok": True, "status": 200,
                "mission_status": m.status if m else "succeeded"}


class FakeChecks:
    def __init__(self, by_sha=None, baseline=None):
        self.by_sha = dict(by_sha or {})
        self.baseline = dict(baseline or {})

    def for_sha(self, repo, sha):
        return list(self.by_sha.get((repo, sha), []))

    def main_failing(self, repo):
        return list(self.baseline.get(repo, []))


class FakeDiffs:
    def __init__(self, diffs=None):
        self.diffs = dict(diffs or {})

    def diff(self, repo, pr):
        return self.diffs.get((repo, int(pr)))


class FakeMerger:
    def __init__(self, ok=True, commit="deadbeef"):
        self.ok, self.commit, self.calls = ok, commit, []

    def merge(self, repo, pr, sha, method=""):
        self.calls.append({"repo": repo, "pr": pr, "sha": sha,
                           "method": method})
        if not self.ok:
            return {"ok": False, "status": 405, "message": "not mergeable"}
        return {"ok": True, "status": 200, "merge_commit": self.commit + str(pr)}


class FakeQueue:
    def __init__(self, prs):
        self.prs = list(prs)

    def list_awaiting(self):
        return list(self.prs)


# --------------------------------------------------------------------------- #
# 构造件
# --------------------------------------------------------------------------- #
CLEAN_DIFF = """diff --git a/tools/merge_master/engine.py b/tools/merge_master/engine.py
--- a/tools/merge_master/engine.py
+++ b/tools/merge_master/engine.py
@@ -1,1 +1,2 @@
 import json
+PLACEHOLDER = 1
"""


def blocking_term() -> str:
    """从词表运行时取一个阻断词条（源码里不留任何域词字面量）。"""
    data = json.loads(LEXICON.read_text(encoding="utf-8"))
    for term in data["terms"]:
        for val in term["values"]:
            if term["kind"] == "word" and val.isascii() and " " not in val:
                return val
    raise AssertionError("no blocking term available")


def bad_diff() -> str:
    t = blocking_term()
    return ("diff --git a/backend/api/sneaky.py b/backend/api/sneaky.py\n"
            "--- a/backend/api/sneaky.py\n"
            "+++ b/backend/api/sneaky.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x = 1\n"
            "+VALUE = \"%s_payload\"\n" % t)


def mission(pid, *, accepted=True, status="awaiting_pr_merge",
            checks="pytest tests/test_x.py -q => 12 passed", pr=1,
            decided_by="eco_admin"):
    return MissionRecord(mission_id=pid, status=status, accepted=accepted,
                         decided_by=decided_by, checks_text=checks,
                         pr_number=pr, repo=REPO)


def green_checks():
    return [{"name": "backend suite", "status": "completed",
             "conclusion": "success"},
            {"name": "pytest main", "status": "completed",
             "conclusion": "success"}]


def mk_pr(num, *, sha=None, mergeable=True, state="open", merged=False):
    return PullRequest(repo=REPO, number=num, head_sha=sha or ("sha%d" % num),
                       branch="agent/m%d" % num, state=state, merged=merged,
                       mergeable=mergeable, mergeable_state="clean")


def make(path, prs, missions, *, checks=None, diffs=None, merger=None,
         ledger=None, dry_run=False, budget=None):
    audit = AuditLog(str(Path(path) / "audit.jsonl"))
    ledger = ledger or FakeLedger(missions)
    by_sha = checks if checks is not None else {
        (REPO, pr.head_sha): green_checks() for pr in prs}
    d = diffs if diffs is not None else {(REPO, pr.number): CLEAN_DIFF
                                         for pr in prs}
    rec = Reconciler(queue=FakeQueue(prs), ledger=ledger,
                     checks=FakeChecks(by_sha), diffs=FakeDiffs(d),
                     merger=merger or FakeMerger(), audit=audit,
                     state_path=str(Path(path) / "state.json"),
                     budget=budget or Budget(), dry_run=dry_run)
    return rec, ledger, audit


# --------------------------------------------------------------------------- #
# 1. 判据全绿 → 合并 + 回写 + 回滚规格
# --------------------------------------------------------------------------- #
def test_green_pr_merges_writes_back_and_records_rollback(tmp_path):
    pr = mk_pr(101)
    rec, ledger, audit = make(tmp_path, [pr], {"%s#101" % REPO: mission(9001)})
    rep = rec.run_cycle()
    assert rep.counts() == {"total": 1, "merged": 1, "left": 0, "skipped": 0}
    d = rep.decisions[0]
    assert d.action == ACTION_MERGE and d.merge_commit == "deadbeef101"
    assert ledger.writes and ledger.writes[0]["state"] == "merged"
    assert ledger.writes[0]["mission_id"] == 9001
    assert d.mission_status == "succeeded"
    assert d.rollback["kind"] == "revert_merge"
    assert any("revert -m 1" in s for s in d.rollback["steps"])
    assert [g.gate for g in d.gates] == [G.G1, G.G2, G.G3, G.G4, G.G5]
    assert verify(str(tmp_path / "audit.jsonl"))["ok"] is True


# --------------------------------------------------------------------------- #
# 2. 十二张 awaiting PR 一轮清跑：合并或留因（留因必带理由码）
# --------------------------------------------------------------------------- #
def test_twelve_awaiting_prs_all_cleared_with_action_or_reason(tmp_path):
    prs, missions, by_sha, diffs = [], {}, {}, {}
    # 3 张应当合并（不同 SHA，避开幂等/预算）
    for n in (201, 202, 203):
        pr = mk_pr(n)
        prs.append(pr)
        missions["%s#%d" % (REPO, n)] = mission(9100 + n, pr=n)
        by_sha[(REPO, pr.head_sha)] = green_checks()
        diffs[(REPO, n)] = CLEAN_DIFF
    # 9 张各类留因
    bad_cases = {
        204: ("no_mission", None, green_checks(), CLEAN_DIFF),
        205: ("acceptance", mission(9205, accepted=False), green_checks(),
              CLEAN_DIFF),
        206: ("quality", mission(9206, pr=206),
              [{"name": "backend suite", "status": "completed",
                "conclusion": "failure"}], CLEAN_DIFF),
        207: ("tests", mission(9207, pr=207, checks=""),
              [{"name": "lint", "status": "completed",
                "conclusion": "success"}], CLEAN_DIFF),
        208: ("conflict", mission(9208, pr=208), green_checks(), CLEAN_DIFF),
        209: ("boundary", mission(9209, pr=209), green_checks(), bad_diff()),
        210: ("no_mission", None, green_checks(), CLEAN_DIFF),
        211: ("quality", mission(9211, pr=211),
              [{"name": "frontend", "status": "completed",
                "conclusion": "cancelled"}], CLEAN_DIFF),
        212: ("boundary", mission(9212, pr=212), green_checks(), bad_diff()),
    }
    for n, (_kind, m, chk, df) in bad_cases.items():
        pr = mk_pr(n, mergeable=False if n == 208 else True)
        prs.append(pr)
        if m is not None:
            missions["%s#%d" % (REPO, n)] = m
        by_sha[(REPO, pr.head_sha)] = chk
        diffs[(REPO, n)] = df
    rec, _ledger, _audit = make(tmp_path, prs, missions, checks=by_sha,
                                diffs=diffs, budget=Budget(max_merges=5,
                                                           max_mutations=20))
    rep = rec.run_cycle()
    c = rep.counts()
    assert c["total"] == 12
    assert c["merged"] == 3 and c["left"] == 9
    for d in rep.leaves():
        assert d.reasons, "留因的 PR 必须带理由码: %s" % d.pr.key
    leaves = {d.pr.number: d.reasons for d in rep.leaves()}
    assert Reason.NO_MISSION in leaves[204]
    assert Reason.ACCEPTANCE_MISSING in leaves[205]
    assert Reason.QUALITY_RED in leaves[206]
    assert Reason.TESTS_MISSING in leaves[207]
    assert Reason.CONFLICT in leaves[208]
    assert Reason.BOUNDARY_FAIL in leaves[209]
    assert Reason.BOUNDARY_FAIL in leaves[212]
    assert verify(str(tmp_path / "audit.jsonl"))["ok"] is True


# --------------------------------------------------------------------------- #
# 3. 边界门负例：越界 PR MUST 拒并（且根本不触发 merge 动作）
# --------------------------------------------------------------------------- #
def test_boundary_negative_pr_is_rejected_and_never_merged(tmp_path):
    pr = mk_pr(301)
    merger = FakeMerger()
    rec, ledger, _ = make(tmp_path, [pr],
                          {"%s#301" % REPO: mission(9301, pr=301)},
                          diffs={(REPO, 301): bad_diff()}, merger=merger)
    rep = rec.run_cycle()
    d = rep.decisions[0]
    assert d.action == ACTION_LEAVE
    assert Reason.BOUNDARY_FAIL in d.reasons
    assert merger.calls == [] and ledger.writes == []
    g5 = [g for g in d.gates if g.gate == G.G5][0]
    assert g5.ok is False and g5.evidence["counts"]["violations"] >= 1
    assert g5.evidence["lexicon"]["sha256"]


def test_boundary_whitelisted_path_still_judged_as_code(tmp_path):
    """白名单目录里塞可执行脚本仍按代码扫描（不得成为越界后门）。"""
    t = blocking_term()
    diff = ("diff --git a/docs/reports/x.py b/docs/reports/x.py\n"
            "--- a/docs/reports/x.py\n"
            "+++ b/docs/reports/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " pass\n"
            "+PAYLOAD = \"%s_box\"\n" % t)
    out = G.gate_boundary(diff)
    assert out.ok is False
    assert out.evidence["counts"]["violations"] >= 1


def test_boundary_gate_fails_closed_when_diff_unavailable():
    out = G.gate_boundary(None)
    assert out.ok is False
    assert out.evidence["reason"] == Reason.BOUNDARY_ERROR


# --------------------------------------------------------------------------- #
# 4. 相对红口径：main 上早已红的检查不阻断（不把别人的红算到本 PR）
# --------------------------------------------------------------------------- #
def test_baseline_red_not_blocking_but_new_red_is():
    checks = [{"name": "contract tests", "status": "completed",
               "conclusion": "failure"}]
    ok = G.gate_quality(checks, ["contract tests"])
    assert ok.ok is True and ok.evidence["new_red"] == []
    bad = G.gate_quality(checks + [{"name": "backend suite",
                                    "status": "completed",
                                    "conclusion": "failure"}],
                         ["contract tests"])
    assert bad.ok is False and bad.evidence["new_red"] == ["backend suite"]


def test_pending_check_blocks_merge(tmp_path):
    pr = mk_pr(401)
    checks = {(REPO, pr.head_sha): [{"name": "backend suite",
                                     "status": "in_progress",
                                     "conclusion": None}]}
    rec, _l, _a = make(tmp_path, [pr], {"%s#401" % REPO: mission(9401, pr=401)},
                       checks=checks,
                       diffs={(REPO, 401): CLEAN_DIFF})
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_LEAVE and Reason.QUALITY_RED in d.reasons


# --------------------------------------------------------------------------- #
# 5. 预算闸门：超限的 PR 留在队列并附 budget_exhausted
# --------------------------------------------------------------------------- #
def test_budget_caps_merges_and_requeues_rest(tmp_path):
    prs = [mk_pr(500 + i) for i in range(5)]
    missions = {"%s#%d" % (REPO, p.number): mission(9500 + p.number,
                                                    pr=p.number) for p in prs}
    rec, _l, _a = make(tmp_path, prs, missions,
                       budget=Budget(max_merges=2, max_mutations=8))
    rep = rec.run_cycle()
    assert rep.counts()["merged"] == 2
    left = rep.leaves()
    assert len(left) == 3
    assert all(Reason.BUDGET in d.reasons for d in left)
    assert rec.state["queue"], "预算挡下的 PR 必须进队列（不丢）"


# --------------------------------------------------------------------------- #
# 6. plan 模式：判据全绿也不动远端
# --------------------------------------------------------------------------- #
def test_dry_run_never_merges_or_writes_back(tmp_path):
    pr = mk_pr(601)
    merger = FakeMerger()
    rec, ledger, _ = make(tmp_path, [pr], {"%s#601" % REPO: mission(9601, pr=601)},
                          merger=merger, dry_run=True)
    rep = rec.run_cycle()
    d = rep.decisions[0]
    assert d.action == ACTION_MERGE and Reason.DRY_RUN in d.reasons
    assert merger.calls == [] and ledger.writes == []


# --------------------------------------------------------------------------- #
# 7. 幂等：同 head SHA 第二轮不重复合并
# --------------------------------------------------------------------------- #
def test_second_cycle_is_idempotent(tmp_path):
    pr = mk_pr(701)
    merger = FakeMerger()
    rec, ledger, _ = make(tmp_path, [pr], {"%s#701" % REPO: mission(9701, pr=701)},
                          merger=merger)
    rec.run_cycle()
    rec2, _l2, _a2 = make(tmp_path, [pr],
                          {"%s#701" % REPO: mission(9701, pr=701)},
                          merger=merger)
    rep2 = rec2.run_cycle()
    assert rep2.decisions[0].action == ACTION_SKIP
    assert Reason.ALREADY_DECIDED in rep2.decisions[0].reasons
    assert len(merger.calls) == 1 and len(ledger.writes) == 1


# --------------------------------------------------------------------------- #
# 8. 崩溃恢复：回写失败 → 落 writeback_pending；下一轮按 merged 事实补写
# --------------------------------------------------------------------------- #
def test_writeback_failure_is_retried_on_next_cycle(tmp_path):
    pr = mk_pr(801)
    missions = {"%s#801" % REPO: mission(9801, pr=801)}
    ledger = FakeLedger(missions, writeback_ok=False)
    rec, _l, _a = make(tmp_path, [pr], missions, ledger=ledger)
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_MERGE
    assert Reason.WRITEBACK_FAILED in d.reasons
    assert "%s#801" % REPO in rec.state["writeback_pending"]

    ledger.writeback_ok = True
    merged = mk_pr(801, state="closed", merged=True)
    rec2, _l2, _a2 = make(tmp_path, [merged], missions, ledger=ledger)
    d2 = rec2.run_cycle().decisions[0]
    assert d2.action == ACTION_SKIP and Reason.ALREADY_MERGED in d2.reasons
    assert ledger.writes[-1]["state"] == "merged"


def test_merge_api_failure_leaves_pr_in_queue(tmp_path):
    pr = mk_pr(811)
    merger = FakeMerger(ok=False)
    rec, ledger, _ = make(tmp_path, [pr],
                          {"%s#811" % REPO: mission(9811, pr=811)},
                          merger=merger)
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_LEAVE and Reason.MERGE_FAILED in d.reasons
    assert ledger.writes == [] and rec.state["queue"]


def test_engine_survives_provider_exception(tmp_path):
    class Boom:
        def find(self, repo, pr):
            raise RuntimeError("ledger down")

        def writeback(self, *a, **k):
            raise RuntimeError("down")

    pr = mk_pr(821)
    rec, _l, _a = make(tmp_path, [pr], {})
    rec.ledger = Boom()
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_LEAVE and d.reasons


# --------------------------------------------------------------------------- #
# 9. 审计链：可校验 + 篡改必被发现
# --------------------------------------------------------------------------- #
def test_audit_chain_detects_tampering(tmp_path):
    pr = mk_pr(901)
    rec, _l, _a = make(tmp_path, [pr], {"%s#901" % REPO: mission(9901, pr=901)})
    rec.run_cycle()
    path = str(tmp_path / "audit.jsonl")
    ok = verify(path)
    assert ok["ok"] is True and ok["records"] >= 4
    lines = open(path, encoding="utf-8").read().splitlines()
    rec0 = json.loads(lines[2])
    rec0["payload"]["action"] = "SKIP"
    lines[2] = json.dumps(rec0, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    bad = verify(path)
    assert bad["ok"] is False and bad["first_bad"]["line"] == 3


# --------------------------------------------------------------------------- #
# 10. 回滚路径：规格可解析可执行计划
# --------------------------------------------------------------------------- #
def test_rollback_spec_plan_and_validation(tmp_path):
    pr = mk_pr(1001)
    rec, _l, _a = make(tmp_path, [pr],
                       {"%s#1001" % REPO: mission(10001, pr=1001)})
    d = rec.run_cycle().decisions[0]
    spec_path = tmp_path / "rollback.json"
    spec_path.write_text(json.dumps(d.rollback, ensure_ascii=False),
                         encoding="utf-8")
    spec = RB.load(str(spec_path))
    steps = RB.plan(spec)
    assert steps and all(isinstance(s, str) for s in steps)
    assert any(spec["merge_commit"] in s for s in steps)
    broken = dict(spec)
    broken.pop("merge_commit")
    with pytest.raises(ValueError):
        bad = tmp_path / "broken.json"
        bad.write_text(json.dumps(broken), encoding="utf-8")
        RB.load(str(bad))


def test_acceptance_gate_accepts_report_only_record():
    m = MissionRecord(mission_id=1, status="succeeded", accepted=True,
                      decided_by="", report_id=4242)
    assert G.gate_acceptance(m).ok is True
    m2 = MissionRecord(mission_id=2, status="succeeded", accepted=False,
                       decided_by="")
    assert G.gate_acceptance(m2).ok is False


def test_cycle_report_json_is_serializable(tmp_path):
    pr = mk_pr(1101)
    rec, _l, _a = make(tmp_path, [pr],
                       {"%s#1101" % REPO: mission(11001, pr=1101)})
    rep = rec.run_cycle()
    blob = json.dumps(rep.to_json(), ensure_ascii=False)
    assert "tp.merge_master.cycle/v1" in blob
    assert json.loads(blob)["counts"]["merged"] == 1