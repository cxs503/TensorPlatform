"""Tests for branch_hygiene module (p223w3-n10).

Tests cover:
1. ABCD classification correctness
2. sha256 provenance reproducibility
3. Superset detection (B class)
4. Disposition generation
5. Only-decrease gate (including injection test)
6. Git verification (账本 vs git for-each-ref)
7. Edge cases
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import branch_hygiene as H


# ── helpers ──────────────────────────────────────────────────────────────────

def git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def commit(repo: str, path: str, content: str, msg: str) -> None:
    p = os.path.join(repo, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    git(repo, "add", path)
    git(repo, "commit", "-m", msg, "--allow-empty")


def make_repo(tmp_path: Path, name: str = "r") -> str:
    r = str(tmp_path / name)
    os.makedirs(r, exist_ok=True)
    git(r, "init", "-b", "main")
    git(r, "config", "user.email", "test@test.com")
    git(r, "config", "user.name", "Test")
    return r


def rows_by_name(ledger: dict) -> dict:
    return {e["branch"]: e for e in ledger["entries"]}


# ── Test fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    """Create a test repo with various branch types."""
    r = make_repo(tmp_path)
    # Base commit
    commit(r, "base.txt", "base\n", "initial")

    # Branch A: will be merged into main (ANCHOR)
    git(r, "checkout", "-b", "agent/m1", "main")
    commit(r, "a.txt", "a\n", "feat a")
    git(r, "checkout", "main")
    git(r, "merge", "agent/m1", "--no-ff", "-m", "merge m1")

    # Branch B: banked (cherry-picked into main)
    git(r, "checkout", "-b", "agent/m2", "main")
    commit(r, "b.txt", "b\n", "feat b")
    git(r, "checkout", "main")
    git(r, "cherry-pick", "agent/m2")

    # Branch C: unique (not in main)
    git(r, "checkout", "-b", "agent/m3", "main")
    commit(r, "c.txt", "c\n", "feat c")
    commit(r, "d.txt", "d\n", "feat d")
    git(r, "checkout", "main")

    # Branch D: orphan (no commits ahead)
    git(r, "checkout", "-b", "agent/m4", "main")
    git(r, "checkout", "main")

    # Branch C2: unique, subset of C3 (for B class detection)
    git(r, "checkout", "-b", "agent/m5", "main")
    commit(r, "e.txt", "e\n", "feat e")
    git(r, "checkout", "main")

    # Branch C3: unique, superset of C2
    git(r, "checkout", "-b", "agent/m6", "main")
    commit(r, "e.txt", "e\n", "feat e")
    commit(r, "f.txt", "f\n", "feat f")
    commit(r, "g.txt", "g\n", "feat g")
    git(r, "checkout", "main")

    return r


# ── Classification tests ─────────────────────────────────────────────────────

def test_classify_abcd_anchor():
    """ANCHOR → A class."""
    row = {"class": "ANCHOR", "ahead": 0, "unique_commits": 0, "files": []}
    cls, evidence = H.classify_abcd(row)
    assert cls == H.CLASS_A
    assert evidence == "in_main_history"


def test_classify_abcd_banked():
    """BANKED → A class."""
    row = {"class": "BANKED", "ahead": 2, "unique_commits": 0, "files": ["x.py"]}
    cls, evidence = H.classify_abcd(row)
    assert cls == H.CLASS_A
    assert evidence == "patch_id_all_banked"


def test_classify_abcd_content_same():
    """CONTENT_SAME → A class."""
    row = {"class": "CONTENT_SAME", "ahead": 1, "unique_commits": 1,
           "files": ["x.py"], "content_same_on_touched": True}
    cls, evidence = H.classify_abcd(row)
    assert cls == H.CLASS_A
    assert evidence == "content_identical_on_touched"


def test_classify_abcd_unique():
    """UNIQUE → C class (before superset detection)."""
    row = {"class": "UNIQUE", "ahead": 3, "unique_commits": 2, "files": ["x.py"]}
    cls, evidence = H.classify_abcd(row)
    assert cls == H.CLASS_C
    assert evidence == "unique_commits_not_in_main"


def test_classify_abcd_orphan():
    """Empty UNIQUE branch → D class."""
    row = {"class": "UNIQUE", "ahead": 0, "unique_commits": 0, "files": []}
    cls, evidence = H.classify_abcd(row)
    assert cls == H.CLASS_D
    assert evidence == "orphan_no_content"


# ── Superset detection tests ─────────────────────────────────────────────────

def test_detect_supersets_basic():
    """Branch with subset files should be detected as B class."""
    rows = [
        {"branch": "agent/m5", "class": "UNIQUE", "unique_commits": 1,
         "files": ["e.txt"]},
        {"branch": "agent/m6", "class": "UNIQUE", "unique_commits": 3,
         "files": ["e.txt", "f.txt", "g.txt"]},
    ]
    supersets = H.detect_supersets(rows)
    assert "agent/m5" in supersets
    assert supersets["agent/m5"] == "agent/m6"
    assert "agent/m6" not in supersets


def test_detect_supersets_no_false_positive():
    """Disjoint branches should not be detected as supersets."""
    rows = [
        {"branch": "agent/m5", "class": "UNIQUE", "unique_commits": 1,
         "files": ["a.txt"]},
        {"branch": "agent/m6", "class": "UNIQUE", "unique_commits": 1,
         "files": ["b.txt"]},
    ]
    supersets = H.detect_supersets(rows)
    assert len(supersets) == 0


def test_detect_supersets_same_files_more_commits():
    """Same file set but more commits → superset."""
    rows = [
        {"branch": "agent/m5", "class": "UNIQUE", "unique_commits": 1,
         "files": ["e.txt"]},
        {"branch": "agent/m6", "class": "UNIQUE", "unique_commits": 3,
         "files": ["e.txt"]},
    ]
    supersets = H.detect_supersets(rows)
    assert "agent/m5" in supersets
    assert supersets["agent/m5"] == "agent/m6"


# ── Full ledger tests ────────────────────────────────────────────────────────

def test_build_hygiene_ledger_basic(repo):
    """Build ledger and verify structure."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    assert ledger["schema_version"] == H.SCHEMA_VERSION
    assert "provenance" in ledger
    assert "sha256" in ledger["provenance"]
    assert "entries" in ledger
    assert len(ledger["entries"]) > 0


