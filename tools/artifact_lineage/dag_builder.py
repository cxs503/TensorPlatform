#!/usr/bin/env python3
"""Artifact Lineage DAG — build, query, and verify artifact dependency graphs.

Scans artifact directories, computes sha256, builds a NetworkX DAG,
supports forward/backward tracing and tamper detection.

Design constraints:
  - Standalone module (stdlib + networkx only)
  - Zero domain semantics
  - Additive-only: never mutates indexed files
  - Deterministic: same inputs → same DAG
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

_CHUNK = 1 << 20  # 1 MiB


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ArtifactNode:
    """A single artifact in the lineage graph."""
    path: str               # relative path from scan root
    sha256: str             # content hash
    size: int               # bytes
    mtime: float            # modification time (epoch)
    category: str           # artifacts | runs | docs | work | config | other
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "mtime": self.mtime,
            "category": self.category,
            "metadata": self.metadata,
        }


@dataclass
class LineageDAG:
    """Container for the artifact lineage directed acyclic graph."""
    graph: nx.DiGraph
    nodes: Dict[str, ArtifactNode]
    built_at: str = ""
    scan_roots: List[str] = field(default_factory=list)
    scan_stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        edges = [{"source": u, "target": v, **d}
                 for u, v, d in self.graph.edges(data=True)]
        return {
            "version": 1,
            "built_at": self.built_at,
            "scan_roots": self.scan_roots,
            "scan_stats": self.scan_stats,
            "node_count": len(self.nodes),
            "edge_count": len(edges),
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": edges,
        }

    def save(self, path: Path) -> None:
        """Persist DAG to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "LineageDAG":
        """Load DAG from JSON file."""
        with open(path) as f:
            data = json.load(f)
        G = nx.DiGraph()
        nodes = {}
        for k, v in data["nodes"].items():
            node = ArtifactNode(**v)
            nodes[k] = node
            G.add_node(k, **v)
        for e in data["edges"]:
            src, tgt = e.pop("source"), e.pop("target")
            G.add_edge(src, tgt, **e)
        dag = cls(graph=G, nodes=nodes,
                  built_at=data.get("built_at", ""),
                  scan_roots=data.get("scan_roots", []),
                  scan_stats=data.get("scan_stats", {}))
        return dag

    # --- Query methods ---

    def ancestors(self, node_id: str) -> List[str]:
        """All upstream dependencies (reverse trace)."""
        if node_id not in self.graph:
            return []
        return sorted(nx.ancestors(self.graph, node_id))

    def descendants(self, node_id: str) -> List[str]:
        """All downstream dependents (forward trace)."""
        if node_id not in self.graph:
            return []
        return sorted(nx.descendants(self.graph, node_id))

    def direct_parents(self, node_id: str) -> List[str]:
        """Immediate upstream dependencies."""
        if node_id not in self.graph:
            return []
        return sorted(self.graph.predecessors(node_id))

    def direct_children(self, node_id: str) -> List[str]:
        """Immediate downstream dependents."""
        if node_id not in self.graph:
            return []
        return sorted(self.graph.successors(node_id))

    def is_acyclic(self) -> bool:
        """Verify the graph is a DAG (no cycles)."""
        return nx.is_directed_acyclic_graph(self.graph)

    def topological_layers(self) -> List[List[str]]:
        """Return nodes in topological layers (for visualization)."""
        if not self.is_acyclic():
            return []
        return list(nx.topological_generations(self.graph))


