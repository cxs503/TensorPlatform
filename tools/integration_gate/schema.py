"""工件 schema 校验 (fail-closed，纯 stdlib)。

规则：任何缺字段/类型不对/未知判据结论 → 抛 ``SchemaError``，由上层 CLI 退出码
非零、把该工件标为违约。**不做宽松兜底**：静默兜底等于把错误洗成通过。
"""

from __future__ import annotations

from typing import Any

ATTESTATION_SCHEMA = "tp.integration.attestation/v1"
TRACES_SCHEMA = "tp.integration.traces/v1"
PAIRS_SCHEMA = "tp.integration.pairs/v1"
NODES_SCHEMA = "tp.integration.nodes/v1"

VALID_VERDICTS = ("PASS", "FAIL", "WITHHELD", "SKIP")
VALID_OPS = ("<=", ">=", "==", "abs<=")
VALID_CHECKS = ("trace_agreement", "endpoint_agreement", "jump_ratio", "metric_below",
                "metric_agreement", "profile_deviation", "degenerate")
VALID_TRACE_KINDS = ("callable", "script", "expression")
VALID_SIDES = ("DIRECTED",)


class SchemaError(ValueError):
    """工件不合法 (fail-closed 的触发器)。"""


def _req(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict):
        raise SchemaError(f"{where}: expected object, got {type(d).__name__}")
    if key not in d:
        raise SchemaError(f"{where}: missing required key {key!r}")
    return d[key]


def _num(d: Any, key: str, where: str) -> float:
    v = _req(d, key, where)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise SchemaError(f"{where}.{key}: expected number, got {type(v).__name__}")
    return float(v)


def _str(d: Any, key: str, where: str) -> str:
    v = _req(d, key, where)
    if not isinstance(v, str) or not v:
        raise SchemaError(f"{where}.{key}: expected non-empty string")
    return v


def validate_attestation(doc: Any, where: str = "attestation") -> dict[str, Any]:
    if _req(doc, "schema", where) != ATTESTATION_SCHEMA:
        raise SchemaError(f"{where}: schema must be {ATTESTATION_SCHEMA}")
    node = _req(doc, "node", where)
    _str(node, "ext_id", f"{where}.node")
    _str(node, "repo", f"{where}.node")
    src = _req(doc, "source", where)
    _str(src, "kind", f"{where}.source")
    _str(src, "ref", f"{where}.source")
    sha = _str(src, "sha256", f"{where}.source")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise SchemaError(f"{where}.source.sha256: expected 64 lowercase hex chars")
    crit = _req(doc, "criteria", where)
    if not isinstance(crit, list):
        raise SchemaError(f"{where}.criteria: expected list")
    seen = set()
    for i, c in enumerate(crit):
        w = f"{where}.criteria[{i}]"
        cid = _str(c, "id", w)
        if cid in seen:
            raise SchemaError(f"{w}: duplicate criterion id {cid!r}")
        seen.add(cid)
        v = _str(c, "verdict", w)
        if v not in VALID_VERDICTS:
            raise SchemaError(f"{w}.verdict: {v!r} not in {VALID_VERDICTS}")
        op = c.get("op")
        if op is not None and op not in VALID_OPS:
            raise SchemaError(f"{w}.op: {op!r} not in {VALID_OPS}")
    caps = doc.get("capabilities") or []
    if not isinstance(caps, list):
        raise SchemaError(f"{where}.capabilities: expected list")
    for i, c in enumerate(caps):
        w = f"{where}.capabilities[{i}]"
        _str(c, "id", w)
        _str(c, "claim_level", w)
        for cid in (c.get("blocking_criteria") or []):
            if cid not in seen:
                raise SchemaError(f"{w}.blocking_criteria: unknown criterion {cid!r}")
    return doc


def validate_nodes(doc: Any, where: str = "nodes") -> dict[str, Any]:
    if _req(doc, "schema", where) != NODES_SCHEMA:
        raise SchemaError(f"{where}: schema must be {NODES_SCHEMA}")
    nodes = _req(doc, "nodes", where)
    if not isinstance(nodes, list) or not nodes:
        raise SchemaError(f"{where}.nodes: expected non-empty list")
    for i, n in enumerate(nodes):
        w = f"{where}.nodes[{i}]"
        _str(n, "ext_id", w)
        _str(n, "repo", w)
        _str(n, "label", w)
    return doc


