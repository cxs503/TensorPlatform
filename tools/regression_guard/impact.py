"""Impact analysis: changed paths -> capabilities -> tasks -> acceptance tests.

The resolution is deliberately *fail-safe* (over-select rather than under-select)
and every selected test carries an auditable reason trail, so a human reviewer
can replay why a test was re-run:

``artifact``        the path matched an artifact pattern declared in the ledger
``feature``         the artifact belongs to a capability (direct membership)
``feature_closure`` the capability transitively depends on an affected one
``task``            the task certifies that capability
``import``          static import chain from the changed module to the test file
``manifest_task``   the task itself declared the test (no code link required)

``import`` reasons are computed twice independently — once by ``RepoGraph``
(AST resolution) and once by ``textual_import_edge`` (regex over the raw
source) — so the reported precision can be checked against a second
implementation (criterion c: no unjustified trigger).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .model import Change, ChangeManifest, TaskLedger, canonical_json, sha256_text
from .scanner import RepoGraph, match_any

# change classes ------------------------------------------------------------
CLASS_UNTRACKED = "untracked"
CLASS_IMPORT_ONLY = "import_only"
CLASS_TEST_ONLY = "test_only"


@dataclass
class Reason:
    kind: str
    detail: str
    change: str = ""
    chain: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "detail": self.detail}
        if self.change:
            d["change"] = self.change
        if self.chain:
            d["chain"] = list(self.chain)
        return d

    def identity(self) -> str:
        return canonical_json(self.to_dict())


@dataclass
class TestSelection:
    test_id: str
    reasons: List[Reason] = field(default_factory=list)
    tasks: Set[str] = field(default_factory=set)
    features: Set[str] = field(default_factory=set)
    change_classes: Set[str] = field(default_factory=set)

    def add_reason(self, reason: Reason) -> None:
        if reason.identity() not in {r.identity() for r in self.reasons}:
            self.reasons.append(reason)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "reasons": [r.to_dict() for r in self.reasons],
            "tasks": sorted(self.tasks),
            "features": sorted(self.features),
            "change_classes": sorted(self.change_classes),
        }


@dataclass
class Impact:
    changes: List[Change]
    artifacts: Dict[str, List[str]] = field(default_factory=dict)   # change key -> artifact ids
    features: Dict[str, List[str]] = field(default_factory=dict)    # feature -> reasons
    tasks: Dict[str, List[str]] = field(default_factory=dict)       # task -> reasons
    nodes: List[str] = field(default_factory=list)                  # directly affected nodes
    nodes_at_risk: List[str] = field(default_factory=list)          # downstream blast radius
    selections: List[TestSelection] = field(default_factory=list)
    change_classes: Dict[str, str] = field(default_factory=dict)    # change key -> class
    unmatched: List[str] = field(default_factory=list)

    @property
    def test_ids(self) -> List[str]:
        return [s.test_id for s in self.selections]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changes": [c.to_dict() for c in self.changes],
            "change_classes": dict(sorted(self.change_classes.items())),
            "artifacts": {k: sorted(set(v)) for k, v in sorted(self.artifacts.items())},
            "features": {k: sorted(set(v)) for k, v in sorted(self.features.items())},
            "tasks": {k: sorted(set(v)) for k, v in sorted(self.tasks.items())},
            "nodes": list(self.nodes),
            "nodes_at_risk": list(self.nodes_at_risk),
            "selections": [s.to_dict() for s in self.selections],
            "unmatched": list(self.unmatched),
            "selected_test_count": len(self.selections),
        }

    @property
    def digest(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


def is_test_path(path: str, test_roots: Sequence[str] = ()) -> bool:
    name = Path(path).name
    looks_like_test = name.startswith("test_") or name.endswith("_test.py")
    if not looks_like_test:
        return False
    if not test_roots:
        return True
    return any(path == r.rstrip("/") or path.startswith(r.rstrip("/") + "/") for r in test_roots)


def textual_import_edge(root: Path, src: str, dst_module_leaf: str, imported: Sequence[str] = ()) -> bool:
    """Second, independent implementation of "does ``src`` import the target?".

    Regex over raw text, matching ``import a.b.leaf`` / ``from a.b.leaf import
    name``.  Used only to *confirm* an import reason that the AST resolver
    produced; disagreement is reported, never silently dropped.
    """
    try:
        text = (root / src).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    leaf = re.escape(dst_module_leaf)
    patterns = [
        rf"^\s*import\s+[^\n]*\b{leaf}\b",
        rf"^\s*from\s+[\w.]*\b{leaf}\b\s+import\s+([^\n(]|\([^)]*\))",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.MULTILINE):
            if not imported or any(name in m.group(0) for name in imported):
                return True
    return False


class ImpactAnalyzer:
    def __init__(
        self,
        ledger: TaskLedger,
        graph: Optional[RepoGraph] = None,
        root: Optional[Path | str] = None,
        test_roots: Sequence[str] = (),
        include_import_closure: bool = True,
    ) -> None:
        self.ledger = ledger
        self.graph = graph
        self.root = Path(root) if root else (Path(graph.root) if graph else Path("."))
        self.test_roots = tuple(test_roots)
        self.include_import_closure = include_import_closure

    # -- public API --------------------------------------------------------
    def analyze(self, manifest: ChangeManifest) -> Impact:
        impact = Impact(changes=list(manifest.changes))
        for change in manifest.changes:
            self._analyze_change(change, impact)
        self._close_features(impact)
        self._attach_tasks(impact)
        self._attach_import_tests(impact)
        self._finalise_nodes(impact)
        impact.selections.sort(key=lambda s: s.test_id)
        return impact

    # -- internals ---------------------------------------------------------
    def _analyze_change(self, change: Change, impact: Impact) -> None:
        key = change.key
        classes: Set[str] = set()
        matched_any = False

        for artifact in self.ledger.artifacts:
            pattern = match_any(artifact.paths, change.path)
            if pattern is None:
                continue
            matched_any = True
            classes.add(artifact.kind)
            impact.artifacts.setdefault(key, []).append(artifact.artifact_id)
            for feature in self.ledger.features:
                if artifact.artifact_id in feature.artifacts:
                    impact.features.setdefault(feature.feature_id, []).append(
                        f"artifact:{artifact.artifact_id}<-{change.path}"
                    )

        if not matched_any and self.graph and change.path in self.graph.files:
            classes.add(CLASS_IMPORT_ONLY)
        if is_test_path(change.path, self.test_roots):
            classes.add(CLASS_TEST_ONLY)
        if not classes:
            classes.add(CLASS_UNTRACKED)
            impact.unmatched.append(change.path)
        impact.change_classes[key] = sorted(classes)[0] if len(classes) == 1 else "+".join(sorted(classes))

    def _close_features(self, impact: Impact) -> None:
        """Features that (transitively) depend on an affected feature/artifact."""
        affected = set(impact.features)
        if not affected:
            return
        changed = True
        while changed:
            changed = False
            for feature in self.ledger.features:
                if feature.feature_id in affected:
                    continue
                deps = set(feature.depends_on) | set(feature.artifacts)
                hit = deps & affected
                if hit:
                    affected.add(feature.feature_id)
                    impact.features.setdefault(feature.feature_id, []).append(
                        f"feature_closure<-{sorted(hit)[0]}"
                    )
                    changed = True

    def _attach_tasks(self, impact: Impact) -> None:
        affected = set(impact.features)
        for task in self.ledger.tasks:
            hit = set(task.feature_ids) & affected
            if not hit:
                continue
            reason = f"task:{task.task_id}<-{sorted(hit)[0]}"
            impact.tasks.setdefault(task.task_id, []).append(reason)
            if task.campaign_node:
                impact.nodes.append(task.campaign_node)
            for test_id in task.tests:
                sel = self._selection(impact, test_id)
                sel.tasks.add(task.task_id)
                sel.features |= hit
                sel.add_reason(
                    Reason(
                        kind="task",
                        detail=f"{task.task_id} certifies {sorted(hit)[0]}",
                        change=impact.changes[0].key if impact.changes else "",
                    )
                )
                for fid in hit:
                    sel.add_reason(Reason(kind="feature", detail=f"{fid} depends on changed code"))

    def _attach_import_tests(self, impact: Impact) -> None:
        """Propagate import edges outward from every changed module.

        Symbol narrowing: an empty change-symbol set means "whole module
        dirty", which is fail-safe (every importer is affected).  A populated
        set is propagated hop by hop; an edge is only cut when the importer
        names specific attributes and none of them is in the visible set.
        """
        if not (self.include_import_closure and self.graph):
            return
        from .scanner import module_key_from_path

        for change in impact.changes:
            if change.path not in self.graph.files:
                continue
            visible: Set[str] = set(change.symbols)
            frontier: Dict[str, Set[str]] = {change.path: visible}
            visited: Set[str] = {change.path}
            while frontier:
                nxt: Dict[str, Set[str]] = {}
                for cur, names in sorted(frontier.items()):
                    for importer in sorted(self.graph.imported_by.get(cur, ())):
                        if importer in visited or importer not in self.graph.files:
                            continue
                        imported = set(self.graph.files[importer].imports.get(cur, ("*",)))
                        if names:
                            if "*" not in imported:
                                narrowed = imported & names
                                if not narrowed:
                                    continue
                            else:
                                narrowed = set()
                        else:
                            narrowed = set()
                        visited.add(importer)
                        nxt[importer] = narrowed
                        if not is_test_path(importer, self.test_roots):
                            continue
                        chain = self.graph.import_path(importer, change.path) or (importer, change.path)
                        leaf = module_key_from_path(change.path).rsplit(".", 1)[-1]
                        confirmed = textual_import_edge(
                            self.root, importer, leaf, tuple(change.symbols),
                        )
                        sel = self._selection(impact, importer)
                        sel.change_classes.add(impact.change_classes.get(change.key, CLASS_IMPORT_ONLY))
                        sel.add_reason(
                            Reason(
                                kind="import",
                                detail=f"imports {change.path} (confirmed={str(confirmed).lower()})",
                                change=change.key,
                                chain=tuple(reversed(chain)),
                            )
                        )
                        for task in self.ledger.tasks:
                            if importer in task.tests:
                                sel.tasks.add(task.task_id)
                frontier = nxt

    def _finalise_nodes(self, impact: Impact) -> None:
        direct = sorted(set(impact.nodes))
        impact.nodes = direct
        impact.nodes_at_risk = self.ledger.downstream_nodes(direct) if direct else []

    def _selection(self, impact: Impact, test_id: str) -> TestSelection:
        for sel in impact.selections:
            if sel.test_id == test_id:
                return sel
        sel = TestSelection(test_id=test_id)
        impact.selections.append(sel)
        return sel


def changes_from_git(root: Path | str, rev_range: str = "HEAD") -> Tuple[List[Change], str, str]:
    """Derive a manifest from ``git diff --name-status`` (no shell injection)."""
    import subprocess

    root = Path(root)
    proc = subprocess.run(
        ["git", "diff", "--name-status", rev_range],
        cwd=str(root), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    kind_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added", "T": "modified"}
    changes: List[Change] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        status = cols[0][0]
        path = cols[-1]
        changes.append(Change(path=path, kind=kind_map.get(status, "modified")))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True).stdout.strip()
    return changes, rev_range, head