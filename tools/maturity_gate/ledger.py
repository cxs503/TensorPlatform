"""能力台账：把战役节点映射成一一对应的能力条目，并做双向双射校验。

判据「能力台账 entries 与节点一一对应」的机器化：

* **正向**：每个战役节点必须有且只有一条台账条目（缺 → ``unmapped_nodes``）。
* **反向**：每条条目必须指向一个真实存在的节点（多 → ``orphan_entries``）。
* **唯一**：条目 id 与能力名都不得重复（重复 → ``duplicate_entries`` / ``duplicate_capabilities``）。

台账条目本身也是证据：``evidence_refs`` 记录该节点每条判据读到的文档与 sha256，
``evidence_level`` 三值（REAL / PARTIAL / WITHHELD）——**没有证据就没有 REAL**。
本模块是纯函数，不读盘、不含领域语义。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .gate import LEVEL_PARTIAL, LEVEL_REAL, LEVEL_WITHHELD


def build_entries(gate: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """由判定体生成台账条目（每节点一条，顺序与规格一致）。"""
    entries: List[Dict[str, Any]] = []
    for node, summary in zip(gate["nodes"], gate["node_summaries"]):
        refs = []
        seen = set()
        for crit in node["criteria"]:
            for chk in crit["checks"]:
                doc = chk["evidence"]["doc"]
                sha = chk["evidence"]["sha256"]
                key = (doc, sha)
                if doc is None or sha is None or key in seen:  # 只登记真正读到的证据
                    continue
                seen.add(key)
                refs.append({"doc": doc, "sha256": sha})
        if summary["evaluable_ratio"] == 0.0:
            level = LEVEL_WITHHELD
        elif summary["pass_ratio"] >= 1.0:
            level = LEVEL_REAL
        else:
            level = LEVEL_PARTIAL
        entries.append({
            "entry_id": f"cap::{node['node_key']}",
            "node_key": node["node_key"],
            "capability": summary.get("capability") or node["node_key"],
            "repo": node["repo"],
            "mission_id": node["mission_id"],
            "layer": node.get("layer"),
            "risk": node.get("risk"),
            "evidence_level": level,
            "criteria_total": summary["criteria_total"],
            "criteria_passed": summary["passed"],
            "pass_ratio": summary["pass_ratio"],
            "evidence_refs": refs,
            "withheld_reasons": summary["withheld_reasons"],
            "claim_mismatches": summary["claim_mismatches"],
        })
    return entries


def verify_bijection(entries: Sequence[Mapping[str, Any]],
                     node_keys: Sequence[str]) -> Dict[str, Any]:
    """双向双射校验：节点 ↔ 台账条目。"""
    keys = list(node_keys)
    entry_keys = [e["node_key"] for e in entries]
    counts: Dict[str, int] = {}
    for k in entry_keys:
        counts[k] = counts.get(k, 0) + 1
    unmapped = [k for k in keys if counts.get(k, 0) == 0]
    duplicated = sorted(k for k, c in counts.items() if c > 1)
    orphans = sorted({k for k in entry_keys if k not in set(keys)})
    ids: Dict[str, int] = {}
    caps: Dict[str, int] = {}
    for e in entries:
        ids[e["entry_id"]] = ids.get(e["entry_id"], 0) + 1
        caps[e["capability"]] = caps.get(e["capability"], 0) + 1
    dup_ids = sorted(k for k, c in ids.items() if c > 1)
    dup_caps = sorted(k for k, c in caps.items() if c > 1)
    by_level: Dict[str, int] = {}
    for e in entries:
        by_level[e["evidence_level"]] = by_level.get(e["evidence_level"], 0) + 1
    ok = not (unmapped or duplicated or orphans or dup_ids or dup_caps)
    return {
        "nodes_total": len(keys),
        "entries_total": len(entries),
        "one_to_one": ok,
        "unmapped_nodes": unmapped,
        "duplicate_node_entries": duplicated,
        "orphan_entries": orphans,
        "duplicate_entry_ids": dup_ids,
        "duplicate_capabilities": dup_caps,
        "levels": by_level,
    }


def ledger_document(entries: Sequence[Mapping[str, Any]], bijection: Mapping[str, Any],
                    spec_id: str, input_digest: str) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "campaign_capability_ledger",
        "spec_id": spec_id,
        "input_digest": input_digest,
        "note": "条目由 maturity gate 判定体程序化生成，零手抄；WITHHELD 不等于无能力，"
                "只表示该节点本期未交付可复算证据。",
        "entries": list(entries),
        "bijection": dict(bijection),
    }