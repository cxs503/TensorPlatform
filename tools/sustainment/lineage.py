"""Artifact lineage DAG: sha256 inventory + dependency graph + tamper detection.

Why this exists
---------------
The platform produces artifacts (``artifacts/``, ``runs/``, ``docs/``, ...) and
reports cite them.  Before this module "which artifact was derived from which"
and "did anything change under my feet" were assumptions.  Here both become
machine-adjudicated: every file gets a sha256 node, dependencies become DAG
edges, and a lock file (``lineage_lock.json``) freezes the observed state so a
later *verify* can report tampering (modified / missing / added / edge drift).

Edge inference (deterministic, three rules; each edge records its rule)
-----------------------------------------------------------------------
``R1 declared``  a sidecar ``<artifact>.provenance.json`` (or ``<stem>.meta.json``)
                 naming ``inputs`` / ``source_files`` / ``sources`` / ``derived_from``
                 — strongest evidence, taken verbatim (paths resolved against
                 the sidecar's directory and against every scan root).
``R2 sibling``   files sharing directory+stem are ordered by kind precedence
                 ``code < data < figure < doc`` (a doc describing an artifact
                 depends on it) — models "same-stem family" derivations.
``R3 mention``   a text-like file (``.md/.json/.txt/.py/.yaml/.yml/.csv``) whose
                 body contains another node's *unique* basename → edge
                 ``referenced → referrer``.  Unique basenames only, so the rule
                 cannot invent ambiguity.

There is deliberately **no** mtime-proximity rule: temporal ordering is not
causality and would fabricate edges.

Cycle resolution (evidence-ranked, audited)
------------------------------------------
R2+R3 can close a loop on real trees (a script and the markdown it writes mention
each other).  ``enforce_dag`` resolves loops by dropping the *weakest evidence*
edge of each cycle (``R3_mention`` before ``R2_sibling`` before ``R1_declared``)
and records every dropped edge together with the cycle it closed, so the DAG is
obtained by an auditable decision rather than by ignoring the problem.  A cycle
that only rests on **declared** provenance (R1) is never auto-broken: two
contradictory declarations are a data contradiction and stay reported as
unresolved (``summary.is_dag == False``, ``summary.cycles_unresolved > 0``).

Public API (stable, additive-only)
----------------------------------
``scan(roots, **opts) -> Inventory``
``build_graph(inventory) -> nx.DiGraph``
``save_lock(graph, path)`` / ``verify_lock(graph, path) -> LockDrift``
``forward(node)`` / ``backward(node)`` trace helpers on ``Lineage``
``render_dot(graph)`` / ``plot_dag(graph, path)``

CLI::

    python3 -m tools.sustainment.lineage build  --root artifacts --root docs --out artifacts/sustainment/lineage.json
    python3 -m tools.sustainment.lineage verify --lock artifacts/sustainment/lineage_lock.json --root artifacts
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # networkx is a hard dependency of the DAG layer, but degrade gracefully
    import networkx as nx
except Exception:  # pragma: no cover - exercised only on broken installs
    nx = None  # type: ignore[assignment]

SCHEMA_VERSION = 1
LOCK_VERSION = 1
_CHUNK = 1 << 20

#: extension -> kind.  Unknown extensions fall back to ``other``.
KIND_BY_EXT: Dict[str, str] = {
    ".py": "code", ".sh": "code", ".toml": "code", ".cfg": "code",
    ".json": "data", ".csv": "data", ".npz": "data", ".npy": "data",
    ".db": "data", ".sqlite": "data", ".parquet": "data", ".h5": "data",
    ".png": "figure", ".svg": "figure", ".pdf": "figure", ".jpg": "figure",
    ".md": "doc", ".rst": "doc", ".txt": "doc",
    ".zst": "archive", ".gz": "archive", ".tar": "archive", ".zip": "archive",
}

#: R2 precedence: a node of kind A depends on a node of kind B iff rank[A] > rank[B].
KIND_RANK: Dict[str, int] = {
    "code": 0, "data": 1, "figure": 2, "doc": 3, "archive": 4, "other": 5,
}

#: R1 sidecar suffixes
SIDECAR_SUFFIXES: Tuple[str, ...] = (".provenance.json", ".meta.json")
#: R1 sidecar keys carrying upstream paths
DECLARED_KEYS: Tuple[str, ...] = (
    "inputs", "source_files", "sources", "derived_from", "input_files",
)
#: extensions scanned for R3 textual mentions
TEXTUAL_EXTS = {".md", ".rst", ".txt", ".json", ".py", ".yaml", ".yml", ".csv"}
#: never scanned (noise, not artifacts)
DEFAULT_EXCLUDES = {
    "__pycache__", ".git", ".pytest_cache", "node_modules", ".mypy_cache",
    ".ipynb_checkpoints", ".venv", "venv",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = _CHUNK) -> str:
    """Streaming sha256 (constant memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def kind_of(path: Path) -> str:
    return KIND_BY_EXT.get(path.suffix.lower(), "other")


@dataclass
class Node:
    """One inventoried file."""

    id: str            # stable id: path relative to the first root that contains it
    path: str          # absolute path
    sha256: str
    size: int
    mtime: float
    kind: str
    root: str

    def to_json(self) -> dict:
        return {
            "id": self.id, "path": self.path, "sha256": self.sha256,
            "size": self.size, "mtime": self.mtime, "kind": self.kind,
            "root": self.root,
        }


@dataclass
class Inventory:
    roots: List[str]
    nodes: Dict[str, Node] = field(default_factory=dict)
    skipped: List[dict] = field(default_factory=list)
    scanned_files: int = 0
    elapsed_s: float = 0.0

    def rel_to_root(self, abspath: Path) -> Optional[Tuple[str, str]]:
        for r in self.roots:
            try:
                rel = abspath.relative_to(Path(r))
            except ValueError:
                continue
            return (f"{Path(r).name}/{rel.as_posix()}", r)
        return None


def scan(
    roots: Sequence[str | os.PathLike],
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    max_files: int = 20000,
    max_hash_bytes: int = 64 << 20,
    exclude_paths: Sequence[str] = (),
    exclude_dirs: Sequence[str] = (),
) -> Inventory:
    """Hash every regular file under ``roots`` into an :class:`Inventory`.

    ``max_files`` / ``max_hash_bytes`` cap the work so an accidental scan of a
    huge tree cannot wedge the control plane; capped items are listed in
    ``inventory.skipped`` with a reason (honest truncation, never silent).
    """
    inv = Inventory(roots=[str(Path(r).resolve()) for r in roots])
    excluded = set(excludes)
    #: the audit's own outputs must never be its inputs (otherwise the lock is
    #: stale the moment it is written, and every verify reports phantom drift)
    excluded_abs = {os.path.normpath(str(Path(p).resolve())) for p in exclude_paths if p}
    excluded_dirs = {os.path.normpath(str(Path(d).resolve())) for d in exclude_dirs if d}
    t0 = time.time()
    seen_ids: Dict[str, int] = {}
    for root in inv.roots:
        root_p = Path(root)
        if not root_p.exists():
            inv.skipped.append({"path": str(root_p), "reason": "root_missing"})
            continue
        for dirpath, dirnames, filenames in os.walk(root_p):
            dirnames[:] = sorted(d for d in dirnames if d not in excluded
                                 and not d.startswith(".cache")
                                 and os.path.normpath(os.path.join(dirpath, d)) not in excluded_dirs)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if os.path.normpath(str(p)) in excluded_abs:
                    inv.skipped.append({"path": str(p), "reason": "excluded_output"})
                    continue
                if p.is_symlink() or not p.is_file():
                    inv.skipped.append({"path": str(p), "reason": "not_regular_file"})
                    continue
                inv.scanned_files += 1
                if len(inv.nodes) >= max_files:
                    inv.skipped.append({"path": str(p), "reason": "max_files_cap"})
                    continue
                try:
                    st = p.stat()
                except OSError as exc:  # pragma: no cover - races
                    inv.skipped.append({"path": str(p), "reason": f"stat_error:{exc}"})
                    continue
                if st.st_size > max_hash_bytes:
                    inv.skipped.append({"path": str(p), "reason": "max_hash_bytes_cap", "size": st.st_size})
                    continue
                try:
                    digest = sha256_file(p)
                except OSError as exc:  # pragma: no cover - races
                    inv.skipped.append({"path": str(p), "reason": f"read_error:{exc}"})
                    continue
                rel, rroot = inv.rel_to_root(p) or (p.name, inv.roots[0])
                nid = rel
                if nid in seen_ids:  # two roots share a basename path -> disambiguate
                    seen_ids[nid] += 1
                    nid = f"{rel}#{seen_ids[nid]}"
                else:
                    seen_ids[nid] = 1
                inv.nodes[nid] = Node(
                    id=nid, path=str(p), sha256=digest, size=st.st_size,
                    mtime=st.st_mtime, kind=kind_of(p), root=rroot,
                )
    inv.elapsed_s = round(time.time() - t0, 3)
    return inv


def _read_text(path: Path, limit: int = 512 * 1024) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return ""


def _normalise(p: str) -> str:
    return os.path.normpath(p).replace("\\", "/")


def _resolve_declared(raw: str, sidecar: Path, roots: Sequence[str]) -> Optional[Path]:
    """Resolve an R1 declared input path against sidecar dir, then each root."""
    cand = Path(raw)
    if cand.is_absolute():
        return cand if cand.exists() else None
    for base in [sidecar.parent, *[Path(r) for r in roots]]:
        p = (base / raw)
        if p.exists():
            return p.resolve()
    return None


# ---------------------------------------------------------------------------
# graph construction
# ---------------------------------------------------------------------------


@dataclass
class EdgeEvidence:
    src: str
    dst: str
    rule: str
    detail: str = ""

    def to_json(self) -> dict:
        return {"src": self.src, "dst": self.dst, "rule": self.rule, "detail": self.detail}


def _declared_edges(inv: Inventory) -> List[EdgeEvidence]:
    by_abs = {_normalise(n.path): n.id for n in inv.nodes.values()}
    edges: List[EdgeEvidence] = []
    for node in inv.nodes.values():
        p = Path(node.path)
        if not any(node.id.endswith(s) or node.path.endswith(s) for s in SIDECAR_SUFFIXES):
            # sidecars themselves are nodes; the *artifact* they describe is the target
            continue
        try:
            payload = json.loads(_read_text(p))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        # the sidecar describes the sibling artifact (strip the sidecar suffix)
        art_name = None
        for suf in SIDECAR_SUFFIXES:
            if p.name.endswith(suf):
                art_name = p.name[: -len(suf)]
                break
        art_abs = _normalise(str(p.parent / art_name)) if art_name else None
        target_id = by_abs.get(art_abs) if art_abs else None
        if target_id is None:
            # `.meta.json` for `foo.json` may mean `foo` + `.meta.json`; accept stem match
            for suf in SIDECAR_SUFFIXES:
                stem = p.name[: -len(suf)] if p.name.endswith(suf) else None
                if not stem:
                    continue
                for cand in (p.parent / stem), (p.parent / (stem + ".json")):
                    cid = by_abs.get(_normalise(str(cand)))
                    if cid:
                        target_id = cid
                        break
                if target_id:
                    break
        for key in DECLARED_KEYS:
            vals = payload.get(key)
            if isinstance(vals, str):
                vals = [vals]
            if not isinstance(vals, list):
                continue
            for raw in vals:
                if not isinstance(raw, str):
                    continue
                resolved = _resolve_declared(raw, p, inv.roots)
                if resolved is None:
                    continue
                src_id = by_abs.get(_normalise(str(resolved)))
                dst_id = target_id or node.id
                if src_id and src_id != dst_id:
                    edges.append(EdgeEvidence(src_id, dst_id, "R1_declared", f"{key}={raw}"))
    return edges


def _sibling_edges(inv: Inventory) -> List[EdgeEvidence]:
    groups: Dict[Tuple[str, str], List[Node]] = {}
    for n in inv.nodes.values():
        p = Path(n.path)
        stem = p.name
        for suf in SIDECAR_SUFFIXES:  # sidecars are evidence, not family members
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        else:
            stem = p.stem
        groups.setdefault((str(p.parent), stem), []).append(n)
    edges: List[EdgeEvidence] = []
    for (_parent, _stem), members in groups.items():
        members = sorted(members, key=lambda x: (KIND_RANK.get(x.kind, 9), x.id))
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if KIND_RANK.get(a.kind, 9) < KIND_RANK.get(b.kind, 9):
                    edges.append(EdgeEvidence(a.id, b.id, "R2_sibling", f"shared_stem:{_stem}"))
    return edges


def _mention_edges(inv: Inventory) -> List[EdgeEvidence]:
    by_name: Dict[str, List[Node]] = {}
    for n in inv.nodes.values():
        by_name.setdefault(Path(n.path).name, []).append(n)
    unique = {name: lst[0] for name, lst in by_name.items() if len(lst) == 1}
    edges: List[EdgeEvidence] = []
    for node in inv.nodes.values():
        p = Path(node.path)
        if p.suffix.lower() not in TEXTUAL_EXTS:
            continue
        body = _read_text(p)
        if not body:
            continue
        for name, target in unique.items():
            if target.id == node.id:
                continue
            if name and name in body:
                edges.append(EdgeEvidence(target.id, node.id, "R3_mention", f"mentions:{name}"))
    return edges


def build_graph(
    inv: Inventory,
    rules: Sequence[str] = ("R1", "R2", "R3"),
) -> "nx.DiGraph":
    """Build the lineage DAG.  Because R3 can point "backwards" against R1/R2,
    cycles are possible in principle; they are *reported*, not silently broken."""
    if nx is None:  # pragma: no cover
        raise RuntimeError("networkx is required for the lineage DAG layer")
    g = nx.DiGraph()
    for n in inv.nodes.values():
        g.add_node(n.id, **{k: v for k, v in n.to_json().items() if k != "id"})
    collected: List[EdgeEvidence] = []
    if "R1" in rules:
        collected += _declared_edges(inv)
    if "R2" in rules:
        collected += _sibling_edges(inv)
    if "R3" in rules:
        collected += _mention_edges(inv)
    seen = set()
    for e in collected:
        key = (e.src, e.dst, e.rule)
        if key in seen:
            continue
        seen.add(key)
        if e.src == e.dst or e.src not in g or e.dst not in g:
            continue
        if g.has_edge(e.src, e.dst):
            g[e.src][e.dst].setdefault("rules", []).append(e.rule)
            continue
        g.add_edge(e.src, e.dst, rules=[e.rule], detail=e.detail, rule=e.rule)
    return g


def cycles(g: "nx.DiGraph") -> List[List[str]]:
    if nx is None:  # pragma: no cover
        return []
    try:
        return [list(c) for c in nx.simple_cycles(g)]
    except Exception:  # pragma: no cover
        return []


#: weakest → strongest.  Cycle breaking drops the weakest edge available.
RULE_PRIORITY = ("R3_mention", "R2_sibling", "R1_declared")


#: rules weak enough to be overridden by a cycle.  Declared provenance (R1) is
#: *never* dropped: two contradictory declarations are a data contradiction that
#: must surface, not be silently auto-resolved.
BREAKABLE_RULES = ("R3_mention", "R2_sibling")


def enforce_dag(g: "nx.DiGraph", priority: Sequence[str] = RULE_PRIORITY,
                max_iter: int = 2000,
                breakable: Sequence[str] = BREAKABLE_RULES) -> Tuple["nx.DiGraph", List[dict]]:
    """Break cycles by evidence strength; return the DAG and the audit trail.

    Each iteration takes one shortest cycle and removes its weakest-rule edge
    (ties → first in ``RULE_PRIORITY`` order, then lexicographic).  Removed edges
    are returned with the cycle that motivated them.  The graph is mutated.
    """
    if nx is None:  # pragma: no cover
        return g, []
    dropped: List[dict] = []
    rank = {r: i for i, r in enumerate(priority)}
    for _ in range(max_iter):
        try:
            cyc = nx.find_cycle(g, orientation="original")
        except nx.NetworkXNoCycle:
            break
        nodes_in_cycle = [e[0] for e in cyc]
        edges = [(e[0], e[1]) for e in cyc]
        def weakness(edge: Tuple[str, str]) -> Tuple[int, str, str]:
            rules = g[edge[0]][edge[1]].get("rules", [])
            best = min((rank.get(r, len(priority)) for r in rules), default=len(priority))
            return (best, edge[0], edge[1])
        breakable_edges = [(a, b) for a, b in edges
                           if any(r in breakable for r in g[a][b].get("rules", []))]
        if not breakable_edges:
            break  # only declared evidence left -> contradictory provenance, keep it visible
        victim = min(breakable_edges, key=weakness)
        rule = g[victim[0]][victim[1]].get("rule", "")
        dropped.append({"cycle": nodes_in_cycle + [nodes_in_cycle[0]],
                        "dropped_edge": [victim[0], victim[1]], "rule": rule})
        g.remove_edge(*victim)
    return g, dropped


#: @MX:NOTE: 追溯面（forward/backward）是 DAG 存在的理由——"这个图从哪来"与
#: "改了这个文件会影响谁"必须各有一条路径查询，而不是靠人读目录。
class Lineage:
    """Thin query facade over a built DAG (forward / backward tracing)."""

    def __init__(self, graph: "nx.DiGraph", inventory: Optional[Inventory] = None):
        self.graph = graph
        self.inventory = inventory

    # -- tracing ---------------------------------------------------------
    def backward(self, node_id: str) -> List[str]:
        """Ancestors: everything this artifact was derived from (its provenance)."""
        if node_id not in self.graph:
            return []
        return sorted(nx.ancestors(self.graph, node_id))

    def forward(self, node_id: str) -> List[str]:
        """Descendants: everything that depends on this artifact (impact set)."""
        if node_id not in self.graph:
            return []
        return sorted(nx.descendants(self.graph, node_id))

    def path_between(self, src: str, dst: str) -> Optional[List[str]]:
        try:
            return nx.shortest_path(self.graph, src, dst)
        except Exception:
            return None

    def topo_order(self) -> List[str]:
        try:
            return list(nx.topological_sort(self.graph))
        except Exception:
            return []

    # -- reporting -------------------------------------------------------
    def summary(self) -> dict:
        g = self.graph
        cyc = cycles(g)
        roots = sorted(n for n in g.nodes if g.out_degree(n) == 0 and g.in_degree(n) > 0)
        leaves = sorted(n for n in g.nodes if g.in_degree(n) == 0 and g.out_degree(n) > 0)
        dangling = sorted(n for n in g.nodes if g.degree(n) == 0)
        by_kind: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        for n in g.nodes:
            by_kind[g.nodes[n]["kind"]] = by_kind.get(g.nodes[n]["kind"], 0) + 1
        for _s, _d, d in g.edges(data=True):
            for r in d.get("rules", []):
                by_rule[r] = by_rule.get(r, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "nodes": g.number_of_nodes(),
            "edges": g.number_of_edges(),
            "isolated_nodes": len(dangling),
            "roots_no_incoming": len(roots),
            "leaves_no_outgoing": len(leaves),
            "cycles": len(cyc),
            "cycles_unresolved": len(cyc),
            "is_dag": len(cyc) == 0,
            "dropped_edges": list(getattr(self, "dropped_edges", [])),
            "by_kind": by_kind,
            "by_rule": by_rule,
            "density": round(nx.density(g), 6) if g.number_of_nodes() > 1 else 0.0,
            "weakly_connected_components": (
                nx.number_weakly_connected_components(g) if g.number_of_nodes() else 0
            ),
            "sha256_coverage": (
                round(sum(1 for n in g.nodes if g.nodes[n].get("sha256")) / g.number_of_nodes(), 6)
                if g.number_of_nodes() else 0.0
            ),
            "cycle_examples": cyc[:5],
        }


# ---------------------------------------------------------------------------
# lock file: freeze + verify (tamper detection)
# ---------------------------------------------------------------------------


@dataclass
class LockDrift:
    ok: bool
    checked: int
    modified: List[dict] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    edge_drift: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "ok": self.ok, "checked": self.checked,
            "modified": self.modified, "missing": self.missing, "added": self.added,
            "edge_drift": self.edge_drift,
            "counts": {
                "modified": len(self.modified), "missing": len(self.missing),
                "added": len(self.added),
                "edges_added": len(self.edge_drift.get("added", [])),
                "edges_removed": len(self.edge_drift.get("removed", [])),
            },
        }


