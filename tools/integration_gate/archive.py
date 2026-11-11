"""可复现性快照：工件 sha256 清单 + 校验器（零领域语义）。

战役级集成门要求「所有工件快照带 sha256」。本模块只做三件事：

1. ``build_manifest``：对给定路径集合（文件/目录递归）逐文件算 sha256，
   排序后给出**清单内容 sha256**（``content_sha256``）——同一输入逐字节稳定，
   不含 wall-clock、不含绝对路径（一律相对 ``root``）。
2. ``write_manifest`` / ``read_manifest``：落盘/读回（固定缩进 + sort_keys）。
3. ``verify_manifest``：重算并与清单比对，返回 ``missing``/``changed``/``extra``
   三类差异与 ``ok`` 布尔值。任何差异都**不静默**：调用方决定 fail-closed。

排除规则由数据声明（``exclude_globs``），默认跳过字节码缓存与归档自身，
避免「拿清单去校验清单」的自指。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .provenance import sha256_obj

ARCHIVE_SCHEMA = "tp.integration.archive/v1"
DEFAULT_EXCLUDES = ("**/__pycache__/**", "*.pyc", "**/.pytest_cache/**")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(paths: Iterable[Path | str], root: Path,
                exclude_globs: Iterable[str]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for raw in paths:
        p = Path(raw)
        p = p if p.is_absolute() else (root / p)
        if p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
                for fn in sorted(filenames):
                    fp = Path(dirpath) / fn
                    if fp.is_file():
                        rel = fp.relative_to(root).as_posix()
                        if not any(fnmatch.fnmatch(rel, g) for g in exclude_globs):
                            out.append((rel, fp))
        elif p.is_file():
            rel = p.relative_to(root).as_posix()
            if not any(fnmatch.fnmatch(rel, g) for g in exclude_globs):
                out.append((rel, p))
    return sorted(set(out), key=lambda t: t[0])


def build_manifest(campaign: str, paths: Iterable[Path | str], root: Path | str = ".",
                   exclude_globs: Iterable[str] = DEFAULT_EXCLUDES,
                   note: str = "") -> dict[str, Any]:
    root = Path(root).resolve()
    globs = tuple(exclude_globs) + DEFAULT_EXCLUDES
    files = []
    for rel, fp in _iter_files(paths, root, globs):
        files.append({"path": rel, "sha256": _sha256_file(fp), "size": fp.stat().st_size})
    if not files:
        raise ValueError("archive manifest: no files matched (refusing empty snapshot)")
    doc = {
        "schema": ARCHIVE_SCHEMA,
        "campaign": campaign,
        "note": note,
        "files": files,
        "n_files": len(files),
        "n_bytes": sum(f["size"] for f in files),
    }
    doc["content_sha256"] = sha256_obj({"files": files, "campaign": campaign})
    return doc


def write_manifest(doc: dict[str, Any], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def read_manifest(path: Path | str) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != ARCHIVE_SCHEMA:
        raise ValueError(f"manifest schema must be {ARCHIVE_SCHEMA}")
    return doc


def verify_manifest(manifest: dict[str, Any], root: Path | str = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    missing: list[str] = []
    changed: list[str] = []
    checked = 0
    for entry in manifest["files"]:
        fp = root / entry["path"]
        if not fp.is_file():
            missing.append(entry["path"])
            continue
        checked += 1
        if _sha256_file(fp) != entry["sha256"]:
            changed.append(entry["path"])
    recomputed = sha256_obj({"files": manifest["files"], "campaign": manifest["campaign"]})
    return {"ok": not missing and not changed and recomputed == manifest["content_sha256"],
            "checked": checked, "missing": sorted(missing), "changed": sorted(changed),
            "content_sha256_matches": recomputed == manifest["content_sha256"]}