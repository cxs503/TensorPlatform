"""Worktree lifecycle governance: six classes, tar+zstd archive, disk gate, flock.

The platform accumulates agent worktrees at a rate of hundreds per wave; before
this module "which ones can go" was guesswork and cleanup was a manual, racy,
irreversible operation.  This module makes it a machine decision:

Six mutually exclusive classes (protective classes win, safety first)
--------------------------------------------------------------------
============  =======================================================  ========
class         definition                                              action
============  =======================================================  ========
``dirty``     uncommitted / untracked changes present                PROTECT
``orphan``    no owner marker and not a mission-shaped directory      PROTECT
``active``    unmerged, has unique commits, idle < TTL               KEEP
``unpushed``  unmerged, has unique commits, idle >= TTL               PROTECT
``stale``     git reports the tree prunable (directory gone)        PRUNE
``finalized`` no commit that base lacks (=work already upstream),    ARCHIVE+RMM
              idle >= TTL
============  =======================================================  ========

Nothing in ``PROTECT`` is ever removed — the worst case of a wrong GC is disk
space, never a lost commit.

Archive
-------
``tar --zstd`` of the whole worktree directory into
``<archive_root>/<name>-<head8>-<stamp>.tar.zst`` with a ``.sha256.json``
sidecar; the sidecar is re-read before the worktree is removed so an unarchived
removal is impossible.

Disk pressure gate
------------------
``disk_pressure(path)`` returns ``pass`` (< ``warn_pct``), ``warn``
(>= 85 %) or ``force`` (>= 90 %).  ``force`` additionally disallows creating new
worktrees (``allow_new_worktree=False``) so the platform cannot dig deeper while
the volume is nearly full.  Other tools can import ``assert_can_create_worktree``.

Concurrency
-----------
All mutating work happens under an exclusive ``flock`` on
``<archive_root>/.gc.lock``; a second invocation either waits (``--lock-timeout``)
or exits ``4`` (busy) — two GCs can never archive/remove the same tree at once.

Public API
----------
``discover(repo, **opts) -> list[Worktree]`` · ``classify(wt, **opts) -> Worktree``
``archive_worktree(...) -> dict`` · ``run_gc(...) -> dict`` · ``disk_pressure(path)``
``gc_lock(path, timeout)``

CLI::

    python3 -m tools.sustainment.worktree_gc --repo /root/tensor-ecosystem/TensorPlatform \\
        --worktrees-root /root/tp-agent/work --dry-run --out artifacts/sustainment/gc.json
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

SCHEMA_VERSION = 1

#: classes and their disposition
CLASS_ACTIONS: Dict[str, str] = {
    "dirty": "PROTECT",
    "orphan": "PROTECT",
    "unpushed": "PROTECT",
    "active": "KEEP",
    "stale": "ARCHIVE_REMOVE",
    "finalized": "ARCHIVE_REMOVE",
}
REMOVABLE = {c for c, a in CLASS_ACTIONS.items() if a == "ARCHIVE_REMOVE"}
CLASS_ORDER = ["dirty", "orphan", "active", "unpushed", "stale", "finalized"]

#: owner marker candidates inside a worktree
OWNER_MARKERS = (".agent_owner.json", ".owner.json", ".tp_owner.json")
MISSION_DIR_RE = re.compile(r"^m\d+$")
DEFAULT_TTL_HOURS = 24.0
DEFAULT_WARN_PCT = 85.0
DEFAULT_FORCE_PCT = 90.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(cmd: Sequence[str], cwd: Optional[str] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# disk pressure gate
# ---------------------------------------------------------------------------


def disk_pressure(
    path: str | os.PathLike,
    warn_pct: float = DEFAULT_WARN_PCT,
    force_pct: float = DEFAULT_FORCE_PCT,
) -> dict:
    """Measure volume usage and return the gate verdict for ``path``."""
    usage = shutil.disk_usage(str(path))
    used_pct = round(100.0 * (usage.total - usage.free) / usage.total, 2) if usage.total else 0.0
    if used_pct >= force_pct:
        level = "force"
    elif used_pct >= warn_pct:
        level = "warn"
    else:
        level = "pass"
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.total - usage.free,
        "free_bytes": usage.free,
        "used_pct": used_pct,
        "warn_pct": warn_pct,
        "force_pct": force_pct,
        "level": level,
        "archive_allowed": True,
        "remove_allowed": level in ("warn", "force"),
        "allow_new_worktree": level != "force",
        "checked_at": _now(),
    }


def assert_can_create_worktree(path: str | os.PathLike = "/") -> dict:
    """Raise ``RuntimeError`` when the disk gate forbids new worktrees."""
    gate = disk_pressure(path)
    if not gate["allow_new_worktree"]:
        raise RuntimeError(
            f"disk pressure gate: {gate['used_pct']}% >= {gate['force_pct']}% — "
            "new worktree creation blocked until GC reclaims space")
    return gate


# ---------------------------------------------------------------------------
# flock guard
# ---------------------------------------------------------------------------


class LockBusy(RuntimeError):
    pass


class gc_lock:
    """Exclusive advisory lock (``flock``) for the whole GC critical section."""

    def __init__(self, path: str | os.PathLike, timeout: float = 0.0):
        self.path = Path(path)
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        deadline = time.time() + max(self.timeout, 0.0)
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise LockBusy(f"gc lock busy: {self.path}")
                time.sleep(0.05)
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps({"pid": os.getpid(), "acquired_at": _now()}))
        self._fh.flush()
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


# ---------------------------------------------------------------------------
# worktree model
# ---------------------------------------------------------------------------


@dataclass
class Worktree:
    path: str
    head: str = ""
    branch: str = ""
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False
    owner: Optional[str] = None
    owner_marker: Optional[str] = None
    dirty_files: int = 0
    dirty_sample: List[str] = field(default_factory=list)
    ahead: int = 0
    merged: bool = False
    merged_via_merge: bool = False
    head_is_base_tip: bool = False
    idle_hours: float = 0.0
    last_commit_ts: Optional[float] = None
    size_bytes: int = 0
    size_capped: bool = False
    klass: str = ""
    disposition: str = ""
    reason: str = ""

    def to_json(self) -> dict:
        return {
            "path": self.path, "head": self.head, "branch": self.branch,
            "detached": self.detached, "locked": self.locked, "prunable": self.prunable,
            "head_is_base_tip": self.head_is_base_tip,
            "merged_via_merge": self.merged_via_merge,
            "owner": self.owner, "owner_marker": self.owner_marker,
            "dirty_files": self.dirty_files, "dirty_sample": self.dirty_sample,
            "ahead": self.ahead, "merged": self.merged,
            "idle_hours": round(self.idle_hours, 2),
            "last_commit_ts": self.last_commit_ts,
            "size_bytes": self.size_bytes, "size_capped": self.size_capped,
            "class": self.klass, "disposition": self.disposition, "reason": self.reason,
        }


def parse_worktree_list(porcelain: str) -> List[Dict[str, object]]:
    """Parse ``git worktree list --porcelain`` output."""
    entries: List[Dict[str, object]] = []
    cur: Dict[str, object] = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            if cur:
                entries.append(cur)
            cur = {"path": val}
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val.replace("refs/heads/", "")
        elif key == "detached":
            cur["detached"] = True
        elif key == "bare":
            cur["bare"] = True
        elif key == "locked":
            cur["locked"] = True
        elif key == "prunable":
            cur["prunable"] = True
    if cur:
        entries.append(cur)
    return entries


def _git_ok(repo: str, *args: str, timeout: int = 60) -> bool:
    try:
        return _run(["git", "-C", repo, *args], timeout=timeout).returncode == 0
    except subprocess.SubprocessError:
        return False


def _dir_size(path: Path, cap_files: int = 200_000) -> tuple[int, bool]:
    total, n = 0, 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            n += 1
            if n > cap_files:
                return total, True
            try:
                total += (Path(dirpath) / fn).stat().st_size
            except OSError:
                continue
    return total, False


def _is_marker_line(status_line: str, marker: str) -> bool:
    """True when a ``git status --porcelain`` line only concerns an owner marker."""
    path = status_line[3:].strip().strip('"')
    return path == marker or path.endswith("/" + marker)


def _read_owner(path: Path) -> tuple[Optional[str], Optional[str], Optional[float]]:
    for marker in OWNER_MARKERS:
        p = path / marker
        if p.exists():
            try:
                payload = json.loads(p.read_text())
                owner = payload.get("owner") or payload.get("agent") or payload.get("mission")
                ts = float(payload.get("heartbeat_ts") or p.stat().st_mtime)
                return (str(owner) if owner else None, marker, ts)
            except Exception:
                return None, marker, p.stat().st_mtime
    return None, None, None


def _populate(wt: Worktree, repo: str, base: str, ttl_hours: float, now: float) -> Worktree:
    """Collect one worktree's evidence (git calls) and classify it."""
    p = Path(wt.path)
    owner, marker, hb_ts = _read_owner(p)
    wt.owner_marker = marker
    if owner is None and MISSION_DIR_RE.match(p.name):
        owner = f"mission:{p.name}"
    wt.owner = owner
    try:
        out = _run(["git", "-C", str(p), "log", "-1", "--format=%ct"], timeout=30)
        wt.last_commit_ts = float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        wt.last_commit_ts = None
    idle_anchor = max([x for x in (wt.last_commit_ts, hb_ts) if x]
                      or [p.stat().st_mtime if p.exists() else now])
    wt.idle_hours = max(0.0, (now - idle_anchor) / 3600.0)
    # dirty — owner markers are worktree-local bookkeeping, not work in progress
    try:
        st = _run(["git", "-C", str(p), "status", "--porcelain"], timeout=120)
        lines = [ln for ln in st.stdout.splitlines() if ln.strip()] if st.returncode == 0 else []
        if marker:
            lines = [ln for ln in lines if not _is_marker_line(ln, marker)]
        wt.dirty_files = len(lines)
        wt.dirty_sample = [ln[:120] for ln in lines[:5]]
    except Exception:
        wt.dirty_files = -1  # unknown -> treated as dirty (protective)
    # merged into base / ahead of base
    base_tip = ""
    try:
        bt = _run(["git", "-C", repo, "rev-parse", base], timeout=30)
        base_tip = bt.stdout.strip() if bt.returncode == 0 else ""
    except Exception:
        base_tip = ""
    wt.head_is_base_tip = bool(base_tip) and wt.head == base_tip
    wt.merged = (_git_ok(repo, "merge-base", "--is-ancestor", wt.head or "HEAD", base)
                 and not wt.head_is_base_tip)
    if wt.merged:
        try:
            mm = _run(["git", "-C", repo, "rev-list", "--max-count=1", "--merges",
                       "--ancestry-path", f"{wt.head}..{base}"], timeout=60)
            wt.merged_via_merge = mm.returncode == 0 and bool(mm.stdout.strip())
        except Exception:
            wt.merged_via_merge = False
    try:
        ahead = _run(["git", "-C", repo, "rev-list", "--count",
                      f"{base}..{wt.head or 'HEAD'}"], timeout=60)
        wt.ahead = int(ahead.stdout.strip()) if ahead.returncode == 0 and ahead.stdout.strip() else 0
    except Exception:
        wt.ahead = -1
    classify(wt, ttl_hours=ttl_hours)
    return wt


