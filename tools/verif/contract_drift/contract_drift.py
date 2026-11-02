#!/usr/bin/env python3
"""Repo↔live 合同漂移矩阵（m1762 / tp-infra-1）。

问题
----
平台的验证报告都在裁决 **仓内**实现（``src/ship_perf`` + ``backend/api``），
但客户端实际对话的是 **在线服务**（``:8001``）。一个能力只有在 **在线请求合同
暴露了受证实现所需的输入** 时才可达（reachable）。m1490 的 conformance 门只
用 legacy 方言发包，因此「在线连受证输入都收不到」这种失效模式（合同漂移）
在 12 项判据里表现为 physics FAIL，没人能把它与「公式算错」区分开。

本工具把 **可达性** 变成可复算判据：

* 仓内合同用 ``ast`` 静态抽取 Pydantic 模型 → **零依赖**（不需要 psycopg2 / DB /
  起服务），因此可进 CI；
* 在线合同取自在线 ``openapi.json``（快照落工件，可 ``--live-url`` 刷新）；
* 逐端点分类，见 ``STATUS_*``。

分类（互斥主类 + 可附加 RESPONSE_DRIFT）
----------------------------------------
``SAME``            在线必填字段集与仓内必需集一致，且没有隐藏仓内输入
``INPUT_DRIFT``     在线索取了仓内从未提供的输入（换了一套入口参数）
``HIDDEN_INPUTS``   在线隐藏了仓内暴露的输入（调用方无法注入受证物理量）
``TOPOLOGY_DRIFT``  两者兼有 = 物理量的 **产生位置** 变了（在线内部自行推导）
``UNSERVED``        仓内有、在线无该路径
``UNDOCUMENTED``    在线有、仓内无（仓内无法审计）
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

STATUS_SAME = "SAME"
STATUS_INPUT_DRIFT = "INPUT_DRIFT"
STATUS_HIDDEN = "HIDDEN_INPUTS"
STATUS_TOPOLOGY = "TOPOLOGY_DRIFT"
STATUS_UNSERVED = "UNSERVED"
STATUS_UNDOCUMENTED = "UNDOCUMENTED"
STATUS_RESPONSE_DRIFT = "RESPONSE_DRIFT"

#: 受证实现实际依赖的物理输入（每端点声明，供 reachability 计算）。
#: 这些名字取自仓内受证层（src/ship_perf / m1386 参考积分 / m1749 替代层）的
#: 输入契约；不是「所有字段」，只列 **决定判据结论** 的那些。
VERIFIED_INPUTS: Dict[str, List[str]] = {
    "/api/v1/calculations/stability": [
        "gm_m", "bm_m", "kb_m", "beam_m", "draft_m", "depth_m", "heel_angle_deg",
    ],
    "/api/v1/calculations/resistance": [
        "lwl_m", "lpp_m", "beam_m", "draft_m", "cb", "cm", "cwp", "speed_ms",
    ],
    "/api/v1/calculations/hydrostatics": ["beam_m", "draft_m", "cb", "kg_m"],
    "/api/v1/calculations/propulsion/actuator_disk": ["thrust_n", "speed_ms", "diameter_m"],
    "/api/v1/calculations/propulsion/self_propulsion": ["resistance_n", "speed_ms", "diameter_m"],
}

#: 判据结论字段（仓内受证层输出、在线必须有，否则结论不可复核）。
VERIFIED_OUTPUTS: Dict[str, List[str]] = {
    "/api/v1/calculations/stability": [
        "gz_basis", "wall_sided_valid", "heel_validity_limit_deg",
        "validity_limit_source", "gz_box_exact_m",
    ],
    "/api/v1/calculations/resistance": ["wetted_surface_source", "method"],
    "/api/v1/calculations/hydrostatics": ["method"],
}


# ---------------------------------------------------------------------------
# 仓内合同：ast 静态抽取（零 import）
# ---------------------------------------------------------------------------

@dataclass
class ModelContract:
    name: str
    required: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)
    aliases: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def accepted(self) -> Set[str]:
        """调用方可以使用的所有字段名（正名 + 别名）。"""
        out: Set[str] = set(self.required) | set(self.optional)
        for names in self.aliases.values():
            out |= set(names)
        return out


def _call_str_args(node: ast.AST) -> List[str]:
    """取出 ``AliasChoices("a", "b")`` / ``["a","b"]`` 里的字符串。"""
    out: List[str] = []
    if isinstance(node, ast.Call):
        for a in node.args:
            out += _call_str_args(a)
        for kw in node.keywords:
            out += _call_str_args(kw.value)
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for a in node.elts:
            out += _call_str_args(a)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    return out


def parse_models(source_path: str) -> Dict[str, ModelContract]:
    """抽取文件内所有 ``class X(BaseModel)`` 的字段合同。"""
    with open(source_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=source_path)
    models: Dict[str, ModelContract] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(b) for b in node.bases}
        if not any(b.split(".")[-1] == "BaseModel" for b in bases):
            continue
        mc = ModelContract(name=node.name)
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            fname = stmt.target.id
            val = stmt.value
            required = True
            if isinstance(val, ast.Call):
                kws = {kw.arg: kw.value for kw in val.keywords if kw.arg}
                if "default" in kws:
                    required = not (
                        isinstance(kws["default"], ast.Constant) and kws["default"].value is Ellipsis
                    )
                else:
                    required = False  # Field(gt=0) 之类的无 default ⇒ 有隐式 default? 保守按必需
                    required = True
                for key in ("validation_alias", "alias"):
                    if key in kws:
                        names = [n for n in _call_str_args(kws[key]) if n != fname]
                        if names:
                            mc.aliases[fname] = names
            (mc.required if required else mc.optional).append(fname)
        models[node.name] = mc
    return models


def parse_router_prefix(source_path: str) -> str:
    """``router = APIRouter(prefix="/calculations")`` → ``"/calculations"``。"""
    with open(source_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=source_path)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not (isinstance(tgt, ast.Name) and tgt.id == "router"):
            continue
        call = node.value
        if isinstance(call, ast.Call):
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    return kw.value.value or ""
    return ""


_ROUTE_RE = re.compile(r'^@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')


def parse_routes(source_path: str) -> List[Dict[str, Any]]:
    """抽取 ``@router.post("/path", response_model=X)`` + 紧跟的 ``def f(req: Y)``。"""
    with open(source_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=source_path)
    routes: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not isinstance(fn, ast.Attribute) or not isinstance(fn.value, ast.Name):
                continue
            if fn.value.id != "router":
                continue
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else ""
            kw = {k.arg: k.value for k in dec.keywords if k.arg}
            resp = None
            if "response_model" in kw:
                resp = ast.unparse(kw["response_model"]).split(".")[-1]
            req = None
            for a in node.args.args:
                if a.annotation is not None:
                    req = ast.unparse(a.annotation).split(".")[-1]
                    break
            routes.append({
                "method": fn.attr.lower(),
                "path": path,
                "function": node.name,
                "request_model": req,
                "response_model": resp,
            })
    return routes


# ---------------------------------------------------------------------------
# 在线合同：openapi.json
# ---------------------------------------------------------------------------

def _resolve(schema: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(schema, dict) and "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return spec.get("components", {}).get("schemas", {}).get(name, {})
    return schema or {}


def live_contract(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{(method, path): {req_required, req_props, resp_props, resp_ref}}``。"""
    out: Dict[str, Dict[str, Any]] = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
            req_schema = _resolve(body.get("schema", {}), spec)
            resp_raw = (op.get("responses", {}).get("200", {}) or {}).get(
                "content", {}).get("application/json", {}).get("schema", {})
            resp_ref = resp_raw.get("$ref", "").split("/")[-1] if isinstance(resp_raw, dict) else ""
            resp_schema = _resolve(resp_raw, spec)
            out[f"{method.upper()} {path}"] = {
                "req_required": list(req_schema.get("required") or []),
                "req_props": list((req_schema.get("properties") or {}).keys()),
                "resp_ref": resp_ref,
                "resp_props": list((resp_schema.get("properties") or {}).keys()),
            }
    return out


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

