"""交付面卫生**成熟度自证**的用例（全在临时合成仓库里跑，零领域语义）。

五个测试面 = 节点的五条机器判据各一组：
(a) 覆盖率与指纹  (b) 抽样独立复核  (c) 积压/C 零丢失  (d) 门负控  (e) ref 集合差集
每个用例都断言"机制在缺陷下会红"（负控），否则测例只是把代码跑一遍。
"""
from __future__ import annotations

import copy
import json
import os
import subprocess

import pytest

from tools.delivery_hygiene import gate as gate_mod
from tools.delivery_hygiene import gitio, ledger as ledger_mod, maturity


# ── 合成仓库工具 ──────────────────────────────────────────────────────────────
@pytest.fixture()
def repo(tmp_path):
    path = str(tmp_path / "repo")
    maturity._synth_repo(path)
    maturity._commit(path, "README.md", "base\n")
    return path


def _mk(repo, branch, files, when=None):
    gitio.run_git(repo, "checkout", "-q", "-b", branch, "main")
    for name, text in files.items():
        maturity._commit(repo, name, text, when=when)
    gitio.run_git(repo, "checkout", "-q", "main")
    return gitio.rev_parse(repo, branch)


@pytest.fixture()
def ledger(repo):
    """三条分支覆盖三种形态：A(补丁等价) / C(独有) / D(已在历史)。"""
    _mk(repo, "agent/dup", {"dup.txt": "same\n"})
    # main 里同一处改动（换 message/时间保证 commit 不同、patch-id 相同）⇒ A
    maturity._commit(repo, "dup.txt", "same\n", msg="add dup.txt via squash-merge")
    uid = _mk(repo, "agent/work-1", {"w1/a.txt": "a\n"})  # 独有 ⇒ C
    gitio.run_git(repo, "branch", "agent/orphan", "main")  # tip 已在 main ⇒ D
    led = maturity._scan(repo, "main", "agent/*")
    led["dispositions"] = []
    led["disposition_counts"] = {}
    led["content_sha256"] = maturity.content_sha256(led)
    # 手工补一条处置计划与补单（scan CLI 才写，这里内联出等价物）
    from tools.delivery_hygiene import dispose
    plan = dispose.plan_dispositions(led, date_tag="2026-11-11")
    led["dispositions"] = plan
    led["disposition_counts"] = dispose.summarize_plan(plan)
    led["tickets"] = ledger_mod.build_tickets(led["rows"], led["repo"], "main")
    return led


# ── (a) 覆盖率 + 指纹 ─────────────────────────────────────────────────────────
def test_coverage_full_and_sha256_consistent(ledger):
    a = maturity.measure_coverage(ledger)
    assert a["total_rows"] == 3
    assert a["coverage"] == 1.0 and a["pass"] is True
    assert a["sha256_consistent"] is True
    classes = {r["branch"]: r["class"] for r in ledger["rows"]}
    assert classes["agent/dup"] == "A"
    assert classes["agent/work-1"] == "C"
    assert classes["agent/orphan"] == "D"


def test_sha256_is_recomputable_across_scans(repo, ledger):
    again = maturity._scan(repo, "main", "agent/*")
    assert maturity.content_sha256(again) == maturity.content_sha256(ledger)
    # 加一条分支 ⇒ 指纹必须变（否则"重算一致"毫无约束力）
    _mk(repo, "agent/new-one", {"n.txt": "n\n"})
    third = maturity._scan(repo, "main", "agent/*")
    assert maturity.content_sha256(third) != maturity.content_sha256(ledger)


def test_coverage_red_on_missing_evidence(ledger):
    bad = copy.deepcopy(ledger)
    del bad["rows"][0]["evidence"]["unique_patch_ids"]
    bad["rows"][1]["decided_by"] = ""
    a = maturity.measure_coverage(bad)
    assert a["coverage"] < 1.0 and a["pass"] is False
    reasons = {m["branch"]: m["reasons"] for m in a["missing"]}
    assert any("missing-evidence" in r for rs in reasons.values() for r in rs)
    assert any("no-rule-id" in r for rs in reasons.values() for r in rs)


def test_coverage_requires_c_not_closeable(ledger):
    bad = copy.deepcopy(ledger)
    for r in bad["rows"]:
        if r["class"] == "C":
            r["safe_to_close"] = True
    assert maturity.measure_coverage(bad)["pass"] is False


