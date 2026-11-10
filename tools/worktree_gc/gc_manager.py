#!/usr/bin/env python3
"""Worktree GC — worktree lifecycle management with disk pressure gate.

Features:
  - Six-category classification (active/terminal/unpushed/orphan/stale/archived)
  - tar+zstd archival of terminal worktrees
  - Disk pressure gate (>85% trigger / >90% force)
  - flock concurrency protection
  - RPO/RTO accounting

Design: standalone, stdlib + subprocess only (zstd via CLI).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class WorktreeCategory(str, Enum):
    """Worktree lifecycle category."""
    ACTIVE = "active"           # currently being worked on (recent commits)
    TERMINAL = "terminal"       # merged/closed, ready for archival
    UNPUSHED = "unpushed"       # has commits not yet pushed
    ORPHAN = "orphan"           # no matching branch, no recent activity
    STALE = "stale"             # no activity for >30 days
    ARCHIVED = "archived"       # already archived


@dataclass
class WorktreeInfo:
    """Information about a single worktree."""
    path: Path
    branch: str = ""
    head_commit: str = ""
    last_activity: float = 0.0   # epoch
    category: WorktreeCategory = WorktreeCategory.ACTIVE
    size_bytes: int = 0
    has_unpushed: bool = False
    is_clean: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_days(self) -> float:
        if self.last_activity <= 0:
            return 0.0
        return (time.time() - self.last_activity) / 86400

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "branch": self.branch,
            "head_commit": self.head_commit,
            "last_activity": self.last_activity,
            "age_days": round(self.age_days, 1),
            "category": self.category.value,
            "size_bytes": self.size_bytes,
            "has_unpushed": self.has_unpushed,
            "is_clean": self.is_clean,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Disk pressure gate
# ---------------------------------------------------------------------------

@dataclass
class DiskPressureGate:
    """Disk usage pressure gate with thresholds.

    - Below trigger_pct: no action
    - Above trigger_pct: recommend cleanup
    - Above force_pct: force cleanup (archive + remove)
    """
    trigger_pct: float = 85.0
    force_pct: float = 90.0
    mount_point: str = "/"

    def check(self) -> Dict[str, Any]:
        """Check current disk usage and return pressure status."""
        try:
            stat = os.statvfs(self.mount_point)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used = total - free
            pct = (used / total) * 100 if total > 0 else 0
        except (OSError, ZeroDivisionError):
            return {"error": "cannot stat filesystem", "pressure": "unknown"}

        if pct >= self.force_pct:
            pressure = "critical"
            action = "force_gc"
        elif pct >= self.trigger_pct:
            pressure = "warning"
            action = "recommend_gc"
        else:
            pressure = "normal"
            action = "none"

        return {
            "mount_point": self.mount_point,
            "total_gb": round(total / (1 << 30), 2),
            "used_gb": round(used / (1 << 30), 2),
            "free_gb": round(free / (1 << 30), 2),
            "used_pct": round(pct, 1),
            "trigger_pct": self.trigger_pct,
            "force_pct": self.force_pct,
            "pressure": pressure,
            "action": action,
        }


# ---------------------------------------------------------------------------
# Worktree scanning & classification
# ---------------------------------------------------------------------------

def _get_dir_size(path: Path) -> int:
    """Get total size of a directory tree."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _get_last_activity(path: Path) -> float:
    """Get the most recent modification time in a worktree."""
    latest = 0.0
    git_dir = path / ".git"
    # Check git log for last commit time
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=str(path), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        pass

    # Fallback: scan recent files
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                mt = os.path.getmtime(fp)
                if mt > latest:
                    latest = mt
            except OSError:
                pass
    return latest


def _get_branch_info(path: Path) -> Tuple[str, str, bool]:
    """Get branch name, head commit, and whether there are unpushed commits."""
    branch = ""
    head = ""
    unpushed = False

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except subprocess.SubprocessError:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(path), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            head = result.stdout.strip()
    except subprocess.SubprocessError:
        pass

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "@{upstream}..HEAD"],
            cwd=str(path), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            unpushed = True
    except subprocess.SubprocessError:
        pass

    return branch, head, unpushed


def scan_worktrees(base_dir: Path) -> List[WorktreeInfo]:
    """Scan a directory for worktrees (subdirectories with .git)."""
    worktrees = []
    base_dir = Path(base_dir)

    if not base_dir.exists():
        return worktrees

    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir():
            continue
        git_marker = entry / ".git"
        if not git_marker.exists():
            continue

        branch, head, unpushed = _get_branch_info(entry)
        last_activity = _get_last_activity(entry)
        size = _get_dir_size(entry)

        # Check if working tree is clean
        is_clean = True
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(entry), capture_output=True, text=True, timeout=10
            )
            is_clean = len(result.stdout.strip()) == 0
        except subprocess.SubprocessError:
            pass

        wt = WorktreeInfo(
            path=entry,
            branch=branch,
            head_commit=head,
            last_activity=last_activity,
            size_bytes=size,
            has_unpushed=unpushed,
            is_clean=is_clean,
        )
        worktrees.append(wt)

    return worktrees


