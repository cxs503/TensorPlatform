"""Static import graph of the working tree (AST based, plus history edges).

Design notes
------------
* Nodes are *files* (repo relative posix paths), not module names: a file may
  legitimately be importable under several dotted names (``backend.models`` and
  ``models`` from the ``backend`` source root), and impact analysis is a file
  question ("which tests exercise this file").
* Resolution covers the four import shapes that break naive import scanners:

  1. relative imports (``from . import x``, ``from ..pkg import y``),
  2. ``__init__`` re-exports (``from pkg import name`` where ``pkg/__init__.py``
     does ``from .sub import name``) -- handled by adding edges to every
     ancestor package ``__init__.py``,
  3. ``try/except ImportError`` conditional imports -- kept as edges and marked
     ``conditional`` (conservative: over-approximates impact),
  4. submodule imports through ``from pkg import sub`` (edge to
     ``pkg/sub.py`` when it exists).

* Anything that cannot be resolved statically is *recorded*, not silently
  dropped: unresolved absolute imports are classified ``external`` (third party,
  harmless) or ``cross_repo`` (another repository of the fleet, **not** harmless
  -> fail closed), and modules using dynamic import machinery are flagged
  ``dynamic``.

Performance
-----------
Scanning is split in two phases: a pure per-file AST scan (cacheable and
parallelisable, no cross-file knowledge) and a cheap resolution phase in the
parent process.  The per-file scan cache is keyed by (size, mtime_ns) so an
incremental rebuild only re-parses what changed; a cold build over ~3.7k files
runs in a few seconds with ``jobs>1``.

Determinism: every collection leaving the builder is sorted, and the fingerprint
is a sha256 over the canonical (node, edge, cocchange) triple.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pickle
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import (
    COCCHANGE_JACCARD,
    COCCHANGE_MIN_SUPPORT,
    CODE_EXTENSIONS,
    CROSS_REPO_PREFIXES,
    GENERATED_HEADER_MARKERS,
    GENERATED_NAME_SUFFIXES,
    GENERATED_PATH_MARKERS,
    SKIP_DIR_NAMES,
    SOURCE_ROOT_ALIASES,
    is_test_path,
)

DYNAMIC_IMPORT_NAMES = frozenset(
    {"import_module", "__import__", "spec_from_file_location", "module_from_spec", "find_spec"}
)


@dataclass
class FileNode:
    path: str  # repo relative posix path
    module: str  # primary dotted name (from repo root)
    is_test: bool
    parse_error: str = ""
    generated: bool = False
    dynamic: bool = False
    imports: List[str] = field(default_factory=list)  # resolved in-repo deps
    external: List[str] = field(default_factory=list)  # unresolved absolute imports
    cross_repo: List[str] = field(default_factory=list)  # unresolved = fleet repos
    conditional: List[str] = field(default_factory=list)  # conditional subset of imports
    relative_broken: List[str] = field(default_factory=list)
    dyn_literals: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "module": self.module,
            "is_test": self.is_test,
            "parse_error": self.parse_error,
            "generated": self.generated,
            "dynamic": self.dynamic,
            "imports": sorted(set(self.imports)),
            "external": sorted(set(self.external)),
            "cross_repo": sorted(set(self.cross_repo)),
            "conditional": sorted(set(self.conditional)),
            "relative_broken": sorted(set(self.relative_broken)),
            "dyn_literals": sorted(set(self.dyn_literals)),
        }


def module_name(rel_path: str) -> str:
    p = rel_path[: -len(".py")] if rel_path.endswith(".py") else rel_path
    parts = [x for x in p.split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


_module_name = module_name  # backwards compatible alias


def walk_code_files(root: Path | str) -> List[str]:
    root = Path(root)
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".git"))
        for name in sorted(filenames):
            if not name.endswith(CODE_EXTENSIONS):
                continue
            try:
                rel = (Path(dirpath) / name).relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - defensive
                continue
            if rel.startswith("."):
                continue
            out.append(rel)
    return sorted(out)


_walk_code_files = walk_code_files  # backwards compatible alias


def _first_lines(path: Path, n: int = 6) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readline() for _ in range(n)).lower()
    except OSError:  # pragma: no cover - defensive
        return ""


def is_generated(rel: str, root: Path | str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if any(name.endswith(suf) for suf in GENERATED_NAME_SUFFIXES):
        return True
    if any(marker in rel for marker in GENERATED_PATH_MARKERS):
        return True
    head = _first_lines(Path(root) / rel)
    return any(marker in head for marker in GENERATED_HEADER_MARKERS)


_is_generated = is_generated  # backwards compatible alias


def scan_dynamic_imports(tree: ast.AST) -> Tuple[bool, List[str]]:
    """Classify import machinery usage.

    Returns ``(unresolved, literals)``:

    * ``literals`` -- targets of import machinery calls whose first argument is a
      string constant.  These are *resolvable* and are added as ordinary edges
      (a plugin loaded by name is a real dependency, not a blind spot).
    * ``unresolved`` -- True iff some import machinery call (or a module level
      PEP 562 ``__getattr__``) can load a target we cannot name.  Only this case
      forces the fail-closed path.
    """
    literals: List[str] = []
    unresolved = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            is_dyn = (isinstance(fn, ast.Name) and fn.id in DYNAMIC_IMPORT_NAMES) or (
                isinstance(fn, ast.Attribute) and fn.attr in DYNAMIC_IMPORT_NAMES
            )
            if is_dyn:
                arg0 = node.args[0] if node.args else None
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    literals.append(arg0.value)
                else:
                    unresolved = True
    for stmt in tree.body:  # module level only (PEP 562)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__getattr__":
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if (isinstance(fn, ast.Name) and fn.id in DYNAMIC_IMPORT_NAMES) or (
                        isinstance(fn, ast.Attribute) and fn.attr in DYNAMIC_IMPORT_NAMES
                    ):
                        unresolved = True
            if any(isinstance(sub, (ast.Import, ast.ImportFrom)) for sub in ast.walk(stmt)):
                unresolved = True
    return unresolved, sorted(set(literals))


def dynamic_import_used(tree: ast.AST) -> bool:
    return scan_dynamic_imports(tree)[0]


_dynamic_import_used = dynamic_import_used


CACHE_VERSION = 4
GRAPH_CACHE_VERSION = 1
_DOTTED_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.py")
REF_TOKEN_MAX = 4000


def string_ref_tokens(tree: ast.AST) -> List[str]:
    """Dotted module names / repo relative paths appearing in string literals.

    Tests routinely exercise code through ``subprocess`` (``python -m pkg.mod``,
    ``python tools/pkg/mod.py``), which leaves *no* import edge.  Collecting the
    string literals gives us "literal reference edges": a file whose literals
    mention a module is (transitively) affected when that module changes.
    """
    toks: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if not (3 <= len(val) <= 400):
                continue
            for m in _DOTTED_RE.finditer(val):
                toks.add(m.group(0))
            for m in _PATH_RE.finditer(val):
                tok = m.group(0)
                toks.add(tok)
                if tok.endswith(".py"):
                    toks.add(tok[:-3])
            if len(toks) >= REF_TOKEN_MAX:
                break
    return sorted(toks)


def conditional_import_ids(tree: ast.AST) -> Set[int]:
    """ids of import statements that sit inside a ``try:`` guard body."""
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        ids.add(id(sub))
    return ids


_conditional_import_ids = conditional_import_ids


def iter_imports(tree: ast.AST, conditional_ids: Set[int]) -> Iterable[Tuple[str, int, Sequence[str], bool]]:
    """Yield (module, level, imported names, conditional)."""
    for node in ast.walk(tree):
        conditional = id(node) in conditional_ids
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0, (), conditional
        elif isinstance(node, ast.ImportFrom):
            names = tuple(a.name for a in node.names if a.name != "*")
            yield (node.module or ""), int(node.level or 0), names, conditional


_iter_imports = iter_imports


# ---------------------------------------------------------------------------
# phase 1: per file scan (cacheable, parallelisable)
# ---------------------------------------------------------------------------


def scan_file(args: Tuple[str, str]) -> Tuple[str, dict]:
    root_s, rel = args
    path = Path(root_s) / rel
    rec: dict = {"parse_error": "", "dynamic": False, "generated": False, "imports": [], "refs": [], "dyn_literals": []}
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError) as exc:
        rec["parse_error"] = f"{type(exc).__name__}: {exc}"
        return rel, rec
    except OSError as exc:  # pragma: no cover - defensive
        rec["parse_error"] = f"OSError: {exc}"
        return rel, rec
    rec["generated"] = is_generated(rel, root_s)
    dyn_unresolved, dyn_literals = scan_dynamic_imports(tree)
    rec["dynamic"] = dyn_unresolved
    rec["dyn_literals"] = dyn_literals
    rec["refs"] = string_ref_tokens(tree)
    cids = conditional_import_ids(tree)
    rec["imports"] = [[m, lvl, list(names), bool(cond)] for m, lvl, names, cond in iter_imports(tree, cids)]
    return rel, rec


def _stat_key(root: Path, rel: str) -> Tuple[int, int]:
    st = (root / rel).stat()
    return int(st.st_size), int(st.st_mtime_ns)


class ScanCache:
    """Persistent per-file scan cache keyed by (size, mtime_ns)."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self.entries: Dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        if self.path and self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if (
                    payload.get("schema") == "tp.test_impact_selector.scan_cache/v1"
                    and payload.get("version") == CACHE_VERSION
                ):
                    self.entries = payload.get("entries", {})
            except (OSError, ValueError):  # pragma: no cover
                self.entries = {}

    def get(self, rel: str, key: Tuple[int, int]) -> Optional[dict]:
        ent = self.entries.get(rel)
        if ent and tuple(ent.get("key", ())) == key:
            self.hits += 1
            return ent.get("rec")
        return None

    def put(self, rel: str, key: Tuple[int, int], rec: dict) -> None:
        self.entries[rel] = {"key": list(key), "rec": rec}

    def prune(self, alive: Iterable[str]) -> None:
        alive = set(alive)
        self.entries = {k: v for k, v in self.entries.items() if k in alive}

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "tp.test_impact_selector.scan_cache/v1",
            "version": CACHE_VERSION,
            "entries": self.entries,
        }
        self.path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def snapshot(self) -> Dict[str, dict]:
        return {k: dict(v.get("rec", {})) for k, v in self.entries.items()}