def discover(
    repo: str,
    worktrees_root: Optional[str] = None,
    base: str = "main",
    ttl_hours: float = DEFAULT_TTL_HOURS,
    limit: Optional[int] = None,
    collect_size: bool = True,
    workers: int = 8,
    size_for_removable_only: bool = True,
) -> List[Worktree]:
    """Enumerate and classify worktrees of ``repo``.

    The per-worktree git probing is I/O bound, so it runs on a small thread pool
    (``workers``); ``limit`` takes the first N entries of ``git worktree list``
    order (oldest-created first) — a *sample*, reported as such by ``run_gc``.
    Directory sizes are walked only for removable classes by default, because a
    full size walk of thousands of trees costs far more than the decision needs.
    """
    proc = _run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree list failed: {proc.stderr.strip()[:300]}")
    raw = parse_worktree_list(proc.stdout)
    if worktrees_root:
        root = str(Path(worktrees_root).resolve())
        raw = [r for r in raw if str(r["path"]).startswith(root)]
    if limit:
        raw = raw[:limit]
    now = time.time()
    wts: List[Worktree] = []
    for r in raw:
        wts.append(Worktree(
            path=str(r["path"]), head=str(r.get("head", "")),
            branch=str(r.get("branch", "")),
            detached=bool(r.get("detached")), bare=bool(r.get("bare")),
            locked=bool(r.get("locked")), prunable=bool(r.get("prunable")),
        ))
    if workers > 1 and len(wts) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(workers, len(wts))) as pool:
            list(pool.map(lambda w: _populate(w, repo, base, ttl_hours, now), wts))
    else:
        for w in wts:
            _populate(w, repo, base, ttl_hours, now)
    if collect_size:
        for wt in wts:
            if size_for_removable_only and wt.klass not in REMOVABLE:
                continue
            p = Path(wt.path)
            if p.exists():
                wt.size_bytes, wt.size_capped = _dir_size(p)
    return wts