# ---------------------------------------------------------------------------
# Hashing & scanning
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = _CHUNK) -> str:
    """Stream sha256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def _categorize(rel_path: str) -> str:
    """Categorize a file based on its path."""
    parts = rel_path.split(os.sep)
    for p in parts:
        if p in ("artifacts", "runs", "docs", "work", "config"):
            return p
    return "other"


def _infer_dependencies(path: str, all_paths: Set[str]) -> List[str]:
    """Infer dependencies from naming conventions.

    Heuristics:
    - report_m*.json depends on artifacts from same mission
    - figures depend on the script that generated them
    - validation reports depend on the benchmark data
    - JSON reports in runs/ depend on artifacts in the same run dir
    """
    deps = []
    basename = os.path.basename(path)
    dirname = os.path.dirname(path)

    # Mission-linked reports
    m_match = re.match(r'(?:report|validation_report|generate_).*?m(\d+)', basename)
    if m_match:
        mission = f"m{m_match.group(1)}"
        for p in all_paths:
            if p != path and mission in p and os.path.dirname(p) == dirname:
                deps.append(p)

    # Figure generation: generate_X.py → X.png
    gen_match = re.match(r'generate_(\w+?)\.py$', basename)
    if gen_match:
        target_name = gen_match.group(1)
        for p in all_paths:
            if p != path and target_name in os.path.basename(p) and p.endswith('.png'):
                deps.append(p)

    # Run artifacts: runs/mXXX/* depends on other files in same run
    if dirname.startswith("runs/"):
        for p in all_paths:
            if p != path and os.path.dirname(p) == dirname:
                deps.append(p)

    return sorted(set(deps))


def scan_directory(root: Path, exclude_patterns: Optional[List[str]] = None) -> Dict[str, ArtifactNode]:
    """Scan a directory tree and build artifact nodes."""
    exclude = set(exclude_patterns or [".git", "__pycache__", ".pyc", "node_modules"])
    nodes = {}
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter excluded dirs
        dirnames[:] = [d for d in sorted(dirnames) if d not in exclude]

        for name in sorted(filenames):
            if any(name.endswith(ext) for ext in exclude):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            try:
                rel = str(full.relative_to(root))
                stat = full.stat()
                h = sha256_file(full)
                nodes[rel] = ArtifactNode(
                    path=rel,
                    sha256=h,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    category=_categorize(rel),
                )
            except (OSError, PermissionError):
                continue

    return nodes


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------

def build_lineage_dag(
    scan_roots: List[Path],
    exclude_patterns: Optional[List[str]] = None,
    infer_deps: bool = True,
) -> LineageDAG:
    """Build a lineage DAG from one or more scan roots.

    Args:
        scan_roots: directories to scan
        exclude_patterns: file/dir patterns to exclude
        infer_deps: whether to infer dependencies from naming conventions

    Returns:
        LineageDAG with nodes and edges
    """
    all_nodes: Dict[str, ArtifactNode] = {}
    stats = {"files_scanned": 0, "bytes_scanned": 0, "categories": {}}

    for root in scan_roots:
        root = Path(root)
        if not root.exists():
            continue
        nodes = scan_directory(root, exclude_patterns)
        for k, v in nodes.items():
            # Prefix with root name to avoid collisions
            key = f"{root.name}/{k}" if len(scan_roots) > 1 else k
            all_nodes[key] = v
            stats["files_scanned"] += 1
            stats["bytes_scanned"] += v.size
            stats["categories"][v.category] = stats["categories"].get(v.category, 0) + 1

    # Build graph
    G = nx.DiGraph()
    all_paths = set(all_nodes.keys())

    for key, node in all_nodes.items():
        G.add_node(key, **node.to_dict())

    if infer_deps:
        for key, node in all_nodes.items():
            deps = _infer_dependencies(node.path, all_paths)
            for dep in deps:
                if dep in all_nodes:
                    G.add_edge(dep, key, relation="inferred")

    dag = LineageDAG(
        graph=G,
        nodes=all_nodes,
        built_at=datetime.now(timezone.utc).isoformat(),
        scan_roots=[str(r) for r in scan_roots],
        scan_stats=stats,
    )
    return dag


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------

def verify_integrity(dag: LineageDAG, scan_roots: List[Path]) -> Dict[str, Any]:
    """Verify current files match the DAG's recorded hashes.

    Returns:
        dict with keys: ok (bool), checked (int), mismatches (list), missing (list)
    """
    mismatches = []
    missing = []
    checked = 0

    for key, node in dag.nodes.items():
        # Find the file
        found = False
        for root in scan_roots:
            root = Path(root)
            candidate = root / node.path
            if candidate.exists():
                found = True
                current_hash = sha256_file(candidate)
                checked += 1
                if current_hash != node.sha256:
                    mismatches.append({
                        "path": key,
                        "expected": node.sha256,
                        "actual": current_hash,
                    })
                break
        if not found:
            missing.append(key)

    return {
        "ok": len(mismatches) == 0 and len(missing) == 0,
        "checked": checked,
        "total": len(dag.nodes),
        "mismatches": mismatches,
        "missing": missing,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def detect_tampering(dag: LineageDAG, scan_roots: List[Path]) -> Dict[str, Any]:
    """Detect tampering by comparing current state against DAG.

    Returns:
        dict with tamper_status, affected_nodes, severity
    """
    result = verify_integrity(dag, scan_roots)

    severity = "none"
    if result["mismatches"]:
        severity = "critical"  # content changed
    elif result["missing"]:
        severity = "warning"   # files removed

    return {
        "tamper_status": "clean" if result["ok"] else "compromised",
        "severity": severity,
        "affected_nodes": result["mismatches"] + [{"path": p, "issue": "missing"} for p in result["missing"]],
        "checked": result["checked"],
        "total": result["total"],
        "verified_at": result["verified_at"],
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI: build and verify lineage DAG."""
    import argparse
    parser = argparse.ArgumentParser(description="Artifact Lineage DAG")
    parser.add_argument("--scan", nargs="+", default=["."], help="Directories to scan")
    parser.add_argument("--output", "-o", default="lineage_dag.json", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Verify against existing DAG")
    parser.add_argument("--dag", default="lineage_dag.json", help="DAG to verify against")
    args = parser.parse_args()

    if args.verify:
        dag = LineageDAG.load(Path(args.dag))
        result = verify_integrity(dag, [Path(r) for r in args.scan])
        print(json.dumps(result, indent=2))
    else:
        dag = build_lineage_dag([Path(r) for r in args.scan])
        dag.save(Path(args.output))
        print(f"Built DAG: {dag.scan_stats['files_scanned']} files, "
              f"{len(dag.graph.edges)} edges")
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
