#!/usr/bin/env python3
"""agent_work garbage collector — reclaims finished-mission worktrees.

tensor_agent.py already drops venvs/caches at mission end (immediate ~90%);
this cron job reclaims the worktrees themselves after the retention window:

  awaiting_acceptance        keep (PR under owner review)
  succeeded                  keep 14d past finished_at, then remove
  failed / stopped           keep 3d past finished_at, then remove
  queued/claimed/running     keep (active)
  m<id> dir with no mission row  keep 3d past dir mtime, then remove (orphan)

Mission status is read straight from the platform postgres (read-only
docker exec — the tensor_agent platform account is intentionally VIEWER
with no org binding; the agent protocol endpoints never resolve org).
Removal runs `git worktree remove --force` against the owning repo (branch
agent/m<id> survives for PR review), falls back to rmtree for orphans, and
finishes with `git worktree prune` per repo, which also clears stale
registrations left by pre-GC missions (audit 2026-10-18: 62 regs, ~46 stale).

Runs from crontab under flock; stdlib only, no platform API access needed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

STATE_ROOT = "/srv/tensor/shared/tensor_eco/agent_work"
REPO_PATHS = {
    "TensorPhysics": "/srv/tensor/shared/tensor_eco/TensorPhysics",
    "TensorPlatform": "/srv/tensor/shared/tensor_eco/TensorPlatform",
    "TensorFEM": "/srv/tensor/shared/tensor_eco/TensorFEM",
    "TensorDEM": "/srv/tensor/shared/tensor_eco/TensorDEM",
    "TensorFVM": "/srv/tensor/shared/tensor_eco/TensorFVM",
    "TensorLBM": "/srv/tensor/shared/TensorLBM",
}
KEEP_DAYS = {"succeeded": 14, "failed": 3, "stopped": 3, "_orphan": 3}
CACHE_DIR_NAMES = (".venv", "venv", ".pytest_cache", "__pycache__",
                   ".mypy_cache", ".ruff_cache", "node_modules", ".torch")

PG_CONTAINER = "open-foundation-postgres"
PG_USER = "foundation"
PG_DB = "open_digital_foundation"


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def fetch_missions() -> dict[int, dict]:
    """id -> {status, finished_epoch}; read-only psql inside the pg container."""
    query = ("SELECT id, status, "
             "COALESCE(EXTRACT(EPOCH FROM finished_at), 0) "
             "FROM tensor_dev_missions")
    proc = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
         "-t", "-A", "-F", " ", "-c", query],
        capture_output=True, text=True, timeout=60)
    missions: dict[int, dict] = {}
    if proc.returncode != 0:
        log(f"psql failed: {proc.stderr.strip()[:200]}")
        return missions
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].isdigit():
            missions[int(parts[0])] = {"status": parts[1],
                                       "finished_epoch": float(parts[2])}
    return missions


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr)[-2000:]


def reclaim_caches(missions: dict[int, dict]) -> None:
    """One-time sweep (--reclaim-caches): drop venv/cache dirs from every
    non-running mission directory. tensor_agent does this at mission end
    going forward; this catches the missions that finished before that."""
    for entry in sorted(os.listdir(STATE_ROOT)):
        if not re.fullmatch(r"m\d+", entry):
            continue
        mission = missions.get(int(entry[1:]))
        if mission is not None and mission["status"] in ("queued", "claimed", "running"):
            continue  # live mission — the daemon owns those files
        mdir = os.path.join(STATE_ROOT, entry)
        removed = 0
        for root, dirs, _files in os.walk(mdir, topdown=True):
            for d in list(dirs):
                if d in CACHE_DIR_NAMES:
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                    removed += 1
                    dirs.remove(d)
        if removed:
            log(f"reclaimed {removed} venv/cache dirs from {entry}")


def remove_worktree(mdir: str) -> None:
    # prefer the registered removal (clears repo metadata); fall back to rmtree
    for repo in REPO_PATHS.values():
        rc, out = run(["git", "-C", repo, "worktree", "remove", "--force", mdir])
        if rc == 0:
            log(f"worktree remove ok: {mdir}")
            break
        if "not a working tree" not in out:
            log(f"worktree remove rc={rc}: {out.strip()[:160]}")
    shutil.rmtree(mdir, ignore_errors=True)
    if os.path.exists(mdir):
        log(f"WARN: {mdir} still on disk after removal attempt")


def dir_size_mb(mdir: str) -> int:
    proc = subprocess.run(["du", "-sm", mdir], capture_output=True, text=True)
    return int(proc.stdout.split()[0]) if proc.returncode == 0 else 0


def main() -> None:
    missions = fetch_missions()
    if not missions:
        log("no mission rows readable — aborting sweep (pg container down?)")
        return
    if "--reclaim-caches" in sys.argv:
        reclaim_caches(missions)
        return
    now = time.time()
    freed = 0
    for entry in sorted(os.listdir(STATE_ROOT)):
        if not re.fullmatch(r"m\d+", entry):
            continue
        mid = int(entry[1:])
        mdir = os.path.join(STATE_ROOT, entry)
        if not os.path.isdir(mdir):
            continue
        mission = missions.get(mid)
        if mission is None:
            kind, deadline = "_orphan", KEEP_DAYS["_orphan"]
            ref = os.path.getmtime(mdir)
        else:
            status = mission["status"]
            if status not in KEEP_DAYS:  # active or awaiting acceptance
                continue
            kind, deadline = status, KEEP_DAYS[status]
            ref = mission["finished_epoch"] or os.path.getmtime(mdir)
        if ref and (now - ref) / 86400 < deadline:
            continue
        size = dir_size_mb(mdir)
        remove_worktree(mdir)
        freed += size
        log(f"removed {kind} mission {entry} (~{size}M, kept {deadline}d)")
    # drop stale registrations (dirs already gone) in every owning repo
    for name, repo in REPO_PATHS.items():
        rc, out = run(["git", "-C", repo, "worktree", "prune", "--expire", "now"])
        listed = subprocess.run(
            ["git", "-C", repo, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=60).stdout.count("\nworktree ")
        log(f"prune {name}: rc={rc}, {listed} registrations remain "
            f"(only agent_work ones are ever removed by this tool)")
    log(f"sweep done, ~{freed}M reclaimed")


if __name__ == "__main__":
    main()
