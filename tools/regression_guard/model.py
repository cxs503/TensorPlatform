"""Domain-neutral data model for the cross-mission regression guard.

The guard answers one question mechanically: *a code change landed — which
acceptance tests of which missions/campaign nodes must be re-run, and did any
of them regress?*

Iron law of this repository (TensorPlatform = common mechanism only): **no
domain vocabulary is hard-coded here**.  Every fact about what code belongs to
which capability/feature/task is *injected* through a task ledger file
(JSON produced by the platform) or through ``feature_ledger`` rows.  The only
structural knowledge in this module is the shape of the records.

Record shapes (task ledger, ``schema_version`` = ``LEDGER_SCHEMA``)::

    artifacts: [{artifact_id, kind, paths[], symbols?[], description?}]
    features:  [{feature_id, artifacts[], depends_on?[]}]
    tasks:     [{task_id, feature_ids[], tests[], campaign_node?, title?}]
    nodes:     [{node_id, depends_on?[]}]

``tasks[].tests`` entries are pytest node ids (``path::test_name``) or plain
file paths; both are valid pytest selectors.  ``nodes[]`` carries the campaign
DAG (``campaign_nodes.dependencies``) so that the guard can report *downstream
nodes at risk* without re-running their tests (keeps the false-trigger rate
low while still surfacing blast radius).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

LEDGER_SCHEMA = "1.0"
REPORT_SCHEMA = "1.1"
GUARD_VERSION = "1.0.0"

CHANGE_KINDS: Tuple[str, ...] = ("modified", "added", "deleted", "renamed")

# Verdicts ------------------------------------------------------------------
VERDICT_NO_DEPENDENTS = "no_dependents"      # change touched nothing we track
VERDICT_PASS = "pass"                        # tests re-run, all green / no delta
VERDICT_REGRESSION = "regression"            # >=1 pass->fail transition
VERDICT_FAIL = "fail"                        # >=1 *new* failure (not in baseline)
VERDICT_PASS_WITH_KNOWN = "pass_with_known_failures"  # only baseline-red tests re-ran red
VERDICT_ERROR = "error"                      # guard could not complete

EXIT_CODES = {
    VERDICT_NO_DEPENDENTS: 0,
    VERDICT_PASS: 0,
    VERDICT_REGRESSION: 1,
    VERDICT_FAIL: 1,
    VERDICT_PASS_WITH_KNOWN: 0,
    VERDICT_ERROR: 2,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """Stable serialisation used for every digest in the guard."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _dedupe(seq: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# change manifest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Change:
    """One touched path (repo-relative POSIX form).

    ``symbols`` optionally narrows the change to specific top-level names of
    the file (function/class/constant).  When empty the whole module is
    considered dirty, which is the fail-safe default.
    """

    path: str
    kind: str = "modified"
    symbols: Tuple[str, ...] = ()
    change_class: str = "unknown"   # resolved by impact analysis from ledger

    def __post_init__(self) -> None:
        if self.kind not in CHANGE_KINDS:
            raise ValueError(f"unknown change kind {self.kind!r}; expected {CHANGE_KINDS}")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.path}"

    def to_dict(self) -> Dict[str, Any]:
        d = {"path": self.path, "kind": self.kind, "change_class": self.change_class}
        if self.symbols:
            d["symbols"] = list(self.symbols)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Change":
        return Change(
            path=str(d["path"]).replace("\\", "/"),
            kind=str(d.get("kind", "modified")),
            symbols=tuple(d.get("symbols") or ()),
            change_class=str(d.get("change_class", "unknown")),
        )


@dataclass
class ChangeManifest:
    """What changed, and the revisions it was observed between."""

    changes: List[Change] = field(default_factory=list)
    base_rev: str = ""
    head_rev: str = ""

    def __post_init__(self) -> None:
        self.changes = sorted(self.changes, key=lambda c: (c.path, c.kind))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_rev": self.base_rev,
            "head_rev": self.head_rev,
            "changes": [c.to_dict() for c in self.changes],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ChangeManifest":
        return ChangeManifest(
            changes=[Change.from_dict(c) for c in d.get("changes", [])],
            base_rev=str(d.get("base_rev", "")),
            head_rev=str(d.get("head_rev", "")),
        )

    @classmethod
    def load(cls, path: Path | str) -> "ChangeManifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def digest(self) -> str:
        return sha256_obj(self.to_dict())


