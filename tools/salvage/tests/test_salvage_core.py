"""Unit tests for classification, de-duplication, resume matching and evidence rows."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tools.salvage import classify, dedupe, evidence, resume


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=salvage-test",
            "-c",
            "user.email=salvage@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
    )


# ───────────────────────────── classification ─────────────────────────────

@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/alpha/widget.py", "code"),
        ("benchmarks/alpha/run.sh", "code"),
        ("config/alpha.yaml", "code"),
        ("docs/notes.md", "doc"),
        ("notes/observations.txt", "doc"),
        ("runs/alpha_probe.json", "data"),
        ("docs/figures/fig1.png", "data"),
        ("runs/probe.log", "data"),
        (".scratch/dbg1.py", "scratch"),
        ("__pycache__/widget.cpython-311.pyc", "scratch"),
        ("src/alpha/.widget.py.swp", "scratch"),
        ("tmp/cache/alpha.json", "scratch"),
        ("assets/blob.unknownbi", "other"),
    ],
)
def test_classify_matrix(path: str, expected: str) -> None:
    assert classify.classify(path)[0] == expected


def test_scratch_is_the_only_drop_class() -> None:
    dropped = [c for c in classify.CLASSES if classify.is_drop_class(c)]
    assert dropped == ["scratch"]


def test_classification_is_pure() -> None:
    assert classify.classify("a/b.py") == classify.classify("a/b.py")


# ───────────────────────────── de-duplication ─────────────────────────────

def test_dedupe_prunes_identical_content(baseline_repo, work_root) -> None:
    work = work_root / "m1001"
    (work / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "existing.py").parent.mkdir(exist_ok=True)
    (work / "README.md").write_text("# synthetic fixture\n", encoding="utf-8")
    d = dedupe.Deduper(str(work), baseline_ref="main")
    same = d.decide("README.md", __import__("tools.salvage.gitutil", fromlist=["x"]).file_sha256(work / "README.md"))
    assert same.pruned and same.reason == dedupe.REASON_IDENTICAL and same.tier == dedupe.PRUNE_HEAD


def test_dedupe_keeps_new_content(baseline_repo, work_root) -> None:
    work = work_root / "m1001"
    (work / "README.md").write_text("# synthetic fixture v2\n", encoding="utf-8")
    from tools.salvage import gitutil

    d = dedupe.Deduper(str(work), baseline_ref="main")
    decision = d.decide("README.md", gitutil.file_sha256(work / "README.md"))
    assert not decision.pruned
    assert decision.reason == dedupe.KEEP_DIFFERS
    assert decision.baseline_sha256 is not None


def test_dedupe_history_tier_catches_superseded_content(baseline_repo, work_root) -> None:
    """Content that landed in the baseline and was later rewritten is still merged."""
    (baseline_repo / "widget.py").write_text("VERSION = 'a'\n", encoding="utf-8")
    _git(baseline_repo, "add", "-A")
    _git(baseline_repo, "commit", "-q", "-m", "add widget a")
    (baseline_repo / "widget.py").write_text("VERSION = 'b'\n", encoding="utf-8")
    _git(baseline_repo, "add", "-A")
    _git(baseline_repo, "commit", "-q", "-m", "bump widget b")

    work = work_root / "m1002"
    _git(work, "reset", "--hard", "-q", "main")
    (work / "widget.py").write_text("VERSION = 'a'\n", encoding="utf-8")
    from tools.salvage import gitutil

    sha = gitutil.file_sha256(work / "widget.py")
    head_only = dedupe.Deduper(str(work), baseline_ref="main", use_history_tier=False)
    with_history = dedupe.Deduper(str(work), baseline_ref="main", use_history_tier=True)
    assert not head_only.decide("widget.py", sha).pruned
    decision = with_history.decide("widget.py", sha)
    assert decision.pruned and decision.tier == dedupe.PRUNE_HISTORY


def test_dedupe_unknown_path_is_kept(baseline_repo, work_root) -> None:
    work = work_root / "m1001"
    (work / "brand_new.py").write_text("X = 1\n", encoding="utf-8")
    from tools.salvage import gitutil

    d = dedupe.Deduper(str(work), baseline_ref="main")
    decision = d.decide("brand_new.py", gitutil.file_sha256(work / "brand_new.py"))
    assert not decision.pruned and decision.reason == dedupe.KEEP_ABSENT


def test_prune_rate_bounds() -> None:
    assert dedupe.prune_rate([]) == 0.0
    pruned = dedupe.Decision(True, dedupe.PRUNE_HEAD, dedupe.REASON_IDENTICAL, "x")
    kept = dedupe.Decision(False, None, dedupe.KEEP_DIFFERS, None)
    assert dedupe.prune_rate([pruned, kept]) == 0.5


# ───────────────────────────── resume matching ─────────────────────────────

def test_resume_splits_covered_and_missing() -> None:
    files = [
        {"path": "benchmarks/alpha/widget_ridge.py", "class": "code"},
        {"path": "docs/widget_ridge.md", "class": "doc"},
    ]
    texts = {
        "benchmarks/alpha/widget_ridge.py": "# widget ridge closure verification\n",
        "docs/widget_ridge.md": "widget ridge closure verification report\n",
    }
    criteria = [
        "widget ridge closure verification passes",
        "totally unrelated orbital telemetry calibration",
    ]
    result = resume.analyse(criteria, files, lambda rel: texts.get(rel, ""))
    assert result["criteria_total"] == 2
    assert result["criteria_covered"] == 1
    assert [m["text"] for m in result["missing"]] == [criteria[1]]
    assert result["inventory"]["code"] == ["benchmarks/alpha/widget_ridge.py"]


def test_resume_handles_cjk_only_criteria() -> None:
    files = [{"path": "docs/阿尔法报告.md", "class": "doc"}]
    texts = {"docs/阿尔法报告.md": "阿尔法 闭环 验证 报告 已 生成 图表\n"}
    result = resume.analyse(["阿尔法闭环验证通过"], files, lambda rel: texts.get(rel, ""))
    assert result["criteria_covered"] == 1


def test_resume_marks_untokenisable_gap() -> None:
    result = resume.analyse(["***"], [{"path": "a.py", "class": "code"}], lambda rel: "")
    assert result["missing"][0]["verdict"] == "untokenisable"


# ───────────────────────────── evidence rows ─────────────────────────────

def _index(tmp_path, kept: int = 3, status: str = "blocked") -> dict:
    return {
        "entry_count": 1,
        "entries": [
            {
                "mission_key": "m1001",
                "mission_id": 1001,
                "plan_id": 7,
                "node_id": 11,
                "node_status": status,
                "repo": "TensorX",
                "ext_id": "PX-N01",
                "manifest": "m1001/manifest.json",
                "manifest_sha256": "0" * 64,
                "counts": {"kept": kept, "pruned": 0, "by_class_kept": {"code": kept}},
            }
        ],
    }


def test_evidence_rows_carry_salvage_kind(tmp_path) -> None:
    rows = evidence.build_rows(_index(tmp_path), tmp_path, status_after={"1001": "blocked"})
    assert len(rows) == 1
    assert rows[0]["evidence_kind"] == "salvage"
    assert rows[0]["node_status_observed"] == rows[0]["node_status_after"] == "blocked"


def test_zero_material_produces_no_evidence_row(tmp_path) -> None:
    assert evidence.build_rows(_index(tmp_path, kept=0), tmp_path) == []


def test_status_drift_is_rejected(tmp_path) -> None:
    rows = evidence.build_rows(_index(tmp_path), tmp_path, status_after={"1001": "done"})
    with pytest.raises(ValueError):
        evidence.assert_no_status_mutation(rows)


def test_acceptance_like_status_is_rejected() -> None:
    rows = evidence.build_rows(_index(tmp_path=Path("."), status="verified"), Path("."))
    with pytest.raises(ValueError):
        evidence.assert_no_status_mutation(rows)


def test_sqlite_ledger_enforces_salvage_kind(tmp_path) -> None:
    rows = evidence.build_rows(_index(tmp_path), tmp_path, status_after={"1001": "blocked"})
    db = evidence.write_sqlite(rows, tmp_path / "ledger.db")
    conn = sqlite3.connect(str(db))
    try:
        stored = conn.execute("SELECT evidence_kind, files_kept FROM salvage_evidence").fetchall()
        assert stored == [("salvage", 3)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO salvage_evidence (mission_id, evidence_kind, artifact_path, manifest_sha256,"
                " files_kept, classes) VALUES (9,'acceptance','p','s',1,'{}')"
            )
    finally:
        conn.close()


def test_jsonl_rows_are_sorted_and_parseable(tmp_path) -> None:
    index = _index(tmp_path)
    index["entries"].append({**index["entries"][0], "mission_key": "m1000", "mission_id": 1000})
    rows = evidence.build_rows(index, tmp_path, status_after={"1000": "blocked", "1001": "blocked"})
    path = evidence.write_jsonl(rows, tmp_path / "rows.jsonl")
    parsed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["mission_id"] for row in parsed] == [1000, 1001]
    assert all(row["evidence_kind"] == "salvage" for row in parsed)