def validate_traces(doc: Any, where: str = "traces") -> dict[str, Any]:
    if _req(doc, "schema", where) != TRACES_SCHEMA:
        raise SchemaError(f"{where}: schema must be {TRACES_SCHEMA}")
    traces = _req(doc, "traces", where)
    if not isinstance(traces, list) or not traces:
        raise SchemaError(f"{where}.traces: expected non-empty list")
    ids = set()
    for i, t in enumerate(traces):
        w = f"{where}.traces[{i}]"
        tid = _str(t, "id", w)
        if tid in ids:
            raise SchemaError(f"{w}: duplicate trace id {tid!r}")
        ids.add(tid)
        kind = _str(t, "kind", w)
        if kind not in VALID_TRACE_KINDS:
            raise SchemaError(f"{w}.kind: {kind!r} not in {VALID_TRACE_KINDS}")
        if kind == "callable":
            _str(t, "module", w)
            _str(t, "func", w)
        elif kind == "script":
            _str(t, "path", w)
        elif kind == "expression":
            _str(t, "expr", w)
        grid = _req(t, "grid", w)
        _str(grid, "param", f"{w}.grid")
        vals = _req(grid, "values", f"{w}.grid")
        if not isinstance(vals, list) or len(vals) < 1:
            raise SchemaError(f"{w}.grid.values: expected non-empty list")
    return doc


def validate_pairs(doc: Any, where: str = "pairs") -> dict[str, Any]:
    if _req(doc, "schema", where) != PAIRS_SCHEMA:
        raise SchemaError(f"{where}: schema must be {PAIRS_SCHEMA}")
    pairs = _req(doc, "pairs", where)
    if not isinstance(pairs, list) or not pairs:
        raise SchemaError(f"{where}.pairs: expected non-empty list")
    ids = set()
    for i, p in enumerate(pairs):
        w = f"{where}.pairs[{i}]"
        pid = _str(p, "id", w)
        if pid in ids:
            raise SchemaError(f"{w}: duplicate pair id {pid!r}")
        ids.add(pid)
        _str(p, "title", w)
        checks = _req(p, "checks", w)
        if not isinstance(checks, list) or not checks:
            raise SchemaError(f"{w}.checks: expected non-empty list")
        for j, c in enumerate(checks):
            cw = f"{w}.checks[{j}]"
            kind = _str(c, "check", cw)
            if kind not in VALID_CHECKS:
                raise SchemaError(f"{cw}.check: {kind!r} not in {VALID_CHECKS}")
            sev = c.get("severity", "blocking")
            if sev not in ("blocking", "advisory"):
                raise SchemaError(f"{cw}.severity: {sev!r} invalid")
    return doc


def compute_verdict(measured: float, op: str, threshold: float) -> str:
    """由数字重算判据结论 (不接受自述 verdict 与数字矛盾的情形)。"""
    if op == "<=":
        ok = measured <= threshold
    elif op == ">=":
        ok = measured >= threshold
    elif op == "==":
        ok = measured == threshold
    elif op == "abs<=":
        ok = abs(measured) <= threshold
    else:  # pragma: no cover - guarded by validator
        raise SchemaError(f"unknown op {op!r}")
    return "PASS" if ok else "FAIL"


def reconcile_criterion(c: dict[str, Any]) -> tuple[str, str]:
    """返回 (最终结论, 是否与自述冲突说明)。数字优先：自述 PASS 但数字越界 → FAIL。"""
    declared = c["verdict"]
    measured, op, thr = c.get("measured"), c.get("op"), c.get("threshold")
    if measured is None or op is None or thr is None:
        return declared, ""
    computed = compute_verdict(float(measured), op, float(thr))
    if declared in ("PASS", "FAIL") and computed != declared:
        return computed, f"declared {declared} contradicted by numbers -> {computed}"
    if declared in ("WITHHELD", "SKIP"):
        return declared, "no numeric gate declared"
    return declared, ""