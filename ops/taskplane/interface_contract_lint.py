#!/usr/bin/env python3
"""interface_contract_lint —— 跨域集成合约的**设计期**静态自洽检查（零领域语义）。

为什么需要它（与证据期检查的分工）
------------------------------------
``campaign_integration_verify`` 只在**上游发布证据**之后才能裁「拓扑契约」：
供给只认报告证据/工件里的量名，不认合约自填的 ``produces``（否则检查退化为恒真）。
代价是——战役早期全部节点在飞时，那条判据只能是 ``unproven``，**设计错误也就
一路带到证据期才炸**（那时返工成本最高）。

本模块补的是**设计期**那一格：不碰证据、不裁验收，只对合约本身做静态自洽检查，
把「上下游客接口面写错了」这类问题在派单前就暴露出来。两者严格分工：

* 本模块（设计期）：合约内部自洽 —— **不构成任何验收通过**
* 集成门（证据期）：真实供给 vs 需求 —— 唯一可计入验收的判据

判据（全部机算，落 ``findings[]``，``severity=error`` 即不通过）
----------------------------------------------------------------
  L1 ``node_resolved``       每条合约的 ``node_id`` 必须能在计划快照里解析到节点
  L2 ``identity_unique``     ``node_id`` / ``ext_id`` 不得重复（一个节点两条合约）
  L3 ``dep_contract_present`` DAG 依赖边的上游必须也有合约条目（否则边不可验）
  L4 ``requires_declared``   ``requires`` 的每个键必须有**某个上游**在合约里 declare
  L5 ``unit_type_conflict``  同一键被多节点 declare 时类型/量纲必须一致（歧义即错）
  L6 ``orphan_requires``     ``requires`` 的键不可只由自己 declare（自给自足=写错）

零领域语义：状态名、键名、类型、量纲、节点 id 全部来自注入的 ``contract`` 与
``plan_nodes``；测试夹具用 ``alpha_key`` / ``unit_x`` 这类合成名字。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import campaign_integration_verify as civ  # noqa: E402  (复用类型/量纲归一化)

SCHEMA_VERSION = "interface_contract_lint/v1"

ERROR = "error"
WARN = "warn"


def _fields(raw: Any) -> list[dict[str, str]]:
    """把 produces/requires 的多种写法归一成 [{key,type,unit}]。"""
    out: list[dict[str, str]] = []
    if isinstance(raw, Mapping):
        items: Iterable[Any] = [{"key": k, **(v if isinstance(v, Mapping) else {})}
                                for k, v in raw.items()]
    else:
        items = raw or []
    for item in items:
        if isinstance(item, str):
            item = {"key": item}
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        out.append({"key": key,
                    "type": civ.norm_type(item.get("type")),
                    "unit": civ.norm_unit(item.get("unit"))})
    return out


def lint_contract(contract: Mapping[str, Any],
                  plan_nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """静态检查合约自洽性；返回 findings + ok（无 error 即 ok）。"""
    nodes = dict((contract.get("nodes") or {}))
    plan_by_id = {int(n["id"]): n for n in plan_nodes if n.get("id") is not None}
    findings: list[dict[str, Any]] = []
    counters = {k: 0 for k in
                ("L1_node_resolved", "L2_identity_unique", "L3_dep_contract_present",
                 "L4_requires_declared", "L5_unit_type_conflict", "L6_orphan_requires")}

    # L1 / L2：身份面
    seen_ids: dict[int, str] = {}
    seen_ext: dict[str, str] = {}
    key_of_id: dict[int, str] = {}
    for key, entry in sorted(nodes.items()):
        nid = entry.get("node_id")
        if nid is None or int(nid) not in plan_by_id:
            counters["L1_node_resolved"] += 1
            findings.append({"rule": "L1_node_resolved", "severity": ERROR,
                             "node": key, "detail": f"node_id_unresolved:{nid}"})
        else:
            key_of_id[int(nid)] = key
            if int(nid) in seen_ids:
                counters["L2_identity_unique"] += 1
                findings.append({"rule": "L2_identity_unique", "severity": ERROR,
                                 "node": key,
                                 "detail": f"node_id_duplicate:{nid}({seen_ids[int(nid)]})"})
            seen_ids[int(nid)] = key
        ext = str(entry.get("ext_id") or "")
        if ext:
            if ext in seen_ext:
                counters["L2_identity_unique"] += 1
                findings.append({"rule": "L2_identity_unique", "severity": ERROR,
                                 "node": key, "detail": f"ext_id_duplicate:{ext}"})
            seen_ext[ext] = key

    # 键 → [(node_key, type, unit)] 的声明面
    produced: dict[str, list[tuple[str, str, str]]] = {}
    for key, entry in nodes.items():
        for f in _fields(entry.get("produces")):
            produced.setdefault(f["key"], []).append((key, f["type"], f["unit"]))

    # L3 / L4 / L6：拓扑面
    for key, entry in sorted(nodes.items()):
        requires = _fields(entry.get("requires"))
        if not requires:
            continue
        nid = entry.get("node_id")
        deps = [int(d) for d in ((plan_by_id.get(int(nid)) or {}).get("dependencies") or [])] \
            if nid is not None else []
        up_keys = [key_of_id[d] for d in deps if d in key_of_id]
        missing_contract = [d for d in deps if d not in key_of_id]
        for d in missing_contract:
            counters["L3_dep_contract_present"] += 1
            findings.append({"rule": "L3_dep_contract_present", "severity": ERROR,
                             "node": key, "detail": f"upstream_without_contract:{d}"})
        sibling_deps = set(up_keys)
        for req in requires:
            sources = produced.get(req["key"]) or []
            upstream_sources = [s for s in sources if s[0] in sibling_deps]
            if not upstream_sources:
                if [s for s in sources if s[0] == key]:
                    counters["L6_orphan_requires"] += 1
                    findings.append({"rule": "L6_orphan_requires", "severity": ERROR,
                                     "node": key,
                                     "detail": f"self_supplied_requirement:{req['key']}"})
                else:
                    counters["L4_requires_declared"] += 1
                    findings.append({"rule": "L4_requires_declared", "severity": ERROR,
                                     "node": key,
                                     "detail": f"no_upstream_declares:{req['key']}"})
                continue
            for src_key, stype, sunit in upstream_sources:
                if req["type"] not in ("", "any") and stype not in ("", "any") \
                        and req["type"] != stype:
                    counters["L5_unit_type_conflict"] += 1
                    findings.append({"rule": "L5_unit_type_conflict", "severity": ERROR,
                                     "node": key,
                                     "detail": f"{req['key']}:type {req['type']}!={stype} "
                                               f"(from {src_key})"})
                if req["unit"] != "1" and sunit != "1" and req["unit"] != sunit:
                    counters["L5_unit_type_conflict"] += 1
                    findings.append({"rule": "L5_unit_type_conflict", "severity": ERROR,
                                     "node": key,
                                     "detail": f"{req['key']}:unit {req['unit']}!={sunit} "
                                               f"(from {src_key})"})

    # L5 另一面：同一键被多节点 declare 出互相冲突的类型/量纲（歧义）
    for k, srcs in sorted(produced.items()):
        types = {s[1] for s in srcs if s[1] not in ("", "any")}
        units = {s[2] for s in srcs if s[2] not in ("1", "")}
        if len(types) > 1 or len(units) > 1:
            counters["L5_unit_type_conflict"] += 1
            findings.append({"rule": "L5_unit_type_conflict", "severity": ERROR,
                             "node": ",".join(sorted(s[0] for s in srcs)),
                             "detail": f"ambiguous_declaration:{k} "
                                       f"types={sorted(types)} units={sorted(units)}"})

    errors = [f for f in findings if f["severity"] == ERROR]
    warnings = [f for f in findings if f["severity"] == WARN]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "counts": {"nodes": len(nodes), "plan_nodes": len(plan_by_id),
                   "errors": len(errors), "warnings": len(warnings)},
        "rule_violations": {k: v for k, v in counters.items() if v},
        "findings": findings,
        "input_digest": civ.digest({"contract": contract, "nodes": plan_nodes}),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    c = report.get("counts") or {}
    lines = [
        "# 集成合约设计期静态检查",
        "",
        f"- ok: **{report.get('ok')}**",
        f"- input_digest: `{report.get('input_digest')}`",
        f"- 合约节点 {c.get('nodes')} / 计划节点 {c.get('plan_nodes')} / "
        f"error {c.get('errors')} / warn {c.get('warnings')}",
        "",
    ]
    if report.get("findings"):
        lines += ["| rule | severity | node | detail |", "|---|---|---|---|"]
        for f in report["findings"]:
            lines.append(f"| {f['rule']} | {f['severity']} | {f.get('node')} | "
                         f"`{f.get('detail')}` |")
    else:
        lines.append("六条规则零违规：接口声明面自洽（**不代表证据面已可验收**）。")
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="集成合约设计期静态检查（零领域语义）")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--snapshot", required=True, help="战役快照 JSON（取 plan 节点/依赖）")
    ap.add_argument("--json-out")
    ap.add_argument("--md-out")
    args = ap.parse_args(argv)
    try:
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 3
    report = lint_contract(contract, list(snapshot.get("nodes") or []))
    if args.json_out:
        civ._write_json(args.json_out, report)  # noqa: SLF001 - 同族模块
    md = render_markdown(report)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
    else:
        print(md)
    print(f"ok={report['ok']} errors={report['counts']['errors']}", file=sys.stderr)
    return 0 if report["ok"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())