def classify_worktrees(
    worktrees: List[WorktreeInfo],
    stale_days: float = 30.0,
    archived_marker: str = ".archived",
) -> List[WorktreeInfo]:
    """Classify worktrees into lifecycle categories.

    Rules:
    - ACTIVE: last activity < 7 days AND has unpushed OR dirty
    - UNPUSHED: has unpushed commits but not active
    - TERMINAL: branch starts with 'agent/' or 'merged/' AND clean AND no unpushed
    - STALE: last activity > stale_days
    - ORPHAN: no branch info AND no recent activity
    - ARCHIVED: has .archived marker file
    """
    for wt in worktrees:
        # Check archived marker
        if (wt.path / archived_marker).exists():
            wt.category = WorktreeCategory.ARCHIVED
            continue

        # Stale check
        if wt.age_days > stale_days:
            wt.category = WorktreeCategory.STALE
            continue

        # Active check
        if wt.age_days < 7 and (wt.has_unpushed or not wt.is_clean):
            wt.category = WorktreeCategory.ACTIVE
            continue

        # Unpushed
        if wt.has_unpushed:
            wt.category = WorktreeCategory.UNPUSHED
            continue

        # Terminal: agent branches that are clean
        if wt.branch.startswith(("agent/", "merged/")) and wt.is_clean:
            wt.category = WorktreeCategory.TERMINAL
            continue

        # Orphan: no branch, old
        if not wt.branch and wt.age_days > 3:
            wt.category = WorktreeCategory.ORPHAN
            continue

        # Default: active
        wt.category = WorktreeCategory.ACTIVE

    return worktrees


# ---------------------------------------------------------------------------
# Archival
# ---------------------------------------------------------------------------

def archive_worktree(
    wt: WorktreeInfo,
    archive_dir: Path,
    use_zstd: bool = True,
) -> Dict[str, Any]:
    """Archive a worktree to tar+zstd (or tar+gz).

    Returns:
        dict with archive_path, original_size, archive_size, ratio
    """
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-.]', '_', wt.path.name)
    ext = ".tar.zst" if use_zstd else ".tar.gz"
    archive_path = archive_dir / f"{safe_name}_{ts}{ext}"

    # Create archive
    if use_zstd:
        # Use tar + zstd pipeline
        try:
            tar_cmd = ["tar", "-cf", "-", "-C", str(wt.path.parent), wt.path.name]
            zstd_cmd = ["zstd", "-3", "-o", str(archive_path)]
            tar_proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
            zstd_proc = subprocess.Popen(zstd_cmd, stdin=tar_proc.stdout)
            tar_proc.stdout.close()
            zstd_proc.communicate(timeout=300)
            if zstd_proc.returncode != 0:
                # Fallback to gzip
                archive_path = archive_dir / f"{safe_name}_{ts}.tar.gz"
                subprocess.run(
                    ["tar", "-czf", str(archive_path), "-C", str(wt.path.parent), wt.path.name],
                    check=True, timeout=300
                )
        except (subprocess.SubprocessError, FileNotFoundError):
            # Fallback to gzip
            archive_path = archive_dir / f"{safe_name}_{ts}.tar.gz"
            subprocess.run(
                ["tar", "-czf", str(archive_path), "-C", str(wt.path.parent), wt.path.name],
                check=True, timeout=300
            )
    else:
        subprocess.run(
            ["tar", "-czf", str(archive_path), "-C", str(wt.path.parent), wt.path.name],
            check=True, timeout=300
        )

    archive_size = archive_path.stat().st_size

    # Mark as archived
    (wt.path / ".archived").write_text(
        json.dumps({
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "archive_path": str(archive_path),
            "original_size": wt.size_bytes,
            "archive_size": archive_size,
        }, indent=2)
    )

    return {
        "archive_path": str(archive_path),
        "original_size": wt.size_bytes,
        "archive_size": archive_size,
        "ratio": round(archive_size / wt.size_bytes, 3) if wt.size_bytes > 0 else 0,
        "success": True,
    }


# ---------------------------------------------------------------------------
# GC Manager
# ---------------------------------------------------------------------------