def classify(repo_req: Optional[ModelContract],
             repo_resp: Optional[ModelContract],
             live: Dict[str, Any],
             path: str) -> Dict[str, Any]:
    accepted = repo_req.accepted if repo_req else set()
    repo_required = set(repo_req.required) if repo_req else set()
    live_required = set(live["req_required"])
    live_props = set(live["req_props"])

    verified_in = {n for n in VERIFIED_INPUTS.get(path, []) if n in accepted}
    hidden = sorted(n for n in verified_in if n not in live_props)
    demanded = sorted(live_required - accepted)
    reach = (len(verified_in) - len(hidden)) / len(verified_in) if verified_in else None

    if demanded and hidden:
        status = STATUS_TOPOLOGY
    elif hidden:
        status = STATUS_HIDDEN
    elif demanded:
        status = STATUS_INPUT_DRIFT
    else:
        status = STATUS_SAME

    verified_out = set(VERIFIED_OUTPUTS.get(path, []))
    missing_out = sorted(verified_out - set(live["resp_props"])) if verified_out else []

    return {
        "status": status,
        "response_drift": bool(missing_out),
        "missing_verified_outputs": missing_out,
        "live_demands_unseen_inputs": demanded,
        "live_hides_verified_inputs": hidden,
        "reachability": reach,
        "n_verified_inputs": len(verified_in),
        "repo_required": sorted(repo_required),
        "live_required": sorted(live_required),
        "repo_accepted": sorted(accepted),
        "live_props": sorted(live_props),
    }


