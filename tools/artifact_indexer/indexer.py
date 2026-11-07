"""Artifact sha256 indexer — cross-repo artifact directory integrity index.

Maps every regular file under one or more artifact roots to
``path -> sha256 -> size -> mtime`` and persists the table as a versioned
JSON document (the "index table").  Supports:

* full build (hash everything),
* incremental update (an entry is reused iff size AND mtime-ns are
  unchanged, i.e. ``mtime 未变跳过``; deleted files are pruned),
* idempotent re-runs (a no-op update rewrites nothing),
* spot check (random sample of N entries, recompute sha256, compare),
* full verify (recompute every entry),
* dry-run mode (report what would happen, write nothing).

Design constraints (IN15-O5 discipline): additive-only.  This module
imports only the standard library, touches no existing API surface,
schema or database table, and never mutates the indexed files.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

INDEX_VERSION = 1
DEFAULT_INDEX_NAME = ".artifact_index.json"
_CHUNK = 1 << 20  # 1 MiB read chunks

# ---------------------------------------------------------------------------
# hashing / scanning
# ---------------------------------------------------------------------------


def sha256_file(path: Path, chunk_size: int = _CHUNK) -> str:
    """Stream sha256 of a file (constant memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def iter_files(root: Path, exclude_names: Iterable[str] = ()) -> List[Path]:
    """All regular files under *root*, sorted, symlinks skipped."""
    exclude = set(exclude_names)
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name in exclude:
                continue
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# index document
# ---------------------------------------------------------------------------


@dataclass
class IndexDoc:
    version: int = INDEX_VERSION
    updated_at: str = ""
    roots: List[str] = field(default_factory=list)
    entries: Dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "updated_at": self.updated_at,
                "roots": self.roots,
                "entries": self.entries,
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )


def load_index(path: Path) -> Optional[IndexDoc]:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return IndexDoc(
        version=int(raw.get("version", INDEX_VERSION)),
        updated_at=str(raw.get("updated_at", "")),
        roots=list(raw.get("roots", [])),
        entries=dict(raw.get("entries", {})),
    )


def save_index(path: Path, doc: IndexDoc) -> None:
    """Atomic write (tmp file + os.replace) so a crash never corrupts the table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".idx_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(doc.to_json())
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# build / incremental update
# ---------------------------------------------------------------------------


def build_index(
    roots: Sequence[Path],
    index_path: Path,
    dry_run: bool = False,
) -> dict:
    """Build or incrementally update the index table.

    Incremental rule: a previously indexed entry is *skipped* (its cached
    sha256 is trusted) iff the file still exists and both its size and
    mtime (nanosecond precision) match the cached record.  Anything else
    is re-hashed.  Entries whose file vanished are pruned.

    Returns a stats dict:
        {scanned, hashed, skipped, added, pruned, errors, changed, dry_run}
    """
    roots = [Path(r) for r in roots]
    for r in roots:
        if not r.is_dir():
            raise FileNotFoundError(f"root not a directory: {r}")
    index_path = Path(index_path)

    old = load_index(index_path)
    old_entries = old.entries if old is not None else {}

    entries: Dict[str, dict] = {}
    stats = {
        "scanned": 0,
        "hashed": 0,
        "skipped": 0,
        "added": 0,
        "pruned": 0,
        "errors": [],
        "changed": False,
        "dry_run": dry_run,
    }

    for root in roots:
        root_key = str(root)
        for p in iter_files(root, exclude_names={index_path.name}):
            rel = p.relative_to(root).as_posix()
            key = f"{root_key}/{rel}"
            stats["scanned"] += 1
            try:
                st = p.stat()
            except OSError as exc:  # vanished / permission
                stats["errors"].append(f"stat {key}: {exc}")
                continue
            rec = {
                "root": root_key,
                "rel": rel,
                "sha256": None,
                "size": st.st_size,
                "mtime": st.st_mtime_ns,
            }
            prev = old_entries.get(key)
            if (
                prev is not None
                and prev.get("size") == rec["size"]
                and prev.get("mtime") == rec["mtime"]
            ):
                rec["sha256"] = prev["sha256"]
                stats["skipped"] += 1
            else:
                try:
                    rec["sha256"] = sha256_file(p)
                except OSError as exc:
                    stats["errors"].append(f"hash {key}: {exc}")
                    continue
                stats["hashed"] += 1
                if prev is None:
                    stats["added"] += 1
            entries[key] = rec

    for key in old_entries:
        if key not in entries:
            stats["pruned"] += 1

    stats["changed"] = (
        stats["hashed"] > 0 or stats["added"] > 0 or stats["pruned"] > 0
    )

    if not dry_run and stats["changed"]:
        doc = IndexDoc(
            version=INDEX_VERSION,
            updated_at=_utcnow(),
            roots=[str(r) for r in roots],
            entries=entries,
        )
        save_index(index_path, doc)
    # Idempotency: when nothing changed we deliberately do NOT rewrite the
    # file, so a no-op update leaves the index byte-identical.

    return stats


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def _check_entry(index_path: Path, key: str) -> dict:
    doc = load_index(index_path)
    assert doc is not None
    rec = doc.entries[key]
    p = Path(rec["root"]) / rec["rel"]
    if not p.is_file():
        return {"key": key, "ok": False, "reason": "missing_on_disk"}
    actual = sha256_file(p)
    ok = actual == rec["sha256"]
    return {
        "key": key,
        "ok": ok,
        "expected": rec["sha256"],
        "actual": actual,
        "reason": None if ok else "hash_mismatch",
    }


def spot_check(
    index_path: Path,
    n: int = 20,
    seed: Optional[int] = None,
) -> dict:
    """Randomly sample up to *n* entries, recompute sha256, compare.

    Deterministic for a given *seed* (reproducible audit).
    """
    doc = load_index(index_path)
    if doc is None:
        raise FileNotFoundError(f"no index at {index_path}")
    keys = sorted(doc.entries)
    k = max(0, min(int(n), len(keys)))
    rng = random.Random(seed)
    sample = rng.sample(keys, k) if k else []
    results = [_check_entry(index_path, key) for key in sample]
    return {
        "index": str(index_path),
        "total": len(keys),
        "sampled": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": [r for r in results if not r["ok"]],
        "results": results,
    }


def verify_all(index_path: Path) -> dict:
    """Recompute sha256 for every entry (full re-derivation)."""
    doc = load_index(index_path)
    if doc is None:
        raise FileNotFoundError(f"no index at {index_path}")
    results = [_check_entry(index_path, key) for key in sorted(doc.entries)]
    return {
        "index": str(index_path),
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": [r for r in results if not r["ok"]],
        "results": results,
    }


def index_summary(index_path: Path) -> dict:
    """Aggregate stats for reporting (per-root counts, size distribution)."""
    doc = load_index(index_path)
    if doc is None:
        raise FileNotFoundError(f"no index at {index_path}")
    per_root: Dict[str, dict] = {}
    sizes: List[int] = []
    for rec in doc.entries.values():
        agg = per_root.setdefault(rec["root"], {"files": 0, "bytes": 0})
        agg["files"] += 1
        agg["bytes"] += int(rec["size"])
        sizes.append(int(rec["size"]))
    return {
        "index": str(index_path),
        "updated_at": doc.updated_at,
        "roots": doc.roots,
        "files": len(doc.entries),
        "bytes": sum(sizes),
        "per_root": per_root,
        "sizes": sizes,
    }