@dataclass
class GraphBuildReport:
    files: int = 0
    scanned: int = 0
    cache_hits: int = 0
    parse_errors: List[str] = field(default_factory=list)
    jobs: int = 1
    reference_edges: int = 0
    graph_cache_hit: bool = False
    phase_scan_s: float = 0.0
    phase_resolve_s: float = 0.0
    phase_history_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "files": self.files,
            "scanned": self.scanned,
            "cache_hits": self.cache_hits,
            "parse_errors": list(self.parse_errors),
            "jobs": self.jobs,
            "reference_edges": self.reference_edges,
            "graph_cache_hit": self.graph_cache_hit,
            "phase_scan_s": round(self.phase_scan_s, 3),
            "phase_resolve_s": round(self.phase_resolve_s, 3),
            "phase_history_s": round(self.phase_history_s, 3),
        }


class ImportGraph:
    """Deterministic file level import graph + reverse index."""

    def __init__(
        self,
        nodes: Dict[str, FileNode],
        edges: Dict[str, Set[str]],
        cocchange: Dict[str, Set[str]],
        build_report: Optional[GraphBuildReport] = None,
        reference_edges: Optional[Dict[str, Set[str]]] = None,
    ):
        self.nodes = nodes
        self._edges = {k: set(v) for k, v in edges.items()}
        self._cocchange = {k: set(v) for k, v in cocchange.items()}
        self._rdeps: Dict[str, Set[str]] = {p: set() for p in nodes}
        for src, dsts in self._edges.items():
            for dst in dsts:
                self._rdeps.setdefault(dst, set()).add(src)
        # static import only reverse index (reference/co-change edges excluded):
        # "does anything *import* this file" is a much stronger statement than
        # "does anything mention this path in a string".
        self._static_rdeps: Dict[str, Set[str]] = {p: set() for p in nodes}
        for rel, node in nodes.items():
            for dep in node.imports:
                self._static_rdeps.setdefault(dep, set()).add(rel)
        self._references = {k: set(v) for k, v in (reference_edges or {}).items()}
        self.build_report = build_report or GraphBuildReport(files=len(nodes))
        self.fingerprint = self._fingerprint()

    # -- construction -------------------------------------------------------
    @classmethod
    def build(
        cls,
        root: Path | str,
        use_history: bool = True,
        history_commits: int = 400,
        aliases: Sequence[str] = SOURCE_ROOT_ALIASES,
        jobs: int = 1,
        cache_path: Path | str | None = None,
        scans: Optional[Dict[str, dict]] = None,
        use_references: bool = True,
    ) -> "ImportGraph":
        import time

        root = Path(root).resolve()
        report = GraphBuildReport(jobs=jobs)
        files = walk_code_files(root)
        graph_cache = Path(cache_path).with_name("graph.pkl") if cache_path else None
        stamp = _tree_stamp(root, files, use_history, history_commits) if graph_cache else None
        if graph_cache is not None and graph_cache.is_file():
            try:
                with graph_cache.open("rb") as fh:
                    payload = pickle.load(fh)
                if (
                    payload.get("version") == GRAPH_CACHE_VERSION
                    and payload.get("stamp") == stamp
                ):
                    g = cls.from_cache(payload)
                    g.build_report.graph_cache_hit = True
                    g.build_report.files = len(files)
                    g.build_report.cache_hits = len(files)
                    return g
            except (OSError, pickle.PickleError, ValueError, KeyError):  # pragma: no cover
                pass
        report.files = len(files)
        aliases = tuple(a for a in aliases if (root / a).is_dir())

        t0 = time.time()
        if scans is None:
            scans, report = _collect_scans(root, files, jobs, cache_path, report)
        else:
            report.scanned = len(scans)
        report.phase_scan_s = time.time() - t0

        t1 = time.time()
        index: Dict[str, List[str]] = {}
        nodes: Dict[str, FileNode] = {}
        for rel in files:
            mod = module_name(rel)
            nodes[rel] = FileNode(path=rel, module=mod, is_test=is_test_path(rel))
            names = {mod}
            top = rel.split("/", 1)[0]
            if "/" in rel:
                names.add(module_name(rel.split("/", 1)[1]))
            for alias in aliases:
                if rel.startswith(alias + "/"):
                    names.add(module_name(rel[len(alias) + 1 :]))
            if top:
                names.add(top)
            for nm in names:
                if nm:
                    index.setdefault(nm, []).append(rel)
        for nm in index:
            index[nm] = sorted(set(index[nm]))

        def resolve(module: str, importer: str) -> Optional[str]:
            if not module:
                return None
            cands = index.get(module)
            if not cands:
                return None
            if len(cands) == 1:
                return cands[0]
            iparts = importer.split("/")[:-1]

            def score(c: str) -> Tuple[int, str]:
                cparts = c.split("/")[:-1]
                common = 0
                for a, b in zip(iparts, cparts):
                    if a != b:
                        break
                    common += 1
                return (-common, c)

            return sorted(cands, key=score)[0]

        for rel in files:
            node = nodes[rel]
            rec = scans.get(rel)
            if rec is None:
                node.parse_error = "not scanned"
                continue
            node.parse_error = rec.get("parse_error", "")
            node.generated = bool(rec.get("generated"))
            node.dynamic = bool(rec.get("dynamic"))
            node.dyn_literals = list(rec.get("dyn_literals", []))
            if node.parse_error:
                continue
            pkg_parts = node.module.split(".") if node.module else []
            if not rel.endswith("__init__.py"):
                pkg_parts = pkg_parts[:-1]
            dyn_specs = [[lit, 0, [], False] for lit in rec.get("dyn_literals", [])]
            for module, level, names, conditional in [*rec.get("imports", []), *dyn_specs]:
                if level:
                    base = pkg_parts[: len(pkg_parts) - (level - 1)] if level - 1 <= len(pkg_parts) else []
                    mod_name = ".".join([*base, *([module] if module else [])]).strip(".")
                    targets: List[str] = []
                    if mod_name:
                        r = resolve(mod_name, rel)
                        if r:
                            targets.append(r)
                        else:
                            node.relative_broken.append(mod_name)
                    for nm in names:
                        r = resolve(f"{mod_name}.{nm}" if mod_name else nm, rel)
                        if r:
                            targets.append(r)
                    if not targets and mod_name:
                        node.relative_broken.append(mod_name)
                    _attach(node, targets, conditional)
                    continue

                top = module.split(".")[0] if module else ""
                targets = []
                r = resolve(module, rel) if module else None
                if r:
                    targets.append(r)
                for nm in names:
                    rr = resolve(f"{module}.{nm}" if module else nm, rel)
                    if rr:
                        targets.append(rr)
                if not targets and top:
                    low = top.lower()
                    if any(low == p or low.startswith(p + ".") for p in CROSS_REPO_PREFIXES):
                        node.cross_repo.append(module)
                        continue
                    node.external.append(top)
                    continue
                _attach(node, targets, conditional)

        # importing pkg.sub also executes pkg/__init__.py
        for rel, node in nodes.items():
            extra = []
            parts = node.module.split(".") if node.module else []
            for i in range(1, len(parts)):
                anc = root.joinpath(*parts[:i]) / "__init__.py"
                if anc.is_file():
                    extra.append(anc.relative_to(root).as_posix())
            if extra:
                node.imports = sorted(set(node.imports) | set(extra))

        edges: Dict[str, Set[str]] = {rel: set(node.imports) for rel, node in nodes.items()}

        reference_edges: Dict[str, Set[str]] = {}
        if use_references:
            token_holders: Dict[str, Set[str]] = {}
            for rel in files:
                rec = scans.get(rel) or {}
                for tok in rec.get("refs", []):
                    token_holders.setdefault(tok, set()).add(rel)
            for rel, node in nodes.items():
                keys = {rel, node.module}
                if rel.endswith(".py"):
                    keys.add(rel[:-3])
                holders: Set[str] = set()
                for k in keys:
                    if len(k) < 3:
                        continue
                    holders |= token_holders.get(k, set())
                holders.discard(rel)
                if holders:
                    reference_edges[rel] = holders
                    for h in holders:
                        edges.setdefault(h, set()).add(rel)
        report.reference_edges = sum(len(v) for v in reference_edges.values())
        report.parse_errors = sorted(p for p, n in nodes.items() if n.parse_error)
        report.phase_resolve_s = time.time() - t1

        t2 = time.time()
        cocchange: Dict[str, Set[str]] = {}
        if use_history:
            cocchange = cocchange_edges(root, history_commits)
            for src, dsts in cocchange.items():
                if src in edges:
                    edges[src] |= {d for d in dsts if d in nodes}
        report.phase_history_s = time.time() - t2
        graph = cls(nodes, edges, cocchange, report, reference_edges)
        if graph_cache is not None:
            try:
                graph_cache.parent.mkdir(parents=True, exist_ok=True)
                with graph_cache.open("wb") as fh:
                    pickle.dump({**graph.to_cache(), "stamp": stamp, "version": GRAPH_CACHE_VERSION}, fh)
            except OSError:  # pragma: no cover
                pass
        return graph

    # -- queries ------------------------------------------------------------
    def deps(self, path: str) -> Set[str]:
        return set(self._edges.get(path, set()))

    def importers(self, path: str) -> Set[str]:
        return set(self._rdeps.get(path, set()))

    def importers_static(self, path: str) -> Set[str]:
        return set(self._static_rdeps.get(path, set()))

    def impact(self, seeds: Iterable[str], include_cocchange: bool = True) -> Set[str]:
        """Reverse transitive closure: everything that (transitively) imports a seed."""
        seen: Set[str] = set()
        stack = [s for s in seeds if s in self.nodes]
        seen.update(stack)
        while stack:
            cur = stack.pop()
            for parent in self._rdeps.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
            if include_cocchange:
                for t in self._cocchange.get(cur, ()):
                    if t in self.nodes and t not in seen:
                        seen.add(t)
                        stack.append(t)
        if include_cocchange:
            for s in seeds:
                if s not in self.nodes:
                    for t in self._cocchange.get(s, ()):
                        if t in self.nodes:
                            seen.add(t)
        return seen

    def tests(self) -> List[str]:
        return sorted(p for p, n in self.nodes.items() if n.is_test)

    def test_closure(self, test_path: str) -> Set[str]:
        return self.closure([test_path])

    def closure(self, seeds: Iterable[str]) -> Set[str]:
        seen: Set[str] = set()
        stack = [s for s in seeds if s in self.nodes]
        while stack:
            cur = stack.pop()
            for d in self._edges.get(cur, ()):
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        return seen

    def cocchange(self, path: str) -> Set[str]:
        return set(self._cocchange.get(path, set()))

    def impact_references(self, path: str) -> Set[str]:
        """Files whose string literals mention *path* (subprocess / registry use)."""
        return set(self._references.get(path, set()))

    def stats(self) -> dict:
        nodes = list(self.nodes.values())
        return {
            "files": len(nodes),
            "tests": sum(1 for n in nodes if n.is_test),
            "edges": sum(len(v) for v in self._edges.values()),
            "cocchange_edges": sum(len(v) for v in self._cocchange.values()),
            "parse_errors": sum(1 for n in nodes if n.parse_error),
            "dynamic_modules": sum(1 for n in nodes if n.dynamic),
            "generated_files": sum(1 for n in nodes if n.generated),
            "cross_repo_refs": sum(len(n.cross_repo) for n in nodes),
            "external_import_names": len({e for n in nodes for e in n.external}),
            "unresolved_relative": sum(len(n.relative_broken) for n in nodes),
        }

    # -- serialisation ------------------------------------------------------
    def to_dict(self, include_nodes: bool = True) -> dict:
        return {
            "schema": "tp.test_impact_selector.graph/v1",
            "fingerprint": self.fingerprint,
            "stats": self.stats(),
            "build": self.build_report.as_dict(),
            "nodes": [self.nodes[p].as_dict() for p in sorted(self.nodes)] if include_nodes else [],
            "cocchange": {k: sorted(v) for k, v in sorted(self._cocchange.items())},
        }

    def save(self, path: Path | str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=True), encoding="utf-8")
        return p

    def to_cache(self) -> dict:
        return {
            "nodes": {rel: node.as_dict() for rel, node in self.nodes.items()},
            "edges": {k: sorted(v) for k, v in self._edges.items()},
            "cocchange": {k: sorted(v) for k, v in self._cocchange.items()},
            "references": {k: sorted(v) for k, v in self._references.items()},
            "report": self.build_report.as_dict(),
        }

    @classmethod
    def from_cache(cls, payload: dict) -> "ImportGraph":
        nodes: Dict[str, FileNode] = {}
        for rel, rec in payload["nodes"].items():
            nodes[rel] = FileNode(
                path=rec["path"],
                module=rec["module"],
                is_test=rec["is_test"],
                parse_error=rec.get("parse_error", ""),
                generated=rec.get("generated", False),
                dynamic=rec.get("dynamic", False),
                imports=list(rec.get("imports", [])),
                external=list(rec.get("external", [])),
                cross_repo=list(rec.get("cross_repo", [])),
                conditional=list(rec.get("conditional", [])),
                relative_broken=list(rec.get("relative_broken", [])),
                dyn_literals=list(rec.get("dyn_literals", [])),
            )
        edges = {k: set(v) for k, v in payload["edges"].items()}
        cocchange = {k: set(v) for k, v in payload["cocchange"].items()}
        references = {k: set(v) for k, v in payload.get("references", {}).items()}
        rep = GraphBuildReport(**{k: v for k, v in payload.get("report", {}).items() if k in GraphBuildReport.__dataclass_fields__})
        return cls(nodes, edges, cocchange, rep, references)

    def _fingerprint(self) -> str:
        h = hashlib.sha256()
        for rel in sorted(self.nodes):
            h.update(rel.encode())
            h.update(b"\0")
            h.update("\x1f".join(sorted(self._edges.get(rel, set()))).encode())
            h.update(b"\0")
            h.update("\x1f".join(sorted(self._cocchange.get(rel, set()))).encode())
            h.update(b"\1")
        return h.hexdigest()