def test_sha256_mismatch_is_red(ledger):
    bad = copy.deepcopy(ledger)
    bad["content_sha256"] = "0" * 64
    a = maturity.measure_coverage(bad)
    assert a["sha256_consistent"] is False and a["pass"] is False


# ── (b) 抽样独立复核 ─────────────────────────────────────────────────────────
def test_sample_audit_agrees_and_is_stratified(repo, ledger):
    b = maturity.sample_audit(repo, ledger, "main", sample=30)
    assert b["sample"] == 3  # 只有 3 条，全部入样
    assert b["agreement"] == 1.0 and b["pass"] is True
    got = {i["branch"]: i["fresh_class"] for i in b["items"]}
    assert got["agent/work-1"] == "C" and got["agent/orphan"] == "D"


def test_sample_audit_catches_mislabelled_row(repo, ledger):
    """把一条 C 谎报成 A：独立引擎必须抓出来（否则复核等于自证）。"""
    fake = copy.deepcopy(ledger)
    for r in fake["rows"]:
        if r["branch"] == "agent/work-1":
            r["class"] = "A"
            r["safe_to_close"] = True
    b = maturity.sample_audit(repo, fake, "main", sample=30)
    item = [i for i in b["items"] if i["branch"] == "agent/work-1"][0]
    assert item["consistent"] is False and item["fresh_class"] == "C"


def test_sample_audit_marks_b_unverifiable_without_superset(repo, ledger):
    fake = copy.deepcopy(ledger)
    for r in fake["rows"]:
        if r["branch"] == "agent/work-1":
            r["class"] = "B"
            r["superset_source"] = None
    b = maturity.sample_audit(repo, fake, "main", sample=30)
    item = [i for i in b["items"] if i["branch"] == "agent/work-1"][0]
    assert item["consistent"] is False


# ── (c) 积压收敛 + C 零丢失 ──────────────────────────────────────────────────
def test_backlog_projection_and_zero_loss(ledger):
    c = maturity.measure_backlog(ledger, target_unmerged=None)
    assert c["c_branches"] == 1 and c["tickets"] == 1
    assert c["c_zero_loss"] is True
    # A/D 不在 unmerged 口径里，归档它们不改变 unmerged（防止"假装下降"）
    assert c["unmerged_projected_after_closure"] == c["unmerged_before"] - \
        c["archive_by_class"].get("B", 0)


def test_backlog_red_when_c_ticket_missing(ledger):
    bad = copy.deepcopy(ledger)
    bad["tickets"] = [t for t in bad["tickets"] if t["branch"] != "agent/work-1"]
    c = maturity.measure_backlog(bad)
    assert c["c_zero_loss"] is False and c["pass"] is False
    assert c["c_without_ticket"] == ["agent/work-1"]


def test_backlog_target_unmet_is_red(ledger):
    c = maturity.measure_backlog(ledger, target_unmerged=1)
    assert c["target_met"] is False and c["pass"] is False


# ── (d) 门禁负控 ─────────────────────────────────────────────────────────────
def test_gate_negative_control_detects_all_injected():
    d = maturity.gate_negative_control(injected=3)
    assert d["gate_before"]["pass"] is True, "冻结后、注入前门必须是绿的"
    assert d["went_red"] is True
    assert d["detection_rate"] == 1.0
    assert set(d["detected"]) == set(d["injected_branches"])
    assert "V2-new-undelivered-since-freeze" in d["gate_after"]["violations"]
    assert d["pass"] is True


def test_paper_gate_is_caught_by_control(repo):
    """反例：只比总数、不看新增的门（纸门）在负控下仍然绿 ⇒ 说明负控是承重的。"""
    maturity._commit(repo, "seed.txt", "seed\n")
    led = maturity._scan(repo, "main", "agent/*")
    baseline = gate_mod.freeze_baseline(led, None)
    baseline["repos"][led["repo"]]["ceiling"] = 10_000  # 放水到天花板
    from datetime import datetime, timedelta, timezone
    when = datetime.now(timezone.utc) + timedelta(seconds=120)
    for i in range(3):
        _mk(repo, f"agent/paper-{i}", {f"p{i}.txt": "x\n"}, when=when)
    after = maturity._scan(repo, "main", "agent/*")
    paper = gate_mod.evaluate_gate(after, baseline, strict_new=False)
    strict = gate_mod.evaluate_gate(after, baseline, strict_new=True)
    assert paper["pass"] is True            # 纸门：绿
    assert strict["pass"] is False          # 负控所依赖的那颗门牙：红


