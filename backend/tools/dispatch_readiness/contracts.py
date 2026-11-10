"""Contract loading and structural (zero semantic) contract inference.

Two sources of contracts, in decreasing priority:

1. ``declared`` — an explicit ``input_contract`` / ``output_contract`` record
   injected through configuration or through the node metadata payload. This is
   the only path that can carry units / field / version expectations.
2. ``inferred`` — derived from *structure only*: the node's declared upstream
   set (``dependencies``) and its node type. Gate/checkpoint nodes need their
   upstream set to be terminally successful (``terminal_success``); task nodes
   only need the upstream artefact to be *declared*, i.e. an upstream task must
   exist (``declared``). The task-node default mirrors the platform's documented
   dispatch policy — the dispatcher treats ``{done, in-flight}`` as ready, i.e.
   contract-parallel dispatch — and is therefore an a-priori choice, not a
   threshold tuned on backtest results.

Nothing here inspects node names, goals or physics wording; the inference is a
pure function of the structural fields, which keeps the mechanism domain-free.
"""

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .models import (
    MODE_DECLARED,
    MODE_DELIVERED,
    MODE_TERMINAL_SUCCESS,
    ArtifactRequirement,
    InputContract,
    OutputContract,
)

GATE_NODE_TYPES = frozenset({"gate", "checkpoint"})


def _requirements_for(upstream_keys: Iterable[str], node_type: str) -> List[ArtifactRequirement]:
    mode = MODE_TERMINAL_SUCCESS if str(node_type) in GATE_NODE_TYPES else MODE_DECLARED
    return [ArtifactRequirement(upstream=str(key), mode=mode) for key in upstream_keys]


def normalize_upstream(node: Mapping[str, Any]) -> List[str]:
    """Return the declared upstream node keys of ``node`` (structure only)."""
    raw = node.get("dependencies") or node.get("depends_on") or ()
    if isinstance(raw, str):
        raw = [p for p in raw.replace(";", ",").split(",") if p.strip()]
    keys = [str(k).strip() for k in raw if str(k).strip() not in ("", "None", "null")]
    return keys


def infer_contract(node: Mapping[str, Any]) -> InputContract:
    """Build an input contract from structural fields only.

    ``node`` must carry ``key``/``id``, ``node_type`` and ``dependencies``.
    A node without declared upstreams gets an empty requirement set, i.e. the
    gate trivially answers READY (fail-open for unknown topology, fail-closed
    once a contract exists).
    """
    key = str(node.get("key", node.get("id", "")))
    upstream = normalize_upstream(node)
    node_type = str(node.get("node_type", "task"))
    return InputContract(
        node=key,
        requirements=tuple(_requirements_for(upstream, node_type)),
        version="1.0",
        source="inferred",
    )


def contract_from_metadata(node: Mapping[str, Any]) -> Optional[InputContract]:
    """Extract a declared contract from the node payload, if any."""
    key = str(node.get("key", node.get("id", "")))
    raw = node.get("input_contract")
    if raw is None:
        metadata = node.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError):
                metadata = {}
        if isinstance(metadata, Mapping):
            raw = metadata.get("input_contract")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, Mapping):
        return None
    return InputContract.from_dict(key, dict(raw), source="declared")


def output_contract_from_metadata(node: Mapping[str, Any]) -> Optional[OutputContract]:
    key = str(node.get("key", node.get("id", "")))
    raw = node.get("output_contract")
    if raw is None:
        metadata = node.get("metadata") or {}
        if isinstance(metadata, Mapping):
            raw = metadata.get("output_contract")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, Mapping):
        return None
    return OutputContract.from_dict(key, dict(raw))


def load_contracts(path: Optional[str] = None) -> Dict[str, InputContract]:
    """Load a contract table from JSON.

    Degrades to an empty mapping (identity gate) when the file is absent or
    unreadable — a missing configuration must never break dispatch.

    Accepted shapes::

        {"contracts": {"<node key>": {"requires": [...]}}}
        {"<node key>": {"requires": [...]}}
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    table = payload.get("contracts", payload)
    if not isinstance(table, Mapping):
        return {}
    out: Dict[str, InputContract] = {}
    for key, raw in table.items():
        if not isinstance(raw, Mapping):
            continue
        out[str(key)] = InputContract.from_dict(str(key), dict(raw), source="declared")
    return out


def build_contract_table(
    nodes: Iterable[Mapping[str, Any]],
    declared: Optional[Mapping[str, InputContract]] = None,
) -> Dict[str, InputContract]:
    """Merge declared contracts over inferred ones for a node collection."""
    declared = declared or {}
    table: Dict[str, InputContract] = {}
    for node in nodes:
        key = str(node.get("key", node.get("id", "")))
        explicit = declared.get(key) or contract_from_metadata(node)
        table[key] = explicit if explicit is not None else infer_contract(node)
    return table