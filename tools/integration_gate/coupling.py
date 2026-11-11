"""跨节点耦合闸：数据交换接口契约校验 + 假设口径一致性检查（零领域语义）。

两份**数据声明**驱动，代码里没有任何物理/标准/装备词条：

``interfaces.json``
    节点间数据交换接口契约。每条边声明
    ``producer`` / ``consumer`` / ``payload{id, unit, coverage{min,max}}`` /
    ``requirement{unit, min, max}`` / ``doc_ref``。
    闸做五件机器可查的事：①契约文档是否存在（``doc_ref`` 且文件可哈希）；
    ②生产方/消费方都在战役节点表里；③两侧单位一致；④消费方需求区间被生产方
    声明适用区间覆盖；⑤生产方是否有可核证据。任一不满足 → 该接口**不得**判
    VALIDATED（fail-closed，绝不默认通过）。

``assumptions.json``
    跨节点共享假设登记表。每键声明 ``kind``：
    ``edition``（标准版本串必须逐字一致）/ ``value``（同一量在容差内一致）/
    ``law``（同一条经验律的多参数声明必须自洽：``implied`` 给出**由其它参数推出
    目标参数**的表达式，闸用受限 AST 求值后与声明值比对）。
    违约分三种：``CONTRADICTED``（跨节点冲突）/ ``IMPLIED_MISMATCH``（节点内部
    声明与律的自洽性冲突）/ ``UNDETERMINED``（声明不全，不许当一致）。
    每条冲突必须带 ``mitigation``：``pair_downgrade`` 要求被引用的互裁对这一对
    真的降级（引用的 pair 不存在或未降级 → 该冲突计为未处置）。

设计纪律：本模块只做字符串/区间/表达式的机械比对，不解释量的物理含义；
量的语义、单位、适用域、标准版本全部来自数据文件，因此可复用到任意战役。
"""

from __future__ import annotations

import ast
import math
from typing import Any

INTERFACES_SCHEMA = "tp.integration.interfaces/v1"
ASSUMPTIONS_SCHEMA = "tp.integration.assumptions/v1"

IFACE_STATUSES = ("VALIDATED", "CONTRACT_MISMATCH", "UNATTESTED", "UNDOCUMENTED")
ASSUMPTION_STATUSES = ("AGREED", "CONTRADICTED", "IMPLIED_MISMATCH", "UNDETERMINED")
MITIGATIONS = ("pair_downgrade", "reference_resolved")
DOWNGRADING_VERDICTS = ("DISAGREE", "DEGENERATE")

_DIMENSIONLESS = {"-", "", "1", "dimensionless", "nd", "none"}


class CouplingSchemaError(ValueError):
    """契约/假设声明文件违约：调用方必须 fail-closed（不产出任何 AGREE）。"""


# ── schema helpers ────────────────────────────────────────────────────────
def _req(d: Any, k: str, where: str) -> Any:
    if not isinstance(d, dict) or k not in d:
        raise CouplingSchemaError(f"{where}.{k}: missing")
    return d[k]


def _num(d: Any, k: str, where: str) -> float:
    v = _req(d, k, where)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CouplingSchemaError(f"{where}.{k}: expected number")
    if not math.isfinite(float(v)):
        raise CouplingSchemaError(f"{where}.{k}: expected finite number")
    return float(v)


def _str(d: Any, k: str, where: str) -> str:
    v = _req(d, k, where)
    if not isinstance(v, str) or not v:
        raise CouplingSchemaError(f"{where}.{k}: expected non-empty string")
    return v


def _range(d: Any, where: str) -> tuple[float, float]:
    lo, hi = _num(d, "min", where), _num(d, "max", where)
    if lo > hi:
        raise CouplingSchemaError(f"{where}: min {lo} > max {hi}")
    return lo, hi


