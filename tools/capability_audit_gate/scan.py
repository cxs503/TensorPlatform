"""证据信号提取：实现代码 AST 信号、测试 AST 信号、基准工件数值。

所有信号都是**可复算的结构量**（行数、函数数、空壳占比、随机调用数、断言数、
实测值/参考值/误差），不是"看起来像"的语义判断；判定规则在 ``rules.py`` 里单列，
阈值在 spec 里先声明。基准数值一律从工件里程序化读取（零手抄）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

MOCK_MARKERS = re.compile(
    r"(?i)(?:#|//|'''|\"\"\").{0,40}?(mock|hardcod|fake|placeholder|dummy|todo:\s*implement)")
RANDOM_FUNCS = frozenset({"random", "rand", "randn", "randint", "uniform", "choice",
                          "normal", "shuffle", "randrange"})


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _is_stub_function(fn: ast.AST) -> bool:
    body = _strip_docstring(list(getattr(fn, "body", [])))
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and stmt.value.value is Ellipsis:
            return True
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            name = ast.unparse(stmt.exc) if hasattr(ast, "unparse") else ""
            if "NotImplementedError" in name:
                return True
    return False


def _returns_constant(fn: ast.AST) -> bool:
    body = _strip_docstring(list(getattr(fn, "body", [])))
    return (len(body) == 1 and isinstance(body[0], ast.Return)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, (int, float))
            and not isinstance(body[0].value.value, bool))


def scan_impl(path: Path) -> dict:
    """实现文件信号。语法错误不掩盖：``parse_error`` 置位并由规则判 WITHHELD。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    signal: dict[str, Any] = {
        "sloc": sum(1 for ln in text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")),
        "functions": 0, "stub_functions": 0, "constant_returns": 0,
        "random_calls": 0, "marker_hits": 0, "parse_error": False,
    }
    try:
        tree = ast.parse(text)
    except SyntaxError:
        signal["parse_error"] = True
        return signal
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signal["functions"] += 1
            if _is_stub_function(node):
                signal["stub_functions"] += 1
            if _returns_constant(node):
                signal["constant_returns"] += 1
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in RANDOM_FUNCS:
                signal["random_calls"] += 1
    signal["marker_hits"] = len(MOCK_MARKERS.findall(text))
    return signal


def scan_tests(path: Path) -> dict:
    """测试文件信号：测试函数名、数值断言数、占位断言数。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    signal: dict[str, Any] = {"test_names": [], "numeric_assertions": 0,
                              "skeleton_assertions": 0, "parse_error": False}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        signal["parse_error"] = True
        return signal
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            signal["test_names"].append(node.name)
        if isinstance(node, ast.Assert):
            seg = ast.unparse(node) if hasattr(ast, "unparse") else ""
            has_number = any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                             and not isinstance(n.value, bool) for n in ast.walk(node))
            if "approx" in seg or "abs(" in seg or "tol" in seg.lower() or has_number:
                signal["numeric_assertions"] += 1
            else:
                signal["skeleton_assertions"] += 1
    signal["test_names"] = sorted(set(signal["test_names"]))
    return signal


# ── 基准工件 ────────────────────────────────────────────────────────────────
def _dig(obj: Any, dotted: str) -> list[Any]:
    """支持 ``a.b[*].c`` 的点路径取值，返回所有叶子（展平）。"""
    if not dotted:
        return [obj]
    parts = dotted.split(".")
    cur = [obj]
    for part in parts:
        nxt: list[Any] = []
        wildcard = part.endswith("[*]")
        key = part[:-3] if wildcard else part
        for item in cur:
            if key:
                if isinstance(item, dict) and key in item:
                    val = item[key]
                else:
                    continue
            else:
                val = item
            if wildcard:
                if isinstance(val, list):
                    nxt.extend(val)
            else:
                nxt.append(val)
        cur = nxt
    return [c for c in cur if isinstance(c, (int, float)) and not isinstance(c, bool)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_benchmark(entry: dict | None, root: Path) -> dict | None:
    """把基准定义解析成「实测/参考/误差/容差」行；工件缺失 ⇒ status=MISSING。"""
    if not entry or entry.get("kind") in (None, "none"):
        return None
    rel = entry["path"]
    path = root / rel
    payload = {"path": rel, "kind": entry["kind"], "check_class": entry.get("check_class", ""),
               "metric": entry.get("metric") or "", "status": "MISSING", "rows": [],
               "worst_error_pct": None, "tolerance_pct": float(entry.get("tolerance_pct", 0)), }
    if not path.is_file():
        return payload
    payload["sha256"] = _sha256(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload["status"] = "UNREADABLE"
        return payload

    rows: list[dict] = []
    if entry["kind"] == "json_cases":
        cases = _dig(data, entry.get("cases_path") or "") if entry.get("cases_path") else []
        raw_cases = _raw_cases(data, entry.get("cases_path") or "")
        for i, case in enumerate(raw_cases):
            if not isinstance(case, dict):
                continue
            comp = _first_number(case, entry.get("computed_fields") or [])
            ref = _first_number(case, entry.get("reference_fields") or [])
            err = _first_number(case, entry.get("error_fields") or [])
            if err is None and comp is not None and ref not in (None, 0):
                err = abs(comp - ref) / abs(ref) * 100.0
            if err is None:
                continue
            rows.append({"case": case.get("case_id") or case.get("case") or f"#{i}",
                         "computed": comp, "reference": ref, "error_pct": round(float(err), 6)})
        del cases
    elif entry["kind"] == "json_values":
        unit = entry.get("unit", "abs")
        ref = entry.get("reference")
        ref_val = float(ref) if isinstance(ref, (int, float)) else None
        for spec_path in entry.get("values") or []:
            vals = _dig(data, spec_path)
            for j, v in enumerate(vals):
                if unit == "pct":
                    err = float(v)
                elif ref_val is not None and ref_val != 0:
                    err = abs(float(v) - ref_val) / abs(ref_val) * 100.0
                else:
                    continue
                rows.append({"case": f"{spec_path}#{j}", "computed": v,
                             "reference": ref_val, "error_pct": round(err, 6)})
    elif entry["kind"] == "json_verdict":
        verdict = str(data.get(entry.get("verdict_key") or "verdict", "")).upper()
        failed = data.get(entry.get("failed_key") or "failed_checks") or []
        err = _first_number(data, entry.get("error_fields") or [])
        payload["declared_verdict"] = verdict
        payload["failed_checks"] = [str(x) for x in failed][:8]
        if err is not None:
            rows.append({"case": entry.get("metric") or "verdict", "computed": None,
                         "reference": None, "error_pct": round(float(err), 6)})

    payload["rows"] = rows[:40]
    errs = [r["error_pct"] for r in rows]
    if errs:
        agg = entry.get("aggregate", "worst")
        worst = max(errs) if agg != "mean" else sum(errs) / len(errs)
        payload["worst_error_pct"] = round(float(worst), 6)
        tol = payload["tolerance_pct"] or 3.0
        payload["status"] = "PASS" if worst <= tol else "OVER_TOLERANCE"
    elif payload.get("declared_verdict"):
        payload["status"] = "DECLARED_" + payload["declared_verdict"]
    else:
        payload["status"] = "NO_ROWS"
    return payload


def _raw_cases(data: Any, cases_path: str) -> list[Any]:
    if not cases_path:
        return data if isinstance(data, list) else []
    cur: Any = data
    for part in cases_path.split("."):
        if part.endswith("[*]"):
            key = part[:-3]
            cur = cur[key] if key else cur
            if not isinstance(cur, list):
                return []
            result: list[Any] = []
            for item in cur:
                result.extend(item if isinstance(item, list) else [item])
            cur = result
        else:
            if not isinstance(cur, dict) or part not in cur:
                return []
            cur = cur[part]
    return cur if isinstance(cur, list) else []


def _first_number(obj: dict, keys: list[str]) -> float | None:
    for k in keys or []:
        v = obj.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None