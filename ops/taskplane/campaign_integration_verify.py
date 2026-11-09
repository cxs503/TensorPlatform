#!/usr/bin/env python3
"""campaign_integration_verify —— 战役**跨域集成验证层**（artifact schema 契约 × DAG 接口自洽 × 验收门汇总）。

为什么需要这一层
----------------
已有的两层各自解决一半问题，但**都不断言"跨域成果可组合"**：

* ``ops/taskplane/inheritance_gate.py``：单节点验收 + 同一 key 跨节点数值互裁
  （``quantities`` spread/reference 裁决）；
* ``ops/taskplane/campaign_gate_snapshot.py``：DB/API → 快照 + 门节点任务书判据审计叠层。

缺口是**接口面**：一个多域战役（求解器仓 × 船舶仓 × 平台仓）里的产物除了"数值对不对"，
还必须回答三个可机器裁的问题：

  (1) 每个节点声明的 artifact 是否**符合上游/下游约定的 JSON Schema**（形状契约）；
  (2) DAG 的每条依赖边 ``u → v`` 上，``v`` 声明的 ``requires`` 字段是否真被 ``u`` 的
      ``produces`` **覆盖**（键名 / 类型 / 量纲三重兼容）；
  (3) 全战役汇总时，``pass`` / ``honest_fail``（有据可查的诚实失败）/ ``unproven``
      （在飞、无证据、判据未测）/ ``fail``（未申报的失败）四类**必须分开计数**，
      且 honest-FAIL 预算（允许 ≤N 个）与 unproven 零容忍是可配置硬门。

本模块把这三件事做成纯函数 + 一个 CLI，不重复实现继承门的判据求值：单节点验收直接
调用 ``inheritance_gate.evaluate_inheritance_gate``，本层只在其上加**形状契约**与
**拓扑契约**两个维度，并把四类结局汇成门级裁决。

零领域语义（TensorPlatform 铁律）
-------------------------------
模块内**不含任何领域词条 / 域判据 / 域算例**：

* 哪些字段是 ``produces`` / ``requires``、类型与量纲字符串、schema 文件位置、
  哪些节点属于 honest-FAIL 台账 —— 全部来自 ``contract`` / ``config`` 两份注入文档；
* 判据文本一律来自 ``campaign_nodes.metadata.acceptance_criteria``（快照带入），
  本层不做文本切词，也不猜容差；
* 测试夹具用合成域名（``N-A``/``alpha``/``unit_x``），故任何域都可直接复用。

裁决判据（全部机算，落 ``criteria[]``）
--------------------------------------
  ``a_schema_all_valid``     所有合约节点的全部 artifact 通过 schema 校验（缺失=不通过）
  ``b_edges_consistent``     DAG 每条依赖边的 requires ⊆ 上游 produces 且类型/量纲兼容
  ``c_contract_coverage``    计划内每个节点都有合约条目（``require_full_coverage``）
  ``d_acceptance_budget``    ``n_pass >= min_pass_nodes`` 且 ``n_honest_fail <= max_honest_fail_nodes``
  ``e_no_undocumented_fail`` 不存在 ``fail`` 类节点（未申报的失败一律阻塞）
  ``f_no_unproven``          不存在 ``unproven`` 节点（``allow_unproven`` 可放开，默认零容忍）

``verdict = "pass"`` 当且仅当六条全过；否则 ``"not_ready"``。退出码 0/2/3，**未就绪绝不返回 0**。

确定性
------
报告不取 wall clock（``generated_at`` 仅在 config 显式给出时写入），``input_digest``
由 snapshot+contract+config 的规范化 JSON 派生 → 同一输入连跑两次逐字节相同。

CLI
---
    python3 ops/taskplane/campaign_integration_verify.py \
        --fetch-api --plan 357 --base-url http://127.0.0.1:8010/api/v1 \
        --contract artifacts/p143w2/integration_contract.json \
        --config   artifacts/p143w2/gate_config.json \
        --json-out artifacts/p143w2/integration_gate.json \
        --md-out   artifacts/p143w2/integration_gate.md

    # 离线复算（快照已落盘）
    python3 ops/taskplane/campaign_integration_verify.py --snapshot artifacts/.../plan_snapshot.json ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import inheritance_gate as ig  # noqa: E402  (同目录模块)

try:  # jsonschema 是平台既有依赖；缺失时退化为内置最小校验器（保持离线可跑）
    import jsonschema as _jsonschema  # type: ignore

    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - 环境相关
    _jsonschema = None  # type: ignore
    _HAS_JSONSCHEMA = False

SCHEMA_VERSION = "campaign_integration_verify/v1"
CONTRACT_VERSION = "campaign_integration_contract/v1"

CLASSES = ("pass", "honest_fail", "unproven", "fail")

DEFAULT_CONFIG: dict[str, Any] = {
    "min_pass_nodes": 1,
    "max_honest_fail_nodes": 0,
    "allow_unproven": False,
    "require_artifacts": True,
    "require_full_coverage": True,
    "edge_mode": "direct",            # direct | transitive
    "honest_fail_requires_evidence": True,
    "roots": ["."],
    "exclude_node_keys": [],
    "honest_fail": {},                # node_key -> {"reason": str, "evidence": str}
    "inheritance_gate": {},           # 透传给 inheritance_gate.evaluate_inheritance_gate
}

_TYPE_ALIASES = {
    "number": "number", "float": "number", "double": "number", "int": "number",
    "integer": "number", "num": "number",
    "str": "string", "string": "string", "text": "string",
    "bool": "boolean", "boolean": "boolean",
    "array": "array", "list": "array", "sequence": "array",
    "object": "object", "map": "object", "dict": "object", "mapping": "object",
    "seq": "array", "vector": "array", "ndarray": "array",
    "any": "any", "": "any",
}

_DIMENSIONLESS = {"", "1", "-", "dimensionless", "na", "n/a", "ratio", "count"}


# ─────────────────────────── 小工具 ───────────────────────────

def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def norm_type(value: Any) -> str:
    return _TYPE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def norm_unit(value: Any) -> str:
    unit = str(value or "").strip().lower().replace(" ", "")
    return "1" if unit in _DIMENSIONLESS else unit


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8")


def _resolve(path: str, roots: Sequence[str | Path]) -> Path | None:
    """工件/schema 路径解析：逐个 root 试，全部落空返回 None（不跨仓误判）。"""
    rel = str(path).replace("\\", "/").lstrip("./")
    if not rel:
        return None
    for root in roots or (".",):
        cand = Path(root) / rel
        if cand.is_file():
            return cand
    return None


# ─────────────────────────── 形状契约：artifact schema ───────────────────────────

def _minimal_validate(instance: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """内置最小 JSON Schema 子集（jsonschema 不可用时的降级通道）。"""
    errors: list[str] = []
    if not isinstance(schema, Mapping):
        return [f"{path}: schema_not_object"]
    expected = norm_type(schema.get("type"))
    if expected not in ("", "any"):
        ok = {
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "string": lambda v: isinstance(v, str),
            "boolean": lambda v: isinstance(v, bool),
            "array": lambda v: isinstance(v, list),
            "object": lambda v: isinstance(v, Mapping),
        }.get(expected, lambda v: True)(instance)
        if not ok:
            return [f"{path}: type_mismatch(expected={expected})"]
    if isinstance(instance, Mapping):
        for key in schema.get("required") or []:
            if key not in instance:
                errors.append(f"{path}: missing_required({key})")
        for key, sub in (schema.get("properties") or {}).items():
            if key in instance:
                errors.extend(_minimal_validate(instance[key], sub, f"{path}.{key}"))
    if isinstance(instance, list) and isinstance(schema.get("items"), Mapping):
        for i, item in enumerate(instance):
            errors.extend(_minimal_validate(item, schema["items"], f"{path}[{i}]"))
    return errors


def _validate_doc(instance: Any, schema_doc: Any) -> tuple[str | None, list[str], str]:
    if _HAS_JSONSCHEMA:
        validator = _jsonschema.Draft7Validator(schema_doc)  # type: ignore[union-attr]
        errs = [f"{'/'.join(str(x) for x in e.path)}:{e.message}"
                for e in validator.iter_errors(instance)]
        return ("valid" if not errs else "invalid"), errs[:10], "jsonschema"
    errs = _minimal_validate(instance, schema_doc)
    return ("valid" if not errs else "invalid"), errs[:10], "minimal"


def check_evidence_bundle(entry: Mapping[str, Any], evidence: Mapping[str, Any] | None,
                          roots: Sequence[str | Path]) -> dict | None:
    """跨仓战役的**主发布通道**：平台报告里的证据包按契约 schema 校验。

    本地工件路径在别的仓里解析不到，但报告证据包随快照全量到手 —— 因此把
    "证据包形状"本身当工件校验（缺 acceptance/artifacts 就是不可复算）。
    """
    ref = entry.get("evidence_schema")
    if not ref:
        return None
    rec: dict[str, Any] = {"path": "report:evidence", "status": "unknown", "errors": []}
    resolved = _resolve(str(ref), roots) if not isinstance(ref, Mapping) else None
    schema_doc = dict(ref) if isinstance(ref, Mapping) else None
    if schema_doc is None:
        if resolved is None:
            rec["status"] = "schema_missing"
            rec["errors"] = [f"evidence_schema_unresolved:{ref}"]
            return rec
        try:
            schema_doc = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rec["status"] = "schema_missing"
            rec["errors"] = [f"evidence_schema_unreadable:{exc}"]
            return rec
    rec["schema_source"] = str(ref)
    if not evidence:
        rec["status"] = "missing"
        rec["errors"] = ["no_evidence_bundle_published"]
        return rec
    status, errs, checker = _validate_doc(dict(evidence), schema_doc)
    rec.update(status=status, errors=errs, checker=checker)
    return rec


def check_artifacts(entry: Mapping[str, Any], roots: Sequence[str | Path],
                    evidence: Mapping[str, Any] | None = None,
                    reports: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """逐条 artifact 做「存在性 + schema 合规性」双检。缺失 = 不通过（不给白分）。"""
    decl = list(entry.get("artifacts") or [])
    results: list[dict] = []
    if evidence is None and reports:
        ev, _src, _warn = ig.extract_evidence(list(reports))
        evidence = ev or None
    bundle_rec = check_evidence_bundle(entry, evidence, roots)
    if bundle_rec is not None:
        results.append(bundle_rec)
    for item in decl:
        if isinstance(item, str):
            item = {"path": item}
        path = str(item.get("path") or "")
        rec: dict[str, Any] = {"path": path, "status": "unknown", "errors": []}
        resolved = _resolve(path, roots)
        if resolved is None:
            rec["status"] = "missing"
            rec["errors"] = ["artifact_path_unresolved"]
            results.append(rec)
            continue
        schema_ref = item.get("schema")
        schema_doc: Any = None
        if isinstance(schema_ref, Mapping):
            schema_doc = dict(schema_ref)
            rec["schema_source"] = "inline"
        elif isinstance(schema_ref, str) and schema_ref:
            sp = _resolve(schema_ref, roots)
            if sp is None:
                rec["status"] = "schema_missing"
                rec["errors"] = [f"schema_unresolved:{schema_ref}"]
                results.append(rec)
                continue
            try:
                schema_doc = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                rec["status"] = "schema_missing"
                rec["errors"] = [f"schema_unreadable:{exc}"]
                results.append(rec)
                continue
            rec["schema_source"] = schema_ref
        if schema_doc is None:
            rec["status"] = "unvalidated"
            rec["errors"] = ["schema_not_declared"]
            results.append(rec)
            continue
        try:
            instance = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rec["status"] = "json_error"
            rec["errors"] = [f"artifact_unreadable:{exc}"]
            results.append(rec)
            continue
        if _HAS_JSONSCHEMA:
            validator = _jsonschema.Draft7Validator(schema_doc)  # type: ignore[union-attr]
            errs = [f"{'/'.join(str(x) for x in e.path)}:{e.message}" for e in validator.iter_errors(instance)]
            rec["checker"] = "jsonschema"
        else:  # pragma: no cover - 环境相关
            errs = _minimal_validate(instance, schema_doc)
            rec["checker"] = "minimal"
        rec["status"] = "valid" if not errs else "invalid"
        rec["errors"] = errs[:10]
        results.append(rec)

    n_declared = len(decl) + (1 if bundle_rec is not None else 0)
    if not results:
        overall = "no_artifacts_declared"
    elif all(r["status"] == "valid" for r in results):
        overall = "valid"
    elif any(r["status"] in ("missing", "schema_missing", "json_error", "unvalidated") for r in results):
        overall = "incomplete"
    else:
        overall = "invalid"
    return {"status": overall, "n_declared": n_declared,
            "n_valid": sum(1 for r in results if r["status"] == "valid"),
            "items": results}


# ─────────────────────────── 拓扑契约：DAG 边接口自洽 ───────────────────────────

def _dep_closure(plan_nodes: Iterable[Mapping[str, Any]], node_id: Any,
                 mode: str = "direct") -> list[Any]:
    deps = {int(n["id"]): [int(d) for d in (n.get("dependencies") or [])]
            for n in plan_nodes if n.get("id") is not None}
    seen: list[int] = []
    stack = list(deps.get(int(node_id), [])) if node_id is not None else []
    while stack:
        d = stack.pop(0)
        if d in seen:
            continue
        seen.append(d)
        if mode == "transitive":
            stack.extend(deps.get(d, []))
    return seen


def node_supply(entry: Mapping[str, Any], reports: Sequence[Mapping[str, Any]],
                roots: Sequence[str | Path]) -> tuple[dict[str, dict], list[str]]:
    """上游节点的**真实供给面** = 它已发布证据里的量名。

    两条发布通道都认（多仓战役里主力通道是平台报告，而不是本地工件）：
      * 平台报告证据块 ``inheritance_evidence``（``extract_evidence``）；
      * 合约声明的工件 JSON（``quantities``/``acceptance``/``criteria``/``metrics``）。

    上游在合约里自填的 ``produces`` **不**进这里：需求方写什么都能"自洽"就是循环论证，
    声明只能在无实物时降级为 ``contract_declaration``（见 ``check_edges``）。
    """
    supply: dict[str, dict] = {}
    sources: list[str] = []

    def _absorb(block: Mapping[str, Any], source: str) -> int:
        found = 0
        for field in ("quantities", "acceptance", "criteria", "measurements", "metrics"):
            sub = block.get(field)
            if not isinstance(sub, Mapping):
                continue
            for k, v in sub.items():
                meta = v if isinstance(v, Mapping) else {"value": v}
                rec = {"type": norm_type(meta.get("type")), "unit": norm_unit(meta.get("unit")),
                       "source": source}
                supply.setdefault(str(k), rec)
                supply.setdefault(ig.normalize_key(str(k)), rec)
                found += 1
        return found

    ev, source, _warn = ig.extract_evidence(list(reports or []))
    if isinstance(ev, Mapping) and ev and _absorb(ev, str(source or "report")):
        sources.append(str(source or "report"))

    for item in entry.get("artifacts") or []:
        path = item if isinstance(item, str) else str(item.get("path") or "")
        resolved = _resolve(path, roots)
        if resolved is None:
            continue
        try:
            doc = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        ev2 = doc.get(ig.EVIDENCE_KEY)
        if not isinstance(ev2, Mapping):
            ev2 = doc
        if isinstance(ev2, Mapping) and _absorb(ev2, f"artifact:{path}"):
            sources.append(f"artifact:{path}")
    return supply, sources


def check_edges(plan_nodes: Sequence[Mapping[str, Any]],
                contract_nodes: Mapping[str, Mapping[str, Any]],
                roots: Sequence[str | Path] = (".",),
                allow_declared_only: bool = False,
                reports_by_node: Mapping[Any, Sequence[Mapping[str, Any]]] | None = None) -> dict:
    """每条依赖边：下游 ``requires`` 的键须被**上游已发布工件**覆盖（键/类型/量纲三重）。

    合约里上游自填的 ``produces`` 只作为"声明供给"，命中时边标 ``consistent_declared_only``
    —— 声明不是证据；默认（``allow_declared_only=False``）不计为通过。
    """
    id_to_key = {}
    for key, entry in contract_nodes.items():
        nid = entry.get("node_id")
        if nid is not None:
            id_to_key[int(nid)] = key

    declared: dict[str, dict[str, dict]] = {}
    actual: dict[str, dict[str, dict]] = {}
    supply_sources: dict[str, list[str]] = {}
    for key, entry in contract_nodes.items():
        fields: dict[str, dict] = {}
        for f in entry.get("produces") or []:
            if isinstance(f, str):
                f = {"key": f}
            fkey = str(f.get("key") or "").strip()
            if fkey:
                fields[fkey] = {"type": norm_type(f.get("type")), "unit": norm_unit(f.get("unit"))}
        declared[key] = fields
        got, srcs = node_supply(entry, list((reports_by_node or {}).get(entry.get("node_id")) or []),
                               roots)
        actual[key] = got
        supply_sources[key] = srcs

    edges: list[dict] = []
    for node in plan_nodes:
        key = id_to_key.get(int(node["id"])) if node.get("id") is not None else None
        if key is None:
            continue
        entry = contract_nodes[key]
        requires = []
        for f in entry.get("requires") or []:
            if isinstance(f, str):
                f = {"key": f}
            if str(f.get("key") or "").strip():
                requires.append({"key": str(f["key"]).strip(),
                                 "type": norm_type(f.get("type")),
                                 "unit": norm_unit(f.get("unit"))})
        if not requires:
            continue
        dep_ids = _dep_closure(plan_nodes, node.get("id"), mode="direct")
        upstream_keys = [id_to_key[d] for d in dep_ids if d in id_to_key]
        missing_contract = [d for d in dep_ids if d not in id_to_key]
        available: dict[str, dict] = {}
        for uk in upstream_keys:
            for src in (actual.get(uk) or {}, ):
                for fkey, meta in src.items():
                    available.setdefault(fkey, meta)
        for uk in upstream_keys:
            for fkey, meta in (declared.get(uk) or {}).items():
                if fkey not in available:
                    available[fkey] = dict(meta, source="contract_declaration")
        edge: dict[str, Any] = {"downstream": key, "upstream": upstream_keys,
                                "missing_upstream_contract": missing_contract,
                                "upstream_supply_artifacts": {uk: supply_sources.get(uk, [])
                                                              for uk in upstream_keys},
                                "requires": requires, "violations": [], "declared_only": []}
        for req in requires:
            have = available.get(req["key"]) or available.get(ig.normalize_key(req["key"]))
            if have is None:
                edge["violations"].append({"key": req["key"], "kind": "missing_required_key"})
                continue
            if req["type"] not in ("", "any") and have["type"] not in ("", "any") \
                    and req["type"] != have["type"]:
                edge["violations"].append({"key": req["key"], "kind": "type_mismatch",
                                           "required": req["type"], "produced": have["type"]})
            if req["unit"] != "1" and have["unit"] != "1" and req["unit"] != have["unit"]:
                edge["violations"].append({"key": req["key"], "kind": "unit_mismatch",
                                           "required": req["unit"], "produced": have["unit"]})
            if str(have.get("source", "")).startswith("contract_declaration"):
                edge["declared_only"].append(req["key"])
        if not upstream_keys:
            edge["status"] = "unverifiable_no_upstream_contract"
        elif missing_contract:
            edge["status"] = "unverifiable_missing_upstream_contract"
        elif edge["violations"]:
            edge["status"] = "inconsistent"
        elif edge["declared_only"] and not allow_declared_only:
            edge["status"] = "consistent_declared_only"
        else:
            edge["status"] = "consistent"
        edges.append(edge)

    def count(pred):
        return sum(1 for e in edges if pred(e["status"]))

    return {"n_edges": len(edges), "n_consistent": count(lambda s: s == "consistent"),
            "n_declared_only": count(lambda s: s == "consistent_declared_only"),
            "n_inconsistent": count(lambda s: s == "inconsistent"),
            "n_unverifiable": count(lambda s: s.startswith("unverifiable")),
            "allow_declared_only": bool(allow_declared_only),
            "edges": edges}


# ─────────────────────────── 结局分类 + 汇总 ───────────────────────────

def classify(node_eval: Mapping[str, Any], honest_fail: Mapping[str, Any] | None,
             cfg: Mapping[str, Any]) -> tuple[str, str]:
    """把继承门的单节点裁决映射到四分类之一，返回 (class, reason)。"""
    if node_eval.get("accepted"):
        return "pass", "inheritance_gate_accepted"
    blockers = [str(b) for b in node_eval.get("blockers") or []]
    if honest_fail:
        if cfg.get("honest_fail_requires_evidence", True) and not str(
                honest_fail.get("evidence") or "").strip():
            return "fail", "honest_fail_declared_without_evidence"
        return "honest_fail", str(honest_fail.get("reason") or "declared_honest_fail")

    hard_fail = [b for b in blockers if b.startswith("criterion_failed")]
    if hard_fail:
        return "fail", hard_fail[0]
    if any(b.startswith("reproduction_verdict=fail") or b.startswith("mission_failed")
           for b in blockers):
        return "fail", "terminal_failure_signal"
    unproven_markers = ("criterion_unproven", "node_not_done", "no_evidence",
                        "mission_missing", "reproduction_verdict_missing")
    if any(b.startswith(unproven_markers) for b in blockers) or not blockers:
        return "unproven", (blockers[0] if blockers else "no_blockers_but_not_accepted")
    return "fail", blockers[0]


def verify(snapshot: Mapping[str, Any], contract: Mapping[str, Any],
           config: Mapping[str, Any] | None = None) -> dict:
    """纯函数入口：snapshot + contract + config → 跨域集成验证报告（可复算）。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in dict(config or {}).items() if v is not None})
    roots = cfg.get("roots") or ["."]
    plan_nodes = list(snapshot.get("nodes") or [])
    contract_nodes = {str(k): dict(v) for k, v in (contract.get("nodes") or {}).items()}
    excluded = {str(k) for k in (cfg.get("exclude_node_keys") or [])}

    gate_cfg = dict(cfg.get("inheritance_gate") or {})
    gate_cfg.setdefault("roots", roots)
    gate_cfg.setdefault("exclude_node_ids", [])
    gate_report = ig.evaluate_inheritance_gate(snapshot, gate_cfg)
    evals = {int(ev["node_id"]): ev for ev in gate_report.get("nodes") or []
             if ev.get("node_id") is not None}

    honest_map = {str(k): dict(v) if isinstance(v, Mapping) else {"reason": str(v)}
                  for k, v in (cfg.get("honest_fail") or {}).items()}

    reports_by_node: dict[Any, list[dict]] = {}
    for node in plan_nodes:
        nid, mid = node.get("id"), node.get("mission_id")
        if nid is None or mid is None:
            continue
        items = (snapshot.get("reports") or {}).get(str(mid)) or []
        reports_by_node[int(nid)] = sorted(list(items),
                                           key=lambda r: str(r.get("created_at") or ""))

    rows: list[dict] = []
    counts = {c: 0 for c in CLASSES}
    id_to_key = {int(e["node_id"]): k for k, e in contract_nodes.items()
                 if e.get("node_id") is not None}
    for node in sorted(plan_nodes, key=lambda n: (n.get("id") is None, n.get("id"))):
        nid = node.get("id")
        key = id_to_key.get(int(nid)) if nid is not None else None
        if key is None or key in excluded:
            continue
        entry = contract_nodes[key]
        ev = evals.get(int(nid)) or {"node_id": nid, "accepted": False,
                                     "blockers": ["node_eval_missing"], "criteria": [],
                                     "criteria_declared": 0, "criteria_passed": 0}
        cls, reason = classify(ev, honest_map.get(key), cfg)
        art = check_artifacts(entry, roots, reports=reports_by_node.get(int(nid)) or [])
        if cfg.get("require_artifacts", True) and art["status"] != "valid" and cls == "pass":
            cls, reason = "unproven", f"artifact_contract:{art['status']}"
        counts[cls] += 1
        rows.append({
            "node_key": key, "node_id": nid, "ext_id": entry.get("ext_id") or node.get("metadata", {}).get("ext_id"),
            "repo": entry.get("repo") or (node.get("metadata") or {}).get("repo"),
            "status": node.get("status"), "reproduction_verdict": node.get("reproduction_verdict"),
            "classification": cls, "reason": reason,
            "criteria_declared": ev.get("criteria_declared", 0),
            "criteria_passed": ev.get("criteria_passed", 0),
            "artifact_status": art["status"], "artifacts": art["items"],
            "blockers": [str(b) for b in (ev.get("blockers") or [])][:8],
        })

    edge_report = check_edges(plan_nodes, contract_nodes, roots,
                              allow_declared_only=bool(cfg.get("allow_declared_only_edges", False)),
                              reports_by_node=reports_by_node)

    plan_ids = {int(n["id"]) for n in plan_nodes if n.get("id") is not None}
    contracted_ids = {int(e["node_id"]) for e in contract_nodes.values() if e.get("node_id") is not None}
    uncovered = sorted(plan_ids - contracted_ids)

    criteria = [
        {"id": "a_schema_all_valid",
         "passed": bool(rows) and all(r["artifact_status"] == "valid" for r in rows),
         "detail": {"valid": sum(1 for r in rows if r["artifact_status"] == "valid"),
                    "total": len(rows),
                    "statuses": sorted({r["artifact_status"] for r in rows})}},
        {"id": "b_edges_consistent",
         "passed": edge_report["n_inconsistent"] == 0 and edge_report["n_unverifiable"] == 0
                   and edge_report["n_declared_only"] == 0,
         "detail": {k: edge_report[k] for k in
                    ("n_edges", "n_consistent", "n_declared_only",
                     "n_inconsistent", "n_unverifiable")}},
        {"id": "c_contract_coverage",
         "passed": (not cfg.get("require_full_coverage", True)) or not uncovered,
         "detail": {"uncovered_node_ids": uncovered,
                    "contracted": len(contracted_ids), "plan_nodes": len(plan_ids)}},
        {"id": "d_acceptance_budget",
         "passed": counts["pass"] >= int(cfg["min_pass_nodes"])
                   and counts["honest_fail"] <= int(cfg["max_honest_fail_nodes"]),
         "detail": {"pass": counts["pass"], "min_pass_nodes": int(cfg["min_pass_nodes"]),
                    "honest_fail": counts["honest_fail"],
                    "max_honest_fail_nodes": int(cfg["max_honest_fail_nodes"])}},
        {"id": "e_no_undocumented_fail",
         "passed": counts["fail"] == 0,
         "detail": {"fail": counts["fail"],
                    "nodes": [r["node_key"] for r in rows if r["classification"] == "fail"]}},
        {"id": "f_no_unproven",
         "passed": bool(cfg.get("allow_unproven", False)) or counts["unproven"] == 0,
         "detail": {"unproven": counts["unproven"],
                    "allow_unproven": bool(cfg.get("allow_unproven", False)),
                    "nodes": [r["node_key"] for r in rows if r["classification"] == "unproven"]}},
    ]

    verdict = "pass" if all(c["passed"] for c in criteria) else "not_ready"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": str(contract.get("schema_version") or CONTRACT_VERSION),
        "plan": {"id": (snapshot.get("plan") or {}).get("id"), "name": (snapshot.get("plan") or {}).get("name")},
        "verdict": verdict,
        "counts": dict(counts, nodes=len(rows)),
        "criteria": criteria,
        "blockers": [f"{c['id']}:{json.dumps(c['detail'], ensure_ascii=False, sort_keys=True)}"
                     for c in criteria if not c["passed"]],
        "nodes": rows,
        "edges": edge_report,
        "inheritance_gate": {"verdict": gate_report.get("verdict"),
                             "score": gate_report.get("score"),
                             "hard_gates": gate_report.get("hard_gates")},
        "config_digest": digest(cfg),
        "input_digest": digest({"plan": snapshot.get("plan"), "nodes": plan_nodes,
                                "contract": contract, "config": cfg}),
        "checker": {"jsonschema": _HAS_JSONSCHEMA, "fallback_minimal": not _HAS_JSONSCHEMA},
    }
    if cfg.get("generated_at"):
        report["generated_at"] = cfg["generated_at"]
    return report


