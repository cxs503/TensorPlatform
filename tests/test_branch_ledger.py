"""未落地分支台账的机器判据测试（合成 git 仓库夹具，不依赖平台/网络）。

被测对象：``tools/branch_ledger.py``
测试原则：每条断言对应一个可被推翻的命题——
patch-id 判等是否真把 cherry-pick 过的分支判成 BANKED /
未落地提交是否真被判成 UNIQUE / 报告交叉核验是否按 mission_id 关联 /
重复劳动聚类是否只合并共改同一批文件的分支 / 工具是否真的只读。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import branch_ledger as B  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/tmp",
}


def git(repo: str, *args: str) -> str:
    env = dict(os.environ, **GIT_ENV)
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout


def commit(repo: str, path: str, text: str, msg: str) -> str:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path):
    """main 分支 + 三条 agent 分支：anchor / banked(cherry-pick) / unique。

    注意 main 在分支点之后必须**前进**再 cherry-pick：否则 cherry-pick 会生成与分支上
    逐位相同的 commit sha，分支直接变成 main 的祖先（ahead=0），便测不出 BANKED 判据。
    """
    r = str(tmp_path / "r")
    os.makedirs(r)
    git(r, "init", "-b", "main")
    commit(r, "src/core.py", "v1\n", "c1")
    commit(r, "src/util.py", "u1\n", "c2")

    # ANCHOR：在 main 尖端建分支，无自有提交
    git(r, "branch", "agent/m10")

    # BANKED：分支在 c2 提交，main 前进后才 cherry-pick（patch-id 相等、sha 不等）
    git(r, "checkout", "-b", "agent/m11")
    sha = commit(r, "src/feat_a.py", "feat-a\n", "feat a")
    git(r, "checkout", "main")
    commit(r, "src/core.py", "v2\n", "c3 on main")
    git(r, "cherry-pick", sha)

    # UNIQUE：分支上提交，main 没有等价补丁
    git(r, "checkout", "-b", "agent/m12")
    commit(r, "src/feat_b.py", "feat-b\n", "feat b unique")
    git(r, "checkout", "main")
    commit(r, "src/core.py", "v3\n", "c5 on main")
    return r


def rows_by_name(collected) -> dict:
    return {r["branch"]: r for r in collected["rows"]}


def test_parse_mission_id_variants():
    assert B.parse_mission_id("agent/m1875") == 1875
    assert B.parse_mission_id("agent/m1875-extra") == 1875
    assert B.parse_mission_id("origin/agent/m641") == 641
    assert B.parse_mission_id("agent/fix-runtime-local-patches") is None


def test_classify_boundaries():
    assert B.classify(0, 0) == B.CLASS_ANCHOR
    assert B.classify(-1, 0) == B.CLASS_ANCHOR
    assert B.classify(3, 0) == B.CLASS_BANKED
    assert B.classify(3, 1) == B.CLASS_UNIQUE


def test_landing_status_matrix():
    assert B.landing_status(B.CLASS_UNIQUE, True) == B.LANDING_REPORTED_UNMERGED
    assert B.landing_status(B.CLASS_UNIQUE, False) == B.LANDING_UNREPORTED
    assert B.landing_status(B.CLASS_BANKED, True) == B.LANDING_IN_BASE
    assert B.landing_status(B.CLASS_ANCHOR, False) == B.LANDING_IN_BASE


def test_patch_id_decides_banked_not_rev_count(repo):
    """核心判据：cherry-pick 过的分支 ahead>0 但 patch-id 全命中 ⇒ BANKED（不是 UNIQUE）。"""
    rows = rows_by_name(B.collect(repo, base="main"))
    banked = rows["agent/m11"]
    assert banked["ahead"] == 1, "分支确实有 1 条 main 之外的提交"
    assert banked["unique_commits"] == 0 and banked["banked_commits"] == 1
    assert banked["class"] == B.CLASS_BANKED


def test_anchor_branch_is_merged(repo):
    rows = rows_by_name(B.collect(repo, base="main"))
    assert rows["agent/m10"]["ahead"] == 0
    assert rows["agent/m10"]["class"] == B.CLASS_ANCHOR


def test_unique_branch_is_flagged(repo):
    rows = rows_by_name(B.collect(repo, base="main"))
    uniq = rows["agent/m12"]
    assert uniq["class"] == B.CLASS_UNIQUE
    assert uniq["unique_commits"] == 1
    assert "src/feat_b.py" in uniq["files"]
    assert uniq["behind"] >= 1, "main 已前进，分支落后数应可观测"


def test_report_cross_check_marks_reported_unmerged(repo, tmp_path):
    snap = tmp_path / "reports.json"
    snap.write_text(json.dumps({"data": [
        {"id": 900, "mission_id": 12, "title": "m12 报告", "created_at": "2026-11-01T00:00:00"},
        {"id": 1, "mission_id": 11, "title": "m11 旧报告", "created_at": "2026-10-01T00:00:00"},
    ]}), encoding="utf-8")
    reports = B.load_reports(str(snap))
    rows = rows_by_name(B.collect(repo, base="main", reports=reports))
    assert rows["agent/m12"]["landing"] == B.LANDING_REPORTED_UNMERGED
    assert rows["agent/m12"]["report_id"] == 900
    assert rows["agent/m11"]["landing"] == B.LANDING_IN_BASE, "已入库分支不计入未落地"
    # 无报告快照时同一分支应落到 UNREPORTED
    rows2 = rows_by_name(B.collect(repo, base="main"))
    assert rows2["agent/m12"]["landing"] == B.LANDING_UNREPORTED


def test_load_reports_keeps_latest_per_mission(tmp_path):
    snap = tmp_path / "r.json"
    snap.write_text(json.dumps([
        {"id": 1, "mission_id": 5, "created_at": "2026-10-01T00:00:00"},
        {"id": 2, "mission_id": 5, "created_at": "2026-10-09T00:00:00"},
        {"id": 3, "mission_id": "bad"},
    ]), encoding="utf-8")
    assert B.load_reports(str(snap))[5]["id"] == 2
    assert B.load_reports(None) == {}


def test_duplicate_clusters_group_shared_paths(repo):
    """两条 UNIQUE 分支共改同一批文件应聚成一簇；无关分支不并入。"""
    git(repo, "checkout", "-b", "agent/m13", "main")
    for i in range(5):
        commit(repo, f"backend/svc/mod{i}.py", "x\n", f"m13 {i}")
    git(repo, "checkout", "-b", "agent/m14", "main")
    for i in range(5):
        commit(repo, f"backend/svc/mod{i}.py", "x\n", f"m14 {i}")
    git(repo, "checkout", "main")
    collected = B.collect(repo, base="main")
    ledger = B.build_ledger(collected)
    clusters = ledger["duplicate_clusters"]
    flat = [set(c["branches"]) for c in clusters]
    assert any({"agent/m13", "agent/m14"} <= s for s in flat), clusters
    assert not any("agent/m12" in s for s in flat), "改动不相交的分支不应被并簇"


def test_jaccard_bounds():
    assert B.jaccard([], ["a"]) == 0.0
    assert B.jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert B.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)


def test_stats_are_internally_consistent(repo):
    ledger = B.build_ledger(B.collect(repo, base="main"))
    s = ledger["stats"]
    assert sum(s["by_class"].values()) == s["total_refs"] == len(ledger["rows"])
    assert sum(s["by_landing"].values()) == s["total_refs"]
    assert s["unique_commits_total"] == sum(r["unique_commits"] for r in ledger["rows"])
    assert 0.0 <= s["unlanded_branch_ratio"] <= 1.0


def test_markdown_reports_the_gate_numbers(repo):
    ledger = B.build_ledger(B.collect(repo, base="main"))
    md = B.render_markdown(ledger)
    s = ledger["stats"]
    assert "未落地占比" in md
    assert f"UNIQUE（真未落地）**{s['by_class']['UNIQUE']}**" in md
    assert "开工前自检" in md
    assert "## 口径与局限" in md


def test_write_outputs_roundtrip(repo, tmp_path):
    ledger = B.build_ledger(B.collect(repo, base="main"))
    paths = B.write_outputs(str(tmp_path / "out"), ledger)
    data = json.loads(open(paths["json"], encoding="utf-8").read())
    assert data["stats"]["total_refs"] == ledger["stats"]["total_refs"]
    assert os.path.getsize(paths["markdown"]) > 200
    # 除 generated_at 外确定性：重跑一次 rows 必须逐位相同
    again = B.build_ledger(B.collect(repo, base="main"))
    assert again["rows"] == ledger["rows"]


def test_tool_is_read_only(repo):
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").strip()
    B.build_ledger(B.collect(repo, base="main", conflict_check=True))
    after = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").strip()
    assert before == after, "台账工具不得创建/移动任何 ref"


def test_conflict_preview_detects_divergent_edit(tmp_path):
    r = str(tmp_path / "c")
    os.makedirs(r)
    git(r, "init", "-b", "main")
    commit(r, "a.txt", "line1\nline2\n", "base")
    git(r, "checkout", "-b", "agent/m20")
    commit(r, "a.txt", "line1\nBRANCH\n", "branch edit")
    git(r, "checkout", "main")
    commit(r, "a.txt", "line1\nMAIN\n", "main edit")
    collected = B.collect(r, base="main", conflict_check=True)
    row = rows_by_name(collected)["agent/m20"]
    assert row["merge_conflict"] is True


def test_conflict_preview_clean_when_disjoint(tmp_path):
    r = str(tmp_path / "d")
    os.makedirs(r)
    git(r, "init", "-b", "main")
    commit(r, "keep.txt", "k\n", "base")
    git(r, "checkout", "-b", "agent/m21")
    commit(r, "other.txt", "o\n", "branch add")
    git(r, "checkout", "main")
    commit(r, "more.txt", "m\n", "main add")
    row = rows_by_name(B.collect(r, base="main", conflict_check=True))["agent/m21"]
    assert row["merge_conflict"] is False

def test_probe_reports_by_mission_uses_filter_and_keeps_latest():
    """按 mission 补探：注入 fetcher（离线），必须只认 mission_id 匹配的条目并取最新一篇。"""
    calls = []

    def fake_fetch(mid):
        calls.append(mid)
        if mid == 7:
            return {"items": [
                {"id": 1, "mission_id": 7, "created_at": "2026-10-01T00:00:00"},
                {"id": 2, "mission_id": 7, "created_at": "2026-10-08T00:00:00"},
                {"id": 3, "mission_id": 99, "created_at": "2026-10-09T00:00:00"},
            ]}
        return {"items": []}

    got = B.probe_reports_by_mission([7, 8], "http://x/api/v1", "tok", fetch=fake_fetch)
    assert calls == [7, 8]
    assert got[7]["id"] == 2, "必须取该 mission 最新一篇，且不得串到别的 mission"
    assert 8 not in got, "查不到不算命中"


def test_probe_failure_is_not_a_hit():
    def boom(mid):
        raise RuntimeError("api down")

    assert B.probe_reports_by_mission([1, 2], "http://x", "tok", fetch=boom) == {}


def test_ledger_records_reports_coverage(repo):
    ledger = B.build_ledger(B.collect(repo, base="main"),
                            reports_meta={"snapshot_reports": 3,
                                          "snapshot_created_range": "2026-10-01 ~ 2026-11-01",
                                          "probed_missions": 5, "matched_missions": 2})
    assert ledger["reports_coverage"]["snapshot_reports"] == 3
    md = B.render_markdown(ledger)
    assert "报告核验覆盖" in md and "不是" in md, "口径必须声明 UNREPORTED 不等于没有报告"


def test_classify_with_content_equivalence():
    assert B.classify(2, 1, content_same=True) == B.CLASS_CONTENT_SAME
    assert B.classify(2, 1, content_same=False) == B.CLASS_UNIQUE
    assert B.classify(2, 0, content_same=True) == B.CLASS_BANKED


def test_squash_merged_branch_is_content_same_not_unique(tmp_path):
    """squash 合并会让 patch-id 全变（git cherry 全 +），内容闸必须把它判成 CONTENT_SAME。"""
    r = str(tmp_path / "s")
    os.makedirs(r)
    git(r, "init", "-b", "main")
    commit(r, "a.py", "base\n", "c1")
    git(r, "checkout", "-b", "agent/m30")
    commit(r, "a.py", "base\nfeat1\n", "f1")
    commit(r, "b.py", "feat2\n", "f2")
    git(r, "checkout", "main")
    git(r, "merge", "--squash", "agent/m30")
    git(r, "commit", "-m", "squash m30")
    row = rows_by_name(B.collect(r, base="main"))["agent/m30"]
    assert row["ahead"] == 2
    assert row["unique_commits"] >= 1, "squash 后 patch-id 不等价，patch-id 闸会放过"
    assert row["content_same_on_touched"] is True
    assert row["class"] == B.CLASS_CONTENT_SAME
    assert row["landing"] == B.LANDING_IN_BASE, "内容已入库不算未落地"


def test_content_identical_helper_is_symmetric_on_paths(tmp_path):
    r = str(tmp_path / "p")
    os.makedirs(r)
    git(r, "init", "-b", "main")
    commit(r, "x.py", "1\n", "c1")
    git(r, "checkout", "-b", "agent/m31")
    commit(r, "x.py", "2\n", "edit")
    git(r, "checkout", "main")
    assert B.content_identical(r, "main", "agent/m31", ["x.py"]) is False
    assert B.content_identical(r, "main", "agent/m31", []) is False, "空路径集不得误判为等价"


def test_top_dirs_counts_branches_not_files(repo):
    """目录表口径必须是「触及该目录的分支数」，不是文件计数（曾把 42876 当成分支数）。"""
    git(repo, "checkout", "-b", "agent/m40", "main")
    for i in range(3):
        commit(repo, f"docs/note{i}.md", "n\n", f"m40 doc {i}")
    git(repo, "checkout", "main")
    stats = B.build_ledger(B.collect(repo, base="main"))["stats"]
    docs = [d for d in stats["top_touched_dirs"] if d["dir"] == "docs"][0]
    assert docs["branches_touching"] == 1, "三条文件都在同一分支里，分支维度应为 1"
    assert docs["files_touching"] == 3, "文件计数单独记在 files_touching"
