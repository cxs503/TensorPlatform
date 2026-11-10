"""Worktree GC — worktree lifecycle management with disk pressure gate.

Features:
  - Six-category classification (active/terminal/unpushed/orphan/stale/archived)
  - tar+zstd archival of terminal worktrees
  - Disk pressure gate (>85% trigger / >90% force)
  - flock concurrency protection
  - RPO/RTO accounting

Design: standalone, stdlib + subprocess only (zstd via CLI).
"""
from .gc_manager import (
    WorktreeInfo,
    WorktreeGC,
    DiskPressureGate,
    classify_worktrees,
    archive_worktree,
    run_gc,
)

__all__ = [
    "WorktreeInfo",
    "WorktreeGC",
    "DiskPressureGate",
    "classify_worktrees",
    "archive_worktree",
    "run_gc",
]
__version__ = "1.0.0"