# ─────────────────────────── 渲染 ───────────────────────────

_CLS_ORDER = {c: i for i, c in enumerate(CLASSES)}


def render_markdown(report: Mapping[str, Any]) -> str:
    plan = report.get("plan") or {}
    counts = report.get("counts") or {}
    lines = [
        f"# 跨域集成验证门报告 · plan {plan.get('id')} {plan.get('name') or ''}".rstrip(),
        "",
        f"- verdict: **{report.get('verdict')}**",
        f"- input_digest: `{report.get('input_digest')}`",
        f"- 结局分布: pass={counts.get('pass')} honest_fail={counts.get('honest_fail')} "
        f"unproven={counts.get('unproven')} fail={counts.get('fail')} / total={counts.get('nodes')}",
        "",
        "## 判据",
        "",
        "| 判据 | 结果 | 细节 |",
        "|---|---|---|",
    ]
    for c in report.get("criteria") or []:
        detail = json.dumps(c.get("detail"), ensure_ascii=False, sort_keys=True)
        lines.append(f"| {c['id']} | {'PASS' if c['passed'] else 'BLOCK'} | `{detail}` |")
    lines += ["", "## 节点矩阵", "",
              "| 节点 | 仓 | status | 分类 | 判据 passed/declared | artifact | 原因 |",
              "|---|---|---|---|---|---|---|"]
    for row in report.get("nodes") or []:
        lines.append(
            f"| {row['node_key']} | {row.get('repo') or '-'} | {row.get('status')} | "
            f"{row['classification']} | {row['criteria_passed']}/{row['criteria_declared']} | "
            f"{row['artifact_status']} | {str(row['reason'])[:80]} |")
    edges = report.get("edges") or {}
    lines += ["", "## DAG 边接口自洽", "",
              f"edges={edges.get('n_edges')} consistent={edges.get('n_consistent')} "
              f"inconsistent={edges.get('n_inconsistent')} unverifiable={edges.get('n_unverifiable')}",
              ""]
    for e in edges.get("edges") or []:
        vio = ";".join(f"{v['key']}:{v['kind']}" for v in e.get("violations") or []) or "-"
        lines.append(f"- {e.get('upstream')} → **{e['downstream']}**: {e['status']} ({vio})")
    lines += ["", "## 阻塞项", ""]
    lines += [f"- {b}" for b in report.get("blockers") or []] or ["- （无）"]
    return "\n".join(lines) + "\n"


