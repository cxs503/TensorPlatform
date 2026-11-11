#!/usr/bin/env python3
"""Dispatch disk gate — fail-closed precheck before worktree creation.

Problem: 86.1% of recent FAILs (1033/1200) are ENOSPC crashes during
worktree creation.  This module turns "crash and log FAIL" into
"structured rejection before dispatch".

Design principles:
  - Pure function: no side effects, no global state, no I/O beyond statvfs.
  - Zero domain semantics: no ship/ice/solver vocabulary.
  - Thresholds are parameterized and injectable.
  - Reuses the same disk-pressure logic as tools/sustainment/worktree_gc.py
    (shutil.disk_usage / os.statvfs) for consistency.

Public API:
    precheck_dispatch(path, min_free_gb, max_used_pct) -> dict
    assert_dispatch_ok(path, min_free_gb, max_used_pct) -> None | raises
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, Optional

SCHEMA_VERSION = "disk_gate/precheck/v1"

# Default thresholds — conservative enough to prevent ENOSPC during worktree
# creation (a typical worktree + venv can consume 1-3 GB).
DEFAULT_MIN_FREE_GB = 5.0
DEFAULT_MAX_USED_PCT = 90.0


def _disk_usage(path: str) -> Optional[Dict[str, Any]]:
    """Return disk usage info for the filesystem containing *path*, or None."""
    try:
        usage = shutil.disk_usage(path)
        total = usage.total
        free = usage.free
        used = total - free
        used_pct = round(100.0 * used / total, 2) if total > 0 else 0.0
        free_gb = round(free / (1 << 30), 2)
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_pct": used_pct,
            "free_gb": free_gb,
        }
    except (OSError, ValueError):
        return None


def precheck_dispatch(
    path: str | os.PathLike,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    max_used_pct: float = DEFAULT_MAX_USED_PCT,
) -> Dict[str, Any]:
    """Pre-dispatch disk-space gate.

    Returns a structured dict with:
      - ok: bool — True only when BOTH conditions are met
      - reasons: list[str] — empty when ok, otherwise human-readable reasons
      - free_gb: float — available space in GiB
      - used_pct: float — filesystem usage percentage
      - schema: str — version tag for downstream consumers
      - path: str — the path that was checked

    Fail-closed: if the path does not exist or disk usage cannot be determined,
    ok=False (we refuse to guess).

    Conditions:
      1. free_gb >= min_free_gb
      2. used_pct <= max_used_pct
    """
    path_str = str(path)
    result: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "path": path_str,
        "ok": False,
        "reasons": [],
        "free_gb": 0.0,
        "used_pct": 0.0,
        "min_free_gb": min_free_gb,
        "max_used_pct": max_used_pct,
    }

    # Path existence check — fail-closed
    if not os.path.exists(path_str):
        result["reasons"].append(f"path does not exist: {path_str}")
        return result

    usage = _disk_usage(path_str)
    if usage is None:
        result["reasons"].append(f"cannot determine disk usage for: {path_str}")
        return result

    result["free_gb"] = usage["free_gb"]
    result["used_pct"] = usage["used_pct"]

    # Condition 1: minimum free space
    if usage["free_gb"] < min_free_gb:
        result["reasons"].append(
            f"insufficient free space: {usage['free_gb']} GB < {min_free_gb} GB threshold"
        )

    # Condition 2: maximum usage percentage
    if usage["used_pct"] > max_used_pct:
        result["reasons"].append(
            f"disk usage too high: {usage['used_pct']}% > {max_used_pct}% threshold"
        )

    result["ok"] = len(result["reasons"]) == 0
    return result


def assert_dispatch_ok(
    path: str | os.PathLike,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    max_used_pct: float = DEFAULT_MAX_USED_PCT,
) -> None:
    """Raise RuntimeError when the disk gate rejects dispatch.

    Convenience wrapper for callers that prefer exception-based flow control.
    """
    result = precheck_dispatch(path, min_free_gb, max_used_pct)
    if not result["ok"]:
        raise RuntimeError(
            f"dispatch disk gate rejected: {'; '.join(result['reasons'])}"
        )


# ---------------------------------------------------------------------------
# CLI: dry-run worktree GC with disk gate
# ---------------------------------------------------------------------------

def _get_dir_size(path: str) -> int:
    """Get total size of a directory tree (bytes)."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        # Skip .git to avoid counting the entire repo
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _classify_worktree(wt_path: str) -> str:
    """Classify a worktree into one of six categories.

    Categories (matching tools/sustainment/worktree_gc.py):
      - dirty: has uncommitted changes → PROTECT
      - orphan: no owner marker, not mission-shaped → PROTECT
      - active: unmerged, has unique commits, idle < TTL → KEEP
      - unpushed: unmerged, has unique commits, idle >= TTL → PROTECT
      - stale: git reports the tree prunable → ARCHIVE_REMOVE
      - finalized: no unique commits, idle >= TTL → ARCHIVE_REMOVE
    """
    import subprocess
    import re

    p = wt_path
    if not os.path.isdir(p):
        return "stale"

    # Check dirty
    try:
        result = subprocess.run(
            ["git", "-C", p, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return "dirty"
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Check orphan (no .git marker or not a valid worktree)
    git_marker = os.path.join(p, ".git")
    if not os.path.exists(git_marker):
        return "orphan"

    # Check branch info
    try:
        result = subprocess.run(
            ["git", "-C", p, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10
        )
        branch = result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        branch = ""

    # Check unpushed commits
    unpushed = False
    try:
        result = subprocess.run(
            ["git", "-C", p, "log", "--oneline", "@{upstream}..HEAD"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            unpushed = True
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Check last commit time
    last_commit_ts = 0.0
    try:
        result = subprocess.run(
            ["git", "-C", p, "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            last_commit_ts = float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        last_commit_ts = 0.0

    import time
    now = time.time()
    idle_hours = (now - last_commit_ts) / 3600.0 if last_commit_ts > 0 else 0.0
    ttl_hours = 24.0

    # Check owner markers
    owner_markers = (".agent_owner.json", ".owner.json", ".tp_owner.json")
    has_owner = any(os.path.exists(os.path.join(p, m)) for m in owner_markers)
    mission_dir_re = re.compile(r"^m\d+$")
    is_mission_dir = bool(mission_dir_re.match(os.path.basename(p)))

    if not has_owner and not is_mission_dir:
        return "orphan"

    if unpushed:
        if idle_hours < ttl_hours:
            return "active"
        return "unpushed"

    # Check if merged (head is same as base tip)
    # Simplified: if branch starts with agent/ or merged/ and is clean, it's finalized
    if branch.startswith(("agent/", "merged/")):
        if idle_hours >= ttl_hours:
            return "finalized"
        return "active"

    if idle_hours >= ttl_hours:
        return "finalized"

    return "active"


def run_gc_dryrun(
    root: str,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    max_used_pct: float = DEFAULT_MAX_USED_PCT,
) -> Dict[str, Any]:
    """Run a dry-run GC scan over worktrees under *root*.

    Returns a structured report with:
      - root: the scanned directory
      - total_worktrees: number of worktrees found
      - by_class: count per category
      - estimated_releasable_bytes: total bytes from ARCHIVE_REMOVE classes
      - disk_gate: result of precheck_dispatch on root
      - protect_count: number of PROTECT-class worktrees
      - delete_list: empty (dry-run never deletes)
    """
    import json

    result = {
        "root": root,
        "schema": "disk_gate/gc_dryrun/v1",
        "total_worktrees": 0,
        "by_class": {},
        "estimated_releasable_bytes": 0,
        "estimated_releasable_gb": 0.0,
        "disk_gate": precheck_dispatch(root, min_free_gb, max_used_pct),
        "protect_count": 0,
        "delete_list": [],  # dry-run: never deletes
        "scanned_at": None,
    }

    if not os.path.isdir(root):
        result["error"] = f"root directory does not exist: {root}"
        return result

    import time
    from datetime import datetime, timezone
    result["scanned_at"] = datetime.now(timezone.utc).isoformat()

    protect_classes = {"dirty", "orphan", "unpushed"}
    removable_classes = {"stale", "finalized"}

    for entry in sorted(os.listdir(root)):
        entry_path = os.path.join(root, entry)
        if not os.path.isdir(entry_path):
            continue
        git_marker = os.path.join(entry_path, ".git")
        if not os.path.exists(git_marker):
            continue

        klass = _classify_worktree(entry_path)
        size = _get_dir_size(entry_path)

        result["total_worktrees"] += 1
        result["by_class"][klass] = result["by_class"].get(klass, 0) + 1

        if klass in removable_classes:
            result["estimated_releasable_bytes"] += size
        if klass in protect_classes:
            result["protect_count"] += 1

    result["estimated_releasable_gb"] = round(
        result["estimated_releasable_bytes"] / (1 << 30), 2
    )

    return result


def main():
    """CLI entry point for disk gate precheck and GC dry-run."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Dispatch disk gate: precheck + GC dry-run"
    )
    parser.add_argument(
        "--root", default="/root/tp-agent/work",
        help="Root directory to scan for worktrees (default: /root/tp-agent/work)"
    )
    parser.add_argument(
        "--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB,
        help=f"Minimum free space in GB (default: {DEFAULT_MIN_FREE_GB})"
    )
    parser.add_argument(
        "--max-used-pct", type=float, default=DEFAULT_MAX_USED_PCT,
        help=f"Maximum usage percentage (default: {DEFAULT_MAX_USED_PCT})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run GC dry-run (scan and classify worktrees without deleting)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--precheck", action="store_true",
        help="Run precheck_dispatch only (no GC scan)"
    )

    args = parser.parse_args()

    if args.precheck or not args.dry_run:
        result = precheck_dispatch(args.root, args.min_free_gb, args.max_used_pct)
    else:
        result = run_gc_dryrun(args.root, args.min_free_gb, args.max_used_pct)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if "ok" in result:
            status = "PASS" if result["ok"] else "FAIL"
            print(f"Disk gate [{status}] for {result['path']}")
            print(f"  Free: {result['free_gb']} GB, Used: {result['used_pct']}%")
            if result["reasons"]:
                for r in result["reasons"]:
                    print(f"  Reason: {r}")
        else:
            print(f"GC dry-run for {result['root']}")
            print(f"  Total worktrees: {result['total_worktrees']}")
            print(f"  By class: {json.dumps(result['by_class'], indent=4)}")
            print(f"  Estimated releasable: {result['estimated_releasable_gb']} GB")
            print(f"  Protect count: {result['protect_count']}")
            print(f"  Disk gate: {'PASS' if result['disk_gate']['ok'] else 'FAIL'}")

    sys.exit(0 if (result.get("ok", True)) else 1)


if __name__ == "__main__":
    main()
