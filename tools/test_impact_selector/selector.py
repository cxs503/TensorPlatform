"""Change-impact driven test subset selector with explicit fail-closed policy.

Contract
--------
``ImpactSelector.select(changed_files)`` returns a :class:`Selection` with

* ``mode``  -- ``"subset"`` (safe to run only ``tests``) or ``"full"`` (run all),
* ``trigger`` -- ``None`` for a clean subset decision, else the fail-closed class,
* ``tests`` -- **never empty** repo-relative test files,
* ``reasons`` -- human readable audit trail (sorted, deterministic),
* ``evidence`` -- counters used by the ledger / acceptance report.

Fail-closed classes (each one automatically selects the full suite):

``outside_roots`` ``test_infra`` ``generated_file`` ``new_file``
``deleted_module`` ``non_code_asset`` ``graph_incomplete`` ``dynamic_import``
``cross_repo_ref`` ``mass_change`` ``safety_missing`` ``unknown_change_kind``

The only *non* fail-closed special case is a documentation-only change: it runs
the safety set (>= 1 test, criterion C4), because docs cannot alter behaviour but
the pipeline still needs a real green signal.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import (
    CODE_EXTENSIONS,
    DOC_EXTENSIONS,
    TEST_INFRA_BASENAMES,
    TEST_INFRA_SUFFIXES,
    SelectorConfig,
    is_test_path,
)
from .graph import ImportGraph

FAIL_CLOSED_CLASSES: Tuple[str, ...] = (
    "outside_roots",
    "test_infra",
    "generated_file",
    "new_file",
    "deleted_module",
    "non_code_asset",
    "graph_incomplete",
    "dynamic_import",
    "cross_repo_ref",
    "mass_change",
    "safety_missing",
    "unknown_change_kind",
)


def normalize(paths: Iterable[str], root: Path) -> List[str]:
    out: Set[str] = set()
    root_s = root.resolve().as_posix().rstrip("/")
    for p in paths:
        p = (p or "").strip()
        if not p:
            continue
        p = p.replace("\\", "/")
        if p.startswith(root_s + "/"):
            p = p[len(root_s) + 1 :]
        while p.startswith("./"):
            p = p[2:]
        if p.startswith("/"):
            # absolute path outside the tree -> keep as-is so it trips outside_roots
            out.add(p)
            continue
        out.add(p)
    return sorted(out)


@dataclass
class Selection:
    mode: str
    tests: List[str]
    trigger: Optional[str]
    reasons: List[str]
    changed_files: List[str]
    impacted_modules: List[str]
    all_tests: int
    graph_fingerprint: str
    config_hash: str
    requested_mode: str = "enforcing"
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return (len(self.tests) / self.all_tests) if self.all_tests else 0.0

    @property
    def selection_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.mode.encode())
        h.update(b"\0")
        h.update((self.trigger or "").encode())
        for t in self.tests:
            h.update(t.encode())
            h.update(b"\0")
        return h.hexdigest()

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "trigger": self.trigger,
            "tests": self.tests,
            "selected": len(self.tests),
            "all_tests": self.all_tests,
            "ratio": round(self.ratio, 6),
            "changed_files": self.changed_files,
            "impacted_modules": self.impacted_modules,
            "reasons": self.reasons,
            "graph_fingerprint": self.graph_fingerprint,
            "config_hash": self.config_hash,
            "selection_hash": self.selection_hash,
            "requested_mode": self.requested_mode,
            "extra": self.extra,
        }


class ImpactSelector:
    def __init__(self, graph: ImportGraph, config: SelectorConfig | None = None, root: Path | str | None = None):
        self.graph = graph
        self.config = config or SelectorConfig()
        if root is not None:
            self.root = Path(root).resolve()
        else:
            self.root = Path(".").resolve()
        self._all_tests = graph.tests()
        self._safety_cache: Optional[List[str]] = None
        self._config_hash = hashlib.sha256(
            json.dumps(self.config.as_dict(), sort_keys=True).encode()
        ).hexdigest()

    # -- safety set ---------------------------------------------------------
    def safety_tests(self) -> List[str]:
        """Always-run set: explicit list + globs, with a mechanical fallback.

        The fallback (when neither resolves) picks the K tests with the widest
        dependency reach, i.e. the tests that touch most of the tree.  It is
        computed from the graph, never hand maintained -- which keeps criterion
        C4 ("a docs-only change still selects >= 1 test") true on any tree.
        """
        if self._safety_cache is None:
            picked: Set[str] = set()
            for rel in self.config.safety_tests:
                if rel in self.graph.nodes and self.graph.nodes[rel].is_test:
                    picked.add(rel)
            for pattern in self.config.safety_globs:
                for rel in self._all_tests:
                    if fnmatch.fnmatch(rel, pattern):
                        picked.add(rel)
            if not picked and self.config.safety_reach_topk > 0:
                ranked = sorted(self._all_tests, key=lambda t: (-len(self.graph.test_closure(t)), t))
                picked |= set(ranked[: self.config.safety_reach_topk])
            self._safety_cache = sorted(picked)
        return list(self._safety_cache)

    # -- trigger detection --------------------------------------------------
    def _detect_triggers(
        self, changed: Sequence[str], seeds: Sequence[str], impacted: Set[str]
    ) -> List[Tuple[str, str]]:
        """Return ordered (class, detail) fail-closed triggers."""
        trig: List[Tuple[str, str]] = []
        cfg = self.config

        for rel in changed:
            name = rel.rsplit("/", 1)[-1]
            if rel.startswith("/"):
                trig.append(("outside_roots", f"absolute path outside tree: {rel}"))
                continue
            if ".." in rel.split("/"):
                trig.append(("outside_roots", f"path escapes tree: {rel}"))
                continue
            if name in TEST_INFRA_BASENAMES or any(rel.endswith(s) for s in TEST_INFRA_SUFFIXES):
                trig.append(("test_infra", rel))
            elif is_test_path(rel) and name.startswith("_") and name != "__init__.py":
                trig.append(("test_infra", f"test helper: {rel}"))
            if "/" not in rel and rel.endswith((".cfg", ".ini", ".toml", ".yaml", ".yml")) and rel not in (
                "pytest.ini",
            ):
                trig.append(("unknown_change_kind", f"top level config: {rel}"))

        code_changed = [r for r in changed if r.endswith(CODE_EXTENSIONS)]
        other_changed = [
            r for r in changed if not r.endswith(CODE_EXTENSIONS) and not r.endswith(DOC_EXTENSIONS)
        ]

        for rel in other_changed:
            parts = rel.split("/")[:-1]
            in_package = False
            for i in range(len(parts), 0, -1):
                cand = self.root.joinpath(*parts[:i]) / "__init__.py"
                if cand.is_file():
                    in_package = True
                    break
            if in_package:
                trig.append(("non_code_asset", rel))

        for rel in code_changed:
            node = self.graph.nodes.get(rel)
            if node is not None and not (self.root / rel).is_file():
                importers = sorted(self.graph.importers(rel))
                if importers:
                    trig.append(("deleted_module", f"{rel} deleted but still imported by {len(importers)} files"))
                continue
            if node is None:
                full = self.root / rel
                if full.is_file():
                    trig.append(("new_file", rel))
                elif not full.exists():
                    importers = sorted(self.graph.importers(rel))
                    if importers:
                        trig.append(("deleted_module", f"{rel} still imported by {len(importers)} files"))
                    else:
                        trig.append(("unknown_change_kind", f"unknown path: {rel}"))
                else:  # pragma: no cover - defensive
                    trig.append(("unknown_change_kind", rel))
                continue
            if node.generated:
                trig.append(("generated_file", rel))
            if node.parse_error:
                trig.append(("graph_incomplete", f"{rel}: {node.parse_error}"))
            if node.cross_repo:
                trig.append(("cross_repo_ref", f"{rel} -> {sorted(set(node.cross_repo))[0]}"))

        # graph level incompleteness: a broken module invalidates its own reverse
        # edges, so it is critical *iff* something imports it (or it is the
        # changed file itself).  Isolated unparsable leaves are logged instead.
        parse_error_critical: List[str] = []
        parse_error_leaves: List[str] = []
        for rel in sorted(self.graph.nodes):
            node = self.graph.nodes[rel]
            if not node.parse_error:
                continue
            if self.graph.importers_static(rel) or rel in changed:
                parse_error_critical.append(rel)
            else:
                parse_error_leaves.append(rel)
        if cfg.strict_parse_errors and parse_error_critical:
            trig.append(
                ("graph_incomplete", f"{len(parse_error_critical)} unparsable module(s) with importers")
            )
        if parse_error_leaves and not cfg.parse_error_leaves_tolerated:
            trig.append(("graph_incomplete", f"{len(parse_error_leaves)} unparsable leaf module(s)"))
        self._parse_error_leaves = parse_error_leaves

        if len(changed) > cfg.max_changed_files:
            trig.append(("mass_change", f"{len(changed)} changed files > {cfg.max_changed_files}"))

        for rel in sorted(impacted):
            node = self.graph.nodes.get(rel)
            if node is not None and not (self.root / rel).is_file():
                importers = sorted(self.graph.importers(rel))
                if importers:
                    trig.append(("deleted_module", f"{rel} deleted but still imported by {len(importers)} files"))
                continue
            if node is None:
                continue
            if node.dynamic:
                trig.append(("dynamic_import", rel))
            if node.cross_repo:
                trig.append(("cross_repo_ref", f"{rel} -> {sorted(set(node.cross_repo))[0]}"))

        if not self.safety_tests():
            trig.append(("safety_missing", "no safety test resolved"))

        # dedupe, keep first occurrence order stable
        seen: Set[Tuple[str, str]] = set()
        out: List[Tuple[str, str]] = []
        for t in trig:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    # -- main API -----------------------------------------------------------
    def select(self, changed_files: Iterable[str], mode: str | None = None) -> Selection:
        changed = normalize(changed_files, self.root)
        requested = mode or self.config.mode
        reasons: List[str] = []
        seeds = [c for c in changed if c in self.graph.nodes]
        impacted = self.graph.impact(seeds) if seeds else set()

        if not changed:
            if self.config.empty_change_policy == "full":
                return self._full(changed, impacted, "unknown_change_kind", ["no changed files given"], requested)
            return self._subset(sorted(self.safety_tests()), changed, impacted, ["empty change -> safety set"], requested)

        docs_only = all(c.endswith(DOC_EXTENSIONS) for c in changed)
        if docs_only:
            tests = sorted({*self.safety_tests(), *[c for c in changed if is_test_path(c)]})
            return self._subset(tests, changed, impacted, ["documentation only change -> safety set"], requested)

        triggers = self._detect_triggers(changed, seeds, impacted)
        if triggers:
            classes = sorted({t[0] for t in triggers})
            detail = [f"{cls}:{msg}" for cls, msg in triggers][:20]
            return self._full(changed, impacted, classes[0], detail, requested)

        tests = {c for c in changed if is_test_path(c)}
        for rel in sorted(impacted):
            if self.graph.nodes.get(rel) and self.graph.nodes[rel].is_test:
                tests.add(rel)
        tests |= set(self.safety_tests())
        tests = sorted(tests)

        if not tests:
            # cannot happen: safety set is non-empty (checked above), kept as belt and braces
            return self._full(changed, impacted, "unknown_change_kind", ["empty selection"], requested)

        ratio = len(tests) / len(self._all_tests) if self._all_tests else 1.0
        if ratio > self.config.max_selected_ratio:
            return self._full(
                changed,
                impacted,
                "mass_change",
                [f"selection ratio {ratio:.3f} > {self.config.max_selected_ratio}"],
                requested,
            )
        reasons.append(f"impact closure: {len(impacted)} files")
        reasons.append(f"selected {len(tests)}/{len(self._all_tests)} test files")
        return self._subset(tests, changed, impacted, reasons, requested)

    def _subset(self, tests, changed, impacted, reasons, requested) -> Selection:
        return Selection(
            mode="subset",
            tests=list(tests),
            trigger=None,
            reasons=list(reasons),
            changed_files=list(changed),
            impacted_modules=sorted(impacted),
            all_tests=len(self._all_tests),
            graph_fingerprint=self.graph.fingerprint,
            config_hash=self._config_hash,
            requested_mode=requested,
            extra={"safety": self.safety_tests(), "parse_error_leaves": getattr(self, "_parse_error_leaves", [])},
        )

    def _full(self, changed, impacted, trigger, reasons, requested) -> Selection:
        return Selection(
            mode="full",
            tests=list(self._all_tests),
            trigger=trigger,
            reasons=[f"fail-closed[{trigger}]", *reasons],
            changed_files=list(changed),
            impacted_modules=sorted(impacted),
            all_tests=len(self._all_tests),
            graph_fingerprint=self.graph.fingerprint,
            config_hash=self._config_hash,
            requested_mode=requested,
            extra={"safety": self.safety_tests(), "parse_error_leaves": getattr(self, "_parse_error_leaves", [])},
        )

    # -- rollout ------------------------------------------------------------
    def effective_plan(self, selection: Selection) -> Dict[str, object]:
        """Map a selection to the *execution* plan under the configured mode.

        * ``off``       -> run everything (selector purely observational)
        * ``shadow``    -> run everything, ledger records the subset that *would*
                           have run (this is how the >=200 real writes sample is
                           collected before enforcing)
        * ``enforcing`` -> run ``selection.tests`` (full when fail-closed)
        """
        mode = selection.requested_mode
        if mode == "off":
            run = list(self._all_tests)
            executed = "full"
        elif mode == "shadow":
            run = list(self._all_tests)
            executed = "full"
        else:
            run = list(selection.tests)
            executed = selection.mode
        return {
            "requested_mode": mode,
            "executed": executed,
            "tests": run,
            "subset_size": len(selection.tests),
            "full_size": len(self._all_tests),
            "saved_ratio": round(1 - len(selection.tests) / len(self._all_tests), 6) if self._all_tests else 0.0,
        }