# ─────────────────────────── CLI ───────────────────────────

def _fetch_snapshot(base_url: str, plan_id: int, token: str | None) -> dict:
    return ig.build_snapshot_from_api(base_url, plan_id, token)


def _login(base_url: str, user: str, password: str) -> str | None:
    import urllib.request

    url = base_url.rstrip("/") + "/auth/login"
    payload = json.dumps({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - 内网只读
            return json.loads(resp.read().decode("utf-8")).get("access_token")
    except Exception:  # pragma: no cover - 环境相关
        return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="战役跨域集成验证门（零领域语义）")
    ap.add_argument("--snapshot", help="已落盘的快照 JSON（离线复算）")
    ap.add_argument("--fetch-api", action="store_true", help="从 8010 只读拉取快照")
    ap.add_argument("--plan", type=int, help="plan_id（--fetch-api 时必填）")
    ap.add_argument("--base-url", default=os.environ.get("TENSOR_API_BASE",
                                                         "http://127.0.0.1:8010/api/v1"))
    ap.add_argument("--token", default=os.environ.get("TENSOR_TOKEN"))
    ap.add_argument("--user", default=os.environ.get("TENSOR_AGENT_USER"))
    ap.add_argument("--password", default=os.environ.get("TENSOR_AGENT_PASS"))
    ap.add_argument("--contract", required=True, help="集成合约 JSON")
    ap.add_argument("--config", help="门配置 JSON（可选，缺省 DEFAULT_CONFIG）")
    ap.add_argument("--json-out", help="报告 JSON 输出路径")
    ap.add_argument("--md-out", help="报告 Markdown 输出路径")
    ap.add_argument("--snapshot-out", help="拉取的快照落盘路径（审计留痕）")
    args = ap.parse_args(argv)

    try:
        contract = load_json(args.contract)
    except (OSError, ValueError) as exc:
        print(f"contract error: {exc}", file=sys.stderr)
        return 3
    cfg = {}
    if args.config:
        try:
            cfg = load_json(args.config)
        except (OSError, ValueError) as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 3
    try:
        if args.fetch_api:
            if not args.plan:
                print("--fetch-api requires --plan", file=sys.stderr)
                return 3
            token = args.token
            if not token and args.user and args.password:
                token = _login(args.base_url, args.user, args.password)
            snapshot = _fetch_snapshot(args.base_url, args.plan, token)
        elif args.snapshot:
            snapshot = load_json(args.snapshot)
        else:
            print("need --snapshot or --fetch-api", file=sys.stderr)
            return 3
    except Exception as exc:  # noqa: BLE001 - 输入/网络错误统一归 3
        print(f"snapshot error: {exc}", file=sys.stderr)
        return 3

    if args.snapshot_out:
        _write_json(args.snapshot_out, snapshot)
    report = verify(snapshot, contract, cfg)
    if args.json_out:
        _write_json(args.json_out, report)
    md = render_markdown(report)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
    else:
        print(md)
    print(f"verdict={report['verdict']} counts={report['counts']}", file=sys.stderr)
    return 0 if report["verdict"] == "pass" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())