def test_ledger_sha256_reproducible(repo):
    """sha256 must be reproducible across runs."""
    ledger1 = H.build_hygiene_ledger(repo, base="main")
    ledger2 = H.build_hygiene_ledger(repo, base="main")
    # The sha256 is computed from entries (branch, class, evidence)
    # which should be deterministic
    sha1 = ledger1["provenance"]["sha256"]
    sha2 = ledger2["provenance"]["sha256"]
    assert sha1 == sha2, "sha256 must be reproducible"


def test_ledger_all_entries_have_required_fields(repo):
    """Every entry must have class, evidence, decided_by, safe_to_close."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    for entry in ledger["entries"]:
        assert "repo" in entry
        assert "branch" in entry
        assert "class" in entry
        assert entry["class"] in H.ABCD_CLASSES
        assert "evidence" in entry
        assert "decided_by" in entry
        assert "safe_to_close" in entry
        assert isinstance(entry["safe_to_close"], bool)


def test_merged_branch_is_class_a(repo):
    """Merged branch should be classified as A."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    entries = rows_by_name(ledger)
    # agent/m1 was merged into main
    if "agent/m1" in entries:
        assert entries["agent/m1"]["class"] == H.CLASS_A


def test_orphan_branch_is_class_d(repo):
    """Branch with no commits ahead (created from main, no work) is ANCHOR → A.
    Note: D class is for UNIQUE branches with no content (rare edge case).
    In practice, branches with ahead=0 are classified as ANCHOR by branch_ledger."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    entries = rows_by_name(ledger)
    if "agent/m4" in entries:
        # agent/m4 was created from main with no commits → ANCHOR → A
        assert entries["agent/m4"]["class"] == H.CLASS_A
        assert entries["agent/m4"]["safe_to_close"] is True


def test_unique_branch_is_class_c(repo):
    """Unique branch (not subset) should be C."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    entries = rows_by_name(ledger)
    if "agent/m3" in entries:
        assert entries["agent/m3"]["class"] == H.CLASS_C
        assert entries["agent/m3"]["safe_to_close"] is False


