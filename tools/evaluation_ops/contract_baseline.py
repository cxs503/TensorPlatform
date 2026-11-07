"""评价 API 契约基线（IN35-N07 判据 J4「向后兼容」的机器证据）。

做法：**在动手改任何代码之前**先把现有评价面的可观测契约冻结成 JSON：

1. ``stats_queries()`` 的查询名集合（统计口径的 SQL 入口）；
2. ``summarize()`` 在固定夹具上的完整输出（字段名 + 数值），字段集合与数值都不许变；
3. ``backend/api/outcome_evaluations.py`` 的路由表（静态 AST 解析：path + HTTP 方法）。

之后任何时刻重跑本模块并与基线文件逐字段比对，差异 = 契约被破坏。基线文件本身
**不随后续改动重新生成**（除非显式 ``--rewrite`` 并说明理由），否则判据就成了自证。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import tempfile
from typing import Any

from tools import outcome_evaluations as oe

from . import fixtures

SCHEMA = "eval-contract-baseline/1"
API_SOURCE = pathlib.Path("backend/api/outcome_evaluations.py")
#: 兼容性语义上「可以增长、但不许消失/改义」的顶层字段
SUMMARIZE_TOP_KEYS = (
    "schema", "repo", "window", "missions_in_window", "coverage",
    "distribution_confirmed", "sxe_matrix_confirmed", "sxe_matrix_candidate",
    "ladders", "records", "policy", "sql", "sql_params",
)


def api_routes(source: pathlib.Path = API_SOURCE) -> list[dict[str, Any]]:
    """静态解析 APIRouter 装饰器 → [{path, methods}]（不需要导入 backend 依赖）。"""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    prefix = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            call = node.value
            if isinstance(call, ast.Call):
                for kw in call.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = str(kw.value.value)
    routes: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            owner = dec.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "router"):
                continue
            methods = [dec.func.attr.upper()]
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [str(e.value).upper() for e in kw.value.elts
                               if isinstance(e, ast.Constant)]
            path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = str(dec.args[0].value)
            routes.append({"path": prefix + path, "methods": sorted(methods),
                           "handler": node.name})
    return sorted(routes, key=lambda r: (r["path"], ",".join(r["methods"])))


def capture(*, repo: str = fixtures.FIXTURE_REPO) -> dict[str, Any]:
    """在固定夹具上抓契约快照（每次都用全新临时库，保证与历史运行同口径）。"""
    from sqlalchemy import text

    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/contract.db"
        fixtures.build(path)
        conn = fixtures.connect(path)
        params = {"repo": repo, "window": oe.DEFAULT_WINDOW}
        queries = oe.stats_queries(org_filter=False)
        stats = oe.summarize(conn, repo=repo)
        missing = [k for k in SUMMARIZE_TOP_KEYS if k not in stats]
        if missing:
            raise RuntimeError(f" summarize 缺少契约字段: {missing}")
        coverage = dict(stats["coverage"])
        window_rows = [dict(r) for r in conn.execute(
            text(queries["window"]), params).mappings().all()]
        conn.close()

    return {
        "schema": SCHEMA,
        "stats_queries": sorted(queries),
        "summarize_top_keys": sorted(stats),
        "summarize_coverage": coverage,
        "summarize_window_rows": int(window_rows[0]["missions"]) if window_rows else 0,
        "summarize_missions_in_window": int(stats["missions_in_window"]),
        "distribution_confirmed_total": int(stats["distribution_confirmed"]["total"]),
        "policy_keys": sorted(stats["policy"]),
        "api_routes": api_routes(),
        "evaluation_statuses": sorted(oe.STATUSES),
        "registrable_grades": sorted(oe.REGISTRABLE_GRADES),
        "e_levels": sorted(oe.E_LEVELS),
        "default_window": int(oe.DEFAULT_WINDOW),
    }


def diff(baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """逐键比对（集合类键按「不许缩水」判，数值类键按严格相等判）。"""
    out: list[dict[str, Any]] = []
    growing = {"stats_queries", "summarize_top_keys", "api_routes", "policy_keys",
               "evaluation_statuses", "registrable_grades", "e_levels"}
    for key in baseline:
        if key == "schema":
            continue
        a, b = baseline[key], current.get(key)
        if key in growing:
            missing = [x for x in a if x not in (b or [])]
            if missing:
                out.append({"key": key, "kind": "removed", "missing": missing})
        elif a != b:
            out.append({"key": key, "kind": "changed", "baseline": a, "current": b})
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = pathlib.Path("artifacts/IN35-N07/baseline_contract.json")
    for a in args:
        if a.startswith("--out="):
            target = pathlib.Path(a.split("=", 1)[1])
    snap = capture()
    if "--check" in args:
        base = json.loads(target.read_text(encoding="utf-8"))
        d = diff(base, snap)
        print(json.dumps({"baseline": str(target), "diff": d, "ok": not d},
                         ensure_ascii=False, indent=2))
        return 0 if not d else 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"baseline written: {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())