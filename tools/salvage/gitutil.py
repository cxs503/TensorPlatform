"""git plumbing helpers for the salvage scanner (stdlib only, no shell).

Design notes
------------
* Every helper takes an explicit ``worktree`` path and never changes repository
  configuration; only read-only plumbing and ``status`` are used, so scanning a
  dead worktree cannot mutate it.
* ``status_entries`` uses ``--porcelain -uall -z`` (NUL separated) so paths with
  spaces / CJK / newlines survive verbatim.
* Content identity is expressed as **sha256 of the file bytes** at both ends
  (worktree file and ``git show <rev>:<path>`` bytes).  Computing both sides the
  same way keeps the comparison filter-agnostic: if a repository ever grows a
  clean/smudge filter the hashes stop matching and the file is *kept* (the
  conservative direction: never prune content we cannot prove is merged).
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT = 180


class GitError(RuntimeError):
    """Raised when a git invocation fails or the path is not a repository."""


def _run(worktree: str | Path, args: list[str], timeout: int = GIT_TIMEOUT) -> bytes:
    cmd = ["git", "-C", str(worktree), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise GitError(f"timeout after {timeout}s: {' '.join(cmd)}") from exc
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} rc={proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}"
        )
    return proc.stdout


def is_git_worktree(worktree: str | Path) -> bool:
    if not Path(worktree).is_dir():
        return False
    try:
        _run(worktree, ["rev-parse", "--git-dir"])
    except GitError:
        return False
    return True


def git_text(worktree: str | Path, *args: str) -> str:
    return _run(worktree, list(args)).decode("utf-8", "replace")


def rev_parse_opt(worktree: str | Path, rev: str) -> str | None:
    """``git rev-parse <rev>`` or None when the revision does not exist."""
    try:
        out = git_text(worktree, "rev-parse", "--verify", "--quiet", rev).strip()
    except GitError:
        return None
    return out or None


def head_sha(worktree: str | Path) -> str | None:
    return rev_parse_opt(worktree, "HEAD")


def head_commit_epoch(worktree: str | Path) -> int:
    """Commit time of HEAD (content-derived clock; never wall clock)."""
    try:
        return int(git_text(worktree, "log", "-1", "--format=%ct", "HEAD").strip())
    except (GitError, ValueError):
        return 0


def merge_base(worktree: str | Path, a: str, b: str) -> str | None:
    try:
        out = git_text(worktree, "merge-base", a, b).strip()
    except GitError:
        return None
    return out or None


@dataclass(frozen=True)
class StatusEntry:
    """One ``git status --porcelain`` record."""

    code: str  # two-letter XY code, e.g. "??", " M", "A ", "D "
    path: str  # destination path (rename target)
    orig_path: str | None = None

    @property
    def untracked(self) -> bool:
        return self.code == "??"

    @property
    def deleted(self) -> bool:
        return "D" in self.code

    @property
    def renamed(self) -> bool:
        return "R" in self.code

    @property
    def label(self) -> str:
        return self.code.strip() or "??"


def status_entries(worktree: str | Path) -> list[StatusEntry]:
    """Uncommitted changes, including every untracked file (``-uall``)."""
    raw = _run(worktree, ["status", "--porcelain", "-uall", "-z"])
    chunks = raw.split(b"\0")
    out: list[StatusEntry] = []
    idx = 0
    while idx < len(chunks):
        chunk = chunks[idx]
        idx += 1
        if not chunk:
            continue
        if len(chunk) < 4:  # malformed / defensive
            continue
        code = chunk[:2].decode("utf-8", "replace")
        path = chunk[3:].decode("utf-8", "surrogateescape")
        orig = None
        if "R" in code or "C" in code:
            if idx < len(chunks) and chunks[idx]:
                orig = chunks[idx].decode("utf-8", "surrogateescape")
                idx += 1
        out.append(StatusEntry(code=code, path=path, orig_path=orig))
    return out


def mission_changed_paths(
    worktree: str | Path, baseline_ref: str = "main", max_paths: int = 4000
) -> list[StatusEntry]:
    """Paths the mission branch delivered relative to its merge base.

    Used *only* by the negative-control harness: a merged mission is clean in
    ``git status``, so to exercise the "already merged" pruning logic we replay
    the mission's own delivery set (``merge-base(baseline, HEAD)..HEAD``).
    """
    base = merge_base(worktree, baseline_ref, "HEAD")
    if base is None:
        return []
    out = git_text(worktree, "diff", "--name-status", "-z", f"{base}..HEAD")
    chunks = out.split("\0")
    entries: list[StatusEntry] = []
    idx = 0
    while idx < len(chunks):
        code = chunks[idx]
        idx += 1
        if not code:
            continue
        if code[0] in "RC":
            old = chunks[idx] if idx < len(chunks) else ""
            new = chunks[idx + 1] if idx + 1 < len(chunks) else ""
            idx += 2
            entries.append(StatusEntry(code=f"{code[0]} ", path=new, orig_path=old))
        else:
            path = chunks[idx] if idx < len(chunks) else ""
            idx += 1
            entries.append(StatusEntry(code=f"{code[0]} ", path=path))
        if len(entries) >= max_paths:
            break
    return entries


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def blob_sha256(worktree: str | Path, rev: str, path: str) -> str | None:
    """sha256 of ``<rev>:<path>`` blob bytes, or None when absent in that tree."""
    try:
        data = _run(worktree, ["show", f"{rev}:{path}"])
    except GitError:
        return None
    return hashlib.sha256(data).hexdigest()


def commits_touching(worktree: str | Path, rev: str, path: str, limit: int = 10) -> list[str]:
    """Most recent commits on ``rev`` that touched ``path`` (bounded history probe)."""
    try:
        out = git_text(worktree, "rev-list", f"--max-count={limit}", rev, "--", path)
    except GitError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def read_text_limited(path: str | Path, limit: int = 262_144) -> str:
    """Best-effort utf-8 decode of the first ``limit`` bytes (for lexical matching)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", "replace")