# ── (e) ref 集合差集 ─────────────────────────────────────────────────────────
def test_ref_diff_empty_for_fresh_scan(repo, ledger):
    e = maturity.measure_ref_diff(repo, ledger)
    assert e["symmetric_difference"] == 0 and e["strict_equality"] is True and e["pass"] is True


def test_ref_diff_records_arrivals_as_drift(repo, ledger):
    """快照之后新建的分支记 drift，不判红；但 strict_equality 必须为假（否则"相等"无信息量）。"""
    _mk(repo, "agent/late", {"late.txt": "l\n"})
    e = maturity.measure_ref_diff(repo, ledger)
    assert e["arrived_after_snapshot"] == ["agent/late"]
    assert e["pass"] is True and e["strict_equality"] is False


def test_ref_diff_red_on_lost_ref(repo, ledger):
    """账本里的分支被删且无 attic/reflog 备份 ⇒ 内容可能丢，必须判红。"""
    gitio.run_git(repo, "update-ref", "-d", "refs/heads/agent/work-1")
    gitio.run_git(repo, "reflog", "expire", "--expire=now", "--all")
    e = maturity.measure_ref_diff(repo, ledger)
    assert "agent/work-1" in e["lost_ledger_rows"] and e["pass"] is False


def test_ref_diff_records_in_flight_advance(repo, ledger):
    """在飞分支继续追加提交（旧 sha 仍是祖先）⇒ 记 advanced，不判红。"""
    gitio.run_git(repo, "checkout", "-q", "agent/work-1")
    maturity._commit(repo, "w1/b.txt", "b\n")
    gitio.run_git(repo, "checkout", "-q", "main")
    e = maturity.measure_ref_diff(repo, ledger)
    assert e["advanced_in_flight"] == ["agent/work-1"]
    assert e["rewritten"] == [] and e["pass"] is True


def test_ref_diff_red_on_force_moved_branch(repo, ledger):
    """分支被 amend/force-move（旧 sha 不再是祖先）⇒ 账本证据失效，必须判红。"""
    maturity._commit(repo, "w1/a.txt", "rewritten\n")      # 在 main 上另造一条历史
    new = gitio.rev_parse(repo, "main")
    gitio.run_git(repo, "update-ref", "refs/heads/agent/work-1", new)  # force-move：旧 sha 不再被包含
    e = maturity.measure_ref_diff(repo, ledger)
    assert e["rewritten"] == ["agent/work-1"] and e["pass"] is False


# ── 汇总 ─────────────────────────────────────────────────────────────────────
def test_measure_reports_all_five_criteria(repo, ledger):
    rep = maturity.measure(repo, ledger, sample=4, target_unmerged=None)
    assert set(rep["criteria"]) == {"a_coverage", "b_sample_audit", "c_backlog",
                                    "d_gate_negative_control", "e_ref_set"}
    assert rep["verdicts"]["a_coverage"] is True
    assert rep["verdicts"]["e_ref_set"] is True
    assert rep["ledger_content_sha256"] == maturity.content_sha256(ledger)


def test_measure_hard_pass_false_when_any_criterion_red(repo, ledger):
    bad = copy.deepcopy(ledger)
    bad["rows"][0]["class"] = "Z"
    rep = maturity.measure(repo, bad, sample=4, negative_control=False)
    assert rep["verdicts"]["a_coverage"] is False
    assert rep["hard_pass"] is False


def test_cli_writes_report(tmp_path, repo, ledger):
    lp = tmp_path / "ledger.json"
    ledger_mod.write_json(str(lp), ledger)
    out = tmp_path / "mat"
    rc = maturity.main(["--repo", repo, "--ledger", str(lp), "--out", str(out),
                        "--sample", "4", "--label", "synth"])
    assert rc == 0
    payload = json.loads((out / "maturity_synth.json").read_text())
    assert payload["criteria"]["d_gate_negative_control"]["detection_rate"] == 1.0