def classify(wt: Worktree, ttl_hours: float = DEFAULT_TTL_HOURS) -> Worktree:
    """Assign exactly one of the six classes (protective classes win).

    Ordering is deliberate: every class that could lose work is evaluated before
    the two removable ones, so a mis-measurement degrades to "keep", never to
    "delete".
    """
    if wt.prunable:
        # git itself says the tree is gone: metadata-only, there is no file to lose.
        # Checked first because a vanished directory cannot carry an owner marker.
        wt.klass, wt.reason = "stale", "git_prunable_directory_missing"
    elif wt.dirty_files != 0:
        wt.klass, wt.reason = "dirty", f"uncommitted_changes={wt.dirty_files}"
    elif wt.owner is None:
        wt.klass, wt.reason = "orphan", "no_owner_marker"
    elif wt.locked:
        wt.klass, wt.reason = "active", "git_locked"
    elif wt.ahead > 0:
        if wt.idle_hours >= ttl_hours:
            wt.klass = "unpushed"
            wt.reason = f"unique_commits_not_in_base={wt.ahead},idle={wt.idle_hours:.1f}h>=ttl"
        else:
            wt.klass = "active"
            wt.reason = f"in_progress_unique_commits={wt.ahead},idle={wt.idle_hours:.1f}h<ttl"
    else:
        if wt.idle_hours >= ttl_hours:
            wt.klass = "finalized"
            wt.reason = (f"nothing_base_lacks,merged={wt.merged},"
                         f"idle={wt.idle_hours:.1f}h>=ttl")
        else:
            wt.klass = "active"
            wt.reason = f"fresh_no_unique_commits,idle={wt.idle_hours:.1f}h<ttl"
    wt.disposition = CLASS_ACTIONS[wt.klass]
    return wt


