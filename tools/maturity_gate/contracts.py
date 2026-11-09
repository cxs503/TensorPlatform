"""接口契约完整性检查：把「文档写全了没有」变成机器可裁判的分数。

三件事，全部数据驱动（清单由 JSON 工件注入，代码里没有任何路径/章节名）：

1. ``kind="markdown"``：声明必含章节标题（``required_sections``），逐条检查是否出现；
2. ``kind="jsonschema"``：声明必含字段（``required_keys``，支持 ``properties.a.b`` 形式
   的嵌套路径）是否在 schema 里可解析；
3. ``routes``：文档声明的每个端点标识（如 ``POST /api/v1/x``）必须在契约文档正文里
   出现——防止「端点加了、契约没写」。

完整性 = 命中项 / 声明项；``complete`` 仅当全部命中且无文档缺失。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _nested_get(doc: Any, dotted: str) -> Any:
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, Mapping):
            if part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        elif isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                raise KeyError(dotted)
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def check_document(root: Path, item: Mapping[str, Any]) -> Dict[str, Any]:
    """检查单个契约文档，返回命中/缺失明细。"""
    rel = str(item.get("path"))
    kind = item.get("kind")
    path = root / rel
    doc_id = item.get("id") or rel
    out: Dict[str, Any] = {"id": doc_id, "path": rel, "kind": kind, "exists": path.is_file(),
                           "missing": [], "required": 0, "found": 0}
    if not out["exists"]:
        out["missing"] = ["<file>"]
        out["required"] = 1
        return out
    if kind == "markdown":
        text = _read_text(path) or ""
        required = [str(s) for s in item.get("required_sections", [])]
        min_chars = int(item.get("min_chars", 0))
        missing = [s for s in required if s not in text]
        if min_chars and len(text) < min_chars:
            missing.append(f"<min_chars:{min_chars}>")
        out.update(required=len(required) + (1 if min_chars else 0),
                   found=len(required) - len([m for m in missing if m in required])
                   + (1 if min_chars and len(text) >= min_chars else 0),
                   missing=missing)
        return out
    if kind == "jsonschema":
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["missing"] = ["<unparsable-json>"]
            out["required"] = 1
            return out
        required = [str(s) for s in item.get("required_keys", [])]
        missing = []
        for key in required:
            try:
                _nested_get(doc, key)
            except KeyError:
                missing.append(key)
        out.update(required=len(required), found=len(required) - len(missing), missing=missing)
        return out
    out["missing"] = [f"<unknown-kind:{kind}>"]
    out["required"] = 1
    return out


def check_route_coverage(root: Path, route_item: Mapping[str, Any]) -> Dict[str, Any]:
    """端点标识是否在契约文档正文里逐个出现（``doc`` + ``endpoints`` 均由工件声明）。"""
    rel = str(route_item.get("doc"))
    path = root / rel
    text = _read_text(path) if path.is_file() else None
    endpoints = [str(e) for e in route_item.get("endpoints", [])]
    if text is None:
        return {"id": "routes", "path": rel, "exists": False, "required": max(1, len(endpoints)),
                "found": 0, "missing": list(endpoints) or ["<file>"]}
    missing = [e for e in endpoints if e not in text]
    return {"id": "routes", "path": rel, "exists": True, "required": len(endpoints),
            "found": len(endpoints) - len(missing), "missing": missing}


def check_manifest(root: Path, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """跑完整张清单，返回逐文档明细 + 总体完整性分数。"""
    items: List[Dict[str, Any]] = [check_document(root, d) for d in manifest.get("documents", [])]
    routes = manifest.get("routes")
    if routes:
        items.append(check_route_coverage(root, routes))
    required = sum(i["required"] for i in items)
    found = sum(i["found"] for i in items)
    missing = [{"id": i["id"], "missing": i["missing"]} for i in items if i["missing"]]
    return {
        "documents": items,
        "required_total": required,
        "found_total": found,
        "completeness": (found / required) if required else 0.0,
        "complete": bool(items) and not missing,
        "missing": missing,
    }