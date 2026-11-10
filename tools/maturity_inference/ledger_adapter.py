"""台账适配层：把"真库台账 + 能力定义 + 上游活体快照"折成推断引擎的输入。

**上游契约边界**（漂移只改这里）：

::

    interface_contract = {
      "ledger":  {"path": "artifacts/capability_ledger/capability_ledger.json",
                  "rows": "capabilities[]", "key": "id",
                  "fields": ["claimed_level", "evidence[].stance",
                             "evidence[].asserted_level"]},
      "resolved":{"path": ".../capability_ledger_resolved.json",
                  "fields": ["reconciled_level", "divergence", "evidence_refs"]},
      "defs":    {"path": "config/capabilities.json", "rows": "capabilities[]",
                  "key": "capability_id",
                  "fields": ["code_patterns", "test_patterns", "initial_level"]},
      "live":    {"path": "artifacts/maturity_inference/input/live_ledger_snapshot.json",
                  "note": "活体台账在另一检出/DB，抓取为只读快照（含 node.* 记账行）"},
      "evidence_node": {...见 evidence.py 模块文档...},
    }

上游若改字段名，只在 ``normalize_*`` 里加映射；引擎/阶梯/黄金集不受影响。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]

LEDGER_PATH = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
RESOLVED_PATH = ROOT / "artifacts" / "capability_ledger" / "capability_ledger_resolved.json"
DEFS_PATH = ROOT / "config" / "capabilities.json"
LIVE_SNAPSHOT_PATH = ROOT / "artifacts" / "maturity_inference" / "input" / "live_ledger_snapshot.json"

#: 上游数据格式契约（报告字段 ``interface_contract``）
INTERFACE_CONTRACT: Dict[str, Any] = {
    "schema_version": "1.0",
    "engine_input": {
        "capability_key": "str，须过命名空间门 ^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$，禁 node./campaign./delivery./tmp./test. 前缀",
        "nodes": "EvidenceNode[]（见 tools/maturity_inference/evidence.py 模块文档）",
        "claimed_level": "历史四档 NOT_SUPPORTED/STUB/WITHHELD/MOCK/REAL（只用于判高报，不抬级）",
        "contradictions": "历史四档数组（stance=contradicts 的断言档位）→ 封顶",
    },
    "engine_output": {
        "inferred_level": "LADDER 之一",
        "inferred_rank": "0..5",
        "rung_support": "{rung,rank,supported,node_id,reason,candidates}[]，长度 5",
        "evidence_chain": "每环一条：{node_id,locator,rerun_cmd,criterion,digest}",
        "claim_status": "unbacked_overclaim | consistent | understated | no_claim",
    },
    "ledger": {
        "path": "artifacts/capability_ledger/capability_ledger.json",
        "rows": "capabilities[]",
        "key": "id",
        "fields": ["id", "claimed_level", "domain", "evidence[].stance", "evidence[].asserted_level"],
    },
    "resolved": {
        "path": "artifacts/capability_ledger/capability_ledger_resolved.json",
        "rows": "capabilities[]",
        "fields": ["id", "reconciled_level", "divergence", "evidence_refs[]"],
    },
    "defs": {
        "path": "config/capabilities.json",
        "rows": "capabilities[]",
        "key": "capability_id",
        "fields": ["code_patterns", "test_patterns", "initial_level"],
    },
    "live_snapshot": {
        "path": "artifacts/maturity_inference/input/live_ledger_snapshot.json",
        "rows": "rows[]",
        "key": "capability_key",
        "note": "活体台账在另一检出/DB（agent 侧 audit_capabilities 工具），抓取为只读快照",
    },
    "migration": {
        "target_store": "campaign_node_registry",
        "action": "deregister_from_capability_ledger",
        "invariant": "迁移后能力台账 node.* 前缀行数 = 0",
    },
}


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def load_ledger(path: Optional[Path | str] = None) -> Dict[str, Any]:
    p = Path(path) if path else LEDGER_PATH
    data = _load(p)
    rows = data.get("capabilities") or []
    return {"path": str(p), "sha256": sha256_file(p), "rows": rows,
            "schema_version": data.get("schema_version", "")}


def load_resolved(path: Optional[Path | str] = None) -> Dict[str, Any]:
    p = Path(path) if path else RESOLVED_PATH
    data = _load(p)
    return {"path": str(p), "sha256": sha256_file(p),
            "rows": data.get("capabilities") or []}


def load_defs(path: Optional[Path | str] = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFS_PATH
    data = _load(p)
    return {"path": str(p), "sha256": sha256_file(p),
            "rows": data.get("capabilities") or []}


def load_live_snapshot(path: Optional[Path | str] = None) -> Dict[str, Any]:
    p = Path(path) if path else LIVE_SNAPSHOT_PATH
    if not p.exists():
        return {"path": str(p), "sha256": "", "rows": [], "available": False}
    data = _load(p)
    return {"path": str(p), "sha256": sha256_file(p), "rows": data.get("rows") or [],
            "available": True, "observed_at": data.get("observed_at", "")}


def normalize_ledger_row(row: Mapping[str, Any],
                         resolved_row: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """台账行 → 推断输入行（含矛盾证据档位与叙述性支持证据）。"""
    evidence = row.get("evidence") or []
    contradictions: List[str] = []
    narrative_support: List[Dict[str, Any]] = []
    for ev in evidence:
        if not isinstance(ev, Mapping):
            continue
        stance = str(ev.get("stance", "")).lower()
        asserted = ev.get("asserted_level")
        if stance == "contradicts" and asserted:
            contradictions.append(str(asserted).upper())
        elif stance.startswith("supports") and asserted:
            narrative_support.append({"report_id": ev.get("report_id"),
                                      "asserted_level": str(asserted).upper(),
                                      "as_of": ev.get("as_of")})
    out = {
        "id": str(row.get("id") or row.get("capability_key") or ""),
        "claimed_level": (str(row.get("claimed_level")).upper()
                          if row.get("claimed_level") else None),
        "domain": row.get("domain", ""),
        "contradictions": contradictions,
        "narrative_support": narrative_support,
        "note": row.get("note", ""),
    }
    if resolved_row:
        out["reconciled_level"] = (str(resolved_row.get("reconciled_level")).upper()
                                   if resolved_row.get("reconciled_level") else None)
        out["divergence"] = resolved_row.get("divergence")
        out["resolved_evidence_refs"] = len(resolved_row.get("evidence_refs") or [])
    return out


def normalize_ledger(ledger: Mapping[str, Any],
                     resolved: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """整表规范化（按 resolved 行的 ``id`` 对齐，缺则降级为独立行）。"""
    resolved_by_key: Dict[str, Mapping[str, Any]] = {}
    if resolved:
        for r in resolved.get("rows") or []:
            resolved_by_key[str(r.get("id") or "")] = r
    rows = [normalize_ledger_row(r, resolved_by_key.get(str(r.get("id") or "")))
            for r in ledger.get("rows") or []]
    rows.sort(key=lambda r: r["id"])
    return rows


def normalize_live_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """活体快照行 → 推断输入行（键字段名 ``capability_key``）。"""
    return {
        "id": str(row.get("capability_key") or row.get("id") or ""),
        "claimed_level": (str(row.get("observed_level") or row.get("claimed_level") or "").upper()
                          or None),
        "domain": row.get("domain", ""),
        "contradictions": [str(c).upper() for c in (row.get("contradictions") or [])],
        "note": row.get("note", ""),
        "criteria_text": row.get("criteria_text", ""),
    }


def normalize_live(snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = [normalize_live_row(r) for r in snapshot.get("rows") or []]
    rows.sort(key=lambda r: r["id"])
    return rows


def universe(ledger_rows: Sequence[Mapping[str, Any]],
             defs_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """台账 ∩ 定义 的覆盖对账：只在台账的、只在定义的、两侧都在的。"""
    ledger_keys = {str(r.get("id")) for r in ledger_rows}
    def_keys = {str(r.get("capability_id")) for r in defs_rows}
    return {
        "ledger_only": sorted(ledger_keys - def_keys),
        "defs_only": sorted(def_keys - ledger_keys),
        "both": sorted(ledger_keys & def_keys),
        "ledger_count": len(ledger_keys),
        "defs_count": len(def_keys),
    }


def defs_by_key(defs_rows: Iterable[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {str(r.get("capability_id")): r for r in defs_rows}