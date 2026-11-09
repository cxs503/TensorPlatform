"""Pool builders: code-module inventory and campaign export snapshots.

The radar has to answer "has any repo already built this?" — but the deployment
runs inside one worktree.  Instead of cloning sibling repositories at query
time, the radar consumes a *snapshot* of their module inventories, produced
here and committed with a content digest.  Two consequences we care about:

* the query is fast and offline (no git access from the adjudication path);
* the snapshot is auditable: any module added later shows up as a manifest diff,
  which is itself the "index maintenance" signal.

Snapshots are deterministic (sorted module ids, sorted keys, no timestamps), so
regenerating them without repo changes produces identical bytes.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

MANIFEST_SCHEMA = "radar-modules/v1"
CAMPAIGN_SCHEMA = "radar-campaign/v1"

_SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "node_modules", "build", "dist", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", "migrations", "static",
}


def _first_line(doc: Optional[str]) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _symbols(tree: ast.AST, limit: int = 12) -> List[str]:
    out: List[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    out.append(tgt.id)
    return sorted(out)[:limit]


def inventory_repo(
    repo_name: str,
    root: str,
    *,
    max_files: int = 5000,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> Dict[str, Any]:
    """Scan python modules of one repo into a manifest fragment.

    ``include``/``exclude`` are path prefixes (repo-relative).  Deployments use
    them to keep one-off scripts and generated artifacts out of the capability
    inventory: a ``m1234/run_once.py`` is not evidence that a capability exists.
    """
    modules: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith("tests/") or "/tests/" in rel:
                continue
            if include and not any(rel.startswith(pfx) for pfx in include):
                continue
            if exclude and any(rel.startswith(pfx) for pfx in exclude):
                continue
            try:
                src = open(path, "r", encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
                doc = ast.get_docstring(tree) or ""
                syms = _symbols(tree)
            except (SyntaxError, ValueError, OSError):
                src, doc, syms = "", "", []
            modules.append(
                {
                    "id": rel,
                    "title": rel.rsplit("/", 1)[-1],
                    "text": " ".join([_first_line(doc)] + syms),
                    "symbols": syms,
                    "ref": f"{repo_name}:{rel}",
                }
            )
            if len(modules) >= max_files:
                break
        if len(modules) >= max_files:
            break
    modules.sort(key=lambda m: m["id"])
    return {"repo": repo_name, "modules": modules}


def build_manifest(
    repos: Sequence[tuple[str, str]],
    *,
    max_files: int = 5000,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> Dict[str, Any]:
    """``repos`` = [(repo_name, path)]; returns a deterministic manifest."""
    frags = []
    for name, path in repos:
        if not path or not os.path.isdir(path):
            continue
        frags.append(
            inventory_repo(
                name, path, max_files=max_files, include=include, exclude=exclude
            )
        )
    frags.sort(key=lambda f: f["repo"])
    return {
        "schema": MANIFEST_SCHEMA,
        "include": list(include),
        "exclude": list(exclude),
        "repos": frags,
    }


def manifest_digest(manifest: Dict[str, Any]) -> str:
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def empty_campaign_export(note: str) -> Dict[str, Any]:
    """Honest, schema-valid campaign export for deployments with no live DB.

    The adapter is exercised by tests against a synthetic export; shipping an
    empty (but valid) snapshot keeps the pool manifest homogeneous and records
    *why* it is empty instead of silently omitting the source.
    """
    return {"schema": CAMPAIGN_SCHEMA, "plans": [], "nodes": [], "note": note}


def dump(obj: Any, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")