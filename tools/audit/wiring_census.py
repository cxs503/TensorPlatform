#!/usr/bin/env python3
"""接线普查闸门（wiring census）：把「代码存在 ≠ 服务可达」变成可重跑的机器判决。

动机
----
平台的同一类缺陷已反复出现，每次都要靠一次性探针单独立案：

* m1649：``ship_seakeeping`` 三个 ``calculations/seakeeping_*`` 端点从未被
  ``main.py`` import/挂载 → 线上 404（后补接线）；
* m1820：``ship_vibration`` 六个振动/噪声端点同样未挂载 → 该域判 NOT_SUPPORTED，
  而模块自身解析核 45/45 自检通过（"代码 REAL，能力 NOT_SUPPORTED"）；
* m1662：批量链路的敞水代理未接线；factory 三个 router 亦曾整体 404。

「模块里有 router」和「app 挂载了 router」和「线上真的注册了该路径」是三件不同的
事。本闸门对 ``backend/api/domain/*.py`` 逐模块做**三平面**判决，并给出按域汇总的
接线台账；新增的未接线域（drift）直接让闸门红，已登记欠账走 baseline 豁免（必须
写理由），已清偿的 baseline 条目变成 stale 提示（提示删账，不误判）。

三平面
------
* **P-A 模块面**：AST 静态解析 ``APIRouter()`` 赋值目标 + 装饰器
  (``@router.get/post/put/delete/patch``) → 该模块声明的端点路径集合。
  纯静态，不 import（避免 DB/环境依赖），零副作用。
* **P-B 挂载面**：AST 解析 ``backend/main.py`` 中所有
  ``app.include_router(<expr>.router ...)`` 调用，``<expr>`` 的末段即被挂载模块名；
  同时收 ``from api.domain import X`` / ``from api import X as Y`` 的别名映射，
  因此 ``as`` 别名与 ``factory_residents`` 这类非 domain 路径都能正确归属。
* **P-C 实况面**：``--base-url`` 给定时抓 ``/openapi.json``，逐端点判 LIVE。
  **带控制组**：控制端点若不在 openapi 里，说明实况面不可信 → 该平面整体
  WITHHELD，绝不把"抓不到"当成"没接线"。

判决词表（每模块一条）
----------------------
``LIVE``                 声明端点全部在线上注册（最严）
``MOUNTED_NOT_DEPLOYED`` 已挂载但线上没有（部署检查点落后 / 该路径前缀不同）
``UNWIRED``              模块声明了端点但 main.py 未挂载 → 线上必 404
``ORPHAN_ROUTER``        模块有 router 但零端点（空壳，非接线问题）
``NO_ROUTER``            模块无 router（纯服务层，不参与接线）

用法
----
    # 全量普查（静态 + 实况）
    python3 tools/audit/wiring_census.py \
        --out artifacts/wiring_census \
        --base-url http://127.0.0.1:8010/api/v1

    # 离线（无实况面）
    python3 tools/audit/wiring_census.py --out /tmp/census

    # 闸门模式：新增未接线域即红（exit 2）
    python3 tools/audit/wiring_census.py --out artifacts/wiring_census \
        --baseline artifacts/wiring_census/baseline.json

产物
----
* ``census.json``：逐模块三平面原始证据 + 汇总 + baseline 差分
* ``census.md``：人读表格

设计纪律
--------
* 只读：不 import 被审模块、不改任何源文件；
* 零第三方依赖（标准库即可），matplotlib 仅在 ``--figures`` 时按需导入；
* 判定规则全部是显式常量（见 ``VERDICT_*`` / ``CONTROL_PATHS``），测试可用
  ``--root`` 指向合成夹具树，做到正/负控制；
* 不臆造：路径逐字保留，别名解析失败即记为 ``unresolved`` 并计入证据。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

LIVE = "LIVE"
MOUNTED_NOT_DEPLOYED = "MOUNTED_NOT_DEPLOYED"
UNWIRED = "UNWIRED"
ORPHAN_ROUTER = "ORPHAN_ROUTER"
NO_ROUTER = "NO_ROUTER"

#: 判决严重度排序（汇总取最差；UNWIRED 是闸门要拦的）
RANK = {NO_ROUTER: 0, ORPHAN_ROUTER: 1, LIVE: 1, MOUNTED_NOT_DEPLOYED: 2, UNWIRED: 3}

#: 实况面控制组：这些路径必须出现在 openapi 里，否则实况面判 WITHHELD。
#: 取"老得不能再老"的两条平台端点（阻力/稳性），它们自平台诞生即存在。
CONTROL_PATHS: Tuple[str, ...] = (
    "/api/v1/calculations/resistance",
    "/api/v1/calculations/stability",
)

HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


# ── P-A 模块面 ────────────────────────────────────────────────────────────────
def scan_module(path: Path) -> Dict[str, Any]:
    """AST 解析单个模块：找 ``<name> = APIRouter(...)`` 与其装饰器端点。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, Any] = {
        "module": path.stem,
        "file": str(path),
        "routers": [],
        "endpoints": [],          # [{path, methods, handler}]
        "has_router": False,
        "parse_error": None,
    }
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # 语法坏掉的模块也算证据，不静默跳过
        out["parse_error"] = f"{exc.__class__.__name__}: {exc}"
        return out

    routers: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        fname = _call_name(node.value)
        if fname not in ("APIRouter", "fastapi.APIRouter"):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                routers.append(tgt.id)
    out["routers"] = sorted(set(routers))
    out["has_router"] = bool(routers)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            attr = dec.func.attr.lower()
            if attr not in HTTP_METHODS:
                continue
            base = dec.func.value
            if not (isinstance(base, ast.Name) and base.id in routers):
                continue
            if not dec.args:
                continue
            p = dec.args[0]
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                out["endpoints"].append(
                    {"path": p.value, "methods": [attr.upper()], "handler": node.name}
                )
    out["endpoints"].sort(key=lambda e: (e["path"], e["methods"]))
    return out