# ---------------------------------------------------------------------------
# ledger records
# ---------------------------------------------------------------------------
@dataclass
class Artifact:
    """A trackable code artifact: one or more repo paths plus optional symbols."""

    artifact_id: str
    kind: str = "code"
    paths: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    description: str = ""

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Artifact":
        return Artifact(
            artifact_id=str(d["artifact_id"]),
            kind=str(d.get("kind", "code")),
            paths=[str(p).replace("\\", "/") for p in d.get("paths", [])],
            symbols=[str(s) for s in d.get("symbols", [])],
            description=str(d.get("description", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "paths": list(self.paths),
            "symbols": list(self.symbols),
            "description": self.description,
        }


@dataclass
class Feature:
    """A platform capability: a named bundle of artifacts plus its upstream deps."""

    feature_id: str
    artifacts: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Feature":
        return Feature(
            feature_id=str(d["feature_id"]),
            artifacts=list(d.get("artifacts", [])),
            depends_on=list(d.get("depends_on", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "artifacts": list(self.artifacts),
            "depends_on": list(self.depends_on),
        }


@dataclass
class Task:
    """A mission-level acceptance task with the tests that certify it."""

    task_id: str
    feature_ids: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    campaign_node: str = ""
    title: str = ""

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Task":
        return Task(
            task_id=str(d["task_id"]),
            feature_ids=list(d.get("feature_ids", [])),
            tests=list(d.get("tests", [])),
            campaign_node=str(d.get("campaign_node", "")),
            title=str(d.get("title", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "feature_ids": list(self.feature_ids),
            "tests": list(self.tests),
            "campaign_node": self.campaign_node,
            "title": self.title,
        }


@dataclass
class CampaignNode:
    node_id: str
    depends_on: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CampaignNode":
        return CampaignNode(node_id=str(d["node_id"]), depends_on=list(d.get("depends_on", [])))

    def to_dict(self) -> Dict[str, Any]:
        return {"node_id": self.node_id, "depends_on": list(self.depends_on)}


@dataclass
class TaskLedger:
    """Injected dependency ledger: artifacts -> features -> tasks, plus the DAG."""

    artifacts: List[Artifact] = field(default_factory=list)
    features: List[Feature] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    nodes: List[CampaignNode] = field(default_factory=list)
    schema_version: str = LEDGER_SCHEMA
    source: str = ""

    # -- construction ------------------------------------------------------
    @staticmethod
    def from_dict(d: Dict[str, Any], source: str = "") -> "TaskLedger":
        return TaskLedger(
            artifacts=[Artifact.from_dict(x) for x in d.get("artifacts", [])],
            features=[Feature.from_dict(x) for x in d.get("features", [])],
            tasks=[Task.from_dict(x) for x in d.get("tasks", [])],
            nodes=[CampaignNode.from_dict(x) for x in d.get("nodes", [])],
            schema_version=str(d.get("schema_version", LEDGER_SCHEMA)),
            source=source,
        )

    @classmethod
    def load(cls, path: Path | str) -> "TaskLedger":
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")), source=str(p))

    @classmethod
    def from_feature_ledger_rows(cls, rows: Sequence[Dict[str, Any]], source: str = "") -> "TaskLedger":
        """Translate ``feature_ledger`` rows into a task ledger.

        The platform table stores ``(ledger_id, item_type, payload)`` where
        ``payload`` is a JSON document.  We accept the same record shapes as
        the JSON ledger; unknown ``item_type`` values are ignored on purpose
        (forward compatible, never guesses).
        """
        buckets: Dict[str, List[Dict[str, Any]]] = {
            "artifact": [], "feature": [], "task": [], "node": [], "campaign_node": [],
        }
        for row in rows:
            item_type = str(row.get("item_type") or row.get("type") or "").strip().lower()
            payload = row.get("payload", row.get("data", row))
            if isinstance(payload, str):
                payload = json.loads(payload)
            if item_type in buckets:
                buckets[item_type].append(payload)
        return cls.from_dict(
            {
                "artifacts": buckets["artifact"],
                "features": buckets["feature"],
                "tasks": buckets["task"],
                "nodes": buckets["node"] + buckets["campaign_node"],
            },
            source=source,
        )

    # -- accessors ---------------------------------------------------------
    def artifact(self, artifact_id: str) -> Optional[Artifact]:
        for a in self.artifacts:
            if a.artifact_id == artifact_id:
                return a
        return None

    def feature(self, feature_id: str) -> Optional[Feature]:
        for f in self.features:
            if f.feature_id == feature_id:
                return f
        return None

    def task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def feature_ids(self) -> FrozenSet[str]:
        return frozenset(f.feature_id for f in self.features)

    def downstream_nodes(self, node_ids: Iterable[str]) -> List[str]:
        """Campaign nodes that (transitively) depend on any of ``node_ids``."""
        seeds = set(node_ids)
        rev: Dict[str, List[str]] = {}
        for n in self.nodes:
            for dep in n.depends_on:
                rev.setdefault(dep, []).append(n.node_id)
        out: List[str] = []
        frontier = sorted(seeds)
        seen = set(seeds)
        while frontier:
            cur = frontier.pop()
            for nxt in sorted(rev.get(cur, [])):
                if nxt in seen:
                    continue
                seen.add(nxt)
                out.append(nxt)
                frontier.append(nxt)
        return sorted(set(out) - seeds)

    def validate(self) -> List[str]:
        """Structural self-check; returns a list of problems (empty = ok)."""
        problems: List[str] = []
        feat = self.feature_ids()
        arts = {a.artifact_id for a in self.artifacts}
        for f in self.features:
            for a in f.artifacts:
                if a not in arts:
                    problems.append(f"feature {f.feature_id}: unknown artifact {a}")
            for dep in f.depends_on:
                if dep not in feat and dep not in arts:
                    problems.append(f"feature {f.feature_id}: unknown dependency {dep}")
        nodes = {n.node_id for n in self.nodes}
        for t in self.tasks:
            if not t.tests:
                problems.append(f"task {t.task_id}: no tests declared")
            for fid in t.feature_ids:
                if fid not in feat and fid not in arts:
                    problems.append(f"task {t.task_id}: unknown feature {fid}")
            if t.campaign_node and nodes and t.campaign_node not in nodes:
                problems.append(f"task {t.task_id}: unknown campaign node {t.campaign_node}")
        for n in self.nodes:
            for dep in n.depends_on:
                if dep not in nodes:
                    problems.append(f"node {n.node_id}: unknown dependency {dep}")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "features": [f.to_dict() for f in self.features],
            "tasks": [t.to_dict() for t in self.tasks],
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @property
    def digest(self) -> str:
        return sha256_obj(self.to_dict())