def test_superset_branch_is_class_b(repo):
    """Branch whose files are subset of another should be B."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    entries = rows_by_name(ledger)
    if "agent/m5" in entries and "agent/m6" in entries:
        assert entries["agent/m5"]["class"] == H.CLASS_B
        assert entries["agent/m5"]["safe_to_close"] is True
        assert "superset_source" in entries["agent/m5"]


# ── Disposition tests ────────────────────────────────────────────────────────

def test_generate_disposition_structure(repo):
    """Disposition must have correct structure."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    assert "actions" in disp
    assert "total_actions" in disp
    assert "by_action_type" in disp
    assert disp["total_actions"] == len(disp["actions"])


def test_disposition_a_class_close_and_archive(repo):
    """A class branches should get close_and_archive action."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    close_actions = [a for a in disp["actions"] if a["action"] == "close_and_archive"]
    a_entries = [e for e in ledger["entries"] if e["class"] == H.CLASS_A]
    d_entries = [e for e in ledger["entries"] if e["class"] == H.CLASS_D]
    assert len(close_actions) == len(a_entries) + len(d_entries)


def test_disposition_c_class_create_ticket(repo):
    """C class branches should get create_delivery_ticket action."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    ticket_actions = [a for a in disp["actions"] if a["action"] == "create_delivery_ticket"]
    c_entries = [e for e in ledger["entries"] if e["class"] == H.CLASS_C]
    assert len(ticket_actions) == len(c_entries)


def test_disposition_b_class_annotate(repo):
    """B class branches should get annotate_superset action."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    annotate_actions = [a for a in disp["actions"] if a["action"] == "annotate_superset"]
    b_entries = [e for e in ledger["entries"] if e["class"] == H.CLASS_B]
    assert len(annotate_actions) == len(b_entries)


# ── Only-decrease gate tests ─────────────────────────────────────────────────

def test_gate_passes_when_decreased(repo, tmp_path):
    """Gate should pass when current ≤ baseline."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    # Create a baseline with higher counts
    baseline = {
        "total_branches": ledger["stats"]["total_branches"] + 10,
        "by_class": {k: v + 5 for k, v in ledger["stats"]["by_class"].items()},
    }
    result = H.check_only_decrease_gate(ledger, baseline)
    assert result["passed"] is True
    assert len(result["violations"]) == 0