def prune_stale_metadata(repo: str, apply: bool = False) -> dict:
    """Drop git worktree metadata whose directory is gone (``git worktree prune``)."""
    cmd = ["git", "-C", repo, "worktree", "prune", "-v"]
    if not apply:
        cmd.append("--dry-run")
    r = _run(cmd, timeout=300)
    return {"ok": r.returncode == 0, "applied": apply,
            "output": (r.stdout + r.stderr).strip()[:1000]}


# ---------------------------------------------------------------------------
# stray directories: the part of the tree git cannot see
# ---------------------------------------------------------------------------


def measure_stray_dirs(
    worktrees_root: str,
    registered_paths: Iterable[str],
    sample_size: int = 30,
    seed: int = 7,
    cap_files: int = 20000,
) -> dict:
    """Quantify directories under ``worktrees_root`` that are *not* git worktrees.

    ``git worktree list`` only knows the audited repo's worktrees, so anything left
    behind by a crashed worker / manual copy is invisible to the six-class GC — and
    can dominate disk usage.  Directories that still carry a ``.git`` marker are
    worktrees of some *other* repository (counted separately, never touched).  This measures that blind spot (count exactly, footprint
    by uniform random sample) so the blind spot is reported instead of guessed.
    Never deletes anything: unregistered implies unowned.
    """
    import random

    root = Path(worktrees_root)
    if not root.is_dir():
        return {"ok": False, "reason": "worktrees_root_missing", "path": str(root)}
    registered = {str(Path(p)) for p in registered_paths}
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    unregistered = [p for p in dirs if str(p) not in registered]
    # a directory carrying a `.git` marker is a worktree of *some other* repo
    # (e.g. another Tensor* repository) — live, not garbage.  Only marker-less
    # leftovers count as the GC blind spot.
    foreign = [p for p in unregistered if (p / ".git").exists()]
    stray = [p for p in unregistered if not (p / ".git").exists()]
    rnd = random.Random(seed)
    sample = rnd.sample(stray, min(sample_size, len(stray))) if stray else []
    total, capped = 0, 0
    for d in sample:
        size, is_capped = _dir_size(d, cap_files=cap_files)
        total += size
        capped += 1 if is_capped else 0
    mean = (total / len(sample)) if sample else 0.0
    return {
        "ok": True,
        "path": str(root),
        "dirs_total": len(dirs),
        "registered": len(registered),
        "unregistered": len(unregistered),
        "foreign_worktrees": len(foreign),
        "stray": len(stray),
        "sample_n": len(sample),
        "sample_bytes": total,
        "mean_bytes": round(mean, 1),
        "estimated_stray_bytes": int(mean * len(stray)),
        "size_capped_in_sample": capped,
        "sample_names": [p.name for p in sample[:15]],
        "method": f"uniform random sample of {len(sample)}/{len(stray)} dirs, size walk capped at {cap_files} files each",
    }


