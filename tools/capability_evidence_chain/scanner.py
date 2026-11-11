"""Cross-repository verification-artefact scanner.

The scanner walks configured repository roots, reads JSON artefacts that match
declared globs, and extracts *numeric leaves whose key matches a declared
metric pattern*.  It never interprets the physics: a "metric" is a regular
expression over key names supplied by configuration, so the same scanner works
for any solver family.

Every file it touches is recorded with its SHA-256 and status (``ok`` /
``invalid_json`` / ``read_error`` / ``too_large``), so the downstream
association rate can be recomputed from the artefact list alone and unreadable
inputs are visible rather than silently dropped.
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import re
import subprocess
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .config import EvidenceChainConfig, RepoSpec

__all__ = [
    "ArtifactRecord",
    "Observation",
    "ScanResult",
    "compile_patterns",
    "scan",
    "iter_json_files",
    "walk_numeric_leaves",
    "repo_commit",
]

MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 16
MAX_LIST_ITEMS = 500


@dataclass
class ArtifactRecord:
    repo: str
    path: str                     # repository-relative path
    status: str                   # ok | invalid_json | read_error | too_large
    sha256: str = ""
    bytes: int = 0
    n_numeric: int = 0
    n_matched: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "repo": self.repo,
            "path": self.path,
            "status": self.status,
            "bytes": self.bytes,
            "n_numeric_leaves": self.n_numeric,
            "n_matched_leaves": self.n_matched,
            "recognized": self.n_matched > 0,
        }
        if self.sha256:
            out["sha256"] = self.sha256
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class Observation:
    repo: str
    path: str
    key_path: str
    key: str
    value: float
    labels: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "path": self.path,
            "key_path": self.key_path,
            "key": self.key,
            "value": self.value,
            "labels": list(self.labels),
        }


@dataclass
class ScanResult:
    artifacts: List[ArtifactRecord] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    commits: Dict[str, str] = field(default_factory=dict)
    roots: Dict[str, str] = field(default_factory=dict)
    patterns: Dict[str, str] = field(default_factory=dict)

    @property
    def recognized(self) -> List[ArtifactRecord]:
        return [a for a in self.artifacts if a.status == "ok" and a.n_matched > 0]

    def repo_subset(self, repo: str) -> List[ArtifactRecord]:
        return [a for a in self.artifacts if a.repo == repo]

    def observations_for(self, label: str, repo: str) -> List[Observation]:
        return [o for o in self.observations if o.repo == repo and label in o.labels]

    def stats(self) -> Dict[str, Any]:
        per_repo: Dict[str, Dict[str, int]] = {}
        for a in self.artifacts:
            s = per_repo.setdefault(
                a.repo, {"files": 0, "ok": 0, "invalid_json": 0, "read_error": 0,
                         "too_large": 0, "recognized": 0}
            )
            s["files"] += 1
            s[a.status] = s.get(a.status, 0) + 1
            if a.status == "ok" and a.n_matched > 0:
                s["recognized"] += 1
        return {
            "repos": len({a.repo for a in self.artifacts}),
            "files": len(self.artifacts),
            "recognized_files": len(self.recognized),
            "observations": len(self.observations),
            "per_repo": per_repo,
            "commits": dict(self.commits),
            "roots": dict(self.roots),
        }


def compile_patterns(config: EvidenceChainConfig) -> Dict[str, re.Pattern]:
    """Build ``label -> compiled regex`` for every capability/group pair."""
    pats: Dict[str, re.Pattern] = {}
    for cap in config.capabilities:
        for gkey, g in cap.groups.items():
            label = f"{cap.id}:{gkey}"
            pats[label] = re.compile(g.metric)
    return pats


def repo_commit(path: Path) -> str:
    """Best-effort git commit of a repository root (provenance only)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _excluded(rel: str, patterns: Sequence[str]) -> bool:
    """Path-component aware exclusion (``**/.git/**`` -> skip any ``.git`` part)."""
    if not patterns:
        return False
    parts = rel.split("/")
    for pat in patterns:
        core = pat.strip("*/")
        if core and "/" not in core and core in parts:
            return True
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def _segments(pattern: str) -> Tuple[str, ...]:
    return tuple(seg for seg in pattern.split("/") if seg)


