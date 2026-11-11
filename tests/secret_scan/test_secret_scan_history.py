"""History rescan tests on a synthetic mission tree (read-only, bounded, recomputable)."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.history import scan_history
from tools.secret_scan.policy import load_policy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@pytest.fixture()
def mission_tree(tmp_path):
    corpus = build_corpus()
    root = tmp_path / "work"
    root.mkdir()
    for index, name in enumerate(["m9001", "m9002", "m9003"]):
        mission = root / name
        (mission / "docs").mkdir(parents=True)
        (mission / "artifacts" / "sub").mkdir(parents=True)
        (mission / "report.json").write_text(
            json.dumps({"title": "mission {}".format(name), "markdown": "clean text"}, ensure_ascii=False),
            encoding="utf-8")
        (mission / "docs" / "note.md").write_text("no credentials in this note\n", encoding="utf-8")
        (mission / "artifacts" / "sub" / "data.json").write_text(
            json.dumps({"value": "plain"}), encoding="utf-8")
        # only one mission carries a leak (twice, to test de-duplication)
        if index == 1:
            leak = corpus.secrets[13].text
            (mission / "docs" / "leaky.md").write_text(
                "intro\n{}\nagain {}\n".format(leak, leak), encoding="utf-8")
        os.utime(mission, (1_700_000_000 + index, 1_700_000_000 + index))
    return root


def _policy_with_roots(tmp_path, roots, **history_overrides):
    raw = json.loads(json.dumps(load_policy(POLICY).raw))
    raw["history"]["roots"] = list(roots)
    raw["history"].update(history_overrides)
    path = tmp_path / "policy_history.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return str(path)


def test_history_scans_and_reports_exposure(mission_tree, tmp_path):
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3)
    report = scan_history(policy, max_rows=50)
    assert report["schema"] == "tp.secret_scan.history/v1"
    assert report["missions_scanned"] == 3
    assert report["files_scanned"] >= 3
    assert report["files_with_hits"] == 1
    assert report["by_confidence"]["high"] >= 1
    row = report["exposure_rows"][0]
    assert row["mission"] == "m9002"
    assert row["file"] == "docs/leaky.md"
    assert row["confidence"] == "high"
    assert "value_sha256" in row and "preview" in row


def test_history_deduplicates_identical_values(mission_tree, tmp_path):
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3)
    report = scan_history(policy, max_rows=50)
    keys = [(row["file"], row["value_sha256"]) for row in report["exposure_rows"]]
    assert len(keys) == len(set(keys))


def test_history_is_read_only(mission_tree, tmp_path):
    paths = [p for p in mission_tree.rglob("*") if p.is_file()]
    before = {str(p): _sha256(p) for p in paths}
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3)
    report = scan_history(policy, max_rows=50)
    after = {str(p): _sha256(p) for p in paths}
    assert before == after
    assert report["readonly_verified"] is True


def test_history_remediation_loop_closes(mission_tree, tmp_path):
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3)
    report = scan_history(policy, max_rows=50)
    assert report["remediation_files_checked"] >= 1
    assert report["remediation_residual_hits"] == 0


def test_history_respects_mission_limit(mission_tree, tmp_path):
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=2)
    report = scan_history(policy, max_rows=50)
    assert report["missions_scanned"] == 2
    # the two most recent mtimes are m9003 and m9002; the leak lives in m9002
    assert report["files_with_hits"] == 1


def test_history_baseline_profile_is_recomputed(mission_tree, tmp_path):
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3)
    report = scan_history(policy, max_rows=50)
    assert "baseline_profile_hits" in report
    assert report["incremental_hits_vs_baseline"] == (
        report["full_profile_hits"] - report["baseline_profile_hits"])


def test_history_skips_binary_and_oversized_files(mission_tree, tmp_path):
    (mission_tree / "m9003" / "artifacts" / "blob.json").write_bytes(b"\x00\x01\x02binary container")
    (mission_tree / "m9002" / "artifacts" / "huge.json").write_text("x" * 4096, encoding="utf-8")
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3,
                                max_file_bytes=1024)
    report = scan_history(policy, max_rows=50)
    assert report["files_scanned"] >= 1
    assert report["remediation_residual_hits"] == 0
    scanned_files = {row["file"] for row in report["exposures"]}
    assert scanned_files == {"docs/leaky.md"}


def test_history_module_is_structurally_read_only():
    """Regression guard: the rescan must never gain a write path."""
    import re

    import tools.secret_scan.history as history

    assert history._readonly_static_ok() is True
    source = open(history.__file__, "r", encoding="utf-8").read()
    assert not re.search(r"open\([^)]*,\s*[\"']w", source)


def test_history_excludes_its_own_output(mission_tree, tmp_path):
    own = mission_tree / "m9002" / "artifacts" / "secret_scan"
    own.mkdir(parents=True)
    (own / "history_exposure.json").write_text(
        json.dumps({"preview": build_corpus().secrets[13].text}), encoding="utf-8")
    policy = _policy_with_roots(tmp_path, [str(mission_tree)], limit_missions=3,
                                exclude_globs=["artifacts/secret_scan/**"])
    report = scan_history(policy, max_rows=50)
    assert all("secret_scan" not in row["file"] for row in report["exposure_rows"])


def test_missing_root_degrades_gracefully(tmp_path):
    policy = _policy_with_roots(tmp_path, [str(tmp_path / "does_not_exist")], limit_missions=5)
    report = scan_history(policy, max_rows=10)
    assert report["missions_scanned"] == 0
    assert report["files_scanned"] == 0
    assert report["readonly_verified"] is True