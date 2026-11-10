"""Static repository scanner: Python import graph + path matching.

Why static and not dynamic: the guard must decide *before* running anything
which tests could possibly observe a change.  Import graph from ``ast`` is
deterministic, side-effect free, and works on a dirty worktree.

Two facts are produced per ``.py`` file:

``imports``   -- resolved intra-repo module keys this file imports, with the
                 imported attribute names so symbol-level changes can narrow
                 the blast radius.  ``import pkg.mod`` records ``("*",)``
                 (whole module, unfilterable).
``dotted``    -- dotted module key(s) the file can be addressed by.

Resolution is suffix based: for a discovered file ``backend/api/foo.py`` the
candidate dotted names are ``backend.api.foo`` and the longest-match prefixes
used in imports (``api.foo``, ``foo``).  Because ``backend`` has no
``__init__.py`` in this tree, pure package-semantics resolution would silently
miss every backend import; suffix matching is the honest alternative and every
edge it creates is recorded with the matched prefix so it can be audited.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", "build", "dist", ".eggs", "frontend", "frontend-vue",
    "frontend-console", "frontend-factory", ".idea", ".tox",
}


def iter_py_files(root: Path, subdirs: Sequence[str] = ()) -> List[str]:
    """Repo-relative POSIX paths of every ``.py`` file under ``subdirs``."""
    bases = [root / s for s in subdirs] if subdirs else [root]
    out: List[str] = []
    for base in bases:
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                out.append(Path(dirpath, name).relative_to(root).as_posix())
    return sorted(set(out))


def module_key_from_path(rel_path: str) -> str:
    """``backend/api/foo.py`` -> ``backend.api.foo`` (``__init__`` collapses)."""
    parts = rel_path[:-3].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class FileFacts:
    path: str
    module: str
    imports: Dict[str, Tuple[str, ...]] = field(default_factory=dict)  # module -> names
    defs: Tuple[str, ...] = ()          # top-level defs/classes/assignments
    sha256: str = ""
    parse_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "module": self.module,
            "imports": {k: list(v) for k, v in sorted(self.imports.items())},
            "defs": list(self.defs),
            "sha256": self.sha256,
            "parse_error": self.parse_error,
        }


def _resolve_import(modname: str, module_map: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """Longest known dotted prefix of ``modname`` -> (file path, matched key)."""
    parts = modname.split(".")
    for cut in range(len(parts), 0, -1):
        key = ".".join(parts[:cut])
        if key in module_map:
            return module_map[key], key
    return None


def scan_file(root: Path, rel_path: str, module_map: Dict[str, str]) -> FileFacts:
    abs_path = root / rel_path
    facts = FileFacts(path=rel_path, module=module_key_from_path(rel_path))
    facts.sha256 = hashlib.sha256(abs_path.read_bytes()).hexdigest()
    try:
        tree = ast.parse(abs_path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:  # pragma: no cover - defensive
        facts.parse_error = f"{exc.__class__.__name__}: {exc}"
        return facts

    defs: List[str] = []
    imports: Dict[str, Tuple[str, ...]] = {}

    def add(module: str, names: Iterable[str]) -> None:
        prev = set(imports.get(module, ()))
        new = prev | {"*"} if "*" in names or "*" in prev else prev | set(names)
        imports[module] = tuple(sorted(new))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    defs.append(t.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                hit = _resolve_import(alias.name, module_map)
                if hit:
                    add(hit[0], ("*",))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import -> resolve against own package
                base = facts.module.split(".")
                if node.level > 1:
                    base = base[: max(1, len(base) - (node.level - 1))]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            hit = _resolve_import(target, module_map)
            if hit:
                names = tuple(a.name for a in node.names) or ("*",)
                add(hit[0], () if not names else names)

    facts.imports = dict(sorted(imports.items()))
    facts.defs = tuple(sorted(set(defs)))
    return facts


@dataclass
class RepoGraph:
    """Import graph over a discovered file set."""

    root: str
    files: Dict[str, FileFacts] = field(default_factory=dict)
    imported_by: Dict[str, Set[str]] = field(default_factory=dict)  # file -> importers

    # -- construction ------------------------------------------------------
    @classmethod
    def build(cls, root: Path | str, subdirs: Sequence[str] = ()) -> "RepoGraph":
        root = Path(root)
        paths = iter_py_files(root, subdirs)
        module_map: Dict[str, str] = {}
        for rel in paths:
            key = module_key_from_path(rel)
            module_map[key] = rel
            # suffix aliases so `from api.foo import x` resolves inside backend/
            parts = key.split(".")
            for i in range(1, len(parts)):
                module_map.setdefault(".".join(parts[i:]), rel)
        graph = cls(root=str(root))
        for rel in paths:
            facts = scan_file(root, rel, module_map)
            graph.files[rel] = facts
            graph.imported_by.setdefault(rel, set())
        for rel, facts in graph.files.items():
            for dep in facts.imports:
                graph.imported_by.setdefault(dep, set()).add(rel)
        return graph

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "files": {p: f.to_dict() for p, f in sorted(self.files.items())},
            "imported_by": {k: sorted(v) for k, v in sorted(self.imported_by.items())},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RepoGraph":
        g = cls(root=d.get("root", ""))
        for path, fd in d.get("files", {}).items():
            g.files[path] = FileFacts(
                path=fd["path"], module=fd["module"],
                imports={k: tuple(v) for k, v in fd.get("imports", {}).items()},
                defs=tuple(fd.get("defs", [])),
                sha256=fd.get("sha256", ""),
                parse_error=fd.get("parse_error", ""),
            )
        g.imported_by = {k: set(v) for k, v in d.get("imported_by", {}).items()}
        return g

    @property
    def digest(self) -> str:
        payload = {"root": self.root, "files": self.to_dict()["files"]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    # -- queries -----------------------------------------------------------
    def importers_of(self, path: str, transitive: bool = True, max_depth: int = 64) -> List[str]:
        """Files that (transitively) import ``path``, excluding ``path`` itself."""
        seen: Set[str] = set()
        order: List[str] = []
        frontier: List[Tuple[str, int]] = [(path, 0)]
        while frontier:
            cur, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for imp in sorted(self.imported_by.get(cur, ())):
                if imp in seen or imp == path:
                    continue
                seen.add(imp)
                order.append(imp)
                frontier.append((imp, depth + 1))
        return order

    def import_path(self, src: str, dst: str, max_depth: int = 64) -> Optional[List[str]]:
        """Shortest import chain ``src -> ... -> dst`` (for audit trails)."""
        if src == dst:
            return [src]
        prev: Dict[str, str] = {}
        frontier = [src]
        seen = {src}
        for _ in range(max_depth):
            nxt: List[str] = []
            for cur in frontier:
                for dep in sorted(self.files[cur].imports) if cur in self.files else []:
                    if dep in seen:
                        continue
                    seen.add(dep)
                    prev[dep] = cur
                    if dep == dst:
                        chain = [dst]
                        while chain[-1] != src:
                            chain.append(prev[chain[-1]])
                        return list(reversed(chain))
                    nxt.append(dep)
            frontier = nxt
            if not frontier:
                break
        return None


# ---------------------------------------------------------------------------
# path matching
# ---------------------------------------------------------------------------
_GLOB_CHARS = set("*?[")


def path_matches(pattern: str, path: str) -> bool:
    """Match a ledger path pattern against a repo-relative path.

    ``*`` does **not** cross ``/`` (fnmatch semantics per segment); a trailing
    ``/**`` works as a recursive directory prefix.  Prefix directory patterns
    (``pkg/sub``) match everything beneath them.
    """
    pattern = pattern.replace("\\", "/").strip()
    path = path.replace("\\", "/")
    if pattern in ("", "."):
        return False
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3].rstrip("/") + "/") or path == pattern[:-3].rstrip("/")
    if any(ch in pattern for ch in _GLOB_CHARS):
        p_parts, s_parts = pattern.split("/"), path.split("/")
        if "**" in p_parts:
            head = p_parts[: p_parts.index("**")]
            tail = p_parts[p_parts.index("**") + 1:]
            return (
                len(s_parts) >= len(head) + len(tail)
                and all(fnmatch.fnmatchcase(b, a) for a, b in zip(head, s_parts))
                and all(fnmatch.fnmatchcase(b, a) for a, b in zip(reversed(tail), reversed(s_parts)))
            )
        if len(p_parts) != len(s_parts):
            return False
        return all(fnmatch.fnmatchcase(b, a) for a, b in zip(p_parts, s_parts))
    if path == pattern:
        return True
    # directory prefix (ledger authors write "backend/api" for a package)
    return path.startswith(pattern.rstrip("/") + "/")


def match_any(patterns: Sequence[str], path: str) -> Optional[str]:
    for p in patterns:
        if path_matches(p, path):
            return p
    return None