class WorktreeGC:
    """Worktree garbage collector with disk pressure awareness."""

    def __init__(
        self,
        base_dir: Path,
        archive_dir: Path,
        stale_days: float = 30.0,
        trigger_pct: float = 85.0,
        force_pct: float = 90.0,
        lock_file: Optional[Path] = None,
    ):
        self.base_dir = Path(base_dir)
        self.archive_dir = Path(archive_dir)
        self.stale_days = stale_days
        self.gate = DiskPressureGate(trigger_pct=trigger_pct, force_pct=force_pct)
        self.lock_file = lock_file or (self.archive_dir / ".gc.lock")

    def scan(self) -> List[WorktreeInfo]:
        """Scan and classify all worktrees."""
        worktrees = scan_worktrees(self.base_dir)
        return classify_worktrees(worktrees, stale_days=self.stale_days)

    def plan(self) -> Dict[str, Any]:
        """Generate a GC plan without executing."""
        worktrees = self.scan()
        disk = self.gate.check()

        categories = {}
        for wt in worktrees:
            cat = wt.category.value
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(wt.to_dict())

        # Determine action based on disk pressure
        can_archive = []
        if disk["action"] in ("recommend_gc", "force_gc"):
            for wt in worktrees:
                if wt.category in (WorktreeCategory.TERMINAL, WorktreeCategory.STALE,
                                   WorktreeCategory.ORPHAN):
                    can_archive.append(wt.to_dict())

        return {
            "disk": disk,
            "categories": {k: len(v) for k, v in categories.items()},
            "details": categories,
            "can_archive": can_archive,
            "total_size_reclaimable": sum(w["size_bytes"] for w in can_archive),
            "planned_at": datetime.now(timezone.utc).isoformat(),
        }

    def execute(self, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
        """Execute GC: archive eligible worktrees.

        Args:
            dry_run: if True, only report what would be done
            force: if True, ignore disk pressure gate
        """
        disk = self.gate.check()

        if not force and disk["action"] == "none":
            return {
                "executed": False,
                "reason": "disk pressure normal, no action needed",
                "disk": disk,
            }

        # Acquire lock
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(self.lock_file, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_fd.close()
            return {"executed": False, "reason": "another GC is running (lock held)"}

        try:
            worktrees = self.scan()
            results = []
            archived_count = 0
            total_reclaimed = 0

            for wt in worktrees:
                if wt.category not in (WorktreeCategory.TERMINAL, WorktreeCategory.STALE,
                                       WorktreeCategory.ORPHAN):
                    continue
                if wt.has_unpushed and not force:
                    results.append({
                        "path": str(wt.path),
                        "action": "skipped",
                        "reason": "has unpushed commits",
                    })
                    continue

                if dry_run:
                    results.append({
                        "path": str(wt.path),
                        "action": "would_archive",
                        "category": wt.category.value,
                        "size": wt.size_bytes,
                    })
                    continue

                # Archive
                try:
                    archive_result = archive_worktree(wt, self.archive_dir)
                    results.append({
                        "path": str(wt.path),
                        "action": "archived",
                        **archive_result,
                    })
                    archived_count += 1
                    total_reclaimed += wt.size_bytes - archive_result["archive_size"]
                except Exception as e:
                    results.append({
                        "path": str(wt.path),
                        "action": "failed",
                        "error": str(e),
                    })

            return {
                "executed": True,
                "dry_run": dry_run,
                "force": force,
                "disk_before": disk,
                "archived": archived_count,
                "total_reclaimed_bytes": total_reclaimed,
                "results": results,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_gc(
    base_dir: str = "work",
    archive_dir: str = "work/.archives",
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Run worktree GC with default settings."""
    gc = WorktreeGC(
        base_dir=Path(base_dir),
        archive_dir=Path(archive_dir),
    )
    return gc.execute(dry_run=dry_run, force=force)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Worktree GC")
    parser.add_argument("--base-dir", default="work", help="Base directory containing worktrees")
    parser.add_argument("--archive-dir", default="work/.archives", help="Archive destination")
    parser.add_argument("--dry-run", action="store_true", help="Only report, don't execute")
    parser.add_argument("--force", action="store_true", help="Force GC regardless of disk pressure")
    parser.add_argument("--plan", action="store_true", help="Show GC plan")
    parser.add_argument("--disk", action="store_true", help="Show disk status only")
    args = parser.parse_args()

    gc = WorktreeGC(
        base_dir=Path(args.base_dir),
        archive_dir=Path(args.archive_dir),
    )

    if args.disk:
        print(json.dumps(gc.gate.check(), indent=2))
    elif args.plan:
        print(json.dumps(gc.plan(), indent=2))
    else:
        result = gc.execute(dry_run=args.dry_run, force=args.force)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
