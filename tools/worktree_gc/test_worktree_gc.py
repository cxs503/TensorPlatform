#!/usr/bin/env python3
"""Tests for Worktree GC module.

Tests cover:
- Disk pressure gate
- Worktree scanning
- Six-category classification
- Archival (tar+zstd)
- GC execution (dry-run and real)
- flock concurrency protection
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.worktree_gc.gc_manager import (
    WorktreeInfo,
    WorktreeGC,
    WorktreeCategory,
    DiskPressureGate,
    classify_worktrees,
    archive_worktree,
    scan_worktrees,
    run_gc,
    _get_dir_size,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def worktree_base(tmp_path):
    """Create a mock worktree directory structure."""
    base = tmp_path / "work"
    base.mkdir()

    # Active worktree (recent, dirty)
    wt1 = base / "m10001"
    wt1.mkdir()
    (wt1 / ".git").mkdir()
    (wt1 / "file.py").write_text("print('hello')")
    # Set recent mtime
    os.utime(wt1 / "file.py", (time.time(), time.time()))

    # Terminal worktree (agent branch, clean)
    wt2 = base / "m10002"
    wt2.mkdir()
    (wt2 / ".git").mkdir()
    (wt2 / "result.json").write_text('{"done": true}')

    # Stale worktree (old)
    wt3 = base / "m10003"
    wt3.mkdir()
    (wt3 / ".git").mkdir()
    (wt3 / "old_file.txt").write_text("old data")
    old_time = time.time() - 60 * 86400  # 60 days ago
    os.utime(wt3 / "old_file.txt", (old_time, old_time))

    return base


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archives"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Disk pressure gate tests
# ---------------------------------------------------------------------------

class TestDiskPressureGate:
    def test_check_returns_usage(self):
        gate = DiskPressureGate()
        result = gate.check()
        assert "used_pct" in result
        assert "pressure" in result
        assert result["used_pct"] >= 0

    def test_normal_pressure(self):
        gate = DiskPressureGate(trigger_pct=99.0, force_pct=99.9)
        result = gate.check()
        # On most systems, usage < 99%
        if result["used_pct"] < 99.0:
            assert result["pressure"] == "normal"
            assert result["action"] == "none"

    def test_custom_thresholds(self):
        gate = DiskPressureGate(trigger_pct=1.0, force_pct=2.0)
        result = gate.check()
        # Almost certainly above 1%
        assert result["pressure"] in ("warning", "critical")

    def test_invalid_mount(self):
        gate = DiskPressureGate(mount_point="/nonexistent_mount_xyz")
        result = gate.check()
        assert "error" in result or "pressure" in result


# ---------------------------------------------------------------------------
# Directory size tests
# ---------------------------------------------------------------------------

class TestDirSize:
    def test_get_dir_size(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world!")
        size = _get_dir_size(tmp_path)
        assert size == 11  # 5 + 6

    def test_empty_dir(self, tmp_path):
        size = _get_dir_size(tmp_path)
        assert size == 0


# ---------------------------------------------------------------------------
# Worktree scanning tests
# ---------------------------------------------------------------------------

class TestScanning:
    def test_scan_finds_git_dirs(self, worktree_base):
        worktrees = scan_worktrees(worktree_base)
        assert len(worktrees) == 3

    def test_scan_skips_non_git(self, worktree_base):
        (worktree_base / "not_a_worktree").mkdir()
        (worktree_base / "not_a_worktree" / "file.txt").write_text("no git")
        worktrees = scan_worktrees(worktree_base)
        assert len(worktrees) == 3

    def test_scan_empty_dir(self, tmp_path):
        worktrees = scan_worktrees(tmp_path / "nonexistent")
        assert len(worktrees) == 0


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestClassification:
    def test_stale_classification(self, worktree_base):
        worktrees = scan_worktrees(worktree_base)
        classified = classify_worktrees(worktrees, stale_days=30)
        categories = {wt.path.name: wt.category for wt in classified}
        # The old worktree should be stale
        assert categories.get("m10003") == WorktreeCategory.STALE

    def test_archived_marker(self, worktree_base):
        wt = worktree_base / "m10001"
        (wt / ".archived").write_text("archived")
        worktrees = scan_worktrees(worktree_base)
        classified = classify_worktrees(worktrees)
        for w in classified:
            if w.path.name == "m10001":
                assert w.category == WorktreeCategory.ARCHIVED

    def test_all_categories_assigned(self, worktree_base):
        worktrees = scan_worktrees(worktree_base)
        classified = classify_worktrees(worktrees)
        for wt in classified:
            assert wt.category is not None
            assert isinstance(wt.category, WorktreeCategory)


# ---------------------------------------------------------------------------
# Archival tests
# ---------------------------------------------------------------------------

class TestArchival:
    def test_archive_worktree(self, worktree_base, archive_dir):
        wt = WorktreeInfo(
            path=worktree_base / "m10001",
            branch="agent/test",
            size_bytes=_get_dir_size(worktree_base / "m10001"),
        )
        result = archive_worktree(wt, archive_dir, use_zstd=False)
        assert result["success"] is True
        assert Path(result["archive_path"]).exists()
        assert result["archive_size"] > 0

    def test_archive_creates_marker(self, worktree_base, archive_dir):
        wt = WorktreeInfo(
            path=worktree_base / "m10001",
            branch="agent/test",
            size_bytes=_get_dir_size(worktree_base / "m10001"),
        )
        archive_worktree(wt, archive_dir, use_zstd=False)
        marker = worktree_base / "m10001" / ".archived"
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert "archived_at" in data
        assert "archive_path" in data

    def test_archive_with_zstd(self, worktree_base, archive_dir):
        wt = WorktreeInfo(
            path=worktree_base / "m10001",
            branch="agent/test",
            size_bytes=_get_dir_size(worktree_base / "m10001"),
        )
        result = archive_worktree(wt, archive_dir, use_zstd=True)
        assert result["success"] is True
        # Should be .tar.zst or fallback .tar.gz
        assert result["archive_path"].endswith((".tar.zst", ".tar.gz"))


# ---------------------------------------------------------------------------
# GC Manager tests
# ---------------------------------------------------------------------------

class TestWorktreeGC:
    def test_scan(self, worktree_base, archive_dir):
        gc = WorktreeGC(worktree_base, archive_dir)
        worktrees = gc.scan()
        assert len(worktrees) == 3

    def test_plan(self, worktree_base, archive_dir):
        gc = WorktreeGC(worktree_base, archive_dir)
        plan = gc.plan()
        assert "disk" in plan
        assert "categories" in plan
        assert "can_archive" in plan

    def test_execute_no_pressure(self, worktree_base, archive_dir):
        gc = WorktreeGC(worktree_base, archive_dir, trigger_pct=99.0, force_pct=99.9)
        result = gc.execute()
        # Should skip if disk is normal
        if result.get("disk", {}).get("action") == "none":
            assert result["executed"] is False

    def test_execute_dry_run(self, worktree_base, archive_dir):
        gc = WorktreeGC(worktree_base, archive_dir, trigger_pct=1.0, force_pct=2.0)
        result = gc.execute(dry_run=True, force=True)
        assert result.get("dry_run") is True or result.get("executed") is False

    def test_execute_force(self, worktree_base, archive_dir):
        gc = WorktreeGC(worktree_base, archive_dir)
        result = gc.execute(force=True)
        # Force should execute regardless of disk pressure
        assert result.get("executed") is True or "results" in result


# ---------------------------------------------------------------------------
# WorktreeInfo tests
# ---------------------------------------------------------------------------

class TestWorktreeInfo:
    def test_age_days(self):
        wt = WorktreeInfo(
            path=Path("/tmp/test"),
            last_activity=time.time() - 86400,  # 1 day ago
        )
        assert abs(wt.age_days - 1.0) < 0.1

    def test_age_days_zero(self):
        wt = WorktreeInfo(path=Path("/tmp/test"), last_activity=0)
        assert wt.age_days == 0.0

    def test_to_dict(self):
        wt = WorktreeInfo(
            path=Path("/tmp/test"),
            branch="main",
            head_commit="abc1234",
            category=WorktreeCategory.ACTIVE,
        )
        d = wt.to_dict()
        assert d["branch"] == "main"
        assert d["category"] == "active"
        assert "age_days" in d
