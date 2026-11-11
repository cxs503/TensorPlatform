"""可执行证据采集器：pytest 选择器执行 + 不变量探针执行。

两个执行器都只认**配置数据**：选择器、调用目标、参数点、不变量表达式与阈值全部
来自 spec；代码本身不含任何域词条。

证据形状（每能力）::

    {"pytest": {"status": "PASSED", "tests": 39, "passed": 39, "failed": 0, ...,
                "numeric_assertions": 12, "digest": "<sha256>"},
     "probe":  {"status": "OK", "checks": [{"id": "...", "pass": false, ...}],
                "contradictions": ["..."], "digest": "<sha256>"}}

``status`` 取值（判定级联只认这些）：
pytest: ``NOT_BOUND`` / ``PASSED`` / ``TEST_FAILED`` / ``TEST_ERROR`` / ``ENV_ERROR``
        / ``COLLECTION_ERROR`` / ``NO_TESTS`` / ``SKIPPED`` / ``TIMEOUT``
probe:  ``NOT_BOUND`` / ``OK`` / ``ERROR``

**确定性**：证据里不含耗时、时间戳、绝对路径；测试用例按 (classname, name) 排序，
探针数值统一 round(…, 10)。两次运行同一代码库 ⇒ 证据 JSON 的 sha256 相同。
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from tools.capability_audit_gate import scan as gate_scan

ROUND_NDIGITS = 10


# ── 小工具 ───────────────────────────────────────────────────────────
def _sha(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _round(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        r = round(float(value), ROUND_NDIGITS)
        return 0.0 if r == 0 else r
    return value


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _tail(text: str | None, limit: int = 600) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[-limit:]


def _is_env_error(text: str, markers: list[str]) -> bool:
    low = (text or "").lower()
    return any(m.lower() in low for m in markers if m)


def numeric_assertions(path: Path) -> int:
    """数值断言数（交给既有 AST 扫描器，避免重复实现）。"""
    try:
        return int(gate_scan.scan_tests(path).get("numeric_assertions") or 0)
    except (OSError, SyntaxError, ValueError):
        return 0


# ── pytest 执行器 ────────────────────────────────────────────────────
def _parse_junit(path: Path) -> dict:
    out = {"cases": [], "failures": [], "errors": [], "skipped": 0}
    if not path.is_file():
        return out
    try:
        tree = ET.parse(str(path))
    except ET.ParseError:
        return out
    for node in tree.iter():
        if _localname(node.tag) != "testcase":
            continue
        case = (node.get("classname") or "", node.get("name") or "")
        out["cases"].append(case)
        for child in node:
            kind = _localname(child.tag)
            text = _tail(child.get("message") or "") + " " + _tail((child.text or ""))
            if kind == "failure":
                out["failures"].append({"case": case, "text": text.strip()})
            elif kind == "error":
                out["errors"].append({"case": case, "text": text.strip()})
            elif kind == "skipped":
                out["skipped"] += 1
    out["cases"].sort()
    out["failures"].sort(key=lambda x: x["case"])
    out["errors"].sort(key=lambda x: x["case"])
    return out


def run_pytest(cap_id: str, entry: dict, root: Path, default_timeout: int,
               env_markers: list[str]) -> dict:
    """执行声明式 pytest 选择器，返回确定性证据（不含耗时/时间戳）。"""
    selectors = list(entry["selectors"])
    timeout = int(entry.get("timeout_s") or default_timeout)
    missing = [s.split("::")[0] for s in selectors if not (root / s.split("::")[0]).exists()]
    base = {
        "bound": True,
        "selectors": selectors,
        "numeric_assertions": sum(
            numeric_assertions(root / s.split("::")[0])
            for s in selectors if (root / s.split("::")[0]).is_file()
        ),
        "missing_selector_files": sorted(set(missing)),
    }
    if missing:
        base.update({"status": "COLLECTION_ERROR", "tests": 0, "passed": 0, "failed": 0,
                     "errors": 0, "skipped": 0, "failing_tests": [], "detail": "selector file missing"})
        base["digest"] = _sha({k: v for k, v in base.items() if k != "detail"})
        return base

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(root), str(root / "backend")]),
    }
    if entry.get("minimal_plugins"):
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    with tempfile.TemporaryDirectory(prefix="cav2_") as td:
        junit = Path(td) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", "--rootdir", str(root)]
        if entry.get("confcutdir"):
            cmd.append(f"--confcutdir={root / entry['confcutdir']}")
        cmd.extend(selectors)
        try:
            proc = subprocess.run(cmd, cwd=str(root), env=env, timeout=timeout,
                                  capture_output=True, text=True)
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            base.update({"status": "TIMEOUT", "tests": 0, "passed": 0, "failed": 0,
                         "errors": 0, "skipped": 0, "failing_tests": [],
                         "detail": f"timeout>{timeout}s"})
            base["digest"] = _sha({k: v for k, v in base.items() if k != "detail"})
            return base
        parsed = _parse_junit(junit)

    cases = parsed["cases"]
    failures = parsed["failures"]
    errors = parsed["errors"]
    blob = " ".join([out or "", err or ""] + [f["text"] for f in failures]
                    + [e["text"] for e in errors])
    status: str
    if errors:
        status = "ENV_ERROR" if _is_env_error(blob, env_markers) else "TEST_ERROR"
    elif failures:
        status = "ENV_ERROR" if _is_env_error(blob, env_markers) else "TEST_FAILED"
    elif not cases:
        status = "NO_TESTS" if rc == 5 else "COLLECTION_ERROR"
    elif parsed["skipped"] == len(cases):
        status = "SKIPPED"
    else:
        status = "PASSED"

    failing = [f"{c[0]}::{c[1]}" for c in
               [f["case"] for f in failures] + [e["case"] for e in errors]]
    base.update({
        "status": status,
        "tests": len(cases),
        "passed": len(cases) - len(failures) - len(errors) - parsed["skipped"],
        "failed": len(failures),
        "errors": len(errors),
        "skipped": parsed["skipped"],
        "failing_tests": sorted(failing),
        "test_names": [f"{c[0]}::{c[1]}" for c in cases],
        "env_marker_hit": _is_env_error(blob, env_markers),
    })
    base["digest"] = _sha({k: v for k, v in base.items() if k != "test_names"})
    return base


# ── 不变量探针执行器 ─────────────────────────────────────────────────
def _extract(result: Any, names: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, name in enumerate(names):
        if isinstance(result, dict):
            val = result[name]
        elif hasattr(result, name):
            val = getattr(result, name)
        elif isinstance(result, (tuple, list)):
            val = result[i]
        else:
            raise TypeError(f"探针返回值无法取出 {name!r}（类型 {type(result).__name__}）")
        out[name] = float(val)
    return out


_SAFE_NAMES = {k: getattr(math, k) for k in
               ("pi", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
                "exp", "log", "log10", "pow", "fabs", "hypot", "degrees", "radians")}


def _eval_expr(expr: str, variables: dict[str, float]) -> float:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name)
                                               and node.func.id in _SAFE_NAMES):
            raise ValueError(f"不变量表达式只允许数学函数调用: {expr!r}")
        if isinstance(node, ast.Name) and node.id not in variables and node.id not in _SAFE_NAMES:
            raise ValueError(f"不变量表达式出现未定义符号 {node.id!r}: {expr!r}")
        if isinstance(node, ast.Attribute):
            raise ValueError(f"不变量表达式不允许属性访问: {expr!r}")
    env = dict(_SAFE_NAMES)
    env.update({k: v for k, v in variables.items()})
    return float(eval(compile(tree, "<identity>", "eval"), {"__builtins__": {}}, env))  # noqa: S307


def _spread(values: list[float], eps: float) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / max(abs(mean), eps)


def _check_identity(ck: dict, records: list[dict], outputs: list[str]) -> dict:
    residuals = []
    for rec in records:
        variables = dict(rec["point"])
        for name, value in rec["out"].items():
            variables[name] = value
            variables[name.lower()] = value
        residuals.append(abs(_eval_expr(str(ck["expr"]), variables)))
    worst = max(residuals) if residuals else float("inf")
    return {"id": ck["id"], "kind": ck["kind"], "metric": "max_abs_residual",
            "value": _round(worst), "threshold": _round(ck["tolerance"]),
            "pass": bool(worst <= float(ck["tolerance"]))}


def _check_sensitivity(ck: dict, records: list[dict], params: list[str], eps: float) -> dict:
    """控制其余参数不变，测输出对**指定输入**的相对展布。

    只统计"除该输入外其余参数完全相同"的组（每组 ≥2 个不同输入值），否则 j 之类的
    其它变量会把展布撑大，令"零敏感"的实现也能蒙混过关。
    required（必须有敏感度）：取各组展布的**最小值**（最弱观测敏感度）。
    forbidden（必须不敏感）：取各组展布的**最大值**。
    """
    varying = str(ck["input"])
    fixed = [p for p in params if p != varying]
    groups: dict[tuple, dict[Any, float]] = {}
    for rec in records:
        key = tuple(sorted((p, rec["point"][p]) for p in fixed))
        groups.setdefault(key, {})[_round(rec["point"][varying])] = rec["out"][str(ck["output"])]
    spreads = [_spread(list(g.values()), eps) for g in groups.values() if len(g) >= 2]
    required = ck["kind"] == "sensitivity_required"
    value = (min(spreads) if required else max(spreads)) if spreads else 0.0
    ok = value >= float(ck["threshold"]) if required else value <= float(ck["threshold"])
    return {"id": ck["id"], "kind": ck["kind"], "input": varying,
            "output": str(ck["output"]), "metric": "relative_spread",
            "n_groups": len(spreads), "value": _round(value),
            "threshold": _round(ck["threshold"]), "pass": bool(ok)}


def run_probe(cap_id: str, entry: dict, root: Path, eps: float,
              env_markers: list[str]) -> dict:
    """按声明调用实现并核验不变量；返回确定性证据。"""
    target = entry["target"]
    mod_name, _, attr = target.partition(":")
    base: dict[str, Any] = {"bound": True, "target": target,
                            "params": list(entry["params"]),
                            "outputs": list(entry["outputs"])}
    inserted: list[str] = []
    try:
        for rel in entry.get("path_prepend") or []:
            p = str((root / rel).resolve())
            if p not in sys.path:
                sys.path.insert(0, p)
            inserted.append(p)
        try:
            module = importlib.import_module(mod_name)
        except Exception:  # 重试：清掉缓存后再导一次（父包路径差异）
            importlib.invalidate_caches()
            module = importlib.import_module(mod_name)
        fn = getattr(module, attr)
        records: list[dict] = []
        for pt in entry["points"]:
            args = [pt[p] for p in entry["params"]]
            got = _extract(fn(*args), entry["outputs"])
            records.append({"point": {k: _round(v) for k, v in pt.items()},
                            "out": {k: _round(v) for k, v in got.items()}})
        # 决定性：同点二次调用结果必须一致
        deterministic = True
        for pt in entry["points"]:
            again = _extract(fn(*[pt[p] for p in entry["params"]]), entry["outputs"])
            first = [r["out"] for r in records if r["point"] == {k: _round(v) for k, v in pt.items()}]
            if first and {k: _round(v) for k, v in again.items()} != first[0]:
                deterministic = False
                break
        checks = []
        for ck in entry["checks"]:
            if ck["kind"] == "identity":
                checks.append(_check_identity(ck, records, entry["outputs"]))
            elif ck["kind"] in ("sensitivity_required", "sensitivity_forbidden"):
                checks.append(_check_sensitivity(ck, records, entry["params"], eps))
            else:  # determinism
                checks.append({"id": ck["id"], "kind": "determinism", "metric": "bitwise_equal",
                               "value": _round(1.0 if deterministic else 0.0),
                               "threshold": 1.0, "pass": deterministic})
        contradictions = sorted(c["id"] for c in checks if not c["pass"])
        base.update({"status": "OK", "checks": checks, "contradictions": contradictions,
                     "passed_checks": sorted(c["id"] for c in checks if c["pass"]),
                     "records": records})
    except Exception as exc:  # 探针本身跑不起来 ≠ 能力造假
        msg = _tail(f"{type(exc).__name__}: {exc}")
        status = "ENV_ERROR" if _is_env_error(msg, env_markers) else "ERROR"
        base.update({"status": status, "checks": [], "contradictions": [],
                     "detail": msg, "records": []})
    finally:
        for p in inserted:
            if p in sys.path:
                sys.path.remove(p)
    base["digest"] = _sha({k: v for k, v in base.items() if k != "detail"})
    return base


# ── 编排 ─────────────────────────────────────────────────────────────
def collect(spec: dict, root: str | Path = ".", workdir: str | Path | None = None) -> dict:
    """对 spec 里声明的每个能力采集可执行证据；未声明的能力显式记 NOT_BOUND。"""
    root = Path(root).resolve()
    execution = spec.get("execution") or {}
    default_timeout = int(execution.get("timeout_s") or 300)
    env_markers = list(execution.get("env_markers") or [])
    eps = float(spec["thresholds"].get("probe_rel_eps", 1e-12))
    out: dict[str, dict] = {}
    for cap in spec["base_spec"]["capabilities"]:
        cap_id = cap["id"]
        decl = spec["exec"].get(cap_id) or {}
        ev: dict[str, Any] = {}
        if decl.get("pytest"):
            pytest_decl = dict(decl["pytest"])
            if not pytest_decl.get("confcutdir"):
                pytest_decl["confcutdir"] = execution.get("confcutdir")
            if "minimal_plugins" not in pytest_decl:
                pytest_decl["minimal_plugins"] = execution.get("minimal_plugins")
            ev["pytest"] = run_pytest(cap_id, pytest_decl, root, default_timeout, env_markers)
        else:
            ev["pytest"] = {"bound": False, "status": "NOT_BOUND", "tests": 0, "passed": 0,
                            "failed": 0, "errors": 0, "skipped": 0, "failing_tests": [],
                            "numeric_assertions": 0,
                            "digest": _sha({"bound": False, "capability": cap_id})}
        if decl.get("probe"):
            ev["probe"] = run_probe(cap_id, decl["probe"], root, eps, env_markers)
        else:
            ev["probe"] = {"bound": False, "status": "NOT_BOUND", "checks": [],
                           "contradictions": [],
                           "digest": _sha({"bound": False, "capability": cap_id})}
        if decl.get("control_probe"):
            ev["control_probe"] = run_probe(cap_id, decl["control_probe"], root, eps, env_markers)
        else:
            ev["control_probe"] = {"bound": False, "status": "NOT_BOUND", "checks": [],
                                   "contradictions": [],
                                   "digest": _sha({"bound": False, "capability": cap_id})}
        out[cap_id] = ev
    return out