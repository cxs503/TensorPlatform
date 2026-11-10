"""Regression suite: worktree GC (six classes, archive, disk gate, flock)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tools.sustainment import worktree_gc as wg

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(*args: str, cwd: Path, date: str | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _commit(cwd: Path, msg: str, date: str | None = None) -> str:
    (cwd / "file.txt").write_text(msg)
    _git("add", "-A", cwd=cwd)
    _git("commit", "-m", msg, cwd=cwd, date=date)
    return _git("rev-parse", "HEAD", cwd=cwd)


OLD = "2026-10-01T00:00:00+00:00"
NEW = "2026-11-09T00:00:00+00:00"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo with one worktree per class (6 classes, 6 worktrees)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _commit(root, "base", date=OLD)

    wt_root = tmp_path / "work"

    # 1) finalized: branch merged into main
    fin = wt_root / "m1001"
    _git("worktree", "add", "-q", "-b", "agent/fin", str(fin), "main", cwd=root)
    _commit(fin, "feature-fin", date=OLD)
    _git("merge", "-q", "--no-ff", "-m", "merge fin", "agent/fin", cwd=root)
    (fin / ".agent_owner.json").write_text(json.dumps(
        {"owner": "mission:m1001", "heartbeat_ts": time.time() - 20 * 86400}))

    # 2) stale: git reports the tree prunable (directory deleted out from under it)
    st = wt_root / "m1002"
    _git("worktree", "add", "-q", "-b", "agent/stale", str(st), "main", cwd=root)
    (st / ".agent_owner.json").write_text(json.dumps(
        {"owner": "mission:m1002", "heartbeat_ts": time.time() - 10 * 86400}))
    import shutil as _sh
    _sh.rmtree(st)

    # 3) unpushed: unmerged unique commit, idle beyond TTL
    un = wt_root / "m1003"
    _git("worktree", "add", "-q", "-b", "agent/unpushed", str(un), "main", cwd=root)
    _commit(un, "unique-work", date=OLD)
    (un / ".agent_owner.json").write_text(json.dumps(
        {"owner": "mission:m1003", "heartbeat_ts": time.time() - 20 * 86400}))

    # 4) active: unmerged unique commit, fresh heartbeat
    ac = wt_root / "m1004"
    _git("worktree", "add", "-q", "-b", "agent/active", str(ac), "main", cwd=root)
    _commit(ac, "in-progress", date=NEW)
    (ac / ".agent_owner.json").write_text(json.dumps(
        {"owner": "mission:m1004", "heartbeat_ts": time.time()}))

    # 5) dirty: uncommitted modification in a merged branch
    dt = wt_root / "m1005"
    _git("worktree", "add", "-q", "-b", "agent/dirty", str(dt), "main", cwd=root)
    (dt / "file.txt").write_text("uncommitted edit")
    (dt / ".agent_owner.json").write_text(json.dumps(
        {"owner": "mission:m1005", "heartbeat_ts": time.time() - 20 * 86400}))

    # 6) orphan: no owner marker, directory is not mission-shaped
    orp = wt_root / "scratch-no-owner"
    _git("worktree", "add", "-q", "-b", "agent/orphan", str(orp), "main", cwd=root)
    _commit(orp, "orphan-work", date=OLD)
    return root


def _by_path(wts, suffix: str):
    return next(w for w in wts if w.path.endswith(suffix))


def test_six_classes_classified(repo: Path):
    wts = wg.discover(str(repo), worktrees_root=str(repo.parent / "work"),
                      base="main", ttl_hours=24, collect_size=False)
    assert len(wts) == 6, [w.to_json() for w in wts]
    classes = {Path(w.path).name: w.klass for w in wts}
    assert classes["m1001"] == "finalized"
    assert classes["m1002"] == "stale"
    assert classes["m1003"] == "unpushed"
    assert classes["m1004"] == "active"
    assert classes["m1005"] == "dirty"
    assert classes["scratch-no-owner"] == "orphan"
    # dispositions: protective classes never removable
    for w in wts:
        assert w.disposition == wg.CLASS_ACTIONS[w.klass]
        if w.klass in ("dirty", "orphan", "unpushed"):
            assert w.klass not in wg.REMOVABLE


def test_porcelain_parser_handles_detached_and_locked():
    text = (
        "worktree /a\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree /b\nHEAD def456\ndetached\nlocked\n\n"
    )
    ents = wg.parse_worktree_list(text)
    assert len(ents) == 2
    assert ents[0]["branch"] == "main"
    assert ents[1]["detached"] is True and ents[1]["locked"] is True


def test_dirty_worktree_is_protected(repo: Path):
    wts = wg.discover(str(repo), worktrees_root=str(repo.parent / "work"), collect_size=False)
    dirty = _by_path(wts, "m1005")
    assert dirty.dirty_files >= 1
    assert dirty.disposition == "PROTECT"


def test_archive_creates_verified_tarball_without_removing(repo: Path, tmp_path: Path):
    if not shutil_which("zstd"):
        pytest.skip("zstd unavailable")
    wts = wg.discover(str(repo), worktrees_root=str(repo.parent / "work"), collect_size=False)
    fin = _by_path(wts, "m1001")
    rec = wg.archive_worktree(fin, str(tmp_path / "arch"), apply=False)
    assert rec["ok"] is True, rec
    assert Path(rec["archive"]).exists() and Path(rec["archive"]).suffix == ".zst"
    side = json.loads(Path(rec["sidecar"]).read_text())
    assert side["sha256"] == rec["sha256"] and len(side["sha256"]) == 64
    assert Path(fin.path).exists()          # dry archive keeps the worktree
    assert rec["removed"] is False


def test_run_gc_dry_run_plans_only_safe_classes(repo: Path, tmp_path: Path):
    res = wg.run_gc(str(repo), worktrees_root=str(repo.parent / "work"),
                    archive_root=str(tmp_path / "arch"), base="main",
                    ttl_hours=24, apply=False, collect_size=False,
                    disk_path=str(tmp_path), warn_pct=0.0, force_pct=0.0)
    planned = {Path(p["path"]).name for p in res["planned"]}
    assert planned == {"m1001", "m1002"}
    assert res["summary"]["by_class"]["finalized"] == 1
    assert res["summary"]["by_class"]["stale"] == 1
    assert res["summary"]["protected"] == 3       # dirty + orphan + unpushed
    assert res["actions"] == []                   # nothing archived without --apply
    assert res["gate_verdict"]["level"] == "force"
    assert res["gate_verdict"]["allow_new_worktree"] is False


def test_gc_refuses_removal_below_warn_threshold(repo: Path, tmp_path: Path):
    """Level 'pass' means the volume is healthy: removal is skipped, archive only."""
    res = wg.run_gc(str(repo), worktrees_root=str(repo.parent / "work"),
                    archive_root=str(tmp_path / "arch"), base="main",
                    ttl_hours=24, apply=True, collect_size=False,
                    disk_path=str(tmp_path), warn_pct=101.0, force_pct=102.0)
    assert res["gate_verdict"]["level"] == "pass"
    assert res["actions"] == []
    assert any("disk_gate_pass" in e for e in res["errors"])
    # worktrees are all still on disk
    assert Path(repo.parent / "work" / "m1001").exists()


def test_gc_apply_archives_and_removes_only_removable(repo: Path, tmp_path: Path):
    if not shutil_which("zstd"):
        pytest.skip("zstd unavailable")
    res = wg.run_gc(str(repo), worktrees_root=str(repo.parent / "work"),
                    archive_root=str(tmp_path / "arch"), base="main",
                    ttl_hours=24, apply=True, collect_size=False,
                    disk_path=str(tmp_path), warn_pct=0.0, force_pct=101.0)
    archived = {Path(a["worktree"]).name for a in res["actions"]
                if a.get("removed") and "archive" in a}
    pruned = {Path(a["worktree"]).name for a in res["actions"]
              if a.get("action") == "prune_metadata"}
    assert archived == {"m1001"}
    assert pruned == {"m1002"}
    for name in ("m1003", "m1004", "m1005", "scratch-no-owner"):
        assert (repo.parent / "work" / name).exists(), f"{name} must survive GC"
    assert all(Path(a["archive"]).exists() for a in res["actions"] if "archive" in a)
    listed = _git("worktree", "list", "--porcelain", cwd=repo)
    assert "m1002" not in listed and "m1001" not in listed


# ---------------------------------------------------------------------------
# disk pressure gate
# ---------------------------------------------------------------------------


def test_disk_pressure_levels(tmp_path: Path):
    gate = wg.disk_pressure(str(tmp_path), warn_pct=0.0, force_pct=0.0)
    assert gate["level"] == "force" and gate["allow_new_worktree"] is False
    gate = wg.disk_pressure(str(tmp_path), warn_pct=0.0, force_pct=101.0)
    assert gate["level"] == "warn" and gate["allow_new_worktree"] is True
    gate = wg.disk_pressure(str(tmp_path), warn_pct=101.0, force_pct=102.0)
    assert gate["level"] == "pass" and gate["remove_allowed"] is False
    assert 0.0 <= gate["used_pct"] <= 100.0


def test_assert_can_create_worktree_blocked_at_force_level(tmp_path: Path, monkeypatch):
    """The guard must refuse new worktrees when the volume is at/above force level."""
    fake = wg.disk_pressure(str(tmp_path), warn_pct=0.0, force_pct=0.0)
    assert fake["allow_new_worktree"] is False
    monkeypatch.setattr(wg, "disk_pressure", lambda *a, **k: fake)
    with pytest.raises(RuntimeError, match="disk pressure gate"):
        wg.assert_can_create_worktree(str(tmp_path))


def test_assert_can_create_worktree_ok_below_force_level(tmp_path: Path, monkeypatch):
    fake = wg.disk_pressure(str(tmp_path), warn_pct=101.0, force_pct=102.0)
    monkeypatch.setattr(wg, "disk_pressure", lambda *a, **k: fake)
    assert wg.assert_can_create_worktree(str(tmp_path))["level"] == "pass"


# ---------------------------------------------------------------------------
# flock
# ---------------------------------------------------------------------------


def test_flock_rejects_concurrent_run(tmp_path: Path):
    lock = tmp_path / "gc.lock"
    with wg.gc_lock(lock):
        assert lock.exists()
        payload = json.loads(lock.read_text())
        assert payload["pid"] == os.getpid()
        with pytest.raises(wg.LockBusy):
            with wg.gc_lock(lock, timeout=0):
                pass
    # released: a new acquire succeeds
    with wg.gc_lock(lock, timeout=0.2):
        pass


def test_flock_busy_exit_code_via_cli(repo: Path, tmp_path: Path):
    lock = tmp_path / "gc.lock"
    with wg.gc_lock(lock):
        proc = subprocess.run(
            ["python3", "-m", "tools.sustainment", "gc", "--repo", str(repo),
             "--worktrees-root", str(repo.parent / "work"), "--archive-root",
             str(tmp_path / "arch"), "--limit", "2", "--no-size",
             "--lock", str(lock), "--lock-timeout", "0"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "lock_busy" in proc.stdout


def shutil_which(tool: str) -> bool:
    import shutil
    return shutil.which(tool) is not None

# ---------------------------------------------------------------------------
# metric semantics: reclaimed (freed) is NOT archive (compressed) bytes
# ---------------------------------------------------------------------------


def test_reclaimed_bytes_counts_removed_dirs_not_archives(repo: Path, tmp_path: Path, monkeypatch):
    sizes = {"m1001": 1000, "m1002": 500}

    def fake_discover(*a, **kw):
        wts = []
        for name in ("m1001", "m1002", "m1003"):
            p = repo.parent / "work" / name
            w = wg.Worktree(path=str(p), head="deadbeef", owner="mission:" + name)
            if name == "m1003":
                w.klass, w.disposition = "unpushed", "PROTECT"
            else:
                w.klass, w.disposition = "finalized", "ARCHIVE_REMOVE"
            w.size_bytes = sizes.get(name, 0)
            wts.append(w)
        return wts

    monkeypatch.setattr(wg, "discover", fake_discover)

    def fake_archive(wt, archive_root, apply=False, repo=None):
        return {"worktree": wt.path, "class": wt.klass, "ok": True, "bytes": 100,
                "removed": apply, "applied": apply, "archive": "/x.tar.zst"}

    monkeypatch.setattr(wg, "archive_worktree", fake_archive)
    res = wg.run_gc(str(repo), worktrees_root=str(repo.parent / "work"),
                    archive_root=str(tmp_path / "arch"), apply=True,
                    disk_path=str(tmp_path), warn_pct=0.0, force_pct=101.0,
                    collect_size=False)
    assert res["summary"]["reclaimed_bytes"] == 1500      # 1000 + 500 freed
    assert res["summary"]["archive_bytes"] == 200         # two 100-byte archives
    assert res["summary"]["archived"] == 2
    assert res["summary"]["protected"] == 1


# ---------------------------------------------------------------------------
# stray (unregistered) directories: the blind spot is measured, never deleted
# ---------------------------------------------------------------------------


def test_measure_stray_dirs_finds_unregistered_leftovers(tmp_path: Path):
    root = tmp_path / "work"
    reg = root / "m1"
    (reg / "src").mkdir(parents=True)
    (reg / "src" / "a.py").write_text("x" * 100)
    strays = []
    for name, size in (("leftover_a", 200), ("leftover_b", 400), ("copy_of_thing", 600)):
        d = root / name
        d.mkdir()
        (d / "blob.bin").write_text("y" * size)
        strays.append(d)
    foreign = root / "other_repo_wt"
    foreign.mkdir()
    (foreign / ".git").write_text("gitdir: /elsewhere\n")
    res = wg.measure_stray_dirs(str(root), [str(reg)], sample_size=10)
    assert res["ok"] is True
    assert res["dirs_total"] == 5 and res["registered"] == 1
    assert res["unregistered"] == 4 and res["foreign_worktrees"] == 1
    assert res["stray"] == 3
    assert res["sample_n"] == 3
    assert res["sample_bytes"] == 1200          # exact: the whole population was sampled
    assert res["estimated_stray_bytes"] == 1200
    # nothing was removed
    assert all(d.exists() for d in strays) and reg.exists()


def test_measure_stray_dirs_missing_root(tmp_path: Path):
    res = wg.measure_stray_dirs(str(tmp_path / "absent"), [])
    assert res["ok"] is False and res["reason"] == "worktrees_root_missing"