# ---------------------------------------------------------------------------
# archive + remove
# ---------------------------------------------------------------------------


def archive_worktree(
    wt: Worktree,
    archive_root: str,
    apply: bool = False,
    repo: Optional[str] = None,
) -> dict:
    """``tar --zstd`` the worktree, write a sha256 sidecar, then optionally remove."""
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    name = Path(wt.path).name
    safe_head = (wt.head or "nohead")[:8]
    stem = f"{name}-{safe_head}-{_stamp()}"
    tar_path = root / f"{stem}.tar.zst"
    sidecar = root / f"{stem}.sha256.json"
    rec = {"worktree": wt.path, "class": wt.klass, "archive": str(tar_path),
           "sidecar": str(sidecar), "removed": False, "applied": apply}
    if not Path(wt.path).exists():
        rec.update({"ok": False, "error": "worktree_path_missing"})
        return rec
    if shutil.which("zstd") is None:
        rec.update({"ok": False, "error": "zstd_unavailable"})
        return rec
    parent = str(Path(wt.path).parent)
    cmd = ["tar", "--zstd", "-cf", str(tar_path), "-C", parent, name]
    t0 = time.time()
    try:
        r = _run(cmd, timeout=3600)
    except subprocess.SubprocessError as exc:
        rec.update({"ok": False, "error": f"tar_timeout:{exc}"})
        return rec
    if r.returncode != 0:
        rec.update({"ok": False, "error": f"tar_failed:{r.stderr.strip()[:300]}"})
        return rec
    digest = hashlib.sha256()
    with open(tar_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    h = digest.hexdigest()
    sidecar.write_text(json.dumps({
        "worktree": wt.path, "head": wt.head, "branch": wt.branch, "class": wt.klass,
        "sha256": h, "bytes": tar_path.stat().st_size, "archived_at": _now(),
        "member_count_estimate": None,
    }, indent=2, ensure_ascii=False))
    rec.update({"ok": True, "sha256": h, "bytes": tar_path.stat().st_size,
                "archive_seconds": round(time.time() - t0, 3)})
    # verify the sidecar before removal — no unarchived removal is possible
    if apply:
        try:
            check = json.loads(sidecar.read_text())
            assert check["sha256"] == h, "sidecar sha256 mismatch"
        except Exception as exc:  # pragma: no cover - defensive
            rec.update({"ok": False, "error": f"sidecar_verify_failed:{exc}"})
            return rec
        if repo:
            rm = _run(["git", "-C", repo, "worktree", "remove", "--force", wt.path], timeout=600)
            if rm.returncode != 0:
                # fall back to rmtree only when git refuses but archive is verified
                rec["git_remove_error"] = rm.stderr.strip()[:300]
                try:
                    shutil.rmtree(wt.path)
                except OSError as exc:
                    rec.update({"ok": False, "error": f"remove_failed:{exc}"})
                    return rec
        else:
            shutil.rmtree(wt.path)
        rec["removed"] = True
    return rec


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_gc(
    repo: str,
    worktrees_root: Optional[str] = None,
    archive_root: str = "artifacts/sustainment/worktree_archive",
    base: str = "main",
    ttl_hours: float = DEFAULT_TTL_HOURS,
    apply: bool = False,
    limit: Optional[int] = None,
    collect_size: bool = True,
    lock_path: Optional[str] = None,
    lock_timeout: float = 0.0,
    disk_path: Optional[str] = None,
    workers: int = 8,
    stray_sample: int = 30,
    warn_pct: float = DEFAULT_WARN_PCT,
    force_pct: float = DEFAULT_FORCE_PCT,
    out_json: Optional[str] = None,
) -> dict:
    """Discover → classify → gate → (archive/remove) → report."""
    t0 = time.time()
    disk_path = disk_path or worktrees_root or repo
    gate = disk_pressure(disk_path, warn_pct=warn_pct, force_pct=force_pct)
    lock_file = lock_path or str(Path(archive_root) / ".gc.lock")
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "repo": repo, "worktrees_root": worktrees_root, "base": base,
        "ttl_hours": ttl_hours, "apply": apply,
        "disk": gate,
        "actions": [],
        "errors": [],
    }
    with gc_lock(lock_file, timeout=lock_timeout):
        try:
            total_listed = len(parse_worktree_list(
                _run(["git", "-C", repo, "worktree", "list", "--porcelain"]).stdout))
        except Exception:
            total_listed = -1
        wts = discover(repo, worktrees_root=worktrees_root, base=base,
                       ttl_hours=ttl_hours, limit=limit, collect_size=collect_size,
                       workers=workers)
        counts: Dict[str, int] = {c: 0 for c in CLASS_ORDER}
        bytes_by_class: Dict[str, int] = {c: 0 for c in CLASS_ORDER}
        for wt in wts:
            counts[wt.klass] = counts.get(wt.klass, 0) + 1
            bytes_by_class[wt.klass] = bytes_by_class.get(wt.klass, 0) + wt.size_bytes
        candidates = [wt for wt in wts if wt.klass in REMOVABLE]
        reclaimable = sum(wt.size_bytes for wt in candidates)
        report["summary"] = {
            "worktrees": len(wts),
            "worktrees_listed_total": total_listed,
            "sampled": bool(limit and total_listed > len(wts)),
            "by_class": counts,
            "bytes_by_class": bytes_by_class,
            "protected": sum(1 for wt in wts if CLASS_ACTIONS[wt.klass] == "PROTECT"),
            "removable": len(candidates),
            "reclaimable_bytes": reclaimable,
            "size_capped_worktrees": sum(1 for wt in wts if wt.size_capped),
        }
        report["worktrees"] = [wt.to_json() for wt in wts]
        # gate arbitration: 'pass' level refuses removal (archive still allowed)
        allowed = candidates if gate["remove_allowed"] or not apply else []
        if apply and not gate["remove_allowed"]:
            report["errors"].append(
                f"disk_gate_pass:{gate['used_pct']}%<{gate['warn_pct']}% — removal skipped (dry archive only)")
        stale = [wt for wt in candidates if wt.klass == "stale"]
        archivable = [wt for wt in candidates if wt.klass == "finalized"]
        if apply and stale and (wt_allowed := gate["remove_allowed"]):
            pr = prune_stale_metadata(repo, apply=True)
            report["prune"] = pr
            if not pr["ok"]:
                report["errors"].append(f"prune_failed:{pr['output'][:200]}")
            for wt in stale:
                report["actions"].append({
                    "worktree": wt.path, "class": "stale", "action": "prune_metadata",
                    "ok": pr["ok"], "removed": pr["ok"], "applied": True,
                })
            del wt_allowed
        for wt in archivable if apply else []:
            if not gate["remove_allowed"]:
                continue
            rec = archive_worktree(wt, archive_root, apply=apply, repo=repo)
            rec["worktree_bytes"] = wt.size_bytes   # measured before removal
            report["actions"].append(rec)
            if not rec.get("ok"):
                report["errors"].append(f"archive_failed:{wt.path}:{rec.get('error')}")
        if not apply:
            report["planned"] = [
                {"path": wt.path, "class": wt.klass, "size_bytes": wt.size_bytes,
                 "reason": wt.reason} for wt in candidates
            ]
        if worktrees_root:
            report["stray_dirs"] = measure_stray_dirs(
                worktrees_root, [wt.path for wt in wts], sample_size=stray_sample)
            report["summary"]["stray_dirs"] = report["stray_dirs"].get("stray")
            report["summary"]["estimated_stray_bytes"] = \
                report["stray_dirs"].get("estimated_stray_bytes")
        after = disk_pressure(disk_path, warn_pct=warn_pct, force_pct=force_pct)
        report["disk_after"] = after
        # NB: reclaimed = bytes actually freed on the volume (the removed trees),
        # archived = bytes written to the archive (compressed).  Conflating the two
        # was a real metric bug in the first run of this module (see CHANGELOG).
        report["summary"]["reclaimed_bytes"] = sum(
            a.get("worktree_bytes", 0) for a in report["actions"]
            if a.get("removed") and "archive" in a)
        report["summary"]["archive_bytes"] = sum(
            a.get("bytes", 0) for a in report["actions"] if a.get("ok"))
        report["summary"]["archived"] = sum(1 for a in report["actions"] if a.get("ok"))
        report["summary"]["pruned"] = sum(
            1 for a in report["actions"] if a.get("action") == "prune_metadata")
    report["elapsed_s"] = round(time.time() - t0, 3)
    report["gate_verdict"] = {
        "level": gate["level"],
        "allow_new_worktree": gate["allow_new_worktree"],
        "remove_allowed": gate["remove_allowed"],
        "thresholds": {"warn_pct": warn_pct, "force_pct": force_pct},
    }
    if out_json:
        p = Path(out_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report