"""DAG runner: bind ports, execute stages, hash-chain the record.

The runner's job is to make interface incompatibility *impossible to miss*:

* **static checks before execution** -- adapter exists, every required port is
  bound, no binding targets an undeclared port, and producer/consumer port tags
  agree.  A tag mismatch (report fed into a scalar port) is a spec error, so a
  pipeline cannot "sort of work".
* **fail-closed execution** -- an adapter raising :class:`AdapterUnavailable`
  yields ``WITHHELD``, any other exception yields ``WITHHELD`` with the error
  text; downstream nodes that depend on it are ``SKIPPED`` rather than run on a
  stale or zero value.
* **honest evidence roll-up** -- the run's evidence level is the *weakest* node
  level, and a stage that ran through its declared fallback is recorded as
  ``MOCK`` whatever the adapter claims.  A run is therefore never stronger than
  its weakest link.
* **replayable record** -- each node keeps its resolved inputs so the same stage
  can be re-timed standalone (:mod:`tools.toolchain_integration.perf`), and the
  per-node digests chain into one run digest.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from . import provenance
from .registry import (
    Adapter,
    AdapterError,
    AdapterUnavailable,
    ExecutionContext,
    PortValue,
    get as get_adapter,
)
from .schema import PipelineSpec, SpecError, weakest

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED = "SKIPPED"
STATUS_DEGRADED = "DEGRADED"


class PipelineError(RuntimeError):
    """Raised when a pipeline cannot be built or its required stages fail."""


@dataclass
class NodeRecord:
    """What actually happened in one node."""

    node_id: str
    adapter: str
    status: str
    evidence: str
    wall_s: float = 0.0
    input_digest: str = provenance.EMPTY_DIGEST
    output_digest: str = provenance.EMPTY_DIGEST
    chain_digest: str = provenance.EMPTY_DIGEST
    downstream: Tuple[str, ...] = ()
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node_id,
            "adapter": self.adapter,
            "status": self.status,
            "evidence": self.evidence,
            "wall_s": self.wall_s,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "chain_digest": self.chain_digest,
            "outputs": self.outputs,
            "error": self.error,
            "notes": list(self.notes),
        }


@dataclass
class PipelineRun:
    """Result of one execution of a pipeline spec."""

    spec_name: str
    spec_digest: str
    records: List[NodeRecord]
    context: ExecutionContext
    values: Dict[str, Dict[str, PortValue]]
    inputs_used: Dict[str, Dict[str, PortValue]]
    wall_s: float = 0.0

    # ------------------------------------------------------------------ status
    @property
    def status(self) -> str:
        if any(r.status == STATUS_FAIL for r in self.records):
            return STATUS_FAIL
        if any(r.status == STATUS_SKIPPED for r in self.records):
            return STATUS_FAIL
        if self.records and all(r.status == STATUS_OK for r in self.records):
            return STATUS_OK
        return STATUS_DEGRADED

    @property
    def evidence(self) -> str:
        if not self.records:
            return "WITHHELD"
        return weakest(*[r.evidence for r in self.records])

    @property
    def chain_digest(self) -> str:
        if not self.records:
            return provenance.EMPTY_DIGEST
        return self.records[-1].chain_digest

    def record(self, node_id: str) -> NodeRecord:
        for r in self.records:
            if r.node_id == node_id:
                return r
        raise KeyError(node_id)

    def value(self, ref: str) -> PortValue:
        """Fetch ``node.port`` from the run (raises if missing)."""
        node, _, port = ref.rpartition(".")
        try:
            return self.values[node][port]
        except KeyError as exc:
            raise PipelineError(f"no value at {ref!r}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec_name,
            "spec_digest": self.spec_digest,
            "status": self.status,
            "evidence": self.evidence,
            "chain_digest": self.chain_digest,
            "wall_s": self.wall_s,
            "nodes": [r.to_dict() for r in self.records],
        }


# ---------------------------------------------------------------------------
# build / validate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """Static, validated plan: adapters resolved and every edge type-checked."""

    spec: PipelineSpec
    adapters: Dict[str, Adapter]
    dependencies: Dict[str, Tuple[str, ...]]

    def dependents(self, node_id: str) -> Tuple[str, ...]:
        return tuple(
            nid for nid, deps in self.dependencies.items() if node_id in deps
        )


def build_plan(spec: PipelineSpec) -> Plan:
    adapters: Dict[str, Adapter] = {}
    deps: Dict[str, Tuple[str, ...]] = {}
    for node in spec.nodes:
        try:
            adapter = get_adapter(node.adapter)
        except SpecError as exc:
            raise SpecError(f"node {node.node_id!r}: {exc}") from exc
        adapters[node.node_id] = adapter

        declared = {p.name: p for p in adapter.inputs}
        if not adapter.accepts_any_inputs:
            unknown = sorted(set(node.inputs) - set(declared))
            if unknown:
                raise SpecError(
                    f"node {node.node_id!r}: adapter {adapter.kind!r} has no input "
                    f"port(s) {unknown}; declared {sorted(declared)}"
                )
        for port in adapter.inputs:
            if port.required and port.name not in node.inputs:
                raise SpecError(
                    f"node {node.node_id!r}: required input {port.name!r} of adapter "
                    f"{adapter.kind!r} is not bound"
                )

        bound_deps: List[str] = []
        for port_name, binding in node.inputs.items():
            if binding.kind != "ref":
                continue
            bound_deps.append(binding.node)
            producer = adapters.get(binding.node)
            if producer is None:  # cannot happen after spec validation, but be strict
                raise SpecError(
                    f"node {node.node_id!r}: input {port_name!r} references unknown node "
                    f"{binding.node!r}"
                )
            try:
                out_port = producer.output_port(binding.port)
            except SpecError as exc:
                raise SpecError(f"node {node.node_id!r}: {exc}") from exc
            declared_port: Optional[Any] = declared.get(port_name)
            if declared_port is not None and declared_port.tag != out_port.tag:
                raise SpecError(
                    f"node {node.node_id!r}: port {port_name!r} expects tag "
                    f"{declared_port.tag!r} but {binding.node}.{binding.port} produces "
                    f"{out_port.tag!r}"
                )
        deps[node.node_id] = tuple(dict.fromkeys(bound_deps))
    return Plan(spec=spec, adapters=adapters, dependencies=deps)


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def output_summary(outputs: Mapping[str, PortValue]) -> Dict[str, Any]:
    """JSON-able record of a node's outputs.

    Scalar / flag / report values are stored verbatim (they *are* the evidence a
    reader wants); heavier values (``series``, ``field``, ``callable``) are stored as
    a digest, so the artifact stays small without losing reproducibility.
    """
    summary: Dict[str, Any] = {}
    for name, pv in outputs.items():
        if pv.tag in ("scalar", "flag", "report"):
            value = pv.value
            if isinstance(value, np.ndarray):  # pragma: no cover - defensive
                value = value.tolist()
            summary[name] = {"tag": pv.tag, "value": value}
        else:
            summary[name] = {"tag": pv.tag, "digest": provenance.digest(pv.value)}
    return summary


def _resolve_inputs(node, values, plan: Plan) -> Tuple[Dict[str, PortValue], List[str]]:
    """Bind one node's ports; return the bound values and *unresolved required* ports."""
    resolved: Dict[str, PortValue] = {}
    adapter = plan.adapters[node.node_id]
    required = {p.name for p in adapter.inputs if p.required}
    unresolved: List[str] = []
    for port_name, binding in node.inputs.items():
        if binding.kind == "literal":
            if adapter.accepts_any_inputs:
                tag = "scalar" if not isinstance(binding.value, (list, tuple)) else "series"
            else:
                try:
                    tag = adapter.input_port(port_name).tag
                except SpecError:
                    tag = "scalar"
            resolved[port_name] = PortValue(tag, binding.value)
            continue
        if binding.kind == "absent":
            continue
        produced = values.get(binding.node, {})
        if binding.port in produced:
            resolved[port_name] = produced[binding.port]
        elif port_name in required:
            unresolved.append(f"{binding.node}.{binding.port}")
    return resolved, unresolved