def _tree_stamp(root: Path, files: Sequence[str], use_history: bool, history_commits: int) -> str:
    h = hashlib.sha256()
    for rel in files:
        try:
            st = (root / rel).stat()
        except OSError:  # pragma: no cover
            continue
        h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode())
        h.update(b"\n")
    h.update(f"history={use_history}:{history_commits}".encode())
    if use_history:
        head = _git(root, "rev-parse", "HEAD").strip()
        h.update(head.encode())
    return h.hexdigest()


def _attach(node: FileNode, targets: Sequence[str], conditional: bool) -> None:
    for t in targets:
        node.imports.append(t)
        if conditional:
            node.conditional.append(t)


def _collect_scans(
    root: Path, files: Sequence[str], jobs: int, cache_path, report: GraphBuildReport
) -> Tuple[Dict[str, dict], GraphBuildReport]:
    cache = ScanCache(cache_path)
    scans: Dict[str, dict] = {}
    todo: List[Tuple[str, Tuple[int, int]]] = []
    for rel in files:
        try:
            key = _stat_key(root, rel)
        except OSError:  # pragma: no cover
            continue
        hit = cache.get(rel, key)
        if hit is not None:
            scans[rel] = hit
        else:
            todo.append((rel, key))
    report.cache_hits = cache.hits
    report.scanned = len(todo)
    root_s = str(root)
    payload = [(root_s, rel) for rel, _ in todo]
    if jobs > 1 and len(payload) > 64:
        try:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                results = list(pool.map(scan_file, payload, chunksize=max(1, len(payload) // (jobs * 4))))
        except (OSError, RuntimeError):  # pragma: no cover - fall back to serial
            results = [scan_file(p) for p in payload]
    else:
        results = [scan_file(p) for p in payload]
    key_of = dict(todo)
    for rel, rec in results:
        scans[rel] = rec
        cache.put(rel, key_of[rel], rec)
    cache.prune(files)
    if cache_path:
        cache.save()
    report.parse_errors = sorted(p for p, r in scans.items() if r.get("parse_error"))
    return scans, report


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=180, check=False
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return ""


def cocchange_edges(root: Path | str, max_commits: int = 400) -> Dict[str, Set[str]]:
    """Historical co-change table: source file -> test files changed with it.

    Support count is the number of commits in which the pair appeared together;
    jaccard is over the two commit sets.  Only pairs with support >= 2 and
    jaccard >= 0.25 survive, so one accidental commit cannot pollute the safety
    net.
    """
    root = Path(root)
    raw = _git(root, "log", f"-{max_commits}", "--no-merges", "--name-only", "--pretty=format:__C__")
    if not raw:
        return {}
    commits: List[List[str]] = []
    cur: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line == "__C__":
            if cur:
                commits.append(cur)
            cur = []
        elif line.endswith(".py"):
            cur.append(line)
    if cur:
        commits.append(cur)

    pair_support: Dict[Tuple[str, str], int] = {}
    file_commits: Dict[str, int] = {}
    for files in commits:
        uniq = sorted(set(files))
        for f in uniq:
            file_commits[f] = file_commits.get(f, 0) + 1
        tests = [f for f in uniq if is_test_path(f)]
        srcs = [f for f in uniq if not is_test_path(f)]
        for s in srcs:
            for t in tests:
                pair_support[(s, t)] = pair_support.get((s, t), 0) + 1
    table: Dict[str, Set[str]] = {}
    for (s, t), support in pair_support.items():
        if support < COCCHANGE_MIN_SUPPORT:
            continue
        union = file_commits.get(s, 0) + file_commits.get(t, 0) - support
        if union and support / union >= COCCHANGE_JACCARD:
            table.setdefault(s, set()).add(t)
    return table


_cocchange_edges = cocchange_edges