def build_matrix(repo_api_dir: str, spec: Dict[str, Any],
                 prefix: str = "/api/v1") -> Dict[str, Any]:
    """对 ``repo_api_dir`` 下所有 api 模块建矩阵（只统计两边都能定位的路径）。"""
    import glob
    import os

    models: Dict[str, ModelContract] = {}
    routes: List[Dict[str, Any]] = []
    for f in sorted(glob.glob(os.path.join(repo_api_dir, "*.py"))):
        models.update(parse_models(f))
        rprefix = parse_router_prefix(f)
        for r in parse_routes(f):
            r["module"] = os.path.basename(f)
            r["router_prefix"] = rprefix
            routes.append(r)

    live = live_contract(spec)
    live_paths = {k.split(" ", 1)[1] for k in live}

    rows: List[Dict[str, Any]] = []
    for r in routes:
        full = f"{prefix}{r.get('router_prefix', '')}{r['path']}"
        key = f"{r['method'].upper()} {full}"
        if key not in live:
            rows.append({
                "endpoint": key, "module": r["module"], "function": r["function"],
                "status": STATUS_UNSERVED, "response_drift": False,
                "reachability": None,
            })
            continue
        row = classify(models.get(r["request_model"] or ""),
                       models.get(r["response_model"] or ""),
                       live[key], full)
        row.update({"endpoint": key, "module": r["module"], "function": r["function"],
                    "repo_request_model": r["request_model"],
                    "live_request_schema": live[key]["resp_ref"] or None})
        rows.append(row)

    served_keys = {f"{r['method'].upper()} {prefix}{r.get('router_prefix', '')}{r['path']}"
                   for r in routes}
    undocumented = sorted(k for k in live if k.split(" ", 1)[1] not in
                          {s.split(" ", 1)[1] for s in served_keys}
                          and any(t in k.lower() for t in
                                  ("calculations", "resistance", "stability", "propulsion")))

    summary: Dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    summary[STATUS_UNDOCUMENTED] = len(undocumented)
    return {
        "rows": rows,
        "summary": summary,
        "undocumented_sample": undocumented[:40],
        "n_live_paths": len(live_paths),
        "n_repo_routes": len(routes),
    }


def fetch_live_spec(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as fh:
        return json.load(fh)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-api-dir", default="backend/api")
    p.add_argument("--live-url", default="http://127.0.0.1:8001/openapi.json")
    p.add_argument("--snapshot", default=None, help="已有的 openapi 快照（离线模式）")
    p.add_argument("--out", default="tools/verif/contract_drift/artifacts/contract_matrix.json")
    args = p.parse_args(argv)

    if args.snapshot:
        with open(args.snapshot, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        src = args.snapshot
    else:
        spec = fetch_live_spec(args.live_url)
        src = args.live_url

    matrix = build_matrix(args.repo_api_dir, spec)
    matrix["source"] = src
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(matrix, fh, ensure_ascii=False, indent=2)

    print(f"[contract_drift] source={src}  live_paths={matrix['n_live_paths']}  "
          f"repo_routes={matrix['n_repo_routes']}")
    for k in (STATUS_SAME, STATUS_INPUT_DRIFT, STATUS_HIDDEN, STATUS_TOPOLOGY,
              STATUS_UNSERVED, STATUS_UNDOCUMENTED):
        print(f"  {k:16s} {matrix['summary'].get(k, 0)}")
    drift = [r for r in matrix["rows"] if r["status"] in
             (STATUS_TOPOLOGY, STATUS_HIDDEN, STATUS_INPUT_DRIFT)]
    print(f"[drift] {len(drift)} 个在线路径与仓内合同不一致：")
    for r in drift:
        print(f"  {r['endpoint']:52s} {r['status']:15s} reach={r.get('reachability')} "
              f"hidden={r.get('live_hides_verified_inputs')} demanded={r.get('live_demands_unseen_inputs')}")
    print(f"[out] {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())