def run_pipeline(
    spec: PipelineSpec,
    ctx: Optional[ExecutionContext] = None,
    *,
    plan: Optional[Plan] = None,
) -> PipelineRun:
    """Execute ``spec`` once and return a replayable, hash-chained record."""
    ctx = ctx or ExecutionContext()
    plan = plan or build_plan(spec)
    values: Dict[str, Dict[str, PortValue]] = {}
    inputs_used: Dict[str, Dict[str, PortValue]] = {}
    records: List[NodeRecord] = []
    prev_chain = provenance.EMPTY_DIGEST
    status: Dict[str, str] = {}
    t_run = time.perf_counter()

    for node in spec.nodes:
        adapter = plan.adapters[node.node_id]
        dep_statuses = [status.get(d, STATUS_SKIPPED) for d in plan.dependencies[node.node_id]]
        blocked = [d for d in dep_statuses if d not in (STATUS_OK, STATUS_DEGRADED)]
        if blocked:
            rec = NodeRecord(
                node_id=node.node_id,
                adapter=adapter.kind,
                status=STATUS_SKIPPED,
                evidence="WITHHELD",
                downstream=plan.dependents(node.node_id),
                error="upstream stage did not produce a value",
            )
            rec.input_digest = provenance.EMPTY_DIGEST
            rec.output_digest = provenance.EMPTY_DIGEST
            rec.chain_digest = provenance.chain(prev_chain, node.node_id,
                                                rec.input_digest, rec.output_digest)
            prev_chain = rec.chain_digest
            records.append(rec)
            status[node.node_id] = STATUS_SKIPPED
            continue

        resolved, unresolved = _resolve_inputs(node, values, plan)
        inputs_used[node.node_id] = resolved
        input_digest = provenance.digest(
            {k: (v.tag, v.value) for k, v in resolved.items()}
        )
        if unresolved:
            # a required port whose producer degraded is a hard failure: the node is
            # never run on a missing or substituted value.
            rec = NodeRecord(
                node_id=node.node_id, adapter=adapter.kind,
                status=STATUS_FAIL, evidence="WITHHELD",
                downstream=plan.dependents(node.node_id),
                error=f"unresolved required inputs: {sorted(unresolved)}",
            )
            rec.input_digest = input_digest
            rec.chain_digest = provenance.chain(prev_chain, node.node_id,
                                                input_digest, provenance.EMPTY_DIGEST)
            prev_chain = rec.chain_digest
            records.append(rec)
            status[node.node_id] = STATUS_FAIL
            continue

        t0 = time.perf_counter()
        try:
            outputs = adapter.run(ctx, node.params, resolved)
            wall = time.perf_counter() - t0
        except AdapterUnavailable as exc:
            wall = time.perf_counter() - t0
            rec = NodeRecord(
                node_id=node.node_id, adapter=adapter.kind,
                status=STATUS_DEGRADED if not node.required else STATUS_FAIL,
                evidence="WITHHELD", wall_s=wall,
                downstream=plan.dependents(node.node_id),
                error=f"unavailable: {exc}",
            )
            rec.input_digest = input_digest
            rec.chain_digest = provenance.chain(prev_chain, node.node_id,
                                                input_digest, provenance.EMPTY_DIGEST)
            prev_chain = rec.chain_digest
            records.append(rec)
            status[node.node_id] = rec.status
            continue
        except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not raised
            wall = time.perf_counter() - t0
            rec = NodeRecord(
                node_id=node.node_id, adapter=adapter.kind,
                status=STATUS_DEGRADED if not node.required else STATUS_FAIL,
                evidence="WITHHELD", wall_s=wall,
                downstream=plan.dependents(node.node_id),
                error=f"{type(exc).__name__}: {exc}",
                notes=[traceback.format_exc(limit=3)],
            )
            rec.input_digest = input_digest
            rec.chain_digest = provenance.chain(prev_chain, node.node_id,
                                                input_digest, provenance.EMPTY_DIGEST)
            prev_chain = rec.chain_digest
            records.append(rec)
            status[node.node_id] = rec.status
            continue

        if not isinstance(outputs, Mapping):
            raise PipelineError(
                f"adapter {adapter.kind!r} returned {type(outputs).__name__}, expected mapping"
            )
        for port_name, pv in outputs.items():
            if not isinstance(pv, PortValue):
                raise PipelineError(
                    f"adapter {adapter.kind!r} port {port_name!r}: expected PortValue, "
                    f"got {type(pv).__name__}"
                )
        values[node.node_id] = dict(outputs)

        evidence = adapter.evidence
        notes: List[str] = []
        ext = outputs.get("report")
        if adapter.kind == "external" and ext is not None and isinstance(ext.value, Mapping):
            if ext.value.get("substituted"):
                evidence = "MOCK"
                notes.append(
                    "stage ran through its declared fallback ("
                    f"{ext.value.get('import_error', 'unavailable')})"
                )
        out_digest = provenance.digest({k: (v.tag, v.value) for k, v in outputs.items()})
        rec = NodeRecord(
            node_id=node.node_id,
            adapter=adapter.kind,
            status=STATUS_OK,
            evidence=evidence,
            wall_s=wall,
            input_digest=input_digest,
            output_digest=out_digest,
            downstream=plan.dependents(node.node_id),
            outputs=output_summary(outputs),
            notes=notes,
        )
        rec.chain_digest = provenance.chain(prev_chain, node.node_id, input_digest, out_digest)
        prev_chain = rec.chain_digest
        records.append(rec)
        status[node.node_id] = STATUS_OK

    return PipelineRun(
        spec_name=spec.name,
        spec_digest=provenance.digest(spec.digest_payload()),
        records=records,
        context=ctx,
        values=values,
        inputs_used=inputs_used,
        wall_s=time.perf_counter() - t_run,
    )


def run_spec(spec: PipelineSpec, **kwargs: Any) -> PipelineRun:
    run = run_pipeline(spec, **kwargs)
    if run.status == STATUS_FAIL:
        detail = [f"{r.node_id}: {r.error}" for r in run.records if r.status != STATUS_OK]
        raise PipelineError(f"pipeline {spec.name!r} failed: {detail}")
    return run