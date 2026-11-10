"""capability_reconciler 测试套件（战役节点 p176w1-n03 机器判据 C1–C5）。

判据映射
--------
C1 注入 10 条已知失配 → 100% 检出（test_inject_ten_known_mismatches_detected）
C2 零失配场景 → 失配率 = 0（test_zero_mismatch_rate）
C3 report_owed 列表与实际欠账 mission 一致（test_report_owed_*）
C4 负控：空表 → 优雅返回 0 失配（test_empty_ledger_graceful_zero）
C5 pytest ≥15 例（本文件）

全部用例只用 tmp 目录合成数据，零领域语义。
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.capability_reconciler import (  # noqa: E402
    KIND_DECLARED_MISSING,
    KIND_NOT_IN_HEAD,
    KIND_UNDECLARED_PRESENT,
    MISMATCH_KINDS,
    NON_SUBSTANTIVE_CATEGORY,
    CapabilityReconciler,
    DeltaRecord,
    load_deltas_from_db,
    load_deltas_from_json,
    load_report_mission_ids_from_db,
    load_report_mission_ids_from_json,
    main,
    normalize_path,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def make_repo(tmp_path, files=None):
    """建一个含单次 commit 的合成仓库；files 为额外写入的相对路径。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "artifacts").mkdir()
    (repo / "docs").mkdir()
    (repo / "artifacts" / "kept.json").write_text("{}\n")
    (repo / "docs" / "note.md").write_text("note\n")
    (repo / "README.md").write_text("readme\n")
    for rel, content in (files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "config", "user.email", "tester@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "init").returncode == 0
    return repo


def head_sha(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def dangling_commit(repo, label="dangling"):
    """造一个不在任何分支上的 commit sha（非 HEAD 祖先）。"""
    res = _git(repo, "commit-tree", "HEAD^{tree}", "-m", label)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def rec(mid, artifacts, sha=None, category="new_capability"):
    return DeltaRecord(
        mission_id=mid,
        category=category,
        artifacts=list(artifacts),
        evidence={"head_commit": sha} if sha else {},
        head_commit=sha,
    )


def reconciler(repo, roots=("artifacts",), check_git=True):
    return CapabilityReconciler(
        repo_root=repo, artifact_roots=roots, check_git=check_git
    )


# ---------------------------------------------------------------------------
# C2 零失配
# ---------------------------------------------------------------------------


def test_zero_mismatch_rate(tmp_path):
    repo = make_repo(tmp_path)
    report = reconciler(repo).reconcile(
        [rec(1, ["artifacts/kept.json", "docs/note.md"], head_sha(repo))],
        report_mission_ids=[1],
    )
    payload = report.to_dict()
    assert payload["mismatch_count"] == 0
    assert payload["mismatch_rate"] == 0.0
    # 分母非平凡：确实检查了路径/文件/commit
    assert payload["scope"]["total_checks"] >= 3
    assert payload["by_kind"] == {
        KIND_DECLARED_MISSING: 0,
        KIND_UNDECLARED_PRESENT: 0,
        KIND_NOT_IN_HEAD: 0,
    }


# ---------------------------------------------------------------------------
# 第 1 类：声明了但不存在
# ---------------------------------------------------------------------------


def test_declared_missing_detected(tmp_path):
    repo = make_repo(tmp_path)
    report = reconciler(repo).reconcile([rec(7, ["artifacts/ghost.json"])])
    items = report.to_dict()["mismatches"][KIND_DECLARED_MISSING]
    assert len(items) == 1
    assert items[0]["subject"] == "artifacts/ghost.json"
    assert items[0]["mission_ids"] == [7]
    assert items[0]["reason"] == "absent_on_disk"


def test_glob_declaration_without_match_is_missing(tmp_path):
    repo = make_repo(tmp_path)
    report = reconciler(repo).reconcile([rec(1, ["artifacts/*.parquet"])])
    items = report.to_dict()["mismatches"][KIND_DECLARED_MISSING]
    assert len(items) == 1
    assert items[0]["reason"] == "glob_no_match"


# ---------------------------------------------------------------------------
# 第 2 类：存在但没声明
# ---------------------------------------------------------------------------


def test_undeclared_present_detected(tmp_path):
    repo = make_repo(tmp_path, {"artifacts/orphan.bin": "x"})
    report = reconciler(repo).reconcile([rec(1, ["artifacts/kept.json"])])
    items = report.to_dict()["mismatches"][KIND_UNDECLARED_PRESENT]
    assert [i["subject"] for i in items] == ["artifacts/orphan.bin"]
    assert items[0]["reason"] == "no_delta_declares_path"


def test_ignored_patterns_not_flagged_undeclared(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "artifacts/.gitkeep": "",
            "artifacts/__pycache__/x.pyc": "bin",
        },
    )
    report = reconciler(repo).reconcile([rec(1, ["artifacts/kept.json"])])
    assert report.to_dict()["by_kind"][KIND_UNDECLARED_PRESENT] == 0


# ---------------------------------------------------------------------------
# 第 3 类：不在 head
# ---------------------------------------------------------------------------


def test_commit_not_ancestor_detected(tmp_path):
    repo = make_repo(tmp_path)
    orphan = dangling_commit(repo)
    report = reconciler(repo).reconcile(
        [rec(3, ["artifacts/kept.json"], sha=orphan)]
    )
    items = report.to_dict()["mismatches"][KIND_NOT_IN_HEAD]
    assert len(items) == 1
    assert items[0]["subject_type"] == "commit"
    assert items[0]["subject"] == orphan
    assert items[0]["reason"] == "not_ancestor_of_head"


def test_unknown_and_unparsable_commit_flagged(tmp_path):
    repo = make_repo(tmp_path)
    report = reconciler(repo).reconcile(
        [
            rec(1, ["artifacts/kept.json"], sha="deadbeef"),
            rec(2, ["artifacts/kept.json"], sha="not-a-sha"),
        ]
    )
    items = {
        i["subject"]: i["reason"]
        for i in report.to_dict()["mismatches"][KIND_NOT_IN_HEAD]
    }
    assert items == {
        "deadbeef": "not_ancestor_of_head",
        "not-a-sha": "commit_unparsable",
    }


def test_declared_file_untracked_in_head(tmp_path):
    repo = make_repo(tmp_path)
    # 磁盘上存在但未提交（untracked）
    late = repo / "artifacts" / "late.json"
    late.write_text("{}\n")
    report = reconciler(repo).reconcile([rec(5, ["artifacts/late.json"])])
    items = report.to_dict()["mismatches"][KIND_NOT_IN_HEAD]
    assert len(items) == 1
    assert items[0]["subject_type"] == "file"
    assert items[0]["reason"] == "file_untracked_in_head"


def test_git_unavailable_skips_commit_class(tmp_path):
    plain = tmp_path / "plain"
    (plain / "artifacts").mkdir(parents=True)
    (plain / "artifacts" / "kept.json").write_text("{}\n")
    report = reconciler(plain).reconcile(
        [rec(1, ["artifacts/kept.json"], sha="deadbeef")]
    )
    payload = report.to_dict()
    assert payload["git_available"] is False
    assert payload["scope"]["commit_total"] == 0
    assert payload["by_kind"][KIND_NOT_IN_HEAD] == 0
    assert any(w.startswith("git_unavailable") for w in payload["warnings"])


# ---------------------------------------------------------------------------
# C1 注入 10 条已知失配 → 100% 检出
# ---------------------------------------------------------------------------


def test_inject_ten_known_mismatches_detected(tmp_path):
    repo = make_repo(tmp_path, {f"artifacts/orphan{i}.bin": "x" for i in range(1, 4)})
    shas = [dangling_commit(repo, f"dangling-{i}") for i in range(3)]
    records = [
        # 3 条 dangling commit 记录（各带一个合法工件）
        rec(100 + i, ["artifacts/kept.json"], sha=sha) for i, sha in enumerate(shas)
    ] + [
        # 4 条声明了但不存在
        rec(200 + i, [f"artifacts/missing{i}.json"]) for i in range(1, 5)
    ]  # orphan1..3 为 3 条存在但没声明
    report = reconciler(repo).reconcile(records).to_dict()
    detected = report["mismatch_count"]
    assert detected == 10, report["mismatches"]
    assert report["by_kind"] == {
        KIND_DECLARED_MISSING: 4,
        KIND_UNDECLARED_PRESENT: 3,
        KIND_NOT_IN_HEAD: 3,
    }
    # 每条注入失配都能定位到主体/归属
    assert {i["mission_ids"][0] for i in report["mismatches"][KIND_DECLARED_MISSING]} == {
        201,
        202,
        203,
        204,
    }
    assert {i["mission_ids"][0] for i in report["mismatches"][KIND_NOT_IN_HEAD]} == {
        100,
        101,
        102,
    }
    assert all(i["subject"].startswith("artifacts/orphan") for i in report["mismatches"][KIND_UNDECLARED_PRESENT])


# ---------------------------------------------------------------------------
# C4 负控：空台账
# ---------------------------------------------------------------------------


def test_empty_ledger_graceful_zero(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    report = reconciler(plain).reconcile([], report_mission_ids=[])
    payload = report.to_dict()
    assert payload["mismatch_count"] == 0
    assert payload["mismatch_rate"] == 0.0
    assert payload["scope"]["total_checks"] == 0
    assert payload["report_owed"]["count"] == 0


def test_empty_ledger_with_files_still_reports_undeclared(tmp_path):
    """空台账 + 有工件 = 全量未声明（诚实语义，不是静默 0）。"""
    plain = tmp_path / "plain"
    (plain / "artifacts").mkdir(parents=True)
    (plain / "artifacts" / "a.json").write_text("{}\n")
    report = reconciler(plain).reconcile([])
    assert report.to_dict()["by_kind"][KIND_UNDECLARED_PRESENT] == 1


# ---------------------------------------------------------------------------
# C3 report_owed
# ---------------------------------------------------------------------------


def test_report_owed_matches_actual(tmp_path):
    repo = make_repo(tmp_path)
    records = [rec(1, ["artifacts/kept.json"]), rec(2, []), rec(3, [])]
    payload = reconciler(repo).reconcile(
        records, report_mission_ids=[1]
    ).to_dict()
    owed = payload["report_owed"]
    assert owed["check_available"] is True
    assert owed["mission_ids"] == [2, 3]
    assert owed["count"] == 2
    assert owed["declared_missions"] == [1, 2, 3]
    assert owed["reported_missions"] == [1]


def test_report_owed_empty_when_all_reported(tmp_path):
    repo = make_repo(tmp_path)
    payload = reconciler(repo).reconcile(
        [rec(1, []), rec(2, [])], report_mission_ids=[2, 1]
    ).to_dict()
    assert payload["report_owed"]["mission_ids"] == []
    assert payload["report_owed"]["count"] == 0


def test_report_owed_conservative_without_report_source(tmp_path):
    repo = make_repo(tmp_path)
    payload = reconciler(repo).reconcile([rec(4, []), rec(5, [])]).to_dict()
    owed = payload["report_owed"]
    assert owed["check_available"] is False
    assert owed["mission_ids"] == [4, 5]
    assert any(w.startswith("report_source_missing") for w in payload["warnings"])


def test_report_owed_excludes_non_substantive_by_default(tmp_path):
    repo = make_repo(tmp_path)
    records = [
        rec(1, [], category=NON_SUBSTANTIVE_CATEGORY),
        rec(2, [], category="defect_fix"),
    ]
    payload = reconciler(repo).reconcile(
        records, report_mission_ids=[]
    ).to_dict()
    assert payload["report_owed"]["mission_ids"] == [2]
    included = reconciler(repo).reconcile(
        records,
        report_mission_ids=[],
        include_non_substantive_in_owed=True,
    ).to_dict()
    assert included["report_owed"]["mission_ids"] == [1, 2]


# ---------------------------------------------------------------------------
# 数据形态与归一化
# ---------------------------------------------------------------------------


def test_json_string_columns_parsed():
    record = DeltaRecord.from_row(
        {
            "mission_id": "9",
            "category": "defect_fix",
            "artifacts": '["artifacts/a.json"]',
            "evidence": '{"head_commit": "abc1234"}',
        }
    )
    assert record.mission_id == 9
    assert record.artifacts == ["artifacts/a.json"]
    assert record.head_commit == "abc1234"
    assert record.commit_refs() == ["abc1234"]


def test_glob_declaration_covers_present_file(tmp_path):
    repo = make_repo(tmp_path)
    payload = reconciler(repo).reconcile(
        [rec(1, ["artifacts/*.json"], head_sha(repo))]
    ).to_dict()
    assert payload["mismatch_count"] == 0


def test_url_and_absolute_refs_ignored(tmp_path):
    repo = make_repo(tmp_path)
    payload = reconciler(repo).reconcile(
        [rec(1, ["https://example.com/a.json", "", "/abs/path.json"])],
        present_files=[],
    ).to_dict()
    assert payload["scope"]["declared_total"] == 0
    assert len(payload["ignored_refs"]) == 3
    assert payload["mismatch_count"] == 0


def test_duplicate_declaration_single_check_with_both_missions(tmp_path):
    repo = make_repo(tmp_path)
    records = [
        rec(1, ["artifacts/shared_missing.json"]),
        rec(2, ["artifacts/shared_missing.json"]),
    ]
    payload = reconciler(repo).reconcile(records).to_dict()
    assert payload["scope"]["declared_total"] == 1
    assert payload["by_kind"][KIND_DECLARED_MISSING] == 1
    assert payload["mismatches"][KIND_DECLARED_MISSING][0]["mission_ids"] == [1, 2]


def test_normalize_path_rules():
    assert normalize_path("./artifacts//a.json") == "artifacts/a.json"
    assert normalize_path("artifacts\\a.json") == "artifacts/a.json"
    assert normalize_path("artifacts/dir/") == "artifacts/dir"
    assert normalize_path("http://x/y") is None
    assert normalize_path("/abs") is None
    assert normalize_path("   ") is None
    assert normalize_path(None) is None


def test_cli_mission_repo_filter(tmp_path):
    repo = make_repo(tmp_path)
    deltas = tmp_path / "deltas.json"
    deltas.write_text(
        json.dumps(
            [
                {
                    "mission_id": 1,
                    "mission_repo": "TensorPlatform",
                    "category": "defect_fix",
                    "artifacts": ["artifacts/kept.json"],
                },
                {
                    "mission_id": 2,
                    "mission_repo": "OtherRepo",
                    "category": "defect_fix",
                    "artifacts": ["totally/other/repo.py"],
                },
            ]
        )
        + "\n"
    )
    out = tmp_path / "filtered.json"
    rc = main(
        [
            "--repo-root",
            str(repo),
            "--deltas-json",
            str(deltas),
            "--mission-repo",
            "TensorPlatform",
            "--out",
            str(out),
        ]
    )
    payload = json.loads(out.read_text())
    assert rc == 0
    assert payload["ledger"]["delta_rows"] == 1
    assert payload["by_kind"][KIND_DECLARED_MISSING] == 0


def test_mismatch_rate_denominator_formula(tmp_path):
    repo = make_repo(tmp_path)
    # declared=2（kept 存在 + ghost 缺失），present=1（kept），commit=0
    payload = reconciler(repo).reconcile(
        [rec(1, ["artifacts/kept.json", "artifacts/ghost.json"])]
    ).to_dict()
    assert payload["scope"]["declared_total"] == 2
    assert payload["scope"]["present_total"] == 1
    assert payload["scope"]["total_checks"] == 3
    assert payload["mismatch_count"] == 1
    assert payload["mismatch_rate"] == pytest.approx(1 / 3, rel=1e-6)


# ---------------------------------------------------------------------------
# 数据源装载（DB / JSON）
# ---------------------------------------------------------------------------


def test_load_deltas_from_sqlite_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE tensor_capability_deltas ("
        "id INTEGER PRIMARY KEY, mission_id INTEGER, category TEXT, "
        "summary TEXT, artifacts TEXT, evidence TEXT)"
    )
    conn.execute("CREATE TABLE tensor_dev_missions (id INTEGER PRIMARY KEY, repo TEXT)")
    conn.execute("INSERT INTO tensor_dev_missions VALUES (11, 'TensorPlatform')")
    conn.execute(
        "INSERT INTO tensor_capability_deltas VALUES (1, 11, 'defect_fix', 's', ?, ?)",
        ('["artifacts/a.json"]', '{"head_commit": "abc1234"}'),
    )
    records, warnings = load_deltas_from_db(conn)
    assert warnings == []
    assert len(records) == 1
    assert records[0].mission_id == 11
    assert records[0].artifacts == ["artifacts/a.json"]
    assert records[0].mission_repo == "TensorPlatform"
    # 仓库过滤：命中/不命中
    kept, _ = load_deltas_from_db(conn, mission_repo="TensorPlatform")
    assert len(kept) == 1
    dropped, _ = load_deltas_from_db(conn, mission_repo="OtherRepo")
    assert dropped == []
    conn.close()


def test_load_deltas_missing_table_graceful():
    conn = sqlite3.connect(":memory:")
    records, warnings = load_deltas_from_db(conn)
    assert records == []
    assert any(w.startswith("delta_table_unavailable") for w in warnings)
    assert load_report_mission_ids_from_db(conn) is None
    conn.close()


def test_load_report_ids_from_db_and_json(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tensor_reports (id INTEGER PRIMARY KEY, mission_id INTEGER)")
    conn.executemany("INSERT INTO tensor_reports (mission_id) VALUES (?)", [(3,), (1,), (None,)])
    assert load_report_mission_ids_from_db(conn) == [1, 3]
    conn.close()

    path = tmp_path / "reports.json"
    path.write_text(json.dumps({"items": [{"mission_id": 2}, 5]}) + "\n")
    assert load_report_mission_ids_from_json(path) == [2, 5]


def test_load_deltas_from_json(tmp_path):
    path = tmp_path / "deltas.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "mission_id": 5,
                        "category": "new_capability",
                        "artifacts": ["artifacts/a.json"],
                        "evidence": {"head_commit": "abc1234"},
                    }
                ]
            }
        )
        + "\n"
    )
    records = load_deltas_from_json(path)
    assert len(records) == 1
    assert records[0].mission_id == 5
    assert records[0].head_commit == "abc1234"