def validate_interfaces(doc: Any, where: str = "interfaces") -> dict[str, Any]:
    if _req(doc, "schema", where) != INTERFACES_SCHEMA:
        raise CouplingSchemaError(f"{where}: schema must be {INTERFACES_SCHEMA}")
    edges = _req(doc, "interfaces", where)
    if not isinstance(edges, list) or not edges:
        raise CouplingSchemaError(f"{where}.interfaces: expected non-empty list")
    seen: set[str] = set()
    for i, e in enumerate(edges):
        w = f"{where}.interfaces[{i}]"
        eid = _str(e, "id", w)
        if eid in seen:
            raise CouplingSchemaError(f"{w}: duplicate interface id {eid!r}")
        seen.add(eid)
        _str(e, "producer", w)
        _str(e, "consumer", w)
        payload = _req(e, "payload", w)
        _str(payload, "id", f"{w}.payload")
        _str(payload, "unit", f"{w}.payload")
        fields = payload.get("fields") or []
        if not isinstance(fields, list) or any(not isinstance(x, str) for x in fields):
            raise CouplingSchemaError(f"{w}.payload.fields: expected list of strings")
        _range(_req(payload, "coverage", f"{w}.payload"), f"{w}.payload.coverage")
        req = _req(e, "requirement", w)
        _str(req, "unit", f"{w}.requirement")
        _range(req, f"{w}.requirement")
        if req.get("fallback") not in (None, "clamp_with_status", "reject"):
            raise CouplingSchemaError(
                f"{w}.requirement.fallback: only clamp_with_status|reject allowed")
        _str(e, "doc_ref", w)
    return doc


def validate_assumptions(doc: Any, where: str = "assumptions") -> dict[str, Any]:
    if _req(doc, "schema", where) != ASSUMPTIONS_SCHEMA:
        raise CouplingSchemaError(f"{where}: schema must be {ASSUMPTIONS_SCHEMA}")
    keys = _req(doc, "keys", where)
    if not isinstance(keys, list) or not keys:
        raise CouplingSchemaError(f"{where}.keys: expected non-empty list")
    seen: set[str] = set()
    for i, k in enumerate(keys):
        w = f"{where}.keys[{i}]"
        key = _str(k, "key", w)
        if key in seen:
            raise CouplingSchemaError(f"{w}: duplicate assumption key {key!r}")
        seen.add(key)
        kind = _str(k, "kind", w)
        if kind not in ("edition", "value", "law"):
            raise CouplingSchemaError(f"{w}.kind: {kind!r} not in edition|value|law")
        decls = _req(k, "declarations", w)
        if not isinstance(decls, list):
            raise CouplingSchemaError(f"{w}.declarations: expected list")
        for j, d in enumerate(decls):
            dw = f"{w}.declarations[{j}]"
            _str(d, "node", dw)
            src = _req(d, "source", dw)
            _str(src, "ref", f"{dw}.source")
            sha = _str(src, "sha256", f"{dw}.source")
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise CouplingSchemaError(f"{dw}.source.sha256: expected 64 lowercase hex")
            if kind == "law":
                params = _req(d, "params", dw)
                if not isinstance(params, dict) or not params:
                    raise CouplingSchemaError(f"{dw}.params: expected non-empty object")
                for pk, pv in params.items():
                    if isinstance(pv, bool) or not isinstance(pv, (int, float)):
                        raise CouplingSchemaError(f"{dw}.params.{pk}: expected number")
            else:
                _req(d, "value", dw)
        for j, imp in enumerate(k.get("implied") or []):
            iw = f"{w}.implied[{j}]"
            _str(imp, "node", iw)
            _str(imp, "expr", iw)
            _str(imp, "target", iw)
            _num(imp, "tol", iw)
        mit = k.get("mitigation")
        if mit is not None:
            _str(mit, "kind", f"{w}.mitigation")
            if mit["kind"] not in MITIGATIONS:
                raise CouplingSchemaError(
                    f"{w}.mitigation.kind: {mit['kind']!r} not in {MITIGATIONS}")
            _str(mit, "pair", f"{w}.mitigation")
    return doc


# ── safe expression evaluation（受限 AST，只有算术与白名单函数）───────────
_ALLOWED_FUNCS = {"log10": math.log10, "log": math.log, "exp": math.exp,
                  "sqrt": math.sqrt, "abs": abs, "min": min, "max": max}