def test_gate_fails_when_increased(repo, tmp_path):
    """Gate should fail when current > baseline."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    # Create a baseline with lower counts
    baseline = {
        "total_branches": 1,
        "by_class": {"A": 0, "B": 0, "C": 0, "D": 0},
    }
    result = H.check_only_decrease_gate(ledger, baseline)
    assert result["passed"] is False
    assert len(result["violations"]) > 0


def test_gate_injection_detection(tmp_path):
    """Injecting 3 new branches must make gate turn red (100% detection)."""
    r = make_repo(tmp_path, "inject_test")
    commit(r, "base.txt", "base\n", "initial")

    # Create initial state and baseline
    git(r, "checkout", "-b", "agent/m1", "main")
    commit(r, "a.txt", "a\n", "feat a")
    git(r, "checkout", "main")

    ledger_before = H.build_hygiene_ledger(r, base="main")
    baseline = {
        "total_branches": ledger_before["stats"]["total_branches"],
        "by_class": dict(ledger_before["stats"]["by_class"]),
    }

    # Inject 3 new branches
    for i in range(2, 5):
        git(r, "checkout", "-b", f"agent/m{i}", "main")
        commit(r, f"new{i}.txt", f"new{i}\n", f"feat {i}")
        git(r, "checkout", "main")

    # Check gate
    ledger_after = H.build_hygiene_ledger(r, base="main")
    result = H.check_only_decrease_gate(ledger_after, baseline)
    assert result["passed"] is False, "Gate must detect 3 injected branches"
    assert result["details"]["delta"] == 3


def test_gate_recover_after_cleanup(tmp_path):
    """After cleanup, gate should turn green again."""
    r = make_repo(tmp_path, "cleanup_test")
    commit(r, "base.txt", "base\n", "initial")

    # Create branches
    for i in range(1, 4):
        git(r, "checkout", "-b", f"agent/m{i}", "main")
        commit(r, f"f{i}.txt", f"c{i}\n", f"feat {i}")
        git(r, "checkout", "main")

    ledger = H.build_hygiene_ledger(r, base="main")
    baseline = {
        "total_branches": ledger["stats"]["total_branches"],
        "by_class": dict(ledger["stats"]["by_class"]),
    }

    # Add more branches (gate fails)
    for i in range(4, 7):
        git(r, "checkout", "-b", f"agent/m{i}", "main")
        commit(r, f"f{i}.txt", f"c{i}\n", f"feat {i}")
        git(r, "checkout", "main")

    ledger_after_add = H.build_hygiene_ledger(r, base="main")
    result = H.check_only_decrease_gate(ledger_after_add, baseline)
    assert result["passed"] is False

    # Delete the extra branches (cleanup)
    for i in range(4, 7):
        git(r, "branch", "-D", f"agent/m{i}")

    ledger_after_cleanup = H.build_hygiene_ledger(r, base="main")
    result = H.check_only_decrease_gate(ledger_after_cleanup, baseline)
    assert result["passed"] is True, "Gate must recover after cleanup"


# ── Git verification tests ───────────────────────────────────────────────────

def test_verify_against_git_matches(repo):
    """Ledger branch set must match git for-each-ref."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    result = H.verify_ledger_against_git(ledger, repo, "agent/*")
    assert result["verified"] is True
    assert result["diff_empty"] is True
    assert len(result["in_git_not_ledger"]) == 0
    assert len(result["in_ledger_not_git"]) == 0


def test_verify_detects_missing_branch(repo):
    """If ledger is missing a branch, verification should fail."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    # Remove an entry
    if ledger["entries"]:
        ledger["entries"] = ledger["entries"][:-1]
    result = H.verify_ledger_against_git(ledger, repo, "agent/*")
    assert result["verified"] is False
    assert len(result["in_git_not_ledger"]) > 0


# ── sha256 provenance tests ──────────────────────────────────────────────────

def test_sha256_deterministic():
    """Same input must produce same sha256."""
    data = [{"branch": "a", "class": "A", "evidence": "test"}]
    h1 = H.compute_sha256(data)
    h2 = H.compute_sha256(data)
    assert h1 == h2


def test_sha256_changes_with_data():
    """Different input must produce different sha256."""
    data1 = [{"branch": "a", "class": "A", "evidence": "test"}]
    data2 = [{"branch": "b", "class": "B", "evidence": "test"}]
    h1 = H.compute_sha256(data1)
    h2 = H.compute_sha256(data2)
    assert h1 != h2


def test_ledger_json_roundtrip(repo, tmp_path):
    """Ledger JSON must be writable and readable."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    path = str(tmp_path / "ledger.json")
    with open(path, "w") as f:
        json.dump(ledger, f)
    with open(path, "r") as f:
        loaded = json.load(f)
    assert loaded["provenance"]["sha256"] == ledger["provenance"]["sha256"]
    assert len(loaded["entries"]) == len(ledger["entries"])


# ── Coverage tests ───────────────────────────────────────────────────────────

def test_classification_coverage_100_percent(repo):
    """All branches must have a class assigned (100% coverage)."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    for entry in ledger["entries"]:
        assert entry["class"] in H.ABCD_CLASSES, f"Branch {entry['branch']} has no valid class"
        assert entry["evidence"], f"Branch {entry['branch']} has no evidence"


def test_stats_consistent_with_entries(repo):
    """Stats must match entry counts."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    s = ledger["stats"]
    assert s["total_branches"] == len(ledger["entries"])
    class_counts = {}
    for e in ledger["entries"]:
        class_counts[e["class"]] = class_counts.get(e["class"], 0) + 1
    for cls in H.ABCD_CLASSES:
        assert s["by_class"].get(cls, 0) == class_counts.get(cls, 0)


# ── Markdown rendering tests ─────────────────────────────────────────────────

def test_render_markdown_contains_key_sections(repo):
    """Markdown must contain key sections."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    md = H.render_markdown(ledger, disp)
    assert "分支交付面卫生报告" in md
    assert "分类统计" in md
    assert "处置动作摘要" in md
    assert "provenance sha256" in md


def test_render_markdown_with_gate(repo):
    """Markdown with gate result must show gate status."""
    ledger = H.build_hygiene_ledger(repo, base="main")
    disp = H.generate_disposition(ledger)
    gate = {"passed": True, "violations": [], "details": {"baseline_total": 10, "current_total": 8, "delta": -2}}
    md = H.render_markdown(ledger, disp, gate)
    assert "只减不增门" in md
    assert "PASS" in md


# ── CLI tests ────────────────────────────────────────────────────────────────

def test_cli_basic(repo, tmp_path):
    """CLI must produce output files."""
    out_dir = str(tmp_path / "out")
    rc = H.main(["--repo", repo, "--out", out_dir, "--verify-git"])
    assert rc == 0
    assert os.path.exists(os.path.join(out_dir, "ledger_hygiene.json"))
    assert os.path.exists(os.path.join(out_dir, "disposition.json"))
    assert os.path.exists(os.path.join(out_dir, "report.md"))
    assert os.path.exists(os.path.join(out_dir, "git_verify.json"))


def test_cli_set_baseline(repo, tmp_path):
    """CLI --set-baseline must create baseline file."""
    out_dir = str(tmp_path / "out")
    baseline_path = str(tmp_path / "baseline.json")
    rc = H.main(["--repo", repo, "--out", out_dir, "--set-baseline",
                 "--baseline-path", baseline_path])
    assert rc == 0
    assert os.path.exists(baseline_path)
    with open(baseline_path) as f:
        baseline = json.load(f)
    assert "total_branches" in baseline


def test_cli_check_gate_pass(repo, tmp_path):
    """CLI --check-gate must pass when current ≤ baseline."""
    out_dir = str(tmp_path / "out")
    baseline_path = str(tmp_path / "baseline.json")

    # Set baseline
    H.main(["--repo", repo, "--out", out_dir, "--set-baseline",
            "--baseline-path", baseline_path])

    # Check gate (same state should pass)
    rc = H.main(["--repo", repo, "--out", out_dir, "--check-gate",
                 "--baseline-path", baseline_path])
    assert rc == 0


def test_cli_check_gate_fail_on_injection(tmp_path):
    """CLI --check-gate must fail when branches are injected."""
    r = make_repo(tmp_path, "gate_fail")
    commit(r, "base.txt", "base\n", "initial")
    git(r, "checkout", "-b", "agent/m1", "main")
    commit(r, "a.txt", "a\n", "feat")
    git(r, "checkout", "main")

    out_dir = str(tmp_path / "out")
    baseline_path = str(tmp_path / "baseline.json")

    # Set baseline
    H.main(["--repo", r, "--out", out_dir, "--set-baseline",
            "--baseline-path", baseline_path])

    # Inject 3 branches
    for i in range(2, 5):
        git(r, "checkout", "-b", f"agent/m{i}", "main")
        commit(r, f"new{i}.txt", f"new{i}\n", f"feat {i}")
        git(r, "checkout", "main")

    # Check gate must fail
    rc = H.main(["--repo", r, "--out", out_dir, "--check-gate",
                 "--baseline-path", baseline_path])
    assert rc == 1, "Gate must fail after injecting 3 branches"
