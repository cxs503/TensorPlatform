"""Declarative pipeline specification (zero domain semantics).

A *pipeline* is data: an ordered-ish set of nodes, each naming an adapter
(:mod:`tools.toolchain_integration.registry`) plus parameters, and binding its
input ports either to literals or to ``{"from": "node_id.port"}`` references.

Nothing in this module knows what a stage *means*; stage semantics live in the
adapter implementation and stage content (parameters, thresholds, module paths)
lives in the JSON spec file injected by the caller.  That split is what lets the
same harness validate any toolchain without importing a domain word.

Validation is fail-closed at load time (before any node runs):

* node ids are non-empty and unique;
* every ``from`` reference names a port that a *declared* node advertises;
* the graph is acyclic (checked here so a spec error never surfaces mid-run);
* optional ports are marked ``{"optional": true}`` and are the only ones allowed
  to resolve to nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

SCHEMA_VERSION = "tp.toolchain.pipeline/v1"

#: Evidence levels, ordered from strongest to weakest evidence.
EVIDENCE_LEVELS: Tuple[str, ...] = (
    "REAL",
    "MOCK",
    "STUB",
    "WITHHELD",
    "NOT_SUPPORTED",
)
_EVIDENCE_RANK = {lvl: i for i, lvl in enumerate(EVIDENCE_LEVELS)}


class SpecError(ValueError):
    """Raised when a pipeline spec is malformed or references a missing port."""


def weakest(*levels: str) -> str:
    """Roll several evidence levels up to the weakest one (fail-closed)."""
    rank = -1
    out = "REAL"
    for lvl in levels:
        if lvl not in _EVIDENCE_RANK:
            raise SpecError(f"unknown evidence level: {lvl!r}")
        if _EVIDENCE_RANK[lvl] > rank:
            rank, out = _EVIDENCE_RANK[lvl], lvl
    return out


@dataclass(frozen=True)
class Binding:
    """A node input: either a literal value, a reference, or absent (optional)."""

    kind: str                      # "literal" | "ref" | "absent"
    value: Any = None
    node: str = ""
    port: str = ""
    optional: bool = False

    def to_dict(self) -> Dict[str, Any]:
        if self.kind == "ref":
            return {"from": f"{self.node}.{self.port}", "optional": self.optional}
        if self.kind == "literal":
            return {"value": self.value, "optional": self.optional}
        return {"absent": True, "optional": self.optional}

    @classmethod
    def parse(cls, raw: Any) -> "Binding":
        if isinstance(raw, Mapping):
            optional = bool(raw.get("optional", False))
            if "from" in raw:
                ref = raw["from"]
                if not isinstance(ref, str) or "." not in ref:
                    raise SpecError(f"'from' must look like 'node.port', got {ref!r}")
                node, _, port = ref.rpartition(".")
                if not node or not port:
                    raise SpecError(f"'from' must look like 'node.port', got {ref!r}")
                return cls(kind="ref", node=node, port=port, optional=optional)
            if "value" in raw:
                return cls(kind="literal", value=raw["value"], optional=optional)
            raise SpecError(f"binding dict needs 'from' or 'value': {raw!r}")
        return cls(kind="literal", value=raw)


@dataclass(frozen=True)
class NodeSpec:
    """One stage of the pipeline."""

    node_id: str
    adapter: str
    params: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Binding] = field(default_factory=dict)
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "adapter": self.adapter,
            "params": self.params,
            "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
            "required": self.required,
        }

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "NodeSpec":
        node_id = raw.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise SpecError(f"node needs a non-empty string 'id': {raw!r}")
        adapter = raw.get("adapter")
        if not isinstance(adapter, str) or not adapter.strip():
            raise SpecError(f"node {node_id!r} needs a non-empty 'adapter'")
        params = raw.get("params", {})
        if not isinstance(params, Mapping):
            raise SpecError(f"node {node_id!r}: 'params' must be an object")
        inputs_raw = raw.get("inputs", {})
        if not isinstance(inputs_raw, Mapping):
            raise SpecError(f"node {node_id!r}: 'inputs' must be an object")
        inputs = {str(k): Binding.parse(v) for k, v in inputs_raw.items()}
        return cls(
            node_id=node_id,
            adapter=adapter,
            params=dict(params),
            inputs=inputs,
            required=bool(raw.get("required", True)),
        )


@dataclass(frozen=True)
class PipelineSpec:
    """A whole pipeline plus its provenance metadata."""

    name: str
    nodes: Tuple[NodeSpec, ...]
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA_VERSION

    # ------------------------------------------------------------------ parse
    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PipelineSpec":
        if not isinstance(raw, Mapping):
            raise SpecError("pipeline spec must be a JSON object")
        schema = raw.get("schema", SCHEMA_VERSION)
        if schema != SCHEMA_VERSION:
            raise SpecError(f"unsupported spec schema {schema!r} (want {SCHEMA_VERSION!r})")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SpecError("pipeline spec needs a non-empty 'name'")
        nodes_raw = raw.get("nodes")
        if not isinstance(nodes_raw, Sequence) or not nodes_raw:
            raise SpecError("pipeline spec needs a non-empty 'nodes' array")
        nodes = tuple(NodeSpec.parse(n) for n in nodes_raw)
        spec = cls(
            name=name,
            nodes=nodes,
            description=str(raw.get("description", "")),
            metadata=dict(raw.get("metadata", {})),
            schema=schema,
        )
        spec.validate()
        return spec

    @classmethod
    def load(cls, path: str | Path) -> "PipelineSpec":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # --------------------------------------------------------------- validate
    def node(self, node_id: str) -> NodeSpec:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        raise SpecError(f"unknown node id: {node_id!r}")

    def validate(self) -> None:
        ids: List[str] = []
        for n in self.nodes:
            if n.node_id in ids:
                raise SpecError(f"duplicate node id: {n.node_id!r}")
            ids.append(n.node_id)
        index = {nid: i for i, nid in enumerate(ids)}
        for pos, n in enumerate(self.nodes):
            for port, binding in n.inputs.items():
                if binding.kind != "ref":
                    continue
                if binding.node not in index:
                    raise SpecError(
                        f"node {n.node_id!r} input {port!r} references unknown node "
                        f"{binding.node!r}"
                    )
                if index[binding.node] >= pos:
                    raise SpecError(
                        f"node {n.node_id!r} input {port!r} references {binding.node!r} "
                        "which is not declared earlier (spec must be topologically ordered)"
                    )
        # acyclicity follows from the ordering rule above; state it explicitly
        self.topological_order()

    def topological_order(self) -> List[str]:
        """Dependency order, preferring declaration order; raise on a cycle.

        Declaration order is already required to respect dependencies, so for a
        valid spec this returns the declared sequence unchanged -- the method
        exists to make the cycle case detectable rather than to reorder anything.
        """
        deps: Dict[str, set] = {
            n.node_id: {b.node for b in n.inputs.values() if b.kind == "ref"}
            for n in self.nodes
        }
        order: List[str] = []
        remaining = [n.node_id for n in self.nodes]
        while remaining:
            progressed = False
            for nid in list(remaining):
                if not (deps[nid] - set(order)):
                    order.append(nid)
                    remaining.remove(nid)
                    progressed = True
            if not progressed:
                raise SpecError(
                    f"pipeline has a dependency cycle among {sorted(remaining)}"
                )
        return order

    # ----------------------------------------------------------------- export
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    def digest_payload(self) -> Dict[str, Any]:
        """Structural content only (execution timing excluded by construction)."""
        return {"name": self.name, "nodes": [n.to_dict() for n in self.nodes]}