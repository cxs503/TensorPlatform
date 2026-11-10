"""manifest 读取与 fail-closed 校验 (纯 stdlib)。

manifest 是**领域内容注入面**: 节点清单、工件 glob、量名/单位/参考系/
容差、基线指针全部来自 JSON 文件；代码只认结构，不认词条。
结构不合法一律抛 :class:`ManifestError`，不做宽松兜底 (静默兜底等于把
配置错误洗成通过)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import MANIFEST_SCHEMA
from .units import UnitError, parse_unit


class ManifestError(ValueError):
    """manifest 结构违约。"""


NODE_STATUS = ("PRESENT", "PARTIAL", "MISSING")


def _req(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict):
        raise ManifestError(f"{where}: expected object, got {type(d).__name__}")
    if key not in d or d[key] in (None, ""):
        raise ManifestError(f"{where}: missing required key {key!r}")
    return d[key]


def _int(d: Any, key: str, where: str) -> int:
    v = _req(d, key, where)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ManifestError(f"{where}.{key}: expected int, got {type(v).__name__}")
    return v


def _str(d: Any, key: str, where: str) -> str:
    v = _req(d, key, where)
    if not isinstance(v, str):
        raise ManifestError(f"{where}.{key}: expected str, got {type(v).__name__}")
    return v


def _unit(entry: dict[str, Any], where: str) -> None:
    unit = _req(entry, "unit", where)
    try:
        parse_unit(unit)
    except UnitError as exc:
        raise ManifestError(f"{where}.unit: {exc}") from exc


def _quantity(q: Any, where: str) -> dict[str, Any]:
    name = _str(q, "name", where)
    _unit(q, where)
    frame = _str(q, "frame", where)
    tol = q.get("tolerance_rel")
    if tol is not None and (not isinstance(tol, (int, float)) or tol < 0):
        raise ManifestError(f"{where}.tolerance_rel: expected non-negative number")
    adapter = q.get("adapter")
    if adapter is not None:
        if not isinstance(adapter, dict):
            raise ManifestError(f"{where}.adapter: expected object")
        _str(adapter, "artifact", f"{where}.adapter")
        if not str(_req(adapter, "pointer", f"{where}.adapter")).startswith("/"):
            raise ManifestError(f"{where}.adapter.pointer: must be JSON pointer")
        scale = adapter.get("scale", 1.0)
        if not isinstance(scale, (int, float)) or scale == 0:
            raise ManifestError(f"{where}.adapter.scale: expected non-zero number")
        if adapter.get("unit_in_artifact") is not None:
            try:
                parse_unit(adapter["unit_in_artifact"])
            except UnitError as exc:
                raise ManifestError(f"{where}.adapter.unit_in_artifact: {exc}") from exc
    baseline = q.get("baseline")
    if baseline is not None:
        if not isinstance(baseline, dict):
            raise ManifestError(f"{where}.baseline: expected object")
        _str(baseline, "worktree", f"{where}.baseline")
        _str(baseline, "artifact", f"{where}.baseline")
        if not str(_req(baseline, "pointer", f"{where}.baseline")).startswith("/"):
            raise ManifestError(f"{where}.baseline.pointer: must be JSON pointer")
        _unit(baseline, f"{where}.baseline")
    return {"name": name, "unit": q["unit"], "frame": frame,
            "tolerance_rel": tol, "adapter": adapter, "baseline": baseline,
            "note": q.get("note", "")}


def _node(n: Any, where: str, frames: list[str]) -> dict[str, Any]:
    ext_id = _str(n, "ext_id", where)
    for key in ("required_artifacts", "expected_artifacts"):
        v = n.get(key, [])
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise ManifestError(f"{where}.{key}: expected list[str]")
    quantities = n.get("quantities", [])
    if not isinstance(quantities, list):
        raise ManifestError(f"{where}.quantities: expected list")
    qs = [_quantity(q, f"{where}.quantities[{i}]") for i, q in enumerate(quantities)]
    for q in qs:
        if q["frame"] not in frames:
            raise ManifestError(f"{where}: quantity {q['name']!r} frame {q['frame']!r} "
                                f"not in contract.allowed_frames")
    texts = n.get("criteria_text", [])
    if not isinstance(texts, list) or any(not isinstance(t, str) for t in texts):
        raise ManifestError(f"{where}.criteria_text: expected list[str]")
    baseline_texts = n.get("baseline_criteria_text", [])
    if not isinstance(baseline_texts, list):
        raise ManifestError(f"{where}.baseline_criteria_text: expected list[str]")
    return {
        "ext_id": ext_id,
        "mission_id": _int(n, "mission_id", where),
        "ledger_node_id": n.get("ledger_node_id"),
        "repo": _str(n, "repo", where),
        "worktree": _str(n, "worktree", where),
        "label": n.get("label", ""),
        "required_artifacts": list(n.get("required_artifacts", [])),
        "expected_artifacts": list(n.get("expected_artifacts", [])),
        "quantities": qs,
        "criteria_text": list(texts),
        "baseline_criteria_text": list(baseline_texts),
        "baseline_node": n.get("baseline_node"),
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError("manifest: expected object")
    schema = _req(raw, "schema", "manifest")
    if schema != MANIFEST_SCHEMA:
        raise ManifestError(f"manifest.schema: expected {MANIFEST_SCHEMA}, got {schema!r}")
    campaign = _req(raw, "campaign", "manifest")
    campaign_out = {
        "key": _str(campaign, "key", "campaign"),
        "plan_id": _int(campaign, "plan_id", "campaign"),
        "wave": _int(campaign, "wave", "campaign"),
        "title": campaign.get("title", ""),
        "started_utc": campaign.get("started_utc"),
        "baseline": None,
    }
    base = campaign.get("baseline")
    if base is not None:
        campaign_out["baseline"] = {
            "key": _str(base, "key", "campaign.baseline"),
            "plan_id": _int(base, "plan_id", "campaign.baseline"),
            "wave": _int(base, "wave", "campaign.baseline"),
            "title": base.get("title", ""),
        }
    scan = _req(raw, "scan", "manifest")
    scan_out = {"work_root": _str(scan, "work_root", "scan")}
    contract = raw.get("contract") or {}
    frames = contract.get("allowed_frames") or ["none"]
    if not isinstance(frames, list) or "none" not in frames:
        raise ManifestError("contract.allowed_frames: list and must contain 'none'")
    contract_out = {
        "allowed_frames": list(frames),
        "require_envelope_schema": contract.get("require_envelope_schema",
                                                "tp.cdi.envelope/v1"),
    }
    doc = raw.get("doc") or {}
    doc_out = {"path": doc.get("path"), "sections": doc.get("sections") or {}}
    nodes_raw = _req(raw, "nodes", "manifest")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ManifestError("manifest.nodes: expected non-empty list")
    nodes = [_node(n, f"nodes[{i}]", contract_out["allowed_frames"])
             for i, n in enumerate(nodes_raw)]
    seen = set()
    for n in nodes:
        if n["ext_id"] in seen:
            raise ManifestError(f"duplicate node ext_id {n['ext_id']!r}")
        seen.add(n["ext_id"])
    applicable = raw.get("criteria_applicable") or ["I1", "I2", "I3", "I4", "I5"]
    if not isinstance(applicable, list) or any(a not in ("I1", "I2", "I3", "I4", "I5")
                                              for a in applicable):
        raise ManifestError("criteria_applicable: subset of I1..I5 expected")
    return {"schema": schema, "campaign": campaign_out, "scan": scan_out,
            "contract": contract_out, "doc": doc_out, "nodes": nodes,
            "criteria_applicable": list(applicable), "path": str(path)}