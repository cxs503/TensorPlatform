"""交付面卫生机制（四分分类 + 只减不增门）的机器判据测试（AC5–AC8）。

被测对象：``tools/hygiene/delivery_surface.py``
全部 git 操作发生在 **pytest 临时目录里的一次性夹具仓**，不触碰被审计的真实仓：
这也顺带验证「只读纪律」（真实仓的 ref 集合与 HEAD 在调用前后不变）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import branch_ledger as BL  # noqa: E402
from hygiene import delivery_surface as D  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/tmp",
}


def git(repo: str, *args: str) -> str:
    env = dict(os.environ, **GIT_ENV)
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败: {p.stderr}")
    return p.stdout


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def commit(repo: str, rel: str, text: str, msg: str) -> str:
    write(repo, rel, text)
    git(repo, "add", rel)
    git(repo, "commit", "--quiet", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path):
    """一次性夹具仓：main + 六条 agent 分支，覆盖 A/B/C/D 四类判据。"""
    r = str(tmp_path / "fixture")
    os.makedirs(r, exist_ok=True)
    git(r, "init", "--quiet", "-b", "main")
    commit(r, "core.py", "BASE = 1\n", "base: core")
    commit(r, "docs.md", "base docs\n", "base: docs")

    # A/ANCHOR：直接指向 main
    git(r, "branch", "agent/m_anchor", "main")

    # A/BANKED：cherry-pick 到 main 后残留的分支（patch-id 命中主干）
    # 注意：main 必须先分叉，否则 cherry-pick 会快进成同一提交（那就成了 ANCHOR）
    commit(r, "divergence1.txt", "v1\n", "main diverges (1)")
    git(r, "checkout", "--quiet", "-f", "-b", "agent/m_banked", "main")
    commit(r, "banked.txt", "banked payload\n", "work that lands via cherry-pick")
    git(r, "checkout", "--quiet", "-f", "main")
    commit(r, "divergence2.txt", "v2\n", "main diverges (2)")
    git(r, "cherry-pick", "--quiet", "agent/m_banked")
    git(r, "checkout", "--quiet", "-f", "main")

    # C：主干没有的独占补丁
    git(r, "checkout", "--quiet", "-f", "-b", "agent/m_unique", "main")
    commit(r, "unique.txt", "unique payload\n", "unique work not in main")
    git(r, "checkout", "--quiet", "-f", "main")

    # B：在 C 之上再加一个提交 ⇒ C 的唯一 patch 集是它的真子集
    git(r, "checkout", "--quiet", "-f", "-b", "agent/m_superset", "agent/m_unique")
    commit(r, "extra.txt", "extra payload\n", "superset adds more work")
    git(r, "checkout", "--quiet", "-f", "main")

    # A/CONTENT_SAME：改过又被改回与主干逐字节一致
    git(r, "checkout", "--quiet", "-f", "-b", "agent/m_touched", "main")
    commit(r, "core.py", "BASE = 2\n", "touch core")
    commit(r, "core.py", "BASE = 1\n", "revert core")
    git(r, "checkout", "--quiet", "-f", "main")

    # D：无共同祖先（孤儿分支）
    git(r, "checkout", "--quiet", "--orphan", "agent/m_orphan")
    git(r, "rm", "-rf", "--quiet", "--cached", ".")
    git(r, "clean", "-fdxq")
    commit(r, "orphan.txt", "orphan payload\n", "orphan root")
    git(r, "checkout", "--quiet", "-f", "main")
    return r


def rows_by_name(ledger) -> dict:
    return {r["name"]: r for r in ledger["rows"]}


# ═══════════════════════════════════════════════════════════════════════════
# 四分判据
# ═══════════════════════════════════════════════════════════════════════════
def test_anchor_branch_is_A(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    row = rows_by_name(led)["agent/m_anchor"]
    assert row["class"] == D.CLASS_A
    assert row["evidence"]["reason"] == D.REASON_ANCHOR


def test_cherry_picked_branch_is_A(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    row = rows_by_name(led)["agent/m_banked"]
    assert row["class"] == D.CLASS_A
    assert row["evidence"]["reason"] == D.REASON_BANKED
    assert row["evidence"]["unique"] == 0


def test_content_same_branch_is_A(repo):
    rows = D.collect_rows(repo, "main", "agent/*")
    led = D.build_ledger(repo, rows, base="main")
    row = rows_by_name(led)["agent/m_touched"]
    assert row["class"] == D.CLASS_A
    assert row["evidence"]["reason"] == D.REASON_CONTENT_SAME


def test_unique_branch_is_C(repo):
    """m_superset 的唯一 patch 集不被任何分支包含 ⇒ 真未交付（C）。"""
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    row = rows_by_name(led)["agent/m_superset"]
    assert row["class"] == D.CLASS_C
    assert len(row["evidence"]["unique_patch_ids"]) >= 2
    assert "superset_by" not in row["evidence"]


def test_subset_branch_is_B_with_superset_evidence(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    rows = rows_by_name(led)
    assert rows["agent/m_unique"]["class"] == D.CLASS_B
    assert rows["agent/m_unique"]["evidence"]["superset_by"] == "agent/m_superset"
    assert rows["agent/m_superset"]["class"] == D.CLASS_C


def test_orphan_branch_is_D(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    row = rows_by_name(led)["agent/m_orphan"]
    assert row["class"] == D.CLASS_D
    assert row["evidence"]["reason"] == D.REASON_NO_COMMON_ANCESTOR


def test_classification_is_deterministic(repo):
    a = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    b = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    assert a["ledger_sha256"] == b["ledger_sha256"]


# ═══════════════════════════════════════════════════════════════════════════
# AC5 覆盖率 + 账本摘要可重算
# ═══════════════════════════════════════════════════════════════════════════
def test_ac5_coverage_is_100_percent(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    cov = led["coverage"]
    assert cov["rate"] == 1.0 and cov["complete"] is True
    for row in led["rows"]:
        assert row["class"] in D.CLASSES
        assert row["evidence"] and row["evidence"]["reason"]


def test_ac5_counts_partition_total(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    assert sum(led["counts"].values()) == led["total"] == len(led["rows"])
    assert led["unmerged_count"] == led["counts"]["B"] + led["counts"]["C"] + led["counts"]["D"]
    assert led["class_c"] == sorted(r["name"] for r in led["rows"] if r["class"] == "C")


def test_ac5_digest_recomputable_and_tamper_evident(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    assert D.verify_ledger(led)["ok"] is True
    tampered = json.loads(json.dumps(led))
    tampered["rows"][0]["class"] = "C" if tampered["rows"][0]["class"] != "C" else "A"
    out = D.verify_ledger(tampered)
    assert out["digest_match"] is False and out["ok"] is False


def test_ac5_coverage_flags_missing_class(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    bad = json.loads(json.dumps(led))
    bad["rows"][0]["class"] = None
    assert D.coverage(bad["rows"])["complete"] is False


def test_ac5_coverage_flags_missing_evidence(repo):
    rows = D.collect_rows(repo, "main", "agent/*")
    rows[0]["evidence"] = {}
    cov = D.coverage(rows)
    assert cov["complete"] is False and rows[0]["name"] in cov["missing_evidence"]


# ═══════════════════════════════════════════════════════════════════════════
# 只减不增门
# ═══════════════════════════════════════════════════════════════════════════
def test_gate_green_when_unchanged(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(led, D.make_baseline(led), repo=repo)
    assert gate["status"] == "green", gate["violations"]
    assert gate["checks"]["count_le_baseline"] is True


def test_ac8_negative_control_detects_all_injected(repo):
    """AC8：注入 3 条新分支后门必须变红，检出率 100%。"""
    out = D.gate_negative_control(repo, "main", pattern="agent/*", injections=3)
    assert out["baseline_status"] == "green"
    assert out["post_status"] == "red"
    assert len(out["injected"]) == 3
    assert out["detection_rate"] == 1.0
    assert out["ok"] is True


def test_gate_red_when_count_increases(repo):
    rows = D.collect_rows(repo, "main", "agent/*")
    led = D.build_ledger(repo, rows, base="main")
    baseline = D.make_baseline(led)
    baseline["unmerged_count"] = 1  # 人为收紧基线
    gate = D.evaluate_gate(led, baseline, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_COUNT_INCREASE for v in gate["violations"])


def test_gate_red_on_c_loss_without_proof(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    # 真未交付分支被直接删掉（没有交付证明）
    git(repo, "branch", "-D", "agent/m_superset")
    cur = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(cur, baseline, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_C_LOSS and v["detail"]["branch"] == "agent/m_superset"
               for v in gate["violations"])


def test_gate_green_on_c_delivery_with_proof(repo):
    """C 分支消失但能证明其提交已进主干 ⇒ 不算丢失（这是对「删分支」的正向出口）。"""
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    sha = git(repo, "rev-parse", "agent/m_superset").strip()
    git(repo, "checkout", "--quiet", "-f", "main")
    git(repo, "merge", "--quiet", "--no-ff", "-m", "land superset", "agent/m_superset")
    git(repo, "branch", "-D", "agent/m_superset")
    cur = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(cur, baseline, repo=repo, proofs={"agent/m_superset": sha})
    assert not any(v["code"] == D.VIOL_C_LOSS for v in gate["violations"]), gate["violations"]
    assert "agent/m_superset" in gate["checks"]["c_lost_proven"]


def test_gate_red_on_class_flip_regression(repo):
    """A→C 的改判（分支被强推重写）必须红：这是「已入库分支偷偷长出未交付内容」。"""
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    git(repo, "checkout", "--quiet", "-f", "agent/m_anchor")
    commit(repo, "sneaky.txt", "unreviewed payload\n", "sneaky commit on covered branch")
    git(repo, "checkout", "--quiet", "main")
    cur = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(cur, baseline, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_CLASS_FLIP and v["detail"]["branch"] == "agent/m_anchor"
               for v in gate["violations"])


def test_gate_red_fail_closed_without_baseline(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(led, None, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_BASELINE_MISSING for v in gate["violations"])


def test_gate_red_when_new_unmerged_branch_is_C(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    git(repo, "checkout", "--quiet", "-b", "agent/m_new", "main")
    commit(repo, "new.txt", "brand new undelivered\n", "new work")
    git(repo, "checkout", "--quiet", "main")
    cur = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(cur, baseline, repo=repo)
    assert gate["status"] == "red"
    det = [v["detail"]["branch"] for v in gate["violations"] if v["code"] == D.VIOL_NEW_UNMERGED]
    assert det == ["agent/m_new"]


def test_gate_allows_new_branch_that_is_already_covered(repo):
    """新增一条 A 类分支（内容主干已有）不算「未合并增长」——门只对未交付内容设限。"""
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    git(repo, "branch", "agent/m_extra_anchor", "main")
    cur = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    gate = D.evaluate_gate(cur, baseline, repo=repo)
    assert gate["status"] == "green", gate["violations"]


def test_gate_red_on_coverage_gap(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    broken = json.loads(json.dumps(led))
    broken["rows"][0]["class"] = "X"
    broken["ledger_sha256"] = D.ledger_sha256(broken["rows"])  # 摘要自洽，但分类非法
    gate = D.evaluate_gate(broken, baseline, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_COVERAGE for v in gate["violations"])


# ═══════════════════════════════════════════════════════════════════════════
# AC6 抽样独立复核
# ═══════════════════════════════════════════════════════════════════════════
def test_ac6_sample_recheck_agreement(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    out = D.sample_recheck(led, repo=repo, n=6)
    assert out["sampled"] == 6
    assert out["rate"] >= 0.95, out["details"]


def test_ac6_independent_judge_separate_path(repo):
    assert D.independent_class(repo, "main", "agent/m_anchor")["class"] == D.CLASS_A
    assert D.independent_class(repo, "main", "agent/m_banked")["class"] == D.CLASS_A
    assert D.independent_class(repo, "main", "agent/m_superset")["class"] == D.CLASS_C


# ═══════════════════════════════════════════════════════════════════════════
# 只读纪律
# ═══════════════════════════════════════════════════════════════════════════
def test_collect_is_read_only(repo):
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    head = git(repo, "rev-parse", "HEAD")
    D.collect_rows(repo, "main", "agent/*")
    after = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    assert before == after
    assert head == git(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain")
    assert status.strip() == ""


def test_baseline_contains_row_hashes(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    base = D.make_baseline(led, created_at="2026-11-10T00:00:00Z")
    assert base["schema"] == D.SCHEMA_BASELINE
    assert len(base["branches"]) == led["total"]
    assert all(len(b["row_sha256"]) == 64 for b in base["branches"])
    assert base["ledger_sha256"] == led["ledger_sha256"]

# ═══════════════════════════════════════════════════════════════════════════
# CLI：证据落盘 + 门的退出码（CI 靠退出码拦人，不能只靠人读 JSON）
# ═══════════════════════════════════════════════════════════════════════════
def test_cli_delivery_writes_artifacts(repo, tmp_path):
    from hygiene import cli

    out = str(tmp_path / "art")
    rc = cli.main(["delivery", "--repo", repo, "--base", "main", "--out", out,
                   "--write-baseline", "--baseline", os.path.join(out, "baseline.json"),
                   "--sample", "5", "--jobs", "2"])
    assert rc == 0
    for name in ("ledger.json", "baseline.json", "gate.json", "sample_recheck.json"):
        assert os.path.exists(os.path.join(out, name))
    gate = json.load(open(os.path.join(out, "gate.json")))
    assert gate["status"] == "green"


def test_cli_gate_exit_code_red_without_baseline(repo, tmp_path):
    from hygiene import cli

    out = str(tmp_path / "art2")
    rc = cli.main(["gate", "--repo", repo, "--base", "main", "--out", out])
    assert rc == 1
    gate = json.load(open(os.path.join(out, "gate.json")))
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_BASELINE_MISSING for v in gate["violations"])


def test_cli_restart_evidence_roundtrip(tmp_path):
    from hygiene import cli

    out = str(tmp_path / "restart")
    rc = cli.main(["restart", "--out", out, "--n-micro", "60", "--no-figures"])
    assert rc == 0
    ev = json.load(open(os.path.join(out, "restart_evidence.json")))
    assert all(ev["ac"].values())
    assert ev["n_micro"] == 60


# ═══════════════════════════════════════════════════════════════════════════
# 瘦身（大仓行证据可能上千 MB；瘦身不得削弱判据与证据链）
# ═══════════════════════════════════════════════════════════════════════════
def test_slim_rows_preserves_class_and_evidence_chain(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    slim = D.slim_ledger(led, max_files=1)
    assert slim["full_ledger_sha256"] == led["ledger_sha256"]
    assert slim["ledger_sha256"] != led["ledger_sha256"]
    assert D.verify_ledger(slim)["ok"] is True
    assert slim["coverage"]["complete"] is True
    for full_row, slim_row in zip(led["rows"], slim["rows"]):
        assert full_row["class"] == slim_row["class"]
        assert slim_row["evidence_sha256"] == D.row_evidence_sha256(full_row)
        if isinstance(full_row["evidence"].get("files"), list):
            assert slim_row["evidence"]["files_count"] == len(full_row["evidence"]["files"])


def test_slim_rows_leaves_new_unmerged_detection_intact(repo):
    led = D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main")
    baseline = D.make_baseline(led)
    git(repo, "checkout", "--quiet", "-f", "-b", "agent/m_new2", "main")
    commit(repo, "new2.txt", "payload\n", "new work")
    git(repo, "checkout", "--quiet", "-f", "main")
    slim = D.slim_ledger(D.build_ledger(repo, D.collect_rows(repo, "main", "agent/*"), base="main"))
    gate = D.evaluate_gate(slim, baseline, repo=repo)
    assert gate["status"] == "red"
    assert any(v["code"] == D.VIOL_NEW_UNMERGED and v["detail"]["branch"] == "agent/m_new2"
               for v in gate["violations"])
