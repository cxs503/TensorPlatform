"""End-to-end scan / package / manifest tests on synthetic git worktrees."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.salvage import gitutil, manifest, probe


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def dead_mission(work_root: Path, baseline_repo: Path) -> Path:
    work = work_root / "m1001"
    (work / "src" / "alpha").mkdir(parents=True, exist_ok=True)
    (work / "docs").mkdir(parents=True, exist_ok=True)
    (work / ".scratch").mkdir(parents=True, exist_ok=True)
    (work / "src" / "alpha" / "widget.py").write_text(
        "def close_loop(x):\n    return x * 2\n", encoding="utf-8"
    )
    (work / "docs" / "widget_report.md").write_text(
        "# widget\n\nclosure verification results\n", encoding="utf-8"
    )
    (work / "runs").mkdir(exist_ok=True)
    (work / "runs" / "sweep.json").write_text('{"value": 1.0}\n', encoding="utf-8")
    (work / ".scratch" / "dbg.py").write_text("print('debug')\n", encoding="utf-8")
    # modified-but-identical-to-baseline file: must be pruned
    (work / "README.md").write_text("# synthetic fixture\n", encoding="utf-8")
    # modified file with new content: must be kept
    (work / "bench.py").parent.mkdir(exist_ok=True)
    (work / "bench.py").write_text("LIMIT = 7\n", encoding="utf-8")
    _git(work, "add", "bench.py")
    _git(work, "commit", "-q", "-m", "bench baseline")
    (work / "bench.py").write_text("LIMIT = 8\n", encoding="utf-8")
    # a file the mission already committed, which the baseline later superseded:
    # its uncommitted content equals the *baseline* blob -> must be pruned, never
    # counted as this mission's contribution.
    (work / "prune_me.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(work, "add", "prune_me.py")
    _git(work, "commit", "-q", "-m", "mission draft")
    (baseline_repo / "prune_me.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(baseline_repo, "add", "prune_me.py")
    _git(baseline_repo, "commit", "-q", "-m", "baseline supersedes")
    (work / "prune_me.py").write_text("VALUE = 2\n", encoding="utf-8")
    return work


def _cfg(work_root: Path, **kwargs) -> manifest.ScanConfig:
    base = dict(work_root=work_root, baseline_ref="main", probe_limit=4)
    base.update(kwargs)
    return manifest.ScanConfig(**base)


def test_scan_packages_delta_and_drops_scratch(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    man = manifest.scan_mission("m1001", out, _cfg(work_root), meta={"mission_id": 1001, "plan_id": 7})
    assert man["status"] == "packaged"
    counts = man["counts"]
    assert counts["kept"] == 4, [r["path"] for r in man["files"]]
    assert counts["dropped_scratch"] == 1
    assert counts["pruned"] == 1, man["pruned"]
    kept_paths = {r["path"] for r in man["files"]}
    assert kept_paths == {
        "bench.py",
        "docs/widget_report.md",
        "runs/sweep.json",
        "src/alpha/widget.py",
    }
    # packaged copies must hash to the recorded digest
    for record in man["files"]:
        dest = out / "m1001" / manifest.PACKAGE_FILES_DIR / record["path"]
        assert dest.is_file()
        assert gitutil.file_sha256(dest) == record["sha256"]
        assert record["copy_verified"] is True
    # every record carries a class (A1: nothing unclassified)
    for group in ("files", "pruned", "dropped"):
        for record in man[group]:
            assert record["class"] in ("code", "doc", "data", "scratch", "other")


def test_scan_is_byte_idempotent(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    out1, out2 = tmp_path / "a", tmp_path / "b"
    manifest.scan_mission("m1001", out1, _cfg(work_root), meta={"mission_id": 1001})
    manifest.scan_mission("m1001", out2, _cfg(work_root), meta={"mission_id": 1001})
    first = (out1 / "m1001" / manifest.MANIFEST_NAME).read_bytes()
    second = (out2 / "m1001" / manifest.MANIFEST_NAME).read_bytes()
    assert first == second


def test_scan_uses_content_derived_clock(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    man = manifest.scan_mission("m1001", out, _cfg(work_root), meta={})
    epoch = gitutil.head_commit_epoch(dead_mission)
    assert man["source"]["commit_epoch"] == epoch
    assert man["generated_from_commit_utc"].endswith("Z")


def test_probe_does_not_pollute_source_worktree(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    """The scanner must not create __pycache__ inside the dead worktree."""
    before = gitutil.git_text(dead_mission, "status", "--porcelain", "-uall")
    manifest.scan_mission("m1001", tmp_path / "pkg", _cfg(work_root), meta={})
    after = gitutil.git_text(dead_mission, "status", "--porcelain", "-uall")
    assert before == after
    assert not list(dead_mission.rglob("__pycache__"))


def test_probes_record_explicit_verdicts(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    man = manifest.scan_mission("m1001", tmp_path / "pkg", _cfg(work_root), meta={})
    assert man["probes"], "code files must be probed"
    for row in man["probes"]:
        assert row["verdict"] in {
            "usable",
            "compile_error",
            "import_error",
            "compiled_deps_unresolved",
            "not_python",
        }
        assert row["py_compile"] in {"compiled", "compile_error", "not_applicable"}


def test_compile_error_is_reported_not_assumed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    (repo / "broken.py").write_text("def f(:\n", encoding="utf-8")
    ok = probe.probe_file(repo, "ok.py")
    bad = probe.probe_file(repo, "broken.py")
    assert ok["verdict"] == "usable" and ok["py_compile"] == "compiled"
    assert bad["verdict"] == "compile_error" and bad["compile_error"]


def test_missing_worktree_still_yields_an_entry(work_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    man = manifest.scan_mission("m9999", out, _cfg(work_root), meta={"mission_id": 9999})
    assert man["status"] == "worktree_missing"
    assert (out / "m9999" / manifest.MANIFEST_NAME).is_file()
    index = manifest.write_index(out, [man])
    assert index["entry_count"] == 1
    assert index["entries"][0]["manifest_sha256"]


def test_index_hashes_manifests_and_summarises_resume(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    criteria = ["widget closure verification", "orbital telemetry calibration"]
    man = manifest.scan_mission(
        "m1001", out, _cfg(work_root), meta={"mission_id": 1001, "acceptance_criteria": criteria}
    )
    index = manifest.write_index(out, [man])
    row = index["entries"][0]
    assert row["manifest_sha256"] == gitutil.file_sha256(out / "m1001" / manifest.MANIFEST_NAME)
    assert row["resume"]["criteria_total"] == 2
    assert row["resume"]["criteria_covered"] >= 1
    assert any("orbital" in item for item in row["resume"]["missing"])


def test_mission_mode_lists_delivered_paths(dead_mission: Path, work_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    cfg = _cfg(work_root, candidate_mode=manifest.MODE_MISSION, use_history_tier=True)
    man = manifest.scan_mission("m1001", out, cfg, meta={"mission_id": 1001})
    # the mission's committed deliveries: bench.py (delta, kept) and prune_me.py
    # (worktree content equals the baseline blob -> pruned)
    assert man["counts"]["candidates"] == 2
    assert man["counts"]["pruned"] == 1
    assert [r["path"] for r in man["files"]] == ["bench.py"]


def test_unsafe_paths_are_rejected(work_root: Path, tmp_path: Path) -> None:
    assert manifest._safe_rel_path("src/alpha/widget.py")
    assert not manifest._safe_rel_path("../escape.py")
    assert not manifest._safe_rel_path("/abs.py")


def test_canonical_json_is_stable() -> None:
    payload = {"b": [3, 1], "a": {"z": 1, "y": 2}}
    assert manifest.canonical_json(payload) == manifest.canonical_json(json.loads(manifest.canonical_json(payload)))
    assert manifest.canonical_json(payload).endswith("\n")