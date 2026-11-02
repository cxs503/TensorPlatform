#!/usr/bin/env python3
"""Handler-contract drift detector（m1865，models 席·验证欠账类）。

背景：SHIP-MAN-001（m1780 登记）表现为 `POST /api/v1/maneuvering-mmg/{turning,zigzag}` 恒返回 HTTP 500，
根因是「端点输入模型 → 核输入模型」的字段集漂移：handler 内构造 `MMGInput(...)` 时漏传必填字段。
这类缺陷**静态可判**（构造表达式 vs 目标模型必填集合），不必靠逐点手测发现。

本工具做三件事，全部程序化、零手抄：
1. **AST 静态判据**：扫描服务源码目录，抽 pydantic 模型必填字段集（无默认值的注解字段），
   抽 FastAPI 路由 handler 及其体内构造的模型调用，报「缺失必填字段」与「未知关键字」；
   判据自带正/负样例自证（合成缺陷必须命中、合成健康必须静默），死判据不算证据。
2. **Live OpenAPI 交叉核对**：读 `:8001/openapi.json`，核对部署 schema 与该端点输入模型的
   必填字段集是否一致（部署漂移 vs 源码漂移分离）。
3. **实测确认**：按 OpenAPI schema 自动生成最小合法 payload POST 一次，记录状态码；
   500 与静态预测互相印证（互为独立证据链）。

产出 JSON（判据工件）+ 可选 Markdown 文档与图（数字全部从工件注入）。

用法：
  python3 tools/audit/handler_contract_drift.py \
     --src /root/ship-performance-platform-incoming/ship-performance-platform/backend/api \
     --api-base http://127.0.0.1:8001 --emit-doc docs/audits/m1865_handler_contract_drift.md \
     --out artifacts/audit/handler_contract_drift_8001.json --figure artifacts/audit/fig_handler_contract_drift_m1865.png
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 1. 源码侧：模型必填字段 + handler 构造调用
# --------------------------------------------------------------------------- #

MODEL_BASES = ("BaseModel",)


def _is_model_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = getattr(base, "id", None) or getattr(base, "attr", None)
        if name in MODEL_BASES:
            return True
    return False


def model_fields(cls: ast.ClassDef) -> Dict[str, bool]:
    """字段名 → 是否必填（pydantic v1/v2: 无默认值即必填）。"""
    out: Dict[str, bool] = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out[stmt.target.id] = stmt.value is None
    return out


def _decorator_endpoint(dec: ast.AST) -> Optional[Tuple[str, str]]:
    """从 @router.post("/x") 抽 (method, path)。"""
    if not isinstance(dec, ast.Call):
        return None
    f = dec.func
    if not isinstance(f, ast.Attribute):
        return None
    method = f.attr.lower()
    if method not in ("post", "put", "patch"):
        return None
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        return method, dec.args[0].value
    return None


def _first_arg_model(fn: ast.FunctionDef) -> Optional[str]:
    if not fn.args.args:
        return None
    ann = fn.args.args[0].annotation
    if isinstance(ann, ast.Name):
        return ann.id
    return None


def _collect_constructed(node: ast.AST) -> List[Tuple[str, int, Dict[str, Any]]]:
    """handler 体内所有 ClassName(...) 调用（返回 名字/行号/关键字集合）。"""
    calls = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        name = None
        if isinstance(sub.func, ast.Name):
            name = sub.func.id
        elif isinstance(sub.func, ast.Attribute):
            name = sub.func.attr
        if not name:
            continue
        kw = [k.arg for k in sub.keywords if k.arg]
        star = [k for k in sub.keywords if k.arg is None]
        calls.append((name, getattr(sub, "lineno", 0), {"kwargs": set(kw),
                                                        "n_positional": len(sub.args),
                                                        "star_kwargs": bool(star)}))
    return calls


def router_prefix(tree: ast.AST) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name != "APIRouter":
                continue
            for kw in node.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return ""


def scan_source(src_dir: str) -> Dict[str, Any]:
    models: Dict[str, Dict[str, bool]] = {}
    models_where: Dict[str, str] = {}
    endpoints: List[Dict[str, Any]] = []
    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".py"))
    for fname in files:
        path = os.path.join(src_dir, fname)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_model_class(node):
                # 同名模型以先见者为基线，后见者标记冲突
                if node.name in models and models[node.name] != model_fields(node):
                    models_where[node.name] += f" | CONFLICT:{fname}:{node.lineno}"
                models.setdefault(node.name, model_fields(node))
                models_where.setdefault(node.name, f"{fname}:{node.lineno}")
        mod_prefix = router_prefix(tree)
        funcs = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ep = None
            for dec in node.decorator_list:
                ep = _decorator_endpoint(dec)
                if ep:
                    break
            if not ep:
                continue
            input_model = _first_arg_model(node)
            calls = _collect_constructed(node)
            callees = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    nm = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                    if nm in funcs and nm != node.name:
                        callees.add(nm)
            for nm in sorted(callees):
                for c in _collect_constructed(funcs[nm]):
                    calls.append((c[0], c[1], {**c[2], "via": nm}))
            endpoints.append({
                "file": fname,
                "line": node.lineno,
                "handler": node.name,
                "method": ep[0],
                "path": ep[1],
                "live_path": mod_prefix + ep[1],
                "input_model": input_model,
                "calls": calls,
                "callees": sorted(callees),
            })

    # 漂移判定
    drifts: List[Dict[str, Any]] = []
    for ep in endpoints:
        for cname, lineno, info in ep["calls"]:
            RESPONSE_SUFFIX = ("Output", "Response", "Result", "Report", "Prediction")
            if cname not in models or cname == ep["input_model"]:
                continue
            if cname.endswith(RESPONSE_SUFFIX):
                continue
            if info["star_kwargs"] or info["n_positional"] > 0:
                continue
            required = {f for f, req in models[cname].items() if req}
            kwargs = info["kwargs"]
            missing = sorted(required - kwargs)
            unknown = sorted(k for k in kwargs if k not in models[cname])
            if not missing and not unknown:
                continue
            drifts.append({
                "file": ep["file"],
                "handler_line": ep["line"],
                "call_line": lineno,
                "method": ep["method"],
                "path": ep["path"],
                "handler": ep["handler"],
                "input_model": ep["input_model"],
                "constructed_model": cname,
                "via_call": info.get("via"),
                "constructed_model_fields": len(models[cname]),
                "required_count": len(required),
                "missing_required": missing,
                "unknown_kwargs": unknown,
                "input_model_has_field": {f: (f in (models.get(ep["input_model"] or "", {})))
                                          for f in missing},
            })
    return {
        "files_scanned": len(files),
        "models_seen": len(models),
        "models": {k: {"required": sorted(f for f, r in v.items() if r),
                       "optional": sorted(f for f, r in v.items() if not r),
                       "where": models_where.get(k)} for k, v in models.items()},
        "endpoints": [{"file": e["file"], "line": e["line"], "handler": e["handler"],
                       "method": e["method"], "path": e["path"],
                       "live_path": e["live_path"],
                       "input_model": e["input_model"]} for e in endpoints],
        "drifts": drifts,
    }


# --------------------------------------------------------------------------- #
# 2. 自证：合成缺陷必须命中、合成健康必须静默（判据不是死判据）
# --------------------------------------------------------------------------- #

SELF_CHECK_SRC = '''
from pydantic import BaseModel
from fastapi import APIRouter
router = APIRouter()

class Core(BaseModel):
    L: float
    Hr: float
    V: float

class EndpointIn(BaseModel):
    L: float
    V: float

@router.post("/bad")
def bad(data: EndpointIn):
    core = Core(L=data.L, V=data.V)
    return core

@router.post("/good")
def good(data: EndpointIn):
    core = Core(L=data.L, Hr=1.0, V=data.V)
    return core
'''


def self_check() -> Dict[str, Any]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "synthetic.py"), "w") as fh:
            fh.write(SELF_CHECK_SRC)
        rep = scan_source(td)
    hits = {d["path"]: d["missing_required"] for d in rep["drifts"]}
    ok = hits.get("/bad") == ["Hr"] and "/good" not in hits
    return {"synthetic_defect_flagged": hits.get("/bad"),
            "synthetic_healthy_silent": "/good" not in hits,
            "ok": bool(ok)}


# --------------------------------------------------------------------------- #
# 3. Live OpenAPI 交叉核对 + 实测
# --------------------------------------------------------------------------- #

def fetch_openapi(api_base: str) -> Dict[str, Any]:
    with urllib.request.urlopen(api_base.rstrip("/") + "/openapi.json", timeout=30) as r:
        return json.load(r)

# 只为「让请求有意义」而给的量级提示；缺值一律 1.0，规则显式可审。
MAGNITUDE_HINT = {"L": 320.0, "B": 58.0, "T": 20.8, "D": 30.0, "displacement": 320000.0,
                  "Cb": 0.82, "KG": 10.4, "xG": 0.0, "Dp": 9.86, "n": 80.0, "Ar": 120.0,
                  "Hr": 10.0, "V": 7.7, "V0": 7.7, "rudder_angle": 35.0, "heading_angle": 10.0}


def openapi_required(spec: Dict[str, Any], path: str, method: str) -> Optional[Dict[str, Any]]:
    node = spec.get("paths", {}).get(path, {}).get(method.lower())
    if not node:
        return None
    schema = (node.get("requestBody", {}).get("content", {})
              .get("application/json", {}).get("schema", {}))
    ref = schema.get("$ref")
    if not ref:
        return None
    comp = ref.split("/")[-1]
    model = spec.get("components", {}).get("schemas", {}).get(comp, {})
    props = model.get("properties", {})
    return {"component": comp,
            "required": sorted(model.get("required", [])),
            "properties": sorted(props)}


def build_payload(fields: List[str]) -> Dict[str, float]:
    return {f: MAGNITUDE_HINT.get(f, 1.0) for f in fields}


def probe(api_base: str, path: str, method: str, fields: List[str]) -> Dict[str, Any]:
    url = api_base.rstrip("/") + path
    body = json.dumps(build_payload(fields)).encode()
    req = urllib.request.Request(url, data=body, method=method.upper(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read(400).decode("utf-8", "replace")
            return {"status": r.status, "body_head": raw[:200]}
    except urllib.error.HTTPError as exc:
        raw = exc.read(400).decode("utf-8", "replace")
        return {"status": exc.code, "body_head": raw[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# 4. 文档 / 图（数字全部从工件注入）
# --------------------------------------------------------------------------- #

def emit_doc(rep: Dict[str, Any], doc_path: str) -> None:
    d = rep
    lines = []
    A = lines.append
    A("# Handler 契约漂移静态判据（SHIP-MAN-001 类缺陷的通用化）")
    A("")
    A(f"**任务** m1865（自找活·models 席·验证欠账类）｜**仓** TensorPlatform｜"
      f"**产物** `tools/audit/handler_contract_drift.py`、`tests/test_handler_contract_drift.py`、"
      f"`{rep['artifacts']['json']}`、`{rep['artifacts']['figure']}`")
    A("")
    A("## ① 案例介绍")
    A("")
    A("被测平台 `http://127.0.0.1:8001`（船舶总体性能平台）。缺陷族 SHIP-MAN-001："
      "端点声明的输入模型与 handler 内部构造的核模型字段集漂移，导致 pydantic `ValidationError` → HTTP 500。"
      "本单把「果然 500」的逐点手测，升级为**静态可判**：构造表达式 vs 目标模型必填集合。")
    A("")
    A("## ② 怎么做的")
    A("")
    A("1. AST 抽模型必填字段（无默认值注解字段）与 handler 体内 `Model(...)` 构造；")
    A("2. 缺失必填/未知关键字 → 漂移记录；")
    A("3. 判据自证：合成缺陷必须命中、合成健康必须静默；")
    A("4. 与部署 OpenAPI 的 `required` 集合交叉核对（源码漂移 vs 部署漂移分离）；")
    A("5. 按 OpenAPI schema 自动生成 payload 实测状态码（独立证据链）。")
    A("")
    A("## ③ 计算结果")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 扫描源码文件数 | {d['source']['files_scanned']} |")
    A(f"| 识别 pydantic 模型数 | {d['source']['models_seen']} |")
    A(f"| 识别写端点（POST/PUT/PATCH）数 | {len(d['source']['endpoints'])} |")
    A(f"| 漂移记录数 | {len(d['source']['drifts'])} |")
    A(f"| 判据自证 | {'通过' if d['self_check']['ok'] else '失败'} "
      f"(合成缺陷命中 {d['self_check']['synthetic_defect_flagged']}，"
      f"合成健康静默 {d['self_check']['synthetic_healthy_silent']}) |")
    A("")
    A("### 3.1 漂移清单（静态判据）")
    A("")
    A("| 端点 | handler:行 → 构造:行 | 输入模型 → 核模型 | 缺失必填 | 未知关键字 | 实测状态码 |")
    A("|---|---|---|---|---|---|")
    for r in d["source"]["drifts"]:
        live = d["live_probes"].get(r["path"] + " " + r["method"], {})
        A(f"| `{r['method'].upper()} {r['path']}` | {r['file']}:{r['handler_line']} → "
          f"{r['call_line']} | {r['input_model']} → {r['constructed_model']} | "
          f"{', '.join(r['missing_required']) or '—'} | {', '.join(r['unknown_kwargs']) or '—'} | "
          f"{live.get('status')} |")
    A("")
    A("### 3.2 部署 schema 与源码模型必填集合的差异")
    A("")
    A("| 端点 | 部署 schema | 部署 required | 源码输入模型 | 源码 required | 差异 |")
    A("|---|---|---|---|---|---|")
    for r in d["schema_diff"]:
        A(f"| `{r['method'].upper()} {r['path']}` | {r['component']} | "
          f"{', '.join(r['deployed_required'])} | {r['input_model']} | "
          f"{', '.join(r['source_required'])} | {r['verdict']} |")
    A("")
    A("## ④ 结果图")
    A("")
    A(f"![handler contract drift]({os.path.relpath(rep['artifacts']['figure'], os.path.dirname(doc_path))})")
    A("")
    A("## ⑤ 注意点")
    A("")
    A("- 静态判据只覆盖「handler 直接构造其它 pydantic 模型」这一族；通过 dict 透传再 `**kwargs` 展开的写法不可见（记为局限）。")
    A("- 实测状态码依赖 payload 量级提示表（`MAGNITUDE_HINT`），非物理标定；其作用仅是触发/不触发 500，不产生精度结论。")
    A("- 修复后同一判据应转静默，`tests/test_handler_contract_drift.py` 会锁住该期望。")
    A("")
    A("## ⑥ 误差判定")
    A("")
    A("关键量 = 缺陷是否被静态判据命中；判据自证结果为上表「判据自证」行。"
      "静态预测与实测 500 的一致性见 3.1 表最后一列（同一行内自洽）。"
      "本单不产生物理量误差（缺陷=端点不可用，精度判决为 WITHHELD，非 0 误差）。")
    A("")
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    with open(doc_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def emit_figure(rep: Dict[str, Any], fig_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = rep
    os.makedirs(os.path.dirname(fig_path), exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    ax = axes[0]
    labels = ["源码 .py 文件", "pydantic 模型", "写端点", "漂移记录"]
    vals = [d["source"]["files_scanned"], d["source"]["models_seen"],
            len(d["source"]["endpoints"]), len(d["source"]["drifts"])]
    ax.bar(labels, vals, color=["#8ab4f8", "#8ab4f8", "#8ab4f8", "#e8710a"])
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_title("(a) static scan funnel (m1865)")
    ax.set_ylabel("count")

    ax = axes[1]
    names, miss, status = [], [], []
    for r in d["source"]["drifts"]:
        names.append(r["path"].split("/")[-1] + "\n" + r["constructed_model"])
        miss.append(len(r["missing_required"]))
        live = d["live_probes"].get(r["path"] + " " + r["method"], {})
        status.append(live.get("status"))
    if names:
        bars = ax.bar(range(len(names)), miss, color="#d93025")
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, fontsize=8)
        for i, (b, s) in enumerate(zip(bars, status)):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                    f"HTTP {s}", ha="center", va="bottom", fontsize=8)
    else:
        ax.text(0.5, 0.5, "no drift", ha="center")
    ax.set_title("(b) missing required fields (label = live HTTP)")
    ax.set_ylabel("missing required fields")
    fig.tight_layout()
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/root/ship-performance-platform-incoming/"
                                   "ship-performance-platform/backend/api")
    ap.add_argument("--api-base", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default="artifacts/audit/handler_contract_drift_8001.json")
    ap.add_argument("--emit-doc", default=None)
    ap.add_argument("--figure", default=None)
    ap.add_argument("--no-probe", action="store_true")
    args = ap.parse_args()

    src = scan_source(args.src)
    sc = self_check()

    spec = {}
    schema_diff: List[Dict[str, Any]] = []
    live_probes: Dict[str, Any] = {}
    try:
        spec = fetch_openapi(args.api_base)
    except Exception as exc:  # noqa: BLE001
        sc["openapi_error"] = f"{type(exc).__name__}: {exc}"

    src_models = src["models"]
    seen = set()
    spec_paths = list(spec.get("paths", {})) if spec else []

    def resolve_live(rec):
        tail = rec.get("live_path") or rec["path"]
        cands = [p for p in spec_paths if p.endswith(tail)]
        if len(cands) == 1:
            return cands[0]
        stem = rec["file"][:-3].replace("_", "-")
        cands2 = [p for p in spec_paths if p.endswith("/" + stem + tail)]
        if len(cands2) == 1:
            return cands2[0]
        return None

    for r in src["drifts"]:
        r["resolved_path"] = resolve_live(r) if spec_paths else None
        key = (r["path"], r["method"])
        if key in seen:
            continue
        seen.add(key)
        oa = openapi_required(spec, r["resolved_path"], r["method"]) if r.get("resolved_path") else None
        if oa:
            input_required = sorted(f for f in src_models.get(r["input_model"] or "", {})
                                    .get("required", []))
            same = sorted(oa["required"]) == input_required
            schema_diff.append({"path": r["path"], "method": r["method"],
                                "resolved_path": r["resolved_path"],
                                "component": oa["component"],
                                "deployed_required": oa["required"],
                                "input_model": r["input_model"],
                                "source_required": input_required,
                                "verdict": "一致" if same else "部署/源码漂移"})
            if not args.no_probe:
                live_probes[r["path"] + " " + r["method"]] = probe(
                    args.api_base, r["resolved_path"], r["method"], oa["properties"])
        # 即使 OpenAPI 缺失，也用源码输入模型的字段集试一次
        elif not args.no_probe:
            fields = src_models.get(r["input_model"] or "", {}).get("required", [])
            tgt = r.get("resolved_path") or r["path"]
            live_probes[r["path"] + " " + r["method"]] = probe(
                args.api_base, tgt, r["method"], fields)

    rep = {"meta": {"mission": "m1865", "api_base": args.api_base, "src": args.src,
                    "openapi_paths": len(spec.get("paths", {})) if spec else 0},
           "self_check": sc,
           "source": src,
           "schema_diff": schema_diff,
           "live_probes": live_probes,
           "artifacts": {"json": args.out, "figure": args.figure or "", "doc": args.emit_doc or ""}}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)

    if args.figure:
        emit_figure(rep, args.figure)
    if args.emit_doc:
        emit_doc(rep, args.emit_doc)

    print(json.dumps({"drifts": len(src["drifts"]), "self_check": sc,
                      "live": live_probes, "schema_diff": schema_diff,
                      "endpoints": len(src["endpoints"]),
                      "files": src["files_scanned"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())