"""调度器标准门（tenancy_gate.scheduler_gate / dispatch_hook）回归测试。

覆盖本节点（P27W7-N09）的机器可算判据：
  * 路径预留：同路径并发任务 100% 检出冲突；不排队/排队/降级三种处置
  * changed_files 交集：重叠必命中，不重叠必无冲突
  * 三方合并 dry-run：可自动合并 → exit 0；内容冲突 → 1（人工仲裁）；无法判定 → 2（回滚）
  * 负控：无重叠路径/文件无冲突；单任务独占路径无冲突
  * 排队 FIFO 与释放后自动放行（幂等）
  * 派单器接线（dispatch_hook / dispatch_helper.gate_preflight）三种模式
  * 零领域语义（把新模块本身过一遍预注册 denylist）
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.tenancy_gate import dispatch_hook as H  # noqa: E402
from tools.tenancy_gate.config import load_config  # noqa: E402
from tools.tenancy_gate.ledger import read_events, verify_chain  # noqa: E402
from tools.tenancy_gate.model import TenancyError  # noqa: E402
from tools.tenancy_gate.scheduler_gate import (  # noqa: E402
    EXIT_MANUAL,
    EXIT_PASS,
    EXIT_QUEUED,
    EXIT_REFUSED,
    EXIT_ROLLBACK,
    SchedulerGate,
)
from tools.tenancy_gate.semantics import scan_tree  # noqa: E402

PKG_DIR = REPO_ROOT / "tools" / "tenancy_gate"
T0 = "2026-11-09T00:00:00Z"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "gate",
    "GIT_AUTHOR_EMAIL": "gate@example.invalid",
    "GIT_COMMITTER_NAME": "gate",
    "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/tmp",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


# ─────────────────────────── 夹具 ───────────────────────────
@pytest.fixture()
def gate(tmp_path: Path) -> SchedulerGate:
    return SchedulerGate(state_path=tmp_path / "state.json",
                          ledger_path=tmp_path / "ledger.jsonl")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=GIT_ENV, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def mkrepo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "shared.txt").write_text("".join(f"line{i}\n" for i in range(1, 41)), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def branch_with_edit(repo: Path, branch: str, relpath: str, content: str) -> None:
    git(repo, "checkout", "-q", "-B", branch, "main")
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{branch} edit {relpath}")
    git(repo, "checkout", "-q", "main")


def write_lines(repo: Path, branch: str, indices, replacement: str) -> None:
    """在 main 的 shared.txt 上改指定行号（用于构造同文件不同/相同 hunk）。"""
    git(repo, "checkout", "-q", "-B", branch, "main")
    lines = (repo / "shared.txt").read_text(encoding="utf-8").splitlines()
    for idx in indices:
        lines[idx - 1] = replacement.format(i=idx)
    (repo / "shared.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{branch} touch {sorted(indices)}")
    git(repo, "checkout", "-q", "main")


# ───────────────── 1. 路径预留：同路径并发任务 100% 检出 ─────────────────
SAME_PATH_PAIRS = [
    ("src/**", "src/foo.py"),
    ("src/**", "src/**"),
    ("src/*.py", "src/*.py"),
    ("docs/**", "docs/x.md"),
    ("a/b/**", "a/*/c.py"),
    ("pkg/*/mod.py", "pkg/*/mod.py"),
    ("a/*/c.py", "a/*/c.py"),
    ("pkg/**", "pkg/core/mod.py"),
    ("tools/*/x.py", "tools/*/x.py"),
    ("x/**", "x/y/**"),
    ("x/y/**", "x/y/z.py"),
]


@pytest.mark.parametrize("first,second", SAME_PATH_PAIRS)
def test_same_path_concurrency_always_conflicts(gate: SchedulerGate, first: str, second: str):
    a = gate.reserve("m1", "Repo", [first], ts=T0)
    assert a["resolution"] == "pass" and a["exit_code"] == EXIT_PASS
    b = gate.reserve("m2", "Repo", [second], ts="2026-11-09T00:01:00Z")
    assert b["resolution"] == "queued", b["reasons"]
    assert b["exit_code"] == EXIT_QUEUED
    assert [c["mission_id"] for c in b["conflicts"]] == ["m1"]


DISJOINT_PAIRS = [
    ("src/**", "docs/**"),
    ("tests/a/**", "tools/b/**"),
    ("src/*.py", "src/sub/*.py"),
]


@pytest.mark.parametrize("first,second", DISJOINT_PAIRS)
def test_negative_control_disjoint_paths_no_conflict(gate: SchedulerGate, first: str, second: str):
    assert gate.reserve("m1", "Repo", [first], ts=T0)["resolution"] == "pass"
    b = gate.reserve("m2", "Repo", [second], ts="2026-11-09T00:01:00Z")
    assert b["resolution"] == "pass" and b["exit_code"] == EXIT_PASS
    assert b["conflicts"] == []


def test_negative_control_single_task_exclusive_path(gate: SchedulerGate):
    out = gate.reserve("solo", "Repo", ["private/**", "private/a.py"], ts=T0)
    assert out["resolution"] == "pass" and out["exit_code"] == EXIT_PASS
    assert out["queue_position"] is None and out["granted_mode"] == "write"


def test_reserve_idempotent_restatement(gate: SchedulerGate):
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    again = gate.reserve("m1", "Repo", ["src/**"], ts="2026-11-09T00:00:30Z")
    assert again["resolution"] == "pass"
    assert len(gate.active_reservations("2026-11-09T00:00:31Z")) == 1


@pytest.mark.parametrize("pattern", ["**", "**/foo.py", ".git/**"])
def test_leading_globstar_touching_protected_is_refused_fail_closed(gate: SchedulerGate, pattern: str):
    """前导 `**` 的模式"可能"落在受保护面内 → 直接拒派（宁可拒派也不放行未知写面）。"""
    out = gate.reserve("m1", "Repo", [pattern], ts=T0)
    assert out["resolution"] == "refused" and out["exit_code"] == EXIT_REFUSED
    assert "protected_path" in out["reasons"]


def test_protected_path_refused_not_queued(gate: SchedulerGate):
    out = gate.reserve("m1", "Repo", [".git/config"], ts=T0)
    assert out["resolution"] == "refused" and out["exit_code"] == EXIT_REFUSED
    assert "protected_path" in out["reasons"]
    assert gate.state["queue"] == []


def test_cross_repo_same_path_no_conflict(gate: SchedulerGate):
    gate.reserve("m1", "RepoA", ["src/**"], ts=T0)
    out = gate.reserve("m2", "RepoB", ["src/**"], ts="2026-11-09T00:01:00Z")
    assert out["resolution"] == "pass" and out["exit_code"] == EXIT_PASS


def test_downgrade_readonly_policy(tmp_path: Path):
    cfg = load_config(overrides={"scheduler": {"on_conflict": "downgrade_readonly"}})
    gate = SchedulerGate(config=cfg, state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl")
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    out = gate.reserve("m2", "Repo", ["src/**"], ts="2026-11-09T00:01:00Z")
    assert out["resolution"] == "pass" and out["granted_mode"] == "read"
    assert out["exit_code"] == EXIT_PASS


def test_refuse_policy(tmp_path: Path):
    cfg = load_config(overrides={"scheduler": {"on_conflict": "refuse"}})
    gate = SchedulerGate(config=cfg, state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl")
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    out = gate.reserve("m2", "Repo", ["src/x.py"], ts="2026-11-09T00:01:00Z")
    assert out["resolution"] == "refused" and out["exit_code"] == EXIT_REFUSED


def test_queue_limit_refuses(tmp_path: Path):
    cfg = load_config(overrides={"scheduler": {"queue_limit": 1}})
    gate = SchedulerGate(config=cfg, state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.jsonl")
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    assert gate.reserve("m2", "Repo", ["src/a.py"], ts="2026-11-09T00:01:00Z")["resolution"] == "queued"
    third = gate.reserve("m3", "Repo", ["src/b.py"], ts="2026-11-09T00:02:00Z")
    assert third["resolution"] == "refused" and third["exit_code"] == EXIT_REFUSED


# ───────────────── 2. 排队 FIFO 与释放后自动放行 ─────────────────
def test_queue_fifo_drain_after_release(gate: SchedulerGate):
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    gate.reserve("m2", "Repo", ["src/a.py"], ts="2026-11-09T00:01:00Z")
    gate.reserve("m3", "Repo", ["src/b.py"], ts="2026-11-09T00:02:00Z")
    assert [q["mission_id"] for q in gate.state["queue"]] == ["m2", "m3"]

    first = gate.drain_queue(ts="2026-11-09T00:03:00Z")
    assert first["admitted"] == []          # m1 仍占着 src/**
    assert [e["mission_id"] for e in first["still_queued"]] == ["m2", "m3"]
    assert all(e["attempts"] == 1 for e in first["still_queued"])

    gate.release("m1", repo="Repo", ts="2026-11-09T00:04:00Z")
    second = gate.drain_queue(ts="2026-11-09T00:05:00Z")
    assert [a["mission_id"] for a in second["admitted"]] == ["m2", "m3"]

    third = gate.drain_queue(ts="2026-11-09T00:06:00Z")
    assert third["checked"] == 0 and third["admitted"] == []


def test_expired_lease_frees_path(gate: SchedulerGate):
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    late = "2026-11-09T12:00:00Z"      # 默认 ttl=6h，m1 已过期
    out = gate.reserve("m2", "Repo", ["src/a.py"], ts=late)
    assert out["resolution"] == "pass" and out["exit_code"] == EXIT_PASS


def test_adopt_pending_key_to_real_mission(gate: SchedulerGate):
    gate.reserve("pending:abc", "Repo", ["src/**"], ts=T0)
    moved = gate.adopt("pending:abc", "m42", repo="Repo")
    assert moved["moved"] == 1
    out = gate.reserve("m43", "Repo", ["src/x.py"], ts="2026-11-09T00:01:00Z")
    assert [c["mission_id"] for c in out["conflicts"]] == ["m42"]


# ───────────────── 3. changed_files 交集检测 ─────────────────
@pytest.mark.parametrize(
    "mine,theirs,expected",
    [
        (["src/a.py"], ["src/a.py"], ["src/a.py"]),
        (["src/a.py", "src/b.py"], ["src/b.py", "src/c.py"], ["src/b.py"]),
        (["docs/x.md"], ["docs/x.md", "src/a.py"], ["docs/x.md"]),
        (["a/./b.py"], ["a/b.py"], ["a/b.py"]),
    ],
)
def test_changed_files_overlap_hits(gate: SchedulerGate, mine, theirs, expected):
    done = gate.complete("peer", "Repo", theirs, ts="2026-11-09T00:00:00Z")
    assert done["resolution"] == "pass"
    out = gate.complete("me", "Repo", mine, ts="2026-11-09T00:01:00Z")
    overlap = sorted({f for p in out["pairs"] for f in p["file_overlap"]})
    assert overlap == sorted(expected), out["pairs"]
    assert out["conflict_pairs"] == 1
    assert out["resolution"] == "refused" and out["exit_code"] == EXIT_REFUSED


@pytest.mark.parametrize(
    "mine,theirs",
    [
        (["src/a.py"], ["src/b.py"]),
        (["docs/x.md"], ["tests/y.py"]),
        (["a/b.py"], ["a/c.py"]),
    ],
)
def test_negative_control_changed_files_no_overlap(gate: SchedulerGate, mine, theirs):
    gate.complete("peer", "Repo", theirs, ts="2026-11-09T00:00:00Z")
    out = gate.complete("me", "Repo", mine, ts="2026-11-09T00:01:00Z")
    assert out["conflict_pairs"] == 0
    assert out["resolution"] == "pass" and out["exit_code"] == EXIT_PASS


def test_changed_files_window_excludes_old_peer(gate: SchedulerGate):
    gate.complete("peer", "Repo", ["src/a.py"], ts="2026-11-07T00:00:00Z")
    out = gate.complete("me", "Repo", ["src/a.py"], ts="2026-11-09T00:00:00Z")   # 窗口 24h
    assert out["conflict_pairs"] == 0 and out["exit_code"] == EXIT_PASS


def test_changed_files_scope_violation(gate: SchedulerGate):
    gate.reserve("me", "Repo", ["docs/**"], ts=T0)
    out = gate.complete("me", "Repo", ["src/a.py"], path_globs=["docs/**"],
                        ts="2026-11-09T00:01:00Z")
    assert out["scope_violations"] == ["src/a.py"]
    assert out["resolution"] == "refused"


# ───────────────── 4. 三方合并 dry-run ─────────────────
def test_merge_dry_run_disjoint_files_auto_merge(gate: SchedulerGate, tmp_path: Path):
    repo = mkrepo(tmp_path)
    branch_with_edit(repo, "b1", "f1.txt", "one\n")
    branch_with_edit(repo, "b2", "f2.txt", "two\n")
    out = gate.merge_dry_run(repo, "b1", "b2", ts=T0)
    assert out["state"] == "clean"
    assert out["exit_code"] == EXIT_PASS and out["mergeable"] is True
    assert out["resolution"] == "auto_merge"


def test_merge_dry_run_same_file_different_hunks_auto_merge(gate: SchedulerGate, tmp_path: Path):
    repo = mkrepo(tmp_path)
    write_lines(repo, "b1", [2], "ours-{i}\n")
    write_lines(repo, "b2", [35], "theirs-{i}\n")
    out = gate.merge_dry_run(repo, "b1", "b2", ts=T0)
    assert out["state"] == "clean", out["reason"]
    assert out["exit_code"] == EXIT_PASS and out["resolution"] == "auto_merge"


def test_merge_dry_run_conflict_manual_arbitration(gate: SchedulerGate, tmp_path: Path):
    repo = mkrepo(tmp_path)
    write_lines(repo, "b1", [10], "ours-{i}\n")
    write_lines(repo, "b2", [10], "theirs-{i}\n")
    out = gate.merge_dry_run(repo, "b1", "b2", ts=T0)
    assert out["state"] == "conflict"
    assert out["exit_code"] == 1 and out["resolution"] == "manual_arbitration"
    assert out["conflicted_paths"] == ["shared.txt"]
    assert out["hunk_overlaps"]           # hunk 级区间给了仲裁材料


def test_merge_dry_run_unknown_fail_closed(gate: SchedulerGate, tmp_path: Path):
    out = gate.merge_dry_run(tmp_path / "no_such_repo", "b1", "b2", ts=T0)
    assert out["state"] == "unknown"
    assert out["exit_code"] == 2 and out["resolution"] == "rollback"


def test_merge_digest_reproducible_across_independent_repo_copies(gate: SchedulerGate,
                                                                 tmp_path: Path):
    """回归：合并预演 digest 只覆盖判定内容。

    缺陷（本节点修复）：早先 ``merge_dry_run`` 把 ``repo_path`` 与解析出的 commit
    oid 一起进了摘要，导致"同一份内容、两个不同目录"的预演摘要不同——判定明明一样，
    台账却对不上。现在摘要只含 状态/退出码/冲突文件/hunk 区间/ref 名。
    """
    digests = []
    for name in ("copy_a", "copy_b"):
        repo = mkrepo(tmp_path, name)
        write_lines(repo, "ours", [8], "ours-{i}\n")
        write_lines(repo, "theirs", [30], "theirs-{i}\n")
        out = gate.merge_dry_run(repo, "ours", "theirs", ts=T0)
        assert out["state"] == "clean"
        digests.append((out["digest"], out["repo_path"], out["base_oid"]))
    assert digests[0][0] == digests[1][0], "同一判定内容必须同摘要"
    # 观测字段（仓路径）确实不同：摘要不该被它污染
    assert digests[0][1] != digests[1][1]


def test_merge_dry_run_unresolved_ref(gate: SchedulerGate, tmp_path: Path):
    repo = mkrepo(tmp_path)
    out = gate.merge_dry_run(repo, "no-branch-1", "no-branch-2", ts=T0)
    assert out["state"] == "unknown" and out["exit_code"] == 2


# ───────────────── 5. 复合裁决 ─────────────────
def test_decide_pass_full_pipeline(gate: SchedulerGate, tmp_path: Path):
    repo = mkrepo(tmp_path)
    branch_with_edit(repo, "agent/m10", "f1.txt", "one\n")
    branch_with_edit(repo, "agent/m11", "f2.txt", "two\n")
    out = gate.decide("m10", "Repo", paths=["repo/f1.txt"], changed_files=["repo/f1.txt"],
                      repo_path=repo, ours="agent/m10", theirs="agent/m11", ts=T0)
    assert out["exit_code"] == EXIT_PASS and out["passed"] is True
    assert [s["stage"] for s in out["stages"]] == ["reserve", "changed_files", "merge_dry_run"]
    assert verify_chain(gate.ledger_path)["ok"] is True
    kinds = [e["kind"] for e in read_events(gate.ledger_path)]
    assert "gate_decision" in kinds


def test_decide_stops_when_queued(gate: SchedulerGate):
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    out = gate.decide("m2", "Repo", paths=["src/a.py"], changed_files=["src/a.py"],
                      ts="2026-11-09T00:01:00Z")
    assert out["resolution"] == "queued" and out["exit_code"] == EXIT_QUEUED
    assert out["stopped_early"] is True
    assert [s["stage"] for s in out["stages"]] == ["reserve"]


def test_decide_rollback_on_unknown(gate: SchedulerGate, tmp_path: Path):
    out = gate.decide("m9", "Repo", repo_path=tmp_path / "missing", ours="a", theirs="b", ts=T0)
    assert out["resolution"] == "rollback" and out["exit_code"] == EXIT_ROLLBACK


def test_decide_reproducible_digest(gate: SchedulerGate, tmp_path: Path):
    first = gate.decide("m20", "Repo", paths=["src/**"], changed_files=["src/a.py"], ts=T0)
    other = SchedulerGate(state_path=tmp_path / "s2.json", ledger_path=tmp_path / "l2.jsonl")
    second = other.decide("m20", "Repo", paths=["src/**"], changed_files=["src/a.py"], ts=T0)
    assert first["digest"] == second["digest"]


def test_decide_records_are_idempotent(gate: SchedulerGate):
    gate.decide("m30", "Repo", paths=["src/**"], changed_files=["src/a.py"], ts=T0)
    n_before = len(read_events(gate.ledger_path))
    gate2 = SchedulerGate(state_path=gate.state_path, ledger_path=gate.ledger_path)
    gate2.decide("m30", "Repo", paths=["src/**"], changed_files=["src/a.py"], ts=T0)
    assert len(read_events(gate.ledger_path)) == n_before


# ───────────────── 6. 派单器接线（三种模式） ─────────────────
def _env(tmp_path: Path, mode: str) -> dict:
    return {"TENANCY_GATE_MODE": mode,
            "TENANCY_GATE_STATE": str(tmp_path / "state.json"),
            "TENANCY_GATE_LEDGER": str(tmp_path / "ledger.jsonl")}


def test_hook_off_mode_never_touches_state(tmp_path: Path):
    env = _env(tmp_path, "off")
    out = H.pre_dispatch("Repo", ["src/**"], "pending:x", env=env)
    assert out["mode"] == "off" and out["blocked"] is False and out["exit_code"] == EXIT_PASS
    assert not (tmp_path / "state.json").exists()


def test_hook_report_mode_records_but_never_blocks(tmp_path: Path):
    env = _env(tmp_path, "report")
    assert H.pre_dispatch("Repo", ["src/**"], "m1", env=env)["blocked"] is False
    second = H.pre_dispatch("Repo", ["src/a.py"], "m2", env=env)
    assert second["exit_code"] == EXIT_QUEUED and second["blocked"] is False
    assert second["mode"] == "report"
    assert (tmp_path / "state.json").exists()


def test_hook_enforce_mode_blocks_conflict(tmp_path: Path):
    env = _env(tmp_path, "enforce")
    assert H.pre_dispatch("Repo", ["src/**"], "m1", env=env)["blocked"] is False
    blocked = H.pre_dispatch("Repo", ["src/a.py"], "m2", env=env)
    assert blocked["blocked"] is True and blocked["exit_code"] == EXIT_QUEUED
    assert blocked["enforced"] is True


def test_hook_enforce_degrades_fail_closed(tmp_path: Path):
    env = _env(tmp_path, "enforce")
    env["TENANCY_GATE_STATE"] = str(tmp_path / "state.json")
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    out = H.pre_dispatch("Repo", ["src/**"], "m1", env=env)
    assert out["blocked"] is True and out["exit_code"] == EXIT_ROLLBACK


def test_hook_post_finish_merge_gate(tmp_path: Path):
    env = _env(tmp_path, "enforce")
    repo = mkrepo(tmp_path)
    branch_with_edit(repo, "b1", "f1.txt", "a\n")
    branch_with_edit(repo, "b2", "f2.txt", "b\n")
    ok = H.post_finish("Repo", "m1", ["f1.txt"], repo_path=str(repo), ours="b1", theirs="b2",
                       env=env, ts=T0)
    assert ok["exit_code"] == EXIT_PASS and ok["blocked"] is False
    write_lines(repo, "c1", [5], "ours-{i}\n")
    write_lines(repo, "c2", [5], "theirs-{i}\n")
    bad = H.post_finish("Repo", "m2", ["shared.txt"], repo_path=str(repo), ours="c1", theirs="c2",
                        env=env, ts="2026-11-09T00:02:00Z")
    # 复合裁决用调度器退出码词表（0/10/11/12/13）；单阶段 merge_dry_run 用 git 约定 0/1/2
    assert bad["exit_code"] == EXIT_MANUAL and bad["resolution"] == "manual_arbitration"
    assert bad["blocked"] is True


def test_hook_adopt_and_release(tmp_path: Path):
    env = _env(tmp_path, "report")
    H.pre_dispatch("Repo", ["src/**"], "pending:z", env=env)
    assert H.adopt_mission("pending:z", "m77", "Repo", env=env)["moved"] == 1
    assert H.release_mission("m77", "Repo", env=env)["dropped"] == 1


def test_dispatch_helper_wires_gate(tmp_path: Path, monkeypatch):
    """平台派单器（tools/agent/dispatch_helper.py）确实带着门：函数可导入且走 report。"""
    path = REPO_ROOT / "tools" / "agent" / "dispatch_helper.py"
    spec = importlib.util.spec_from_file_location("dispatch_helper_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    monkeypatch.setenv("TENANCY_GATE_MODE", "report")
    monkeypatch.setenv("TENANCY_GATE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("TENANCY_GATE_LEDGER", str(tmp_path / "ledger.jsonl"))
    first = module.gate_preflight("Repo", ["src/**"], "pending:a")
    second = module.gate_preflight("Repo", ["src/a.py"], "pending:b")
    assert first["exit_code"] == EXIT_PASS
    assert second["exit_code"] == EXIT_QUEUED           # 门在工作
    assert second["blocked"] is False                   # 默认 report 不拦单
    assert module.gate_post_finish("Repo", "m1", ["src/a.py"])["exit_code"] in (0, EXIT_REFUSED)


def test_hook_rejects_bad_mode_falls_back_to_report():
    assert H.gate_mode({"TENANCY_GATE_MODE": "banana"}) == "report"
    assert H.gate_mode({}) == "report"
    assert H.gate_mode({"TENANCY_GATE_MODE": "enforce"}) == "enforce"


def test_reserve_requires_paths(gate: SchedulerGate):
    with pytest.raises(TenancyError):
        gate.reserve("m1", "Repo", [], ts=T0)


# ───────────────── 7. 零领域语义 ─────────────────
def test_new_modules_have_zero_domain_semantics():
    scan = scan_tree(PKG_DIR)
    assert scan["ok"] is True, scan["hits"][:5]
    assert scan["hit_count"] == 0


def test_state_file_is_deterministic(gate: SchedulerGate, tmp_path: Path):
    gate.reserve("m1", "Repo", ["src/**"], ts=T0)
    first = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    gate2 = SchedulerGate(state_path=tmp_path / "s2.json", ledger_path=tmp_path / "l2.jsonl")
    gate2.reserve("m1", "Repo", ["src/**"], ts=T0)
    second = json.loads((tmp_path / "s2.json").read_text(encoding="utf-8"))
    assert first["digest"] == second["digest"]
    assert first["reservations"] == second["reservations"]