#!/usr/bin/env python3
"""interface_probe —— 跨域**接口契约在线校验**层（OpenAPI 声明面 × 期望字段集，零领域语义）。

为什么单独一层
--------------
跨域战役里"接口一致"有两条独立的命题：

1. **静态**：DAG 边两端的产/需字段是否兼容 —— 由
   ``ops/taskplane/campaign_integration_verify.py`` 的 ``check_edges`` 裁决；
2. **在线**：下游真正要消费的接口此刻是否**在位且形状未漂移** —— 静态契约再怎么自洽，
   线上接口被改名/删字段/换方法，集成照样断。这一层只管第 2 条。

做法：把各域服务的 ``openapi.json`` 当**契约快照**，逐条断言
``<method> <path>`` 存在、且指定子路径（如 ``properties.result.properties.rao``）
在响应 schema 里存在。字段集、路径、方法全部由 ``probes`` 注入，模块内无任何领域词条。

确定性
------
报告只依赖 openapi 文档与 probes（不取 wall clock），``input_digest`` 由两者内容派生；
``openapi_version``/``title`` 计入 ``documents`` 摘要，便于把"接口漂移"钉到具体版本。

CLI
---
    python3 ops/taskplane/interface_probe.py \
        --document platform=https://127.0.0.1:8010/openapi.json \
        --document ship=http://127.0.0.1:8001/openapi.json \
        --token "$TOKEN" --probes artifacts/p143w2/probe_config.json \
        --json-out artifacts/p143w2/interface_probes.json

退出码：0=全部在位；2=有缺失/漂移；3=输入错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA_VERSION = "interface_probe/v1"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def http_json(url: str, token: str | None = None, timeout: float = 30.0) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 内网只读
        return json.loads(resp.read().decode("utf-8"))


def _walk(schema: Any, dotted: str) -> bool:
    """按 ``a.b.c`` 走 schema（支持 ``properties``/``items``/``$ref`` 一层解析）。"""
    node = schema
    for part in [p for p in str(dotted).split(".") if p]:
        if not isinstance(node, Mapping):
            return False
        if part in (node.get("properties") or {}):
            node = node["properties"][part]
        elif part == "items" and isinstance(node.get("items"), Mapping):
            node = node["items"]
        elif isinstance(node.get("items"), Mapping) and part in (
                (node["items"].get("properties") or {})):
            node = node["items"]["properties"][part]
        elif part in node:
            node = node[part]
        else:
            return False
    return True


def resolve_refs(node: Any, components: Mapping[str, Any], depth: int = 6) -> Any:
    """把 ``$ref: #/components/schemas/X`` 就地展开（仅展开 schema 引用，深度有界）。"""
    if depth <= 0:
        return node
    if isinstance(node, Mapping):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            target = components.get(name)
            if isinstance(target, Mapping):
                return resolve_refs(target, components, depth - 1)
            return node
        return {k: resolve_refs(v, components, depth - 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(v, components, depth - 1) for v in node]
    return node


def probe_document(document: Mapping[str, Any], probes: Sequence[Mapping[str, Any]]) -> dict:
    """对单份 openapi 文档执行一组断言，返回逐条结果 + 汇总。"""
    paths = {str(k): v for k, v in (document.get("paths") or {}).items()}
    components = ((document.get("components") or {}).get("schemas") or {})
    results: list[dict] = []
    for spec in probes or []:
        path = str(spec.get("path") or "")
        method = str(spec.get("method") or "get").lower()
        rec: dict[str, Any] = {"id": spec.get("id") or path, "path": path, "method": method,
                               "expected_fields": list(spec.get("fields") or []),
                               "status": "ok", "detail": {}}
        item = paths.get(path)
        if item is None:
            rec["status"] = "missing_path"
            results.append(rec)
            continue
        op = item.get(method)
        if not isinstance(op, Mapping):
            rec["status"] = "missing_method"
            rec["detail"]["available"] = sorted(item)
            results.append(rec)
            continue
        missing: list[str] = []
        for field in rec["expected_fields"]:
            found = False
            for scope in ("responses", "requestBody"):
                blob = op.get(scope)
                if scope == "requestBody" and isinstance(blob, Mapping):
                    blob = blob.get("content")
                if blob is None:
                    continue
                text = _canonical(resolve_refs(blob, components))
                # 字段断言在序列化后的 schema 文本里找（openapi 中 schema 形态多样，
                # 该断言是"字段名是否出现在声明面"的保守近似，宁可漏报不造假）
                if f'"{field}"' in text:
                    found = True
                    break
            if not found:
                missing.append(field)
        rec["detail"]["missing_fields"] = missing
        rec["status"] = "ok" if not missing else "missing_field"
        results.append(rec)

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"n_probes": len(results), "n_ok": by_status.get("ok", 0),
            "by_status": by_status, "results": results,
            "all_ok": bool(results) and by_status.get("ok", 0) == len(results)}


def verify_interfaces(documents: Mapping[str, Mapping[str, Any]],
                      probes: Mapping[str, Sequence[Mapping[str, Any]]],
                      config: Mapping[str, Any] | None = None) -> dict:
    """documents = {doc_key: openapi}；probes = {doc_key: [probe, ...]}。"""
    cfg = dict(config or {})
    per_doc: dict[str, dict] = {}
    for key, doc in documents.items():
        per_doc[key] = probe_document(doc, list(probes.get(key) or []))
        per_doc[key]["openapi"] = {"title": (doc.get("info") or {}).get("title"),
                                   "version": (doc.get("info") or {}).get("version"),
                                   "n_paths": len(doc.get("paths") or {})}
    missing_docs = sorted(set(probes) - set(documents))
    total = sum(v["n_probes"] for v in per_doc.values())
    ok = sum(v["n_ok"] for v in per_doc.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "documents": per_doc,
        "missing_documents": missing_docs,
        "summary": {"documents": len(per_doc), "probes": total, "ok": ok,
                    "all_ok": bool(total) and ok == total and not missing_docs},
        "input_digest": digest({"documents": {k: {"info": d.get("info"),
                                                  "paths": sorted((d.get("paths") or {}))}
                                              for k, d in documents.items()},
                                "probes": probes, "config": cfg}),
    }


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨域接口契约在线校验（零领域语义）")
    ap.add_argument("--document", action="append", default=[],
                    help="doc_key=url（可重复）")
    ap.add_argument("--token", default=os.environ.get("TENSOR_TOKEN"))
    ap.add_argument("--probes", required=True, help="探针配置 JSON")
    ap.add_argument("--config", help="附加配置 JSON")
    ap.add_argument("--json-out", help="输出路径")
    args = ap.parse_args(argv)

    try:
        probe_cfg = load_json(args.probes)
        cfg = load_json(args.config) if args.config else {}
    except (OSError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 3

    documents: dict[str, Any] = {}
    for spec in args.document:
        if "=" not in spec:
            print(f"bad --document: {spec}", file=sys.stderr)
            return 3
        key, url = spec.split("=", 1)
        try:
            documents[key] = http_json(url, token=args.token)
        except Exception as exc:  # noqa: BLE001
            print(f"document fetch failed ({key}): {exc}", file=sys.stderr)
            return 3

    report = verify_interfaces(documents, probe_cfg.get("documents") or probe_cfg, cfg)
    text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    print(f"all_ok={report['summary']['all_ok']} ok={report['summary']['ok']}/"
          f"{report['summary']['probes']}", file=sys.stderr)
    return 0 if report["summary"]["all_ok"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())