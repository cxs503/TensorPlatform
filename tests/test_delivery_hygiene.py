"""``tools/delivery_hygiene`` 的 pytest 用例（合成仓库，零领域语义）。

覆盖：四类判据 A/B/C/D 各至少一条正例与反例；``safe_to_close`` 链式留存；处置计划与
``--apply`` 的归档/删分支/幂等；只减不增门的三类违规与棘轮留痕；账本可复算；CLI 端到端。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from delivery_hygiene import CLASS_A, CLASS_B, CLASS_C, CLASS_D  # noqa: E402
from delivery_hygiene import classify as classify_mod  # noqa: E402
from delivery_hygiene import cli, dispose, gate, gitio, ledger as ledger_mod  # noqa: E402


# ── 合成仓库 ───────────────────────────────────────────────────────────────────
def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    full_env = dict(os.environ)
    full_env.update({
        "GIT_AUTHOR_NAME": "hygiene", "GIT_AUTHOR_EMAIL": "h@example.invalid",
        "GIT_COMMITTER_NAME": "hygiene", "GIT_COMMITTER_EMAIL": "h@example.invalid",
    })
    if env:
        full_env.update(env)
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env=full_env, check=True)
    return p.stdout


def _commit(repo: Path, path: str, content: str, when: str, msg: str) -> str:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg,
         env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """合成仓库：main + 7 条 agent 分支，覆盖 A/B/C/D 全部判据。

    agent/m1001  D  (tip 在 main 历史内)
    agent/m1002  A  (patch-id 已命中 main，cherry-pick 残壳)
    agent/m1003  A  (改了又改回：净 diff 为空)
    agent/m1004  C  (两条独立提交 P1/P2)
    agent/m1005  B  (只 cherry-pick 了 P1 ⇒ 独有集 ⊂ m1004)
    agent/m1006  B  (cherry-pick P1+P2 ⇒ 与 m1004 等集，较旧 ⇒ 判 B)
    agent/m1007  C  (独立提交)
    """
    r = tmp_path / "repo-alpha"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "hygiene")
    _git(r, "config", "user.email", "h@example.invalid")

    _commit(r, "README.md", "base\n", "2020-01-01T00:00:00+0000", "c0")
    _git(r, "branch", "agent/m1001")           # D：tip 就是 c0，随后 main 前进

    # A(patch-id)：先在工作分支提交，再 cherry-pick 进 main（patch-id 相同、sha 不同）
    _git(r, "checkout", "-q", "-b", "work")
    _commit(r, "a1002.txt", "payload\n", "2020-01-05T00:00:00+0000", "A1")
    _git(r, "branch", "agent/m1002")
    _git(r, "checkout", "-q", "main")
    _git(r, "cherry-pick", "agent/m1002",
         env={"GIT_AUTHOR_DATE": "2020-01-15T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2020-01-15T00:00:00+0000"})
    _git(r, "branch", "-D", "work")

    # A(net-diff-empty)：add then delete
    _git(r, "checkout", "-q", "-b", "agent/m1003", "main")
    _commit(r, "tmp1003.txt", "scratch\n", "2020-02-01T00:00:00+0000", "scratch")
    _commit(r, "tmp1003.txt", "", "2020-02-02T00:00:00+0000", "revert-scratch")
    _git(r, "rm", "-q", "tmp1003.txt")
    _git(r, "commit", "-m", "drop-scratch",
         env={"GIT_AUTHOR_DATE": "2020-02-03T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2020-02-03T00:00:00+0000"})

    # C：两条独立提交
    _git(r, "checkout", "-q", "-b", "agent/m1004", "main")
    _commit(r, "c1.txt", "one\n", "2020-02-10T00:00:00+0000", "P1")
    _commit(r, "c2.txt", "two\n", "2020-02-10T00:00:10+0000", "P2")

    # B：只拿 P1
    _git(r, "checkout", "-q", "-b", "agent/m1005", "main")
    _git(r, "cherry-pick", "agent/m1004~1",
         env={"GIT_AUTHOR_DATE": "2020-02-12T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2020-02-12T00:00:00+0000"})

    # B：拿 P1+P2（等集，tip 更旧 ⇒ 非 canonical）
    _git(r, "checkout", "-q", "-b", "agent/m1006", "main")
    _git(r, "cherry-pick", "agent/m1004~1", "agent/m1004",
         env={"GIT_AUTHOR_DATE": "2020-02-05T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2020-02-05T00:00:00+0000"})

    # C：独立
    _git(r, "checkout", "-q", "-b", "agent/m1007", "main")
    _commit(r, "d.txt", "solo\n", "2020-03-01T00:00:00+0000", "D1")

    _git(r, "checkout", "-q", "main")
    return r


def _scan(repo: Path) -> dict:
    return cli._scan(str(repo), "main", "agent/*", False)


def _by_branch(led: dict) -> dict:
    return {r["branch"]: r for r in led["rows"]}


# ── 分类判据 ───────────────────────────────────────────────────────────────────
def test_class_D_tip_in_base_history(repo: Path):
    row = _by_branch(_scan(repo))["agent/m1001"]
    assert row["class"] == CLASS_D
    assert row["evidence"]["ahead"] == 0
    assert row["evidence"]["tip_in_base_history"] is True
    assert row["safe_to_close"] is True
    assert row["decided_by"] == "rule:D-tip-in-base-history/v1"


def test_class_A_patch_id_all_in_base(repo: Path):
    row = _by_branch(_scan(repo))["agent/m1002"]
    assert row["class"] == CLASS_A
    assert row["evidence"]["ahead"] >= 1
    assert row["evidence"]["unique_shas"] == []          # 所有提交 patch-id 命中 main
    assert row["evidence"]["equivalent_count"] >= 1
    assert row["decided_by"] == "rule:A-patch-id-all-in-base/v1"


def test_class_A_net_diff_empty(repo: Path):
    row = _by_branch(_scan(repo))["agent/m1003"]
    assert row["class"] == CLASS_A
    assert row["evidence"]["unique_shas"]                  # 有独有提交……
    assert row["evidence"]["net_diff_empty"] is True       # ……但净内容为空
    assert row["decided_by"] == "rule:A-net-diff-empty/v1"


def test_class_C_undelivered(repo: Path):
    led = _scan(repo)
    rows = _by_branch(led)
    for name in ("agent/m1004", "agent/m1007"):
        assert rows[name]["class"] == CLASS_C
        assert rows[name]["safe_to_close"] is False
        assert rows[name]["decided_by"] == "rule:C-unique-patch-ids/v1"
    assert len(led["tickets"]) == 2


def test_class_B_strict_subset(repo: Path):
    row = _by_branch(_scan(repo))["agent/m1005"]
    assert row["class"] == CLASS_B
    assert row["superset_source"] == "agent/m1004"
    assert row["safe_to_close"] is True
    assert row["retention_owner"] == "agent/m1004"
    assert row["evidence"]["superset_extra_commits"] == 1


def test_class_B_equal_set_picks_newest_tip_as_canonical(repo: Path):
    rows = _by_branch(_scan(repo))
    # m1006 与 m1004 等集但 tip 更旧 ⇒ m1006 判 B、m1004 保持 C（内容留存源）
    assert rows["agent/m1006"]["class"] == CLASS_B
    assert rows["agent/m1006"]["superset_source"] == "agent/m1004"
    assert rows["agent/m1004"]["class"] == CLASS_C


def test_class_partition_is_complete_and_disjoint(repo: Path):
    led = _scan(repo)
    classes = [r["class"] for r in led["rows"]]
    assert len(classes) == 7
    assert sorted(classes) == sorted([CLASS_A, CLASS_A, CLASS_B, CLASS_B, CLASS_C, CLASS_C, CLASS_D])
    s = led["stats"]
    assert s["unmerged"] == s["by_class"][CLASS_B] + s["by_class"][CLASS_C] == 4
    assert s["safe_to_close"] == 5


def test_ledger_row_contract(repo: Path):
    led = _scan(repo)
    required = {"repo", "branch", "class", "evidence", "decided_by", "safe_to_close"}
    for r in led["rows"]:
        assert required <= set(r), r["branch"]
        assert r["class"] in (CLASS_A, CLASS_B, CLASS_C, CLASS_D)
        assert r["class_label"]
    assert led["schema_version"] == "1.0"
    assert [r["branch"] for r in led["rows"]] == sorted(r["branch"] for r in led["rows"])


def test_ledger_is_recomputable(repo: Path):
    a = _scan(repo)
    b = _scan(repo)
    a.pop("generated_at")
    b.pop("generated_at")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_retention_points_at_existing_branch(repo: Path):
    led = _scan(repo)
    names = {r["branch"] for r in led["rows"]}
    for entry in led["retention"]:
        assert entry["held_by"] in names or entry["held_by"] == "base"


# ── 第三条 A 判据：squash 合并残留 ──────────────────────────────────────────────
@pytest.fixture()
def squash_repo(tmp_path: Path) -> Path:
    """squash 合并仓库：分支两条提交被压成 main 上一条提交（patch-id 全变）。"""
    r = tmp_path / "repo-beta"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "hygiene")
    _git(r, "config", "user.email", "h@example.invalid")
    _commit(r, "README.md", "base\n", "2021-01-01T00:00:00+0000", "c0")
    _git(r, "checkout", "-q", "-b", "agent/m2001")
    _commit(r, "x.txt", "x\n", "2021-01-02T00:00:00+0000", "x")
    _commit(r, "y.txt", "y\n", "2021-01-03T00:00:00+0000", "y")
    _git(r, "checkout", "-q", "-b", "agent/m2002", "main")
    _commit(r, "z.txt", "z\n", "2021-01-04T00:00:00+0000", "z")
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "--squash", "agent/m2001",
         env={"GIT_AUTHOR_DATE": "2021-01-05T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2021-01-05T00:00:00+0000"})
    _git(r, "commit", "-m", "squash agent/m2001",
         env={"GIT_AUTHOR_DATE": "2021-01-05T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2021-01-05T00:00:00+0000"})
    # 主干事后再改同一个文件 ⇒ 净 diff 不再为空，只剩 squash 指纹可依
    _commit(r, "x.txt", "x\nfollowup\n", "2021-01-06T00:00:00+0000", "tweak x")
    return r


def test_class_A_squash_merged_detected(squash_repo: Path):
    rows = _by_branch(cli._scan(str(squash_repo), "main", "agent/*", False))
    row = rows["agent/m2001"]
    assert row["class"] == CLASS_A
    assert row["decided_by"] == "rule:A-squash-combined-patch/v1"
    assert row["evidence"]["squash_patch_in_base"] is True
    assert row["evidence"]["unique_shas"]                  # 单看 patch-id 会误判未交付
    assert row["evidence"]["net_diff_empty"] is False      # 净 diff 非空：A3 判据承重
    assert rows["agent/m2002"]["class"] == CLASS_C         # 真未交付仍是 C


def test_squash_detection_is_load_bearing(squash_repo: Path):
    off = classify_mod.classify(str(squash_repo), "main",
                                gitio.list_refs(str(squash_repo), "agent/*"),
                                squash_detect=False)
    row = {f["branch"]: f for f in off}["agent/m2001"]
    assert row["class"] == CLASS_C        # 关掉判据就退回误判：证明该判据承重


# ── 超集链必须解析到终点（回归用例） ────────────────────────────────────────────
@pytest.fixture()
def chain_repo(tmp_path: Path) -> Path:
    """三层补丁包含关系：S{P1,P2,P3} ⊃ M{P1,P2} ⊃ T{P1}。

    旧实现里 T 的最优候选是 M，而 M 本身是 B ⇒ T 被退回 C（漏判）。修复后 T 应判 B 且
    ``superset_source`` 指向链终点 S。
    """
    r = tmp_path / "repo-gamma"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "hygiene")
    _git(r, "config", "user.email", "h@example.invalid")
    _commit(r, "README.md", "base\n", "2022-01-01T00:00:00+0000", "c0")
    _git(r, "checkout", "-q", "-b", "agent/m3001")
    _commit(r, "p1.txt", "1\n", "2022-01-02T00:00:00+0000", "P1")
    _commit(r, "p2.txt", "2\n", "2022-01-02T00:00:10+0000", "P2")
    _commit(r, "p3.txt", "3\n", "2022-01-02T00:00:20+0000", "P3")
    _git(r, "checkout", "-q", "-b", "agent/m3002", "main")
    _git(r, "cherry-pick", "agent/m3001~2", "agent/m3001~1",
         env={"GIT_AUTHOR_DATE": "2022-01-03T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2022-01-03T00:00:00+0000"})
    _git(r, "checkout", "-q", "-b", "agent/m3003", "main")
    _git(r, "cherry-pick", "agent/m3001~2",
         env={"GIT_AUTHOR_DATE": "2022-01-04T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2022-01-04T00:00:00+0000"})
    _git(r, "checkout", "-q", "main")
    return r


def test_superset_chain_resolves_to_terminal(chain_repo: Path):
    rows = _by_branch(cli._scan(str(chain_repo), "main", "agent/*", False))
    assert rows["agent/m3001"]["class"] == CLASS_C
    assert rows["agent/m3002"]["class"] == CLASS_B
    assert rows["agent/m3002"]["superset_source"] == "agent/m3001"
    # 关键回归点：T 的最优候选 M 本身是 B，仍必须判 B 并指向终点
    assert rows["agent/m3003"]["class"] == CLASS_B
    assert rows["agent/m3003"]["superset_source"] == "agent/m3001"
    assert rows["agent/m3003"]["evidence"]["superset_extra_commits"] == 2
    assert rows["agent/m3003"]["safe_to_close"] is True


@pytest.fixture()
def equal_set_repo(tmp_path: Path) -> Path:
    """等集簇 {P1} 之上还有一个严格超集 {P1,P2}。

    agent/m5001、agent/m5002 等集（都只有 P1，m5002 tip 更新 ⇒ 旧实现的 canonical）；
    agent/m5003 = {P1,P2} 是二者的严格超集。→ 两条等集分支都该判 B、留存源都是 m5003。
    （真实仓对应 agent/m1938-ship：与 10 条分支等集，同时 {p} ⊂ {p,q}。）
    """
    r = tmp_path / "repo-delta"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "hygiene")
    _git(r, "config", "user.email", "h@example.invalid")
    _commit(r, "README.md", "base\n", "2023-01-01T00:00:00+0000", "c0")
    _git(r, "checkout", "-q", "-b", "agent/m5001")
    _commit(r, "q1.txt", "1\n", "2023-01-02T00:00:00+0000", "P1")
    _git(r, "checkout", "-q", "-b", "agent/m5003", "main")
    _git(r, "cherry-pick", "agent/m5001",
         env={"GIT_AUTHOR_DATE": "2023-01-03T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2023-01-03T00:00:00+0000"})
    _commit(r, "q2.txt", "2\n", "2023-01-03T00:00:10+0000", "P2")
    _git(r, "checkout", "-q", "-b", "agent/m5002", "main")
    _git(r, "cherry-pick", "agent/m5001",
         env={"GIT_AUTHOR_DATE": "2023-01-04T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2023-01-04T00:00:00+0000"})
    _git(r, "checkout", "-q", "main")
    return r


def test_equal_set_group_honours_strict_superset(equal_set_repo: Path):
    rows = _by_branch(cli._scan(str(equal_set_repo), "main", "agent/*", False))
    assert rows["agent/m5003"]["class"] == CLASS_C          # 唯一的极大元
    for name in ("agent/m5001", "agent/m5002"):
        assert rows[name]["class"] == CLASS_B, name
        assert rows[name]["superset_source"] == "agent/m5003", name
        assert rows[name]["safe_to_close"] is True
        assert rows[name]["retention_owner"] == "agent/m5003"


#── 处置计划 ───────────────────────────────────────────────────────────────────# ── 处置计划 ───────────────────────────────────────────────────────────────────
def test_plan_actions_by_class(repo: Path):
    led = _scan(repo)
    plan = {p["branch"]: p for p in dispose.plan_dispositions(led, date_tag="2026-01-01")}
    assert plan["agent/m1001"]["actions"] == [dispose.ACTION_ARCHIVE]
    assert plan["agent/m1002"]["actions"] == [dispose.ACTION_ARCHIVE]
    assert dispose.ACTION_ANNOTATE in plan["agent/m1005"]["actions"]
    assert plan["agent/m1005"]["superset_source"] == "agent/m1004"
    assert plan["agent/m1004"]["actions"] == [dispose.ACTION_TICKET]
    assert plan["agent/m1001"]["archive_ref"] == "refs/attic/2026-01-01/agent/m1001"


def test_plan_never_archives_undelivered(repo: Path):
    led = _scan(repo)
    plan = dispose.plan_dispositions(led, date_tag="2026-01-01")
    archived = {p["branch"] for p in plan if dispose.ACTION_ARCHIVE in p["actions"]}
    c_branches = {r["branch"] for r in led["rows"] if r["class"] == CLASS_C}
    assert archived & c_branches == set()


def test_archive_dry_run_touches_nothing(repo: Path):
    led = _scan(repo)
    plan = dispose.plan_dispositions(led, date_tag="2026-01-01")
    manifest = dispose.apply_plan(str(repo), plan, "2026-01-01", apply=False)
    assert manifest["counts"]["applied"] == 0
    assert gitio.rev_parse(str(repo), "refs/heads/agent/m1001") is not None
    assert gitio.rev_parse(str(repo), "refs/attic/2026-01-01/agent/m1001") is None


def test_archive_apply_moves_refs_and_keeps_objects(repo: Path):
    led = _scan(repo)
    plan = dispose.plan_dispositions(led, date_tag="2026-01-01")
    before = gitio.rev_parse(str(repo), "refs/heads/agent/m1002")
    manifest = dispose.apply_plan(str(repo), plan, "2026-01-01", apply=True)
    assert manifest["counts"]["failed"] == 0
    assert manifest["counts"]["applied"] == 5          # A×2 + D×1 + B×2（m1005/m1006 内容在 m1004）
    assert gitio.rev_parse(str(repo), "refs/heads/agent/m1002") is None
    assert gitio.rev_parse(str(repo), "refs/attic/2026-01-01/agent/m1002") == before
    # 内容仍对象库可达（归档 ref 保住了提交）
    assert gitio.run_git(str(repo), "cat-file", "-e", before)[0] == 0
    # C 类必须原地保留
    assert gitio.rev_parse(str(repo), "refs/heads/agent/m1004") is not None
    res = {r["branch"]: r for r in manifest["results"]}
    assert res["agent/m1002"]["rollback"].startswith("git update-ref refs/heads/agent/m1002 ")


def test_archive_apply_is_idempotent(repo: Path):
    led = _scan(repo)
    plan = dispose.plan_dispositions(led, date_tag="2026-01-01")
    dispose.apply_plan(str(repo), plan, "2026-01-01", apply=True)
    second = dispose.apply_plan(str(repo), plan, "2026-01-01", apply=True)
    assert second["counts"]["applied"] == 0
    assert second["counts"]["failed"] == 0
    assert second["counts"]["skipped"] >= 1


def test_ticket_payload_is_actionable(repo: Path, tmp_path: Path):
    led = _scan(repo)
    paths = dispose.write_tickets(led, str(tmp_path / "tickets"))
    assert len(paths) == 2
    t = json.loads(Path(paths[0]).read_text())
    assert t["kind"] == "delivery"
    assert t["unique_commits"] >= 1
    assert t["priority"] in ("P1", "P2")
    assert "merge" in t["acceptance"]


# ── 只减不增门 ─────────────────────────────────────────────────────────────────
def test_gate_without_baseline_fails(tmp_path: Path, repo: Path):
    led = _scan(repo)
    res = gate.evaluate_gate(led, {"repos": {}})
    assert res["pass"] is False
    assert res["exit_code"] == 1
    assert res["violations"][0]["code"] == "V0-no-baseline"


def test_freeze_then_gate_passes(repo: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    res = gate.evaluate_gate(led, baseline)
    assert res["pass"] is True and res["exit_code"] == 0
    assert res["ceiling"] == res["current_unmerged"] == 4
    assert res["headroom"] == 0


def test_gate_fails_when_branch_count_increases(repo: Path, tmp_path: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    _git(repo, "checkout", "-q", "-b", "agent/m1099", "main")
    _commit(repo, "new.txt", "more\n", "2030-01-01T00:00:00+0000", "new work")
    _git(repo, "checkout", "-q", "main")
    res = gate.evaluate_gate(_scan(repo), baseline)
    assert res["pass"] is False
    assert res["violations"][0]["code"] == "V1-count-exceeds-ceiling"
    assert res["violations"][0]["overflow"] == 1


def test_gate_detects_new_undelivered_only_in_strict_mode(repo: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    _git(repo, "checkout", "-q", "-b", "agent/m1098", "main")
    _commit(repo, "new2.txt", "more\n", "2030-01-01T00:00:00+0000", "new work 2")
    _git(repo, "checkout", "-q", "main")
    led2 = _scan(repo)
    soft = gate.evaluate_gate(led2, baseline, strict_new=False)
    codes = {v["code"] for v in soft["violations"]}
    assert "V2-new-undelivered-since-freeze" not in codes       # 默认只记 warning 字段
    assert soft["new_undelivered_count"] == 1
    hard = gate.evaluate_gate(led2, baseline, strict_new=True)
    assert hard["pass"] is False
    assert "V2-new-undelivered-since-freeze" in {v["code"] for v in hard["violations"]}


def test_gate_base_moved_is_warning_not_failure(repo: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    baseline["repos"][led["repo"]]["base_sha"] = "0" * 40
    res = gate.evaluate_gate(led, baseline)
    assert res["pass"] is True
    assert any(v["code"] == "V4-base-moved-unrebaselined" for v in res["violations"])


def test_ratchet_refuses_increase_without_reason(repo: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    _git(repo, "checkout", "-q", "-b", "agent/m1097", "main")
    _commit(repo, "new3.txt", "more\n", "2030-01-02T00:00:00+0000", "new work 3")
    _git(repo, "checkout", "-q", "main")
    led2 = _scan(repo)
    with pytest.raises(SystemExit):
        gate.freeze_baseline(led2, baseline, min_reduction=0)
    grown = gate.freeze_baseline(led2, baseline, accept_increase="并发在建 3 条，已开补单")
    entry = grown["repos"][led2["repo"]]
    assert entry["ceiling"] > baseline["repos"][led["repo"]]["ceiling"]
    assert any(h["event"] == "accepted-increase" for h in entry["history"])
    assert any(h.get("reason") == "并发在建 3 条，已开补单" for h in entry["history"])


def test_ratchet_allows_decrease(repo: Path):
    led = _scan(repo)
    baseline = gate.freeze_baseline(led, None)
    plan = dispose.plan_dispositions(led, date_tag="2026-01-01")
    dispose.apply_plan(str(repo), plan, "2026-01-01", apply=True)   # A/D/B 归档
    led2 = _scan(repo)
    lowered = gate.freeze_baseline(led2, baseline, min_reduction=2)
    assert lowered["repos"][led["repo"]]["ceiling"] == 2            # 只剩 2 条 C
    assert gate.evaluate_gate(led2, lowered)["pass"] is True


# ── CLI 端到端 ────────────────────────────────────────────────────────────────
def test_cli_scan_freeze_gate_roundtrip(repo: Path, tmp_path: Path, capsys):
    out = tmp_path / "out"
    base_file = tmp_path / "baseline.json"
    assert cli.main(["scan", "--repo", str(repo), "--out", str(out), "--figures"]) == 0
    assert (out / "ledger.json").exists() and (out / "ledger.md").exists()
    assert (out / "disposition_plan.json").exists()
    assert len(list((out / "tickets").glob("*.json"))) == 2
    assert (out / "fig_class_distribution.png").exists()

    assert cli.main(["gate", "--repo", str(repo), "--baseline", str(base_file)]) == 1
    assert cli.main(["freeze", "--repo", str(repo), "--baseline", str(base_file)]) == 0
    json_out = tmp_path / "gate.json"
    assert cli.main(["gate", "--repo", str(repo), "--baseline", str(base_file),
                     "--json-out", str(json_out)]) == 0
    assert json.loads(json_out.read_text())["pass"] is True


def test_cli_gzip_ledger_roundtrip(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    led_path = tmp_path / "ledger.json.gz"
    base_file = tmp_path / "baseline.json"
    assert cli.main(["scan", "--repo", str(repo), "--out", str(out),
                     "--ledger-out", str(led_path)]) == 0
    assert led_path.exists() and led_path.stat().st_size > 0
    assert json.loads((out / "ledger.json").read_text() if (out / "ledger.json").exists()
                      else '{"repo": null}')  # 默认路径未被写：显式给了 --ledger-out
    assert cli.main(["freeze", "--ledger", str(led_path), "--baseline", str(base_file)]) == 0
    assert cli.main(["gate", "--ledger", str(led_path), "--baseline", str(base_file)]) == 0


def test_cli_writes_aggregate_ticket_list(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    assert cli.main(["scan", "--repo", str(repo), "--out", str(out)]) == 0
    lines = [ln for ln in (out / "tickets.jsonl").read_text().splitlines() if ln.strip()]
    assert len(lines) == 2                       # 合成仓里两条 C 类
    for ln in lines:
        t = json.loads(ln)
        assert t["kind"] == "delivery" and t["branch"].startswith("agent/")


def test_cli_apply_requires_double_confirmation(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    with pytest.raises(SystemExit):
        cli.main(["scan", "--repo", str(repo), "--out", str(out), "--apply"])
    assert gitio.rev_parse(str(repo), "refs/heads/agent/m1001") is not None


def test_cli_apply_executes_plan(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    assert cli.main(["scan", "--repo", str(repo), "--out", str(out)]) == 0
    assert cli.main(["apply", "--repo", str(repo),
                     "--plan-file", str(out / "disposition_plan.json"),
                     "--manifest", str(out / "m.json"), "--i-mean-it"]) == 0
    m = json.loads((out / "m.json").read_text())
    assert m["apply"] is True and m["counts"]["applied"] == 5


# ── 纪律守卫：零领域语义 ───────────────────────────────────────────────────────
DOMAIN_WORDS = ("resistance", "hydrodynam", "seakeeping", "icebreaking", "lbm", "fvm",
                "fem ", "solver", "hull", "draft", "propuls", "manoeuvr", "wave")


def test_module_has_no_domain_vocabulary():
    pkg = Path(__file__).resolve().parents[1] / "tools" / "delivery_hygiene"
    for py in sorted(pkg.glob("*.py")):
        text = py.read_text(encoding="utf-8").lower()
        hits = [w for w in DOMAIN_WORDS if w in text]
        assert hits == [], f"{py.name} 含领域词条 {hits}（零领域语义铁律）"