# ---------------------------------------------------------------------------
# 报告契约 + CLI
# ---------------------------------------------------------------------------


def test_report_schema_keys_stable(tmp_path):
    repo = make_repo(tmp_path)
    payload = reconciler(repo).reconcile([rec(1, ["artifacts/kept.json"])]).to_dict()
    assert set(payload) >= {
        "schema_version",
        "generated_at",
        "repo_root",
        "git_ref",
        "git_head_sha",
        "git_available",
        "artifact_roots",
        "scope",
        "mismatch_count",
        "mismatch_rate",
        "metrics",
        "by_kind",
        "mismatches",
        "report_owed",
        "ledger",
        "warnings",
    }
    assert set(payload["by_kind"]) == set(MISMATCH_KINDS)
    assert set(payload["metrics"]) >= {
        "declared_total",
        "present_total",
        "commit_total",
        "total_checks",
        "mismatch_count",
        "mismatch_rate",
        "report_owed_count",
    }
    json.dumps(payload)  # 必须可序列化


def test_cli_writes_report_and_fail_flag(tmp_path):
    repo = make_repo(tmp_path)
    deltas = tmp_path / "deltas.json"
    reports = tmp_path / "reports.json"
    out = tmp_path / "out" / "reconcile.json"
    deltas.write_text(
        json.dumps(
            [
                {
                    "mission_id": 1,
                    "category": "new_capability",
                    "artifacts": ["artifacts/kept.json"],
                    "evidence": {"head_commit": head_sha(repo)},
                }
            ]
        )
        + "\n"
    )
    reports.write_text(json.dumps({"mission_ids": [1]}) + "\n")
    rc = main(
        [
            "--repo-root",
            str(repo),
            "--deltas-json",
            str(deltas),
            "--reports-json",
            str(reports),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["mismatch_count"] == 0
    assert payload["report_owed"]["count"] == 0

    deltas.write_text(
        json.dumps([{"mission_id": 2, "category": "defect_fix", "artifacts": ["artifacts/ghost.json"]}])
        + "\n"
    )
    rc = main(
        [
            "--repo-root",
            str(repo),
            "--deltas-json",
            str(deltas),
            "--out",
            str(tmp_path / "out2.json"),
            "--fail-on-mismatch",
        ]
    )
    assert rc == 1