@lru_cache(maxsize=4096)
def _match_segments(parts: Tuple[str, ...], pat: Tuple[str, ...]) -> bool:
    """Glob match with true ``**`` semantics (zero or more path segments)."""
    if not pat:
        return not parts
    head, rest = pat[0], pat[1:]
    if head == "**":
        if _match_segments(parts, rest):
            return True
        return bool(parts) and _match_segments(parts[1:], pat)
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match_segments(parts[1:], rest)


def _included(rel: str, patterns: Sequence[str]) -> bool:
    parts = _segments(rel)
    return any(_match_segments(parts, _segments(pat)) for pat in patterns)


def iter_json_files(root: Path, repo: RepoSpec, limit: int) -> Iterator[Tuple[str, Path]]:
    """Yield ``(repo_relative_path, absolute_path)`` in deterministic order.

    A single ``os.walk`` per repository (sorted) feeds a segment-wise glob
    matcher that honours ``**``; this avoids the ``Path.glob`` cost of walking
    the tree once per include pattern, which dominated the scan on the live
    ecosystem checkout.
    """
    if not root.exists():
        return
    found: Dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if repo.exclude and _excluded(
            (Path(dirpath).relative_to(root).as_posix() or "."), repo.exclude
        ):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".json"):
                continue
            abspath = Path(dirpath) / fn
            rel = abspath.relative_to(root).as_posix()
            if repo.exclude and _excluded(rel, repo.exclude):
                continue
            if not _included(rel, repo.include):
                continue
            found[rel] = abspath
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    for rel in sorted(found)[:limit]:
        yield rel, found[rel]


def walk_numeric_leaves(
    obj: Any,
    key_path: Tuple[str, ...] = (),
    key: str = "",
    depth: int = 0,
) -> Iterator[Tuple[str, str, float]]:
    """Yield ``(key_path, leaf_key, value)`` for every finite numeric leaf.

    ``leaf_key`` is the nearest non-index key (so ``rows[3].relative_error``
    reports the key ``relative_error``), the natural identifier for a metric.
    """
    if depth > MAX_DEPTH:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_numeric_leaves(v, key_path + (str(k),), str(k), depth + 1)
    elif isinstance(obj, (list, tuple)):
        if len(obj) > MAX_LIST_ITEMS:
            obj = obj[:MAX_LIST_ITEMS]
        for i, v in enumerate(obj):
            yield from walk_numeric_leaves(v, key_path + (f"[{i}]",), key, depth + 1)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        val = float(obj)
        if val == val and val not in (float("inf"), float("-inf")):
            yield "/".join(key_path), key, val


def scan(config: EvidenceChainConfig) -> ScanResult:
    """Scan every configured repository and collect labelled observations."""
    patterns = compile_patterns(config)
    result = ScanResult(patterns={k: v.pattern for k, v in patterns.items()})
    for repo in config.repos:
        root = (config.root / repo.path).resolve()
        result.roots[repo.name] = str(root)
        result.commits[repo.name] = repo_commit(root) if root.exists() else "missing"
        for rel, abspath in iter_json_files(root, repo, repo.max_files):
            rec = ArtifactRecord(repo=repo.name, path=rel, status="ok")
            try:
                data = abspath.read_bytes()
            except OSError as exc:
                rec.status = "read_error"
                rec.error = str(exc)
                result.artifacts.append(rec)
                continue
            rec.bytes = len(data)
            rec.sha256 = hashlib.sha256(data).hexdigest()
            if len(data) > MAX_BYTES:
                rec.status = "too_large"
                result.artifacts.append(rec)
                continue
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                rec.status = "invalid_json"
                rec.error = str(exc)[:200]
                result.artifacts.append(rec)
                continue
            for key_path, leaf_key, value in walk_numeric_leaves(payload):
                rec.n_numeric += 1
                labels = tuple(
                    label for label, pat in patterns.items() if pat.search(leaf_key)
                )
                if not labels:
                    continue
                rec.n_matched += 1
                result.observations.append(
                    Observation(
                        repo=repo.name,
                        path=rel,
                        key_path=key_path,
                        key=leaf_key,
                        value=value,
                        labels=labels,
                    )
                )
            result.artifacts.append(rec)
    return result