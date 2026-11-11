"""报告/验收工件 → 节点自证 (attestation) 的机器摄取器。

两种来源，全部由 ``nodes.json`` 里的 ``ingest`` 配置声明（零领域语义）：

1. ``platform_report``：平台 ``/tensor/reports/<id>`` 的 markdown 快照。取其中第一个
   ```json 代码块，按 ``criteria_map`` 的 ``key``（点号路径）取数，用声明
   ``op``/``threshold`` **重算**判据结论（不采信报告自述结论）。
2. ``acceptance_json``：验收工件（如 ``artifacts/<x>/acceptance.json``）。按
   ``groups`` 声明把 ``criteria.<group>.{required,detected,rate,pass}`` 展成判据行，
   并额外校验 ``detected/required >= min_rate``。

产出写进 ``data/campaigns/<c>/attestations/<node>.json``，附带来源文件 sha256 与
抽取时刻，供 ``tools.integration_gate.cli`` 消费。任何缺键 → 抛错 (fail-closed)。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provenance import sha256_file, sha256_text
from .schema import ATTESTATION_SCHEMA, compute_verdict

_JSON_BLOCK = re.compile(r"```(?:json)?\n(\{.*?\})\n```", re.S)


def _dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            cur = cur[int(part)]
        else:
            raise KeyError(f"key {dotted!r} not found (at {part!r})")
    return cur


def extract_json_blocks(markdown: str) -> list[dict[str, Any]]:
    out = []
    for m in _JSON_BLOCK.finditer(markdown):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def attest_from_platform_report(markdown: str, ingest: dict[str, Any],
                                node: dict[str, Any]) -> dict[str, Any]:
    blocks = extract_json_blocks(markdown)
    if not blocks:
        raise ValueError("no JSON block in report markdown")
    payload: Any = blocks[int(ingest.get("block_index", 0))]
    criteria = []
    for spec in ingest["criteria_map"]:
        try:
            measured = _dig(payload, spec["key"])
        except KeyError:
            criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                             "note": f"key {spec['key']} absent from report payload"})
            continue
        if not isinstance(measured, (int, float)):
            criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                             "note": f"key {spec['key']} not numeric"})
            continue
        op, thr = spec["op"], float(spec["threshold"])
        criteria.append({
            "id": spec["id"],
            "measured": float(measured),
            "op": op,
            "threshold": thr,
            "verdict": compute_verdict(float(measured), op, thr),
            "unit": spec.get("unit", "-"),
            "note": spec.get("note", ""),
            "key": spec["key"],
            "role": spec.get("role", "capability_gate"),
        })
    return {
        "schema": ATTESTATION_SCHEMA,
        "node": {k: node.get(k) for k in
                 ("ext_id", "repo", "mission_id", "branch", "base_commit", "head_commit")},
        "source": {"kind": "platform_report",
                   "ref": ingest["ref"],
                   "sha256": sha256_text(markdown),
                   "retrieved_at": ingest.get("retrieved_at")
                   or datetime.now(timezone.utc).isoformat()},
        "criteria": criteria,
        "capabilities": ingest.get("capabilities") or [],
        "interface_contract": ingest.get("interface_contract"),
        "notes": ingest.get("notes", ""),
    }


def attest_from_acceptance(artifact_path: Path, ingest: dict[str, Any],
                           node: dict[str, Any]) -> dict[str, Any]:
    doc = json.loads(artifact_path.read_text(encoding="utf-8"))
    criteria = []
    for spec in ingest.get("groups") or []:
        g = _dig(doc, spec["path"])
        required = float(g.get("required", 0))
        detected = float(g.get("detected", 0))
        rate = float(g.get("rate", detected / required if required else 0.0))
        min_rate = float(spec.get("min_rate", 1.0))
        criteria.append({
            "id": spec["id"],
            "measured": rate,
            "op": ">=",
            "threshold": min_rate,
            "verdict": compute_verdict(rate, ">=", min_rate),
            "unit": "recall",
            "note": f"{spec['path']}: detected {detected:g}/{required:g}; "
                    f"artifact verdict {doc.get('verdict')}",
            "role": spec.get("role", "capability_gate"),
        })
    for spec in ingest.get("numeric") or []:
        if spec.get("combine"):
            if spec["combine"] == "count":
                try:
                    raw = _dig(doc, spec["paths"][0])
                except KeyError:
                    criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                                     "role": spec.get("role", "capability_gate"),
                                     "note": f"count spec: field {spec['paths'][0]} absent"})
                    continue
                if not isinstance(raw, list):
                    criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                                     "role": spec.get("role", "capability_gate"),
                                     "note": f"count spec: {spec['paths'][0]} is not a list"})
                    continue
                measured = float(len(raw))
                op, thr = spec["op"], float(spec["threshold"])
                criteria.append({
                    "id": spec["id"], "measured": measured, "op": op, "threshold": thr,
                    "verdict": compute_verdict(measured, op, thr),
                    "unit": spec.get("unit", "-"),
                    "note": spec.get("note", "") + f" [derived count of {spec['paths'][0]}]",
                    "path": spec["paths"][0], "role": spec.get("role", "capability_gate")})
                continue
            # 派生判据：两个字段的差（如 A - R）。缺任一字段 → WITHHELD，不兜底。
            vals = []
            missing = None
            for path in spec["paths"]:
                try:
                    v = _dig(doc, path)
                except KeyError:
                    missing = path
                    break
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    missing = path
                    break
                vals.append(float(v))
            if missing is not None:
                criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                                 "role": spec.get("role", "capability_gate"),
                                 "note": f"derived spec: field {missing} absent or non-numeric"})
                continue
            op_kind = spec["combine"]
            if op_kind == "subtract":
                measured = vals[0] - vals[1]
            elif op_kind == "count":
                raw = _dig(doc, spec["paths"][0])
                if not isinstance(raw, list):
                    criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                                     "role": spec.get("role", "capability_gate"),
                                     "note": f"count spec: {spec['paths'][0]} is not a list"})
                    continue
                measured = float(len(raw))
            else:  # pragma: no cover - guarded by data review
                raise ValueError(f"unknown combine {op_kind!r}")
            op, thr = spec["op"], float(spec["threshold"])
            criteria.append({
                "id": spec["id"], "measured": measured, "op": op, "threshold": thr,
                "verdict": compute_verdict(measured, op, thr),
                "unit": spec.get("unit", "-"),
                "note": spec.get("note", "") + f" [derived {op_kind} of {spec['paths']}]",
                "path": " & ".join(spec["paths"]),
                "role": spec.get("role", "capability_gate"),
            })
            continue
        try:
            measured = _dig(doc, spec["path"])
        except KeyError:
            criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                             "role": spec.get("role", "capability_gate"),
                             "note": f"path {spec['path']} absent from acceptance artifact"})
            continue
        if not isinstance(measured, (int, float)):
            criteria.append({"id": spec["id"], "verdict": "WITHHELD",
                             "role": spec.get("role", "capability_gate"),
                             "note": f"path {spec['path']} not numeric"})
            continue
        op, thr = spec["op"], float(spec["threshold"])
        criteria.append({
            "id": spec["id"], "measured": float(measured), "op": op, "threshold": thr,
            "verdict": compute_verdict(float(measured), op, thr),
            "unit": spec.get("unit", "-"), "note": spec.get("note", ""),
            "path": spec["path"], "role": spec.get("role", "capability_gate"),
        })
    return {
        "schema": ATTESTATION_SCHEMA,
        "node": {k: node.get(k) for k in
                 ("ext_id", "repo", "mission_id", "branch", "base_commit", "head_commit")},
        "source": {"kind": "acceptance_json", "ref": ingest["ref"],
                   "sha256": sha256_file(artifact_path),
                   "retrieved_at": ingest.get("retrieved_at")
                   or datetime.now(timezone.utc).isoformat()},
        "criteria": criteria,
        "capabilities": ingest.get("capabilities") or [],
        "interface_contract": ingest.get("interface_contract"),
        "notes": ingest.get("notes", ""),
    }


def ingest_campaign(data_dir: Path) -> list[Path]:
    """按 nodes.json 的 ingest 配置批量产出 attestations/*.json。"""
    nodes_doc = json.loads((data_dir / "nodes.json").read_text(encoding="utf-8"))
    out_dir = data_dir / "attestations"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for node in nodes_doc["nodes"]:
        ing = node.get("ingest")
        if not ing:
            continue
        kind = ing["kind"]
        if kind == "platform_report":
            src = Path(ing["path"])
            md = src.read_text(encoding="utf-8")
            doc = attest_from_platform_report(md, ing, node)
        elif kind == "acceptance_json":
            src = Path(ing["path"])
            doc = attest_from_acceptance(src, ing, node)
        else:  # pragma: no cover
            raise ValueError(f"unknown ingest kind {kind!r}")
        p = out_dir / f"{node['ext_id']}.json"
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(p)
    return written