def _safe_eval(expr: str, env: dict[str, float]) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - guarded by callers
        raise CouplingSchemaError(f"bad expression {expr!r}: {exc}") from exc

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CouplingSchemaError(f"non-numeric literal in {expr!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise CouplingSchemaError(f"unknown name {node.id!r} in {expr!r}")
            return float(env[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                if b == 0:
                    raise CouplingSchemaError(f"division by zero in {expr!r}")
                return a / b
            return a ** b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = _ALLOWED_FUNCS.get(node.func.id)
            if fn is None:
                raise CouplingSchemaError(f"function {node.func.id!r} not allowed in {expr!r}")
            args = [ev(a) for a in node.args]
            return float(fn(*args))
        raise CouplingSchemaError(f"unsupported syntax in {expr!r}: {type(node).__name__}")

    val = ev(tree)
    if not math.isfinite(val):
        raise CouplingSchemaError(f"non-finite result for {expr!r}")
    return val


# ── interface contracts ─────────────────────────────────────────────────
def _unit_key(u: str) -> str:
    s = u.strip()
    return "__dimensionless__" if s.lower().lstrip("[]") in _DIMENSIONLESS else s.lower()


def _rel_dev(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0 else abs(a - b) / scale


def check_interfaces(doc: dict[str, Any], *,
                     attested: dict[str, bool] | None = None,
                     documented: dict[str, bool] | None = None) -> dict[str, Any]:
    """逐接口执行五类检查；任一 blocking 检查非 PASS → 接口不得判 VALIDATED。"""
    attested = attested or {}
    documented = documented or {}
    out: list[dict[str, Any]] = []
    for e in doc["interfaces"]:
        checks: list[dict[str, Any]] = []

        def add(name: str, verdict: str, detail: dict[str, Any], severity: str = "blocking",
                note: str = "") -> None:
            checks.append({"check": name, "verdict": verdict, "severity": severity,
                           "detail": detail, "note": note})

        doc_ref = e["doc_ref"]
        doc_ok = bool(documented.get(doc_ref, False))
        add("contract_documented", "PASS" if doc_ok else "FAIL",
            {"doc_ref": doc_ref, "found": doc_ok},
            note="契约文档缺失 ⇒ 接口不可核" if not doc_ok else "")

        prod_ok = e["producer"] in attested
        cons_ok = e["consumer"] in attested
        add("nodes_declared", "PASS" if (prod_ok and cons_ok) else "FAIL",
            {"producer": e["producer"], "consumer": e["consumer"],
             "producer_known": prod_ok, "consumer_known": cons_ok})

        pu, ru = _unit_key(e["payload"]["unit"]), _unit_key(e["requirement"]["unit"])
        add("unit_consistent", "PASS" if pu == ru else "FAIL",
            {"payload_unit": e["payload"]["unit"],
             "requirement_unit": e["requirement"]["unit"]})

        plo, phi = _range(e["payload"]["coverage"], "payload.coverage")
        rlo, rhi = _range(e["requirement"], "requirement")
        covered = (plo <= rlo) and (phi >= rhi)
        fallback = e["requirement"].get("fallback")
        fields = list(e["payload"].get("fields") or [])
        guarded = (not covered) and fallback == "clamp_with_status" and "domain_status" in fields
        add("range_coverage", "PASS" if (covered or guarded) else "FAIL",
            {"payload": [plo, phi], "requirement": [rlo, rhi],
             "shortfall_low": max(0.0, plo - rlo), "shortfall_high": max(0.0, rhi - phi),
             "covered": covered, "guarded": guarded, "fallback": fallback,
             "guard_field_present": "domain_status" in fields},
            note=("声明域外缺口 %.6g 由 domain_status 保持降级（clamp_with_status）"
                  % max(max(0.0, plo - rlo), max(0.0, rhi - phi))) if guarded else "")

        prod_evidenced = bool(attested.get(e["producer"], False))
        add("producer_evidenced", "PASS" if prod_evidenced else "WITHHELD",
            {"producer": e["producer"], "attested": prod_evidenced}, severity="evidence",
            note="" if prod_evidenced else "生产方无可核证据 ⇒ 接口只算契约可核，不算数据可核")
        cons_evidenced = bool(attested.get(e["consumer"], False))
        add("consumer_evidenced", "PASS" if cons_evidenced else "WITHHELD",
            {"consumer": e["consumer"], "attested": cons_evidenced}, severity="evidence",
            note="" if cons_evidenced else "消费方无可核证据 ⇒ 契约可核但消费端实现未验")

        blocking_bad = [c for c in checks
                        if c["severity"] == "blocking" and c["verdict"] != "PASS"]
        if not doc_ok:
            status = "UNDOCUMENTED"
        elif any(c["check"] in ("nodes_declared", "unit_consistent", "range_coverage")
                 and c["verdict"] != "PASS" for c in checks):
            status = "CONTRACT_MISMATCH"
        elif blocking_bad:
            status = "CONTRACT_MISMATCH"
        elif not (prod_evidenced and cons_evidenced):
            status = "UNATTESTED"
        else:
            status = "VALIDATED"
        out.append({"id": e["id"], "producer": e["producer"], "consumer": e["consumer"],
                    "payload": e["payload"], "requirement": e["requirement"],
                    "doc_ref": doc_ref, "status": status, "checks": checks,
                    "note": e.get("note", "")})

    summary = {
        "total": len(out),
        "documented": sum(1 for r in out if documented.get(r["doc_ref"], False)),
        "validated": sum(1 for r in out if r["status"] == "VALIDATED"),
        "mismatch": sum(1 for r in out if r["status"] == "CONTRACT_MISMATCH"),
        "unattested": sum(1 for r in out if r["status"] == "UNATTESTED"),
        "undocumented": sum(1 for r in out if r["status"] == "UNDOCUMENTED"),
    }
    summary["mismatch_ids"] = [r["id"] for r in out if r["status"] == "CONTRACT_MISMATCH"]
    summary["unattested_ids"] = [r["id"] for r in out if r["status"] == "UNATTESTED"]
    return {"schema": INTERFACES_SCHEMA, "interfaces": out, "summary": summary}


# ── cross-node assumption consistency ───────────────────────────────────
def _mitigated(mit: dict[str, Any] | None, pair_verdicts: dict[str, str],
               nodes: list[str] | None = None,
               attested_nodes: set[str] | None = None) -> tuple[bool, str]:
    if not mit:
        return False, "no mitigation declared"
    kind = mit["kind"]
    if kind == "pair_downgrade":
        pair = mit["pair"]
        verdict = pair_verdicts.get(pair)
        if verdict is None:
            return False, f"mitigation pair {pair!r} not present in adjudication results"
        if verdict not in DOWNGRADING_VERDICTS:
            return False, f"mitigation pair {pair!r} verdict {verdict} does not downgrade"
        nodes = nodes or []
        attested_nodes = attested_nodes or set()
        if attested_nodes and (set(nodes) & attested_nodes):
            return True, (f"pair {pair!r} verdict {verdict} caps the affected capabilities "
                          f"(nodes with attested claims: {sorted(set(nodes) & attested_nodes)})")
        return True, (f"pair {pair!r} verdict {verdict}; 受影响节点均无可核能力自证 ⇒ "
                      f"冲突以 WITHHELD 落地，不存在被抬高的声明")
    if kind == "reference_resolved":
        ref = mit.get("reference")
        if not ref:
            return False, "reference_resolved without reference id"
        return True, f"resolved against declared reference {ref!r}"
    return False, f"unknown mitigation kind {kind!r}"  # pragma: no cover


def check_assumptions(doc: dict[str, Any], *,
                      pair_verdicts: dict[str, str] | None = None,
                      attested_nodes: set[str] | None = None) -> dict[str, Any]:
    pair_verdicts = pair_verdicts or {}
    attested_nodes = attested_nodes or set()
    rows: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    for k in doc["keys"]:
        kind = k["kind"]
        decls = k.get("declarations") or []
        row: dict[str, Any] = {"key": k["key"], "kind": kind,
                               "standard": k.get("standard", ""),
                               "description": k.get("description", ""),
                               "n_declarations": len(decls),
                               "nodes": sorted({d["node"] for d in decls})}
        status = "UNDETERMINED"
        detail: dict[str, Any] = {}
        if len(decls) < 2 and kind in ("edition", "value"):
            detail = {"reason": "fewer than 2 node declarations"}
        elif kind == "edition":
            editions = sorted({str(d["value"]) for d in decls})
            detail = {"editions": editions,
                      "by_node": {d["node"]: d["value"] for d in decls}}
            status = "AGREED" if len(editions) == 1 else "CONTRADICTED"
        elif kind == "value":
            rel_tol = float(k.get("rel_tol", 0.0))
            vals = [float(d["value"]) for d in decls]
            spread = 0.0
            if len(vals) > 1:
                for i in range(len(vals)):
                    for j in range(i + 1, len(vals)):
                        spread = max(spread, _rel_dev(vals[i], vals[j]))
            detail = {"values": {d["node"]: float(d["value"]) for d in decls},
                      "max_rel_dev": spread, "rel_tol": rel_tol,
                      "unit": k.get("unit", "-")}
            status = "AGREED" if spread <= rel_tol else "CONTRADICTED"
        else:  # law
            tol = float(k.get("tol", 1e-9))
            by_node = {d["node"]: dict(d["params"]) for d in decls}
            shared = sorted(set.intersection(*(set(p) for p in by_node.values()))) \
                if by_node else []
            worst_shared = 0.0
            for p in shared:
                vals = [by_node[n][p] for n in by_node]
                for i in range(len(vals)):
                    for j in range(i + 1, len(vals)):
                        worst_shared = max(worst_shared, _rel_dev(vals[i], vals[j]))
            implied_rows = []
            worst_implied = 0.0
            for imp in k.get("implied") or []:
                node = imp["node"]
                if node not in by_node:
                    raise CouplingSchemaError(
                        f"{k['key']}.implied: node {node!r} has no declaration")
                env = by_node[node]
                expected = _safe_eval(imp["expr"], env)
                declared = float(env[imp["target"]]) if imp["target"] in env else None
                if declared is None:
                    raise CouplingSchemaError(
                        f"{k['key']}.implied: target {imp['target']!r} absent from {node} params")
                dev = abs(expected - declared)
                implied_rows.append({"node": node, "expr": imp["expr"],
                                     "target": imp["target"], "expected": expected,
                                     "declared": declared, "abs_dev": dev,
                                     "tol": float(imp["tol"]),
                                     "verdict": "PASS" if dev <= float(imp["tol"]) else "FAIL"})
                worst_implied = max(worst_implied, dev)
            detail = {"params_by_node": by_node, "shared_params": shared,
                      "max_rel_dev_shared": worst_shared, "tol": tol,
                      "implied": implied_rows, "implied_max_abs_dev": worst_implied}
            implied_fail = [r for r in implied_rows if r["verdict"] == "FAIL"]
            if implied_fail:
                status = "IMPLIED_MISMATCH"
            elif worst_shared > tol:
                status = "CONTRADICTED"
            else:
                status = "AGREED"
        row["status"] = status
        row["detail"] = detail
        if status in ("CONTRADICTED", "IMPLIED_MISMATCH"):
            ok, why = _mitigated(k.get("mitigation"), pair_verdicts, row["nodes"],
                                attested_nodes)
            row["mitigation"] = {"declared": k.get("mitigation"),
                                 "accepted": ok, "reason": why}
            contradictions.append({"key": k["key"], "kind": kind, "status": status,
                                   "standard": k.get("standard", ""),
                                   "nodes": row["nodes"], "detail": detail,
                                   "mitigated": ok, "mitigation_reason": why})
        rows.append(row)

    unmitigated = [c for c in contradictions if not c["mitigated"]]
    summary = {
        "keys": len(rows),
        "agreed": sum(1 for r in rows if r["status"] == "AGREED"),
        "contradicted": sum(1 for r in rows if r["status"] == "CONTRADICTED"),
        "implied_mismatch": sum(1 for r in rows if r["status"] == "IMPLIED_MISMATCH"),
        "undetermined": sum(1 for r in rows if r["status"] == "UNDETERMINED"),
        "violations": len(contradictions),
        "mitigated": len(contradictions) - len(unmitigated),
        "unmitigated": len(unmitigated),
        "clean": len(contradictions) == 0,
        "nodes": sorted({n for r in rows for n in r["nodes"]}),
    }
    return {"schema": ASSUMPTIONS_SCHEMA, "keys": rows,
            "violations": contradictions, "summary": summary}