def save_lock(lin: Lineage, path: str | os.PathLike, roots: Sequence[str] = (),
              excluded: Sequence[str] = ()) -> dict:
    g = lin.graph
    payload = {
        "lock_version": LOCK_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "roots": [str(r) for r in roots],
        "excluded_paths": sorted({str(Path(e).resolve()) for e in excluded if e}),
        "summary": lin.summary(),
        "nodes": {
            n: {"sha256": g.nodes[n].get("sha256"), "size": g.nodes[n].get("size"),
                "kind": g.nodes[n].get("kind"), "path": g.nodes[n].get("path")}
            for n in sorted(g.nodes)
        },
        "edges": sorted([s, d] for s, d in g.edges),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def verify_lock(lin: Lineage, lock_path: str | os.PathLike) -> LockDrift:
    """Recompute hashes for the locked node set and diff against the lock."""
    lock = json.loads(Path(lock_path).read_text())
    ignore_abs = set(lock.get("excluded_paths", []))

    def _excluded(path: Optional[str]) -> bool:
        if not path:
            return False
        return any(path == ig or path.startswith(ig + os.sep) for ig in ignore_abs)

    g = lin.graph.copy() if hasattr(lin.graph, "copy") else lin.graph
    # the audit's own outputs (and their directory) are not part of the audited universe
    g.remove_nodes_from([n for n in list(g.nodes) if _excluded(g.nodes[n].get("path"))])
    # The lock stores the *cycle-resolved* edge set, so the verifier must apply the
    # same evidence-ranked resolution before diffing — otherwise every dropped
    # weak edge would show up as phantom "added" drift forever.
    g, _dropped_now = enforce_dag(g)
    drift = LockDrift(ok=True, checked=0)
    mirror: Dict[str, Node] = {}
    for n in g.nodes(data=True):
        meta = n[1]
        if meta.get("path"):
            mirror[meta["path"]] = Node(
                id=n[0], path=meta["path"], sha256=meta.get("sha256", ""),
                size=int(meta.get("size") or 0), mtime=float(meta.get("mtime") or 0),
                kind=meta.get("kind", "other"), root="",
            )
    ignore_ids = {rec.get("path") and nid for nid, rec in lock.get("nodes", {}).items()
                  if rec.get("path") in ignore_abs}
    ignore_ids = {nid for nid in ignore_ids if nid}
    locked_ids = set(lock.get("nodes", {}))
    for nid, rec in lock.get("nodes", {}).items():
        node = mirror.get(rec.get("path", ""))
        if node is None:
            if rec.get("path") in ignore_abs:
                continue
            drift.missing.append(nid)
            continue
        # recompute from disk — never trust the freshly scanned hash for a verify
        try:
            actual = sha256_file(Path(node.path))
        except OSError:
            drift.missing.append(nid)
            continue
        drift.checked += 1
        if actual != rec.get("sha256"):
            drift.modified.append({
                "id": nid, "path": node.path,
                "expected": rec.get("sha256"), "actual": actual,
            })
    drift.added = sorted(n for n in g.nodes if n not in locked_ids)
    def _ignored(nid: str) -> bool:
        return nid in ignore_ids or _excluded(g.nodes.get(nid, {}).get("path"))

    locked_edges = {tuple(e) for e in lock.get("edges", [])
                    if not (_ignored(e[0]) or _ignored(e[1]))}
    current_edges = {(s, d) for s, d in g.edges if not (_ignored(s) or _ignored(d))}
    drift.edge_drift = {
        "added": sorted([list(e) for e in current_edges - locked_edges])[:50],
        "removed": sorted([list(e) for e in locked_edges - current_edges])[:50],
    }
    drift.ok = not (drift.modified or drift.missing or drift.added
                    or drift.edge_drift["added"] or drift.edge_drift["removed"])
    return drift


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_KIND_STYLE = {
    "code": ("#4c72b0", "s"), "data": ("#dd8452", "o"), "figure": ("#55a868", "^"),
    "doc": ("#8172b3", "D"), "archive": ("#937860", "v"), "other": ("#8c8c8c", "."),
}


def render_dot(g: "nx.DiGraph", max_nodes: int = 400) -> str:
    lines = ["digraph lineage {", '  rankdir=LR; node [shape=box, fontsize=10];']
    ids = sorted(g.nodes)[:max_nodes]
    keep = set(ids)
    for n in ids:
        meta = g.nodes[n]
        color = _KIND_STYLE.get(meta.get("kind", "other"), _KIND_STYLE["other"])[0]
        short = n if len(n) <= 48 else "..." + n[-45:]
        lines.append(f'  "{short}" [style=filled, fillcolor="{color}", label="{short}"];')
    for s, d, data in g.edges(data=True):
        if s in keep and d in keep:
            lines.append(f'  "{s[:48]}" -> "{d[:48]}" [label="{data.get("rule","")}"];')
    lines.append("}")
    return "\n".join(lines)


def plot_dag(g: "nx.DiGraph", out_png: str | os.PathLike, max_nodes: int = 60,
             title: str = "artifact lineage DAG") -> str:
    """Render a readable subgraph figure (matplotlib, no graphviz dependency)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # keep the most connected nodes so the picture stays legible
    order = sorted(g.nodes, key=lambda n: (-g.degree(n), n))[:max_nodes]
    sub = g.subgraph(order)
    fig, ax = plt.subplots(figsize=(11, 7.5))
    if sub.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "empty graph", ha="center", va="center")
        ax.axis("off")
    else:
        pos = nx.spring_layout(sub, seed=7, k=0.9 / math.sqrt(max(sub.number_of_nodes(), 1)))
        for kind, (color, marker) in _KIND_STYLE.items():
            ns = [n for n in sub.nodes if sub.nodes[n].get("kind") == kind]
            if not ns:
                continue
            nx.draw_networkx_nodes(sub, pos, nodelist=ns, node_color=color, node_shape=marker,
                                   node_size=140, label=kind, ax=ax)
        rule_color = {"R1_declared": "#c44e52", "R2_sibling": "#4c72b0", "R3_mention": "#999999"}
        for rule, color in rule_color.items():
            es = [(s, d) for s, d, data in sub.edges(data=True) if rule in data.get("rules", [])]
            if es:
                nx.draw_networkx_edges(sub, pos, edgelist=es, edge_color=color, width=1.2,
                                       arrows=True, arrowsize=9, ax=ax, label=rule)
        labels = {n: (n if len(n) <= 26 else "…" + n[-24:]) for n in sub.nodes}
        nx.draw_networkx_labels(sub, pos, labels=labels, font_size=6, ax=ax)
        ax.legend(loc="best", fontsize=7, framealpha=0.9)
    ax.set_title(f"{title}  (nodes={g.number_of_nodes()} edges={g.number_of_edges()}"
                 f"{'' if sub.number_of_nodes() == g.number_of_nodes() else f', shown={sub.number_of_nodes()}'})")
    ax.axis("off")
    fig.tight_layout()
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def build_report(
    roots: Sequence[str],
    out_json: Optional[str] = None,
    lock_path: Optional[str] = None,
    rules: Sequence[str] = ("R1", "R2", "R3"),
    max_files: int = 20000,
    out_png: Optional[str] = None,
    trace: Sequence[str] = (),
    enforce_dag_flag: bool = True,
    exclude_paths: Sequence[str] = (),
) -> dict:
    """Full build: scan → graph → (cycle resolution) → summary → lock → figure/traces."""
    t0 = time.time()
    own_outputs = [p for p in (out_json, lock_path, out_png) if p]
    # the audit's own output *directory* is excluded wholesale: an audit that
    # writes next to its inputs would otherwise invalidate its own lock
    # only exclude output directories that actually sit *inside* a scan root;
    # excluding an ancestor of the roots would black-hole the whole tree
    resolved_roots = [str(Path(r).resolve()) for r in roots]
    own_dirs = [str(Path(p).resolve().parent) for p in own_outputs]
    own_dirs = [d for d in own_dirs
                if any(d == r or d.startswith(r + os.sep) for r in resolved_roots)]
    inv = scan(roots, max_files=max_files,
               exclude_paths=list(exclude_paths) + own_outputs, exclude_dirs=own_dirs)
    g = build_graph(inv, rules=rules)
    raw_cycles = len(cycles(g))
    lin = Lineage(g, inv)
    if enforce_dag_flag and raw_cycles:
        g, dropped = enforce_dag(g)
        lin = Lineage(g, inv)
        lin.dropped_edges = dropped
    report = {
        "summary": lin.summary(),
        "inventory": {
            "roots": inv.roots,
            "files_hashed": len(inv.nodes),
            "files_seen": inv.scanned_files,
            "skipped": inv.skipped[:50],
            "skipped_count": len(inv.skipped),
            "hash_seconds": inv.elapsed_s,
            "total_bytes": sum(n.size for n in inv.nodes.values()),
        },
        "traces": {},
        "cycle_examples": cycles(g)[:5],
        "cycles_raw": raw_cycles,
        "cycle_resolution": {
            "applied": bool(enforce_dag_flag and raw_cycles),
            "dropped_edges": getattr(lin, "dropped_edges", []),
        },
    }
    for nid in trace:
        report["traces"][nid] = {
            "backward": lin.backward(nid), "forward": lin.forward(nid),
        }
    if lock_path:
        save_lock(lin, lock_path, roots=roots,
                  excluded=list(exclude_paths) + own_outputs + own_dirs)
        report["lock"] = {"path": str(lock_path), "nodes": g.number_of_nodes()}
    if out_png:
        report["figure"] = plot_dag(g, out_png)
    report["elapsed_s"] = round(time.time() - t0, 3)
    if out_json:
        p = Path(out_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report