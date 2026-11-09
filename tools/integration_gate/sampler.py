"""声明式量测：把"模块/函数/参数网格/常量参数"当数据，产出可复算轨迹。

三种轨迹种类 (全部由 ``data/campaigns/<c>/traces.json`` 注入，代码零领域语义)：

* ``callable``  — ``import_root`` + ``module`` + ``func``，沿 ``grid.param`` 取值调用；
* ``script``    — 独立脚本路径 (域算例脚本放在 data/ 下)，``python <script>`` 打印 JSON；
* ``expression``— 安全算术表达式 (AST 白名单，无函数调用、无属性访问、无名字除变量外)。

失败一律 ``status="error"`` + ``error`` 文本 (fail-closed，由 compare 降级为 WITHHELD)。
每条轨迹记录：模块/脚本文件 sha256、python 版本、耗时、行值；外部 worktree 路径标记
``external=True``，使报告能区分"main 基线内可复现"与"仅本机兄弟 worktree 可见"。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import operator
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .model import Trace
from .provenance import sha256_file, sha256_obj

_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_SAFE_FUNCS = {
    "abs": abs, "min": min, "max": max, "float": float, "int": int,
    "pow": pow, "round": round,
}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """代数表达式求值：只允许 +-*/%**、括号、数字常量与白名单函数。"""
    tree = ast.parse(expr, mode="eval")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("non-numeric constant")
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
            return float(_SAFE_OPS[type(node.op)](ev(node.left), ev(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return float(_SAFE_OPS[type(node.op)](ev(node.operand)))
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unknown name {node.id!r}")
            return float(names[node.id])
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                raise ValueError("only whitelisted plain functions allowed")
            if node.keywords:
                raise ValueError("keyword args not allowed")
            return float(_SAFE_FUNCS[node.func.id](*[ev(a) for a in node.args]))
        raise ValueError(f"disallowed expression node {type(node).__name__}")

    return float(ev(tree))


def _import_callable(import_root: str | None, module: str, func: str):
    root = str(Path(import_root).resolve()) if import_root else None
    added = False
    if root and root not in sys.path:
        sys.path.insert(0, root)
        added = True
    try:
        mod = importlib.import_module(module)
        fn = getattr(mod, func)
        if not callable(fn):
            raise TypeError(f"{module}.{func} is not callable")
        return fn, (sha256_file(mod.__file__) if getattr(mod, "__file__", None) else None)
    finally:
        if added:
            try:
                sys.path.remove(root)  # type: ignore[arg-type]
            except ValueError:  # pragma: no cover
                pass


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        import numpy as np  # local import: optional at runtime
        if isinstance(v, (np.floating, np.integer)):
            return float(v)
    except Exception:  # pragma: no cover
        pass
    return None


def _trace_callable(spec: dict[str, Any], repo_root: Path) -> Trace:
    t0 = time.time()
    grid = spec["grid"]
    pname = grid["param"]
    kwargs = dict(spec.get("kwargs") or {})
    import_root = spec.get("import_root")
    root = None
    if import_root:
        root = Path(import_root)
        if not root.is_absolute():
            root = (repo_root / root)
    prov: dict[str, Any] = {
        "import_root": str(root) if root else None,
        "external": bool(root and repo_root.resolve() not in Path(root).resolve().parents
                        and Path(root).resolve() != repo_root.resolve()),
        "module": spec["module"], "func": spec["func"], "kwargs": kwargs,
        "python": platform.python_version(),
    }
    rows: list[dict[str, Any]] = []
    try:
        fn, mod_sha = _import_callable(str(root) if root else None, spec["module"], spec["func"])
        prov["module_sha256"] = mod_sha
        for v in grid["values"]:
            val = fn(**{pname: v, **kwargs})
            rows.append({"param": float(v), "value": _as_float(val),
                         "raw_type": type(val).__name__})
        status, err = "ok", ""
    except Exception as exc:  # fail-closed
        status, err = "error", f"{type(exc).__name__}: {exc}"
    prov["elapsed_s"] = round(time.time() - t0, 6)
    return Trace(id=spec["id"], kind="callable", param=pname,
                 values=[float(v) for v in grid["values"]], rows=rows,
                 status=status, meta=dict(spec.get("meta") or {}),
                 provenance=prov, error=err)


def _trace_script(spec: dict[str, Any], repo_root: Path) -> Trace:
    t0 = time.time()
    p = Path(spec["path"])
    if not p.is_absolute():
        p = repo_root / p
    prov: dict[str, Any] = {"path": str(p), "external": False,
                            "python": platform.python_version(),
                            "sha256": sha256_file(p) if p.is_file() else None,
                            "timeout_s": spec.get("timeout_s", 120)}
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = dict(spec.get("meta") or {})
    status, err = "ok", ""
    if not p.is_file():
        status, err = "error", f"script not found: {p}"
    else:
        try:
            cp = subprocess.run([sys.executable, str(p)], cwd=str(repo_root),
                                capture_output=True, text=True,
                                timeout=prov["timeout_s"])
            if cp.returncode != 0:
                raise RuntimeError((cp.stderr or cp.stdout).strip()[-400:])
            out = json.loads(cp.stdout.strip().splitlines()[-1])
            rows = [{"param": float(r["param"]), "value": _as_float(r["value"])}
                    for r in out["rows"]]
            meta.update(out.get("meta") or {})
            prov["stdout_sha256"] = hashlib.sha256(cp.stdout.encode()).hexdigest()
        except Exception as exc:
            status, err = "error", f"{type(exc).__name__}: {exc}"
    prov["elapsed_s"] = round(time.time() - t0, 6)
    return Trace(id=spec["id"], kind="script", param=spec["grid"]["param"],
                 values=[float(v) for v in spec["grid"]["values"]], rows=rows,
                 status=status, meta=meta, provenance=prov, error=err)


def _trace_expression(spec: dict[str, Any], repo_root: Path) -> Trace:
    grid = spec["grid"]
    pname = grid["param"]
    consts = {k: float(v) for k, v in (spec.get("consts") or {}).items()}
    rows: list[dict[str, Any]] = []
    status, err = "ok", ""
    try:
        for v in grid["values"]:
            rows.append({"param": float(v),
                         "value": safe_eval(spec["expr"], {pname: float(v), **consts})})
    except Exception as exc:
        status, err = "error", f"{type(exc).__name__}: {exc}"
    return Trace(id=spec["id"], kind="expression", param=pname,
                 values=[float(v) for v in grid["values"]], rows=rows,
                 status=status, meta=dict(spec.get("meta") or {}),
                 provenance={"expr": spec["expr"], "consts": consts, "external": False},
                 error=err)


def produce_trace(spec: dict[str, Any], repo_root: str | Path = ".") -> Trace:
    root = Path(repo_root).resolve()
    kind = spec["kind"]
    if kind == "callable":
        tr = _trace_callable(spec, root)
    elif kind == "script":
        tr = _trace_script(spec, root)
    elif kind == "expression":
        tr = _trace_expression(spec, root)
    else:  # pragma: no cover - guarded by schema
        raise ValueError(f"unknown trace kind {kind!r}")
    tr.meta.setdefault("spec_sha256", sha256_obj({k: v for k, v in spec.items()}))
    tr.provenance["trace_sha256"] = sha256_obj(
        {"id": tr.id, "kind": tr.kind, "rows": tr.rows, "status": tr.status})
    return tr


def produce_all(spec_doc: dict[str, Any], repo_root: str | Path = ".") -> dict[str, Trace]:
    out: dict[str, Trace] = {}
    for spec in spec_doc["traces"]:
        out[spec["id"]] = produce_trace(spec, repo_root)
    return out