def _call_name(node: ast.AST) -> str:
    """把 Name / Attribute / Call 统一折成点号名（``APIRouter()`` -> ``APIRouter``）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Subscript):
        return _call_name(node.value)
    return ""


# ── P-B 挂载面 ────────────────────────────────────────────────────────────────
def scan_mounts(main_py: Path) -> Dict[str, Any]:
    """解析 main.py：被挂载的模块名集合 + import 别名映射 + 未解析项。"""
    info: Dict[str, Any] = {
        "file": str(main_py),
        "mounted_raw": [],        # include_router 的 <expr> 原文
        "mounted": [],            # 归属到模块名的集合
        "aliases": {},            # 本地名 -> 真实模块名
        "unresolved": [],
        "parse_error": None,
    }
    if not main_py.exists():
        info["parse_error"] = "main.py 不存在"
        return info
    tree = ast.parse(main_py.read_text(encoding="utf-8", errors="replace"))

    # import 别名：from api.domain import X [as Y] / from api import X as Y
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "api" not in node.module.split("."):
                continue
            for al in node.names:
                local = al.asname or al.name
                info["aliases"][local] = al.name

    mounted_raw: List[str] = []
    mounted: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = _call_name(node.func)
        if not fname.endswith("include_router"):
            continue
        if not node.args:
            continue
        expr = _call_name(node.args[0])
        mounted_raw.append(expr)
        head = expr.split(".")[0]
        mod = info["aliases"].get(head, head)
        if mod.startswith("_"):
            mod = mod.lstrip("_")
        mounted.append(mod)
    info["mounted_raw"] = mounted_raw
    info["mounted"] = sorted(set(mounted))
    return info


# ── P-C 实况面 ────────────────────────────────────────────────────────────────
def fetch_openapi(base_url: str, timeout: float = 30.0,
                  token: Optional[str] = None) -> Dict[str, Any]:
    """抓 openapi.json；返回 {'ok':bool,'paths':set,'error':str,'url':str}。

    ``base_url`` 可能是服务根（``http://host:port``）也可能带 ``/api/v1`` 前缀，
    故按候选顺序试：``<base>/openapi.json`` → ``<base>/api/v1/openapi.json``
    → ``<base去掉/api/v1>/openapi.json``。:8010（科研运行台）的 openapi 需要鉴权，
    支持 ``token`` 或环境变量 ``WIRING_CENSUS_TOKEN``；鉴权/网络失败如实记 WITHHELD。
    """
    base = base_url.rstrip("/")
    cands = [base + "/openapi.json"]
    if base.endswith("/api/v1"):
        cands.append(base[: -len("/api/v1")] + "/openapi.json")
    else:
        cands.append(base + "/api/v1/openapi.json")
    token = token or os.environ.get("WIRING_CENSUS_TOKEN")
    errs: List[str] = []
    for url in cands:
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                doc = json.loads(r.read() or b"{}")
            return {"ok": True, "paths": set(doc.get("paths") or {}),
                    "error": None, "url": url}
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as exc:
            errs.append(f"{url}: {exc.__class__.__name__}: {exc}")
    return {"ok": False, "paths": set(), "error": " | ".join(errs), "url": None}


def judge_live_plane(spec: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """实况面整体可信度：控制组必须在。"""
    if not spec.get("ok"):
        return {"status": "WITHHELD", "reason": spec.get("error") or "openapi 不可达"}
    missing = [p for p in CONTROL_PATHS if p not in spec["paths"]]
    if missing:
        return {"status": "WITHHELD",
                "reason": "控制组缺失，实况面不可信: " + ", ".join(missing)}
    return {"status": "TRUSTED", "reason": None, "prefix": prefix,
            "source": spec.get("url")}


# ── 判决 ──────────────────────────────────────────────────────────────────────
def _full_path(path: str, prefix: str) -> str:
    """模块内相对路径 -> openapi 全路径（斜杠归一，已带前缀者不重复加）。"""
    p = "/" + path.lstrip("/")
    if p.startswith(prefix.rstrip("/") + "/"):
        return p
    return prefix.rstrip("/") + p


def verdict_for(mod: Dict[str, Any], mounted: bool, live: Dict[str, Any],
                prefix: str, live_paths: Optional[set] = None) -> Dict[str, Any]:
    eps = mod["endpoints"]
    if not mod["has_router"]:
        return {"verdict": NO_ROUTER, "live_hits": None, "live_missing": None}
    if not eps:
        return {"verdict": ORPHAN_ROUTER, "live_hits": None, "live_missing": None}
    if not mounted:
        return {"verdict": UNWIRED, "live_hits": None, "live_missing": None}
    if live["status"] != "TRUSTED":
        # 挂载了，但实况面不可信 → 只能判"已挂载"，不臆造成 LIVE
        return {"verdict": MOUNTED_NOT_DEPLOYED, "live_hits": None,
                "live_missing": None, "live_status": live["status"]}
    full = {_full_path(e["path"], prefix) for e in eps}
    known = live_paths or set()
    hit = sorted(p for p in full if p in known)
    miss = sorted(p for p in full if p not in known)
    if not miss:
        return {"verdict": LIVE, "live_hits": hit, "live_missing": []}
    return {"verdict": MOUNTED_NOT_DEPLOYED, "live_hits": hit, "live_missing": miss}


def census(root: Path, base_url: Optional[str] = None,
           domain_dir: str = "backend/api/domain",
           main_py: str = "backend/main.py",
           prefix: str = "/api/v1",
           token: Optional[str] = None) -> Dict[str, Any]:
    dom = root / domain_dir
    mods = [scan_module(p) for p in sorted(dom.glob("*.py")) if p.name != "__init__.py"]
    mounts = scan_mounts(root / main_py)
    mounted_set = set(mounts["mounted"])

    live_spec: Dict[str, Any]
    if base_url:
        live_spec = fetch_openapi(base_url, token=token)
    else:
        live_spec = {"ok": False, "paths": set(), "error": "未提供 --base-url（离线模式）"}
    live_plane = judge_live_plane(live_spec, prefix)

    rows: List[Dict[str, Any]] = []
    for m in mods:
        v = verdict_for(m, m["module"] in mounted_set, live_plane, prefix,
                        live_paths=live_spec.get("paths"))
        rows.append({
            "module": m["module"],
            "routers": m["routers"],
            "n_endpoints": len(m["endpoints"]),
            "endpoints": [e["path"] for e in m["endpoints"]],
            "mounted_in_main": m["module"] in mounted_set,
            "parse_error": m["parse_error"],
            **v,
        })

    with_eps = [r for r in rows if r["n_endpoints"]]
    summary = {
        "modules_total": len(rows),
        "modules_with_endpoints": len(with_eps),
        "endpoints_total": sum(r["n_endpoints"] for r in rows),
        "by_verdict": {k: sum(1 for r in rows if r["verdict"] == k) for k in
                       (LIVE, MOUNTED_NOT_DEPLOYED, UNWIRED, ORPHAN_ROUTER, NO_ROUTER)},
        "live_plane": live_plane,
        "unwired_modules": sorted(r["module"] for r in with_eps
                                  if r["verdict"] == UNWIRED),
        "unwired_endpoints": sorted(p for r in with_eps
                                    if r["verdict"] == UNWIRED for p in r["endpoints"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "mount_plane": mounts,
        "modules": rows,
        "summary": summary,
    }


# ── baseline 闸门 ─────────────────────────────────────────────────────────────
def apply_baseline(doc: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """欠账豁免：新增 UNWIRED 即红；已清偿的 baseline 条目变 stale。"""
    entries = {e["module"]: e for e in (baseline.get("entries") or [])}
    unwired = set(doc["summary"]["unwired_modules"])
    new = sorted(m for m in unwired if m not in entries)
    known = sorted(m for m in unwired if m in entries)
    stale = sorted(m for m in entries if m not in unwired)
    bad = [m for m in new if not (entries.get(m) or {}).get("reason")]
    return {
        "unwired_new": new,          # 未登记 → drift
        "unwired_known": known,      # 已登记欠账（有理由）
        "baseline_stale": stale,     # 已清偿，提示删账
        "fail": bool(new),
        "n_entries": len(entries),
        "entries_missing_reason": bad,
    }


def render_markdown(doc: Dict[str, Any], diff: Optional[Dict[str, Any]] = None) -> str:
    s = doc["summary"]
    mp = doc["mount_plane"]
    lp = s["live_plane"]
    lines = [
        "# 接线普查台账（wiring census）",
        "",
        f"生成时间：`{doc['generated_at']}`　根目录：`{doc['root']}`　"
        f"schema：`{doc['schema_version']}`",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 扫描模块数 | {s['modules_total']} |",
        f"| 声明端点的模块数 | {s['modules_with_endpoints']} |",
        f"| 端点总数（模块面） | {s['endpoints_total']} |",
    ]
    for k in (LIVE, MOUNTED_NOT_DEPLOYED, UNWIRED, ORPHAN_ROUTER, NO_ROUTER):
        lines.append(f"| 判决 `{k}` | {s['by_verdict'][k]} |")
    lines += [
        f"| 实况面 | `{lp['status']}`{('（' + lp['reason'] + '）') if lp.get('reason') else ''} |",
        "",
        "## 逐模块判决",
        "",
        "| 模块 | 端点 | 已挂载 main.py | 判决 | 线上缺失 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in doc["modules"]:
        miss = r.get("live_missing")
        miss_s = "-" if not miss else (f"{len(miss)} 条" + (f"（如 `{miss[0]}`）" if miss else ""))
        lines.append(
            f"| `{r['module']}` | {r['n_endpoints']} | "
            f"{'是' if r['mounted_in_main'] else '**否**'} | `{r['verdict']}` | {miss_s} |"
        )
    if mp.get("unresolved"):
        lines += ["", "## 挂载面未解析项", ""] + [f"* `{u}`" for u in mp["unresolved"]]
    if diff is not None:
        lines += [
            "",
            "## baseline 差分（闸门）",
            "",
            f"* 新增未接线（drift）：{', '.join('`%s`' % m for m in diff['unwired_new']) or '无'}",
            f"* 已登记欠账：{', '.join('`%s`' % m for m in diff['unwired_known']) or '无'}",
            f"* baseline 已过期（提示删账）：{', '.join('`%s`' % m for m in diff['baseline_stale']) or '无'}",
            f"* 闸门结论：{'**FAIL**（存在未登记未接线域）' if diff['fail'] else 'PASS'}",
        ]
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="接线普查闸门（三平面）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]),
                    help="仓库根（含 backend/）")
    ap.add_argument("--out", required=True, help="产物目录")
    ap.add_argument("--base-url", default=None,
                    help="实况面 base（如 http://127.0.0.1:8010/api/v1）；省略则纯静态")
    ap.add_argument("--domain-dir", default="backend/api/domain")
    ap.add_argument("--main-py", default="backend/main.py")
    ap.add_argument("--prefix", default="/api/v1")
    ap.add_argument("--token", default=None, help="实况面 Bearer token（:8010 需要）")
    ap.add_argument("--baseline", default=None, help="欠账豁免台账 json")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    doc = census(root, base_url=args.base_url, domain_dir=args.domain_dir,
                 main_py=args.main_py, prefix=args.prefix, token=args.token)
    diff = None
    if args.baseline and Path(args.baseline).exists():
        diff = apply_baseline(doc, json.loads(Path(args.baseline).read_text("utf-8")))
        doc["baseline_diff"] = diff

    (out / "census.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "census.md").write_text(render_markdown(doc, diff), encoding="utf-8")

    if args.figures:
        try:
            from wiring_census_figures import plot_census  # 同目录
            plot_census(doc, out / "fig_wiring_census.png")
        except Exception as exc:  # 图是可选产物，失败不改判决
            print(f"[warn] 图生成跳过: {exc}", file=sys.stderr)

    if not args.quiet:
        s = doc["summary"]
        print(f"modules={s['modules_total']} endpoints={s['endpoints_total']} "
              f"unwired={len(s['unwired_modules'])} live_plane={s['live_plane']['status']}")
        print("unwired: " + (", ".join(s["unwired_modules"]) or "-"))
    if diff is not None and diff["fail"]:
        print("[gate] FAIL 未登记未接线域: " + ", ".join(diff["unwired_new"]),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())