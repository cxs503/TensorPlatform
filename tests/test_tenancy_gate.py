"""tenancy_gate（多租户并发写冲突检测与合并预演门）测试。

覆盖：
  * 路径模式相交判定内核（含反例：不相交/越界写法）
  * 派单前租约门：预注册 12 个必检出 + 8 个零假阳性场景、降级策略、只读租约、过期租约
  * 收单冲突矩阵：文件级/租约级/范围级 lane、窗口边界、非法路径
  * 推送前合并预演：clean / conflict（含 hunk 级）/ 三种 unknown（fail-closed）+ merge-tree 失败注入
  * 台账：幂等重放、查询、链表校验（篡改必被发现）、结论复算
  * 零领域语义（预注册 deny-list）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.tenancy_gate import cli  # noqa: E402
from tools.tenancy_gate import ledger as L  # noqa: E402
from tools.tenancy_gate import rehearsal as R  # noqa: E402
from tools.tenancy_gate.config import load_config  # noqa: E402
from tools.tenancy_gate.intake import conflict_matrix  # noqa: E402
from tools.tenancy_gate.lease import check_lease  # noqa: E402
from tools.tenancy_gate.model import FinishRecord, PathLease, TenancyError, canonical_json  # noqa: E402
from tools.tenancy_gate.pattern import (  # noqa: E402
    GlobSyntaxError,
    glob_covers_path,
    globs_intersect,
    normalize_glob,
    segments_intersect,
)
from tools.tenancy_gate.semantics import load_denylist, scan_tree  # noqa: E402

PKG_DIR = REPO_ROOT / "tools" / "tenancy_gate"
SCENARIOS = json.loads((PKG_DIR / "scenarios.json").read_text(encoding="utf-8"))
NOW = "2026-11-09T00:00:00Z"

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
def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=GIT_ENV, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=GIT_ENV)
    (path / "base.txt").write_text("l1\nl2\nl3\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return path


def branch_with(path: Path, branch: str, rel: str, content: str) -> None:
    git(path, "checkout", "-q", "-B", branch, "main")
    target = path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(path, "add", rel)
    git(path, "commit", "-qm", branch)


@pytest.fixture()
def cfgrepo(tmp_path):
    return make_repo(tmp_path / "repo")


# ─────────────────────────── 1. 模式内核 ───────────────────────────
@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("a.py", "a.py", True),
        ("*.py", "a.py", True),
        ("a*.py", "b*.py", False),
        ("?", "x", True),
        ("??", "x", False),
        ("core/*.py", "core/engine.py", True),
    ],
)
def test_segments_intersect(a, b, expected):
    assert segments_intersect(a, b) is expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("src/**", "src/core/engine.py", True),
        ("**", "anything/deep/file.txt", True),
        ("pkg/**/internal/*.py", "pkg/a/internal/b.py", True),
        ("docs/m*/report.md", "docs/m7876/report.md", True),
        ("pkg/a/**", "pkg/b/**", False),
        ("docs/a*.md", "docs/b*.md", False),
        ("src/**", "tests/**", False),
    ],
)
def test_globs_intersect(a, b, expected):
    assert globs_intersect(a, b) is expected


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "pkg/[a-z].py", "pkg/**x/a.py", ""])
def test_normalize_rejects_unsupported(bad):
    with pytest.raises(GlobSyntaxError):
        normalize_glob(bad)


def test_glob_covers_path_and_prefix_stripping():
    assert glob_covers_path("src/**", "src/a/b.py") is True
    assert glob_covers_path("src/*.py", "src/a/b.py") is False
    assert normalize_glob("./src//core/./a.py") == "src/core/a.py"


# ─────────────────────────── 2. 派单前租约门 ───────────────────────────
@pytest.mark.parametrize("scenario", SCENARIOS["detect"], ids=[s["id"] for s in SCENARIOS["detect"]])
def test_registered_detect_scenarios(scenario):
    cfg = load_config(None, scenario.get("config") or {})
    expect = scenario["expect"]
    if scenario["kind"] == "lease":
        leases = [PathLease.from_dict(x) for x in scenario["active"]]
        decision = check_lease(PathLease.from_dict(scenario["candidate"]), leases, cfg, now_ts=NOW)
        assert decision["admitted"] is expect["admitted"]
        assert decision["reason"] == expect["reason"]
        assert len(decision["conflicts"]) == expect["conflicts"]
    else:
        finish = FinishRecord.from_dict(scenario["finish"])
        matrix = conflict_matrix(finish, scenario["peers"], cfg, now_ts=NOW)
        assert matrix["decision"] == expect["decision"]
        assert matrix["reason"] == expect["reason"]
        assert matrix["conflict_pairs"] == expect["conflict_pairs"]
        for lane in expect.get("lanes", []):
            assert lane in {lane for pair in matrix["pairs"] for lane in pair["lanes"]}


@pytest.mark.parametrize("scenario", SCENARIOS["clear"], ids=[s["id"] for s in SCENARIOS["clear"]])
def test_registered_clear_scenarios(scenario):
    cfg = load_config(None, scenario.get("config") or {})
    expect = scenario["expect"]
    if scenario["kind"] == "lease":
        leases = [PathLease.from_dict(x) for x in scenario["active"]]
        decision = check_lease(PathLease.from_dict(scenario["candidate"]), leases, cfg, now_ts=NOW)
        assert decision["admitted"] is expect["admitted"]
        assert len(decision["conflicts"]) == 0
        if "expired_dropped" in expect:
            assert decision["expired_dropped"] == expect["expired_dropped"]
    else:
        finish = FinishRecord.from_dict(scenario["finish"])
        matrix = conflict_matrix(finish, scenario["peers"], cfg, now_ts=NOW)
        assert matrix["decision"] == expect["decision"]
        assert matrix["conflict_pairs"] == 0
        if "risk_pairs" in expect:
            assert matrix["risk_pairs"] == expect["risk_pairs"]
        if "peers_out_of_window" in expect:
            assert len(matrix["peers_out_of_window"]) == expect["peers_out_of_window"]


def test_lease_conflict_can_downgrade_to_readonly():
    cfg = load_config(None, {"lease": {"on_conflict": "downgrade_readonly"}})
    leases = [PathLease.from_dict({"repo": "T", "path_glob": "pkg/**", "mission_id": "m1", "ts": NOW})]
    decision = check_lease(
        PathLease.from_dict({"repo": "T", "path_glob": "pkg/x.py", "mission_id": "m2", "ts": NOW}), leases, cfg
    )
    assert decision["admitted"] is True and decision["granted_mode"] == "read"
    assert decision["reason"] == "lease_conflict_downgraded_readonly"


def test_read_lease_candidate_never_blocks():
    cfg = load_config(None)
    leases = [PathLease.from_dict({"repo": "T", "path_glob": "pkg/**", "mission_id": "m1", "ts": NOW})]
    decision = check_lease(
        PathLease.from_dict({"repo": "T", "path_glob": "pkg/x.py", "mission_id": "m2", "ts": NOW, "mode": "read"}),
        leases, cfg,
    )
    assert decision["admitted"] is True and decision["granted_mode"] == "read" and decision["conflicts"] == []


def test_lease_decision_is_deterministic():
    cfg = load_config(None)
    leases = [PathLease.from_dict({"repo": "T", "path_glob": "pkg/**", "mission_id": "m1", "ts": NOW})]
    cand = PathLease.from_dict({"repo": "T", "path_glob": "pkg/x.py", "mission_id": "m2", "ts": NOW})
    a, b = check_lease(cand, leases, cfg), check_lease(cand, leases, cfg)
    assert canonical_json(a) == canonical_json(b) and a["digest"] == b["digest"]


# ─────────────────────────── 3. 收单冲突矩阵 ───────────────────────────
def test_intake_invalid_path_blocks():
    cfg = load_config(None)
    finish = FinishRecord.from_dict({"mission_id": "m1", "repo": "T", "changed_files": ["../escape.py"], "ts": NOW})
    matrix = conflict_matrix(finish, [], cfg, now_ts=NOW)
    assert matrix["decision"] == "block" and matrix["reason"] == "invalid_path"
    assert matrix["finish"]["invalid_files"] == ["../escape.py"]


def test_intake_window_boundary_is_inclusive():
    cfg = load_config(None, {"intake": {"window_seconds": 3600}})
    finish = FinishRecord.from_dict({"mission_id": "m1", "repo": "T", "changed_files": ["a.py"], "ts": NOW})
    inside = [{"mission_id": "m2", "repo": "T", "changed_files": ["a.py"], "ts": "2026-11-08T23:00:00Z"}]
    outside = [{"mission_id": "m3", "repo": "T", "changed_files": ["a.py"], "ts": "2026-11-08T22:59:59Z"}]
    assert conflict_matrix(finish, inside, cfg, now_ts=NOW)["conflict_pairs"] == 1
    m2 = conflict_matrix(finish, outside, cfg, now_ts=NOW)
    assert m2["conflict_pairs"] == 0 and m2["peers_out_of_window"] == ["m3"]


def test_intake_read_only_peer_lease_is_not_breached():
    cfg = load_config(None)
    finish = FinishRecord.from_dict({"mission_id": "m1", "repo": "T", "changed_files": ["pkg/a.py"], "ts": NOW})
    peers = [{"mission_id": "m2", "repo": "T", "changed_files": ["pkg/a.py"], "ts": NOW,
              "lease": {"repo": "T", "path_glob": "pkg/**", "mission_id": "m2", "ts": NOW, "mode": "read"}}]
    matrix = conflict_matrix(finish, peers, cfg, now_ts=NOW)
    assert "lease_breach" not in {lane for pair in matrix["pairs"] for lane in pair["lanes"]}
    assert matrix["conflict_pairs"] == 1          # 文件级重叠仍算冲突


def test_intake_self_is_excluded_from_peers():
    cfg = load_config(None)
    finish = FinishRecord.from_dict({"mission_id": "m1", "repo": "T", "changed_files": ["a.py"], "ts": NOW})
    peers = [{"mission_id": "m1", "repo": "T", "changed_files": ["a.py"], "ts": NOW}]
    matrix = conflict_matrix(finish, peers, cfg, now_ts=NOW)
    assert matrix["decision"] == "admit" and matrix["pairs"] == []


# ─────────────────────────── 4. 推送前合并预演 ───────────────────────────
def test_rehearse_clean_pair(cfgrepo):
    branch_with(cfgrepo, "ours", "o.txt", "ours\n")
    branch_with(cfgrepo, "theirs", "t.txt", "theirs\n")
    res = R.rehearse_pair(str(cfgrepo), "ours", "theirs", load_config(None))
    assert res["state"] == "clean" and res["allowed"] is True and res["reason"] == "merge_clean"
    assert res["base"] and res["conflicted_paths"] == []


def test_rehearse_reports_hunk_level_conflict(cfgrepo):
    branch_with(cfgrepo, "ours", "base.txt", "l1\nOURS\nl3\n")
    branch_with(cfgrepo, "theirs", "base.txt", "l1\nTHEIRS\nl3\n")
    res = R.rehearse_pair(str(cfgrepo), "ours", "theirs", load_config(None))
    assert res["state"] == "conflict" and res["allowed"] is False
    assert res["conflicted_paths"] == ["base.txt"]
    hunk = res["hunks"][0]
    assert hunk["path"] == "base.txt" and hunk["kind"] == "content"
    assert hunk["overlap"], hunk
    assert hunk["ours"] and hunk["theirs"]


@pytest.mark.parametrize("case", ["repo_missing", "repo_not_a_git_dir", "ref_unresolved"])
def test_rehearse_fail_closed_unknown(cfgrepo, tmp_path, case):
    cfg = load_config(None)
    if case == "repo_missing":
        res = R.rehearse_pair(str(tmp_path / "nope"), "main", "main", cfg)
        assert res["reason"] == "repo_missing"
    elif case == "repo_not_a_git_dir":
        plain = tmp_path / "plain"
        plain.mkdir()
        res = R.rehearse_pair(str(plain), "main", "main", cfg)
        assert res["reason"] == "repo_not_a_git_dir"
    else:
        res = R.rehearse_pair(str(cfgrepo), "main", "ghost", cfg)
        assert res["reason"] == "ref_unresolved"
    assert res["state"] == "unknown" and res["allowed"] is False


def test_rehearse_no_merge_base_is_unknown(tmp_path):
    repo = tmp_path / "orphan"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "left", str(repo)], check=True, env=GIT_ENV)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "left")
    git(repo, "checkout", "--orphan", "right")
    git(repo, "rm", "-rq", "--cached", ".")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "right")
    res = R.rehearse_pair(str(repo), "left", "right", load_config(None))
    assert res["state"] == "unknown" and res["allowed"] is False and res["reason"] == "no_merge_base"


def test_rehearse_merge_tree_failure_is_unknown(cfgrepo, monkeypatch):
    """merge-tree 返回非 0/1 退出码时必须 unknown 且不放行（fail-closed 分支注入）。"""
    monkeypatch.setattr(R, "_merge_tree", lambda *a, **k: (2, "", [], "", "boom"))
    branch_with(cfgrepo, "ours", "o.txt", "ours\n")
    res = R.rehearse_pair(str(cfgrepo), "ours", "main", load_config(None))
    assert res["state"] == "unknown" and res["allowed"] is False
    assert res["reason"] == "merge_tree_failed_rc2"


def test_rehearse_batch_prescreen_skips_disjoint_pairs(cfgrepo, monkeypatch):
    calls = {"n": 0}
    real = R.rehearse_pair

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(R, "rehearse_pair", counting)
    branch_with(cfgrepo, "a1", "a/1.txt", "1\n")
    branch_with(cfgrepo, "a2", "a/2.txt", "2\n")
    branch_with(cfgrepo, "a3", "a/3.txt", "3\n")
    batch = R.rehearse_pairs(str(cfgrepo), [("a1", "a2"), ("a2", "a3")], load_config(None), base="main")
    assert batch["pairs_total"] == 2 and batch["prescreen_clean"] == 2
    assert batch["merge_tree_calls"] == 0 and calls["n"] == 0
    assert batch["state"] == "clean" and batch["allowed"] is True


def test_rehearse_batch_budget_guard_marks_unknown(cfgrepo):
    cfg = load_config(None, {"rehearsal": {"max_merge_tree_calls": 0}})
    branch_with(cfgrepo, "c1", "base.txt", "l1\nC1\nl3\n")
    branch_with(cfgrepo, "c2", "base.txt", "l1\nC2\nl3\n")
    batch = R.rehearse_pairs(str(cfgrepo), [("c1", "c2")], cfg, base="main")
    assert batch["merge_tree_calls"] == 0 and batch["unknown"] == 1
    assert batch["state"] == "unknown" and batch["allowed"] is False


# ─────────────────────────── 5. 台账 ───────────────────────────
def _intake_case():
    scenario = next(s for s in SCENARIOS["detect"] if s["id"] == "I2")
    return FinishRecord.from_dict(scenario["finish"]), scenario["peers"]


def test_ledger_replay_is_byte_identical(tmp_path):
    cfg = load_config(None)
    finish, peers = _intake_case()
    path = tmp_path / "ledger.jsonl"
    blobs = []
    for _ in range(3):
        matrix = conflict_matrix(finish, peers, cfg, now_ts=NOW)
        L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
        blobs.append(path.read_bytes())
    assert len(set(blobs)) == 1
    assert len(L.read_events(path)) == 1
    assert L.verify_chain(path)["ok"] is True


def test_rehearsal_event_is_idempotent_ignoring_elapsed(tmp_path):
    path = tmp_path / "ledger.jsonl"
    base = {"state": "conflict", "allowed": False, "reason": "merge_conflict",
            "conflicted_paths": ["base.txt"], "hunks": [], "repo": "r", "ours": "a", "theirs": "b"}
    L.record_rehearsal(path, {**base, "elapsed_ms": 12.5}, NOW)
    L.record_rehearsal(path, {**base, "elapsed_ms": 99.0}, NOW)
    events = L.read_events(path)
    assert len(events) == 1 and "elapsed_ms" not in events[0]["payload"]
    assert L.verify_chain(path)["ok"] is True


def test_ledger_query_filters(tmp_path):
    path = tmp_path / "ledger.jsonl"
    decision = check_lease(
        PathLease.from_dict({"repo": "TenantA", "path_glob": "pkg/x.py", "mission_id": "m9002", "ts": NOW}),
        [PathLease.from_dict({"repo": "TenantA", "path_glob": "pkg/**", "mission_id": "m9001", "ts": NOW})],
        load_config(None),
    )
    L.record_lease_decision(path, decision, NOW)
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)

    assert len(L.query_events(path)) == 2
    assert len(L.query_events(path, kind="lease_decision")) == 1
    assert [e["kind"] for e in L.query_events(path, mission_id="m2011")] == ["intake_matrix"]
    assert [e["kind"] for e in L.query_events(path, repo="TenantA", kind="lease_decision")] == ["lease_decision"]
    assert L.query_events(path, since="2026-11-10T00:00:00Z") == []


def test_ledger_chain_detects_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
    L.record_lease_decision(path, {"admitted": True, "reason": "admitted"}, NOW)
    assert L.verify_chain(path)["ok"] is True
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"decision":"block"', '"decision":"admit"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = L.verify_chain(path)
    assert report["ok"] is False
    assert {p["problem"] for p in report["problems"]} >= {"event_id_mismatch"}


def test_ledger_recompute_matrices(tmp_path):
    path = tmp_path / "ledger.jsonl"
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
    event = L.read_events(path)[0]
    recomputed = L.recompute_matrix(event, load_config(None))
    assert recomputed["match"] is True and recomputed["recomputed_digest"] == matrix["digest"]


# ─────────────────────────── 5b. 查询 API ───────────────────────────
def test_query_api_filters_and_limit(tmp_path):
    from tools.tenancy_gate import api as A

    path = tmp_path / "ledger.jsonl"
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
    out = A.build_query(path, kind="intake_matrix", repo="TenantA", limit=10)
    assert out["total"] == 1 and out["items"][0]["kind"] == "intake_matrix"
    assert A.build_query(path, kind="lease_decision")["total"] == 0
    assert A.build_query(path, limit=1)["returned"] == 1


def test_query_api_verify_and_recompute(tmp_path):
    from tools.tenancy_gate import api as A

    path = tmp_path / "ledger.jsonl"
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
    event_id = L.read_events(path)[0]["event_id"]
    assert A.build_verify(path)["ok"] is True
    assert A.build_recompute(path, event_id)["match"] is True
    assert A.build_recompute(path, "deadbeef")["error"] == "event_not_found"


def test_query_router_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tools.tenancy_gate import api as A

    path = tmp_path / "ledger.jsonl"
    finish, peers = _intake_case()
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=NOW)
    L.record_intake(path, matrix, finish, peers, NOW, now_ts=NOW)
    event_id = L.read_events(path)[0]["event_id"]

    app = FastAPI()
    app.include_router(A.create_router(path))
    client = TestClient(app)
    assert client.get("/tenancy/conflicts").json()["total"] == 1
    assert client.get("/tenancy/conflicts/verify").json()["ok"] is True
    assert client.post("/tenancy/conflicts/recompute", params={"event_id": event_id}).json()["match"] is True
    assert client.post("/tenancy/conflicts/recompute", params={"event_id": "nope"}).status_code == 404


# ─────────────────────────── 6. 配置 / 语义 / CLI ───────────────────────────
def test_config_rejects_unknown_key():
    with pytest.raises(TenancyError):
        load_config(None, {"lease": {"on_conflicts": "refuse"}})


def test_config_accepts_known_overrides():
    cfg = load_config(None, {"intake": {"window_seconds": 10}})
    assert cfg["intake"]["window_seconds"] == 10 and cfg["lease"]["on_conflict"] == "refuse"


def test_zero_domain_semantics():
    denylist = load_denylist()
    assert denylist["terms"], "denylist must be pre-registered"
    scan = scan_tree(PKG_DIR, denylist)
    assert scan["ok"] is True, scan["hits"]
    assert scan["hit_count"] == 0


def test_cli_exit_codes(tmp_path):
    active = tmp_path / "active.json"
    candidate = tmp_path / "cand.json"
    active.write_text(json.dumps([{"repo": "T", "path_glob": "pkg/**", "mission_id": "m1", "ts": NOW}]), encoding="utf-8")
    candidate.write_text(json.dumps({"repo": "T", "path_glob": "pkg/a.py", "mission_id": "m2", "ts": NOW}), encoding="utf-8")
    ledger = tmp_path / "l.jsonl"
    code = cli.main(["--ledger", str(ledger), "lease-check", "--active", str(active),
                     "--candidate", str(candidate)])
    assert code == cli.EXIT_BLOCK
    candidate.write_text(json.dumps({"repo": "T", "path_glob": "other/a.py", "mission_id": "m3", "ts": NOW}), encoding="utf-8")
    code = cli.main(["--ledger", str(ledger), "lease-check", "--active", str(active),
                     "--candidate", str(candidate)])
    assert code == cli.EXIT_ADMIT
    assert cli.main(["--ledger", str(ledger), "verify"]) == cli.EXIT_ADMIT