"""门 1：py_compile 编译门。

三层判据（任一层不过即 FAIL，补丁被回滚门撤回）：
    1. py_compile 编译：语法/缩进错误 —— 最常见的热文件坏法。
    2. AST 重复顶层定义扫描：正则并集合并造成的重复 def（历史上的
       KNOWN_TRAPS 实锤事故），py_compile 查不出来但运行时会静默覆盖。
    3. 凭据扫描：镜像内不得出现明文口令/令牌（本门 FAIL 意味着脱敏失效）。
"""

from __future__ import annotations

import ast
import hashlib
import py_compile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable

from harness_gateway import redact
from harness_gateway.gates.base import GateResult, GateStatus, timed


def _duplicate_toplevel_defs(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    dupes = [n for n, c in Counter(names).items() if c > 1]
    return sorted(dupes)


def run_compile_gate(paths: Iterable[Path], *, gate: str = "py_compile", check_secrets: bool = True) -> GateResult:
    result = GateResult(gate, GateStatus.PASS, "")
    files: list[dict] = []
    failures: list[str] = []
    with timed(result):
        for path in map(Path, paths):
            entry: dict = {"path": str(path), "exists": path.exists()}
            file_failures: list[str] = []
            if not path.exists():
                entry["status"] = "MISSING"
                file_failures.append(f"{path}: missing")
                failures.extend(file_failures)
                files.append(entry)
                continue
            raw = path.read_bytes()
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            entry["bytes"] = len(raw)
            text = raw.decode("utf-8")
            entry["lines"] = text.count("\n") + 1
            # 1) 编译
            try:
                with tempfile.TemporaryDirectory() as td:
                    py_compile.compile(
                        str(path),
                        cfile=str(Path(td) / (path.name + "c")),
                        doraise=True,
                    )
                entry["compiled"] = True
            except py_compile.PyCompileError as exc:
                entry["compiled"] = False
                entry["error"] = str(exc).strip().splitlines()[-1][:400]
                file_failures.append(f"{path.name}: compile error")
            # 2) AST 重复定义
            dupes: list[str] = []
            try:
                tree = ast.parse(text, filename=str(path))
                dupes = _duplicate_toplevel_defs(tree)
                entry["top_level_defs"] = sum(
                    1
                    for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                )
            except SyntaxError as exc:
                entry["ast_error"] = f"line {exc.lineno}: {exc.msg}"
            entry["duplicate_defs"] = dupes
            if dupes:
                file_failures.append(f"{path.name}: duplicate top-level defs {dupes}")
            # 3) 凭据
            if check_secrets:
                findings = [f.to_dict() for f in redact.scan(text)]
                entry["secret_findings"] = findings
                if findings:
                    file_failures.append(
                        f"{path.name}: {len(findings)} plaintext credential(s) at lines "
                        + ",".join(str(f["line"]) for f in findings)
                    )
            entry["status"] = "FAIL" if file_failures else "OK"
            if file_failures:
                entry["failures"] = file_failures
            failures.extend(file_failures)
            files.append(entry)
        result.evidence = {
            "file_count": len(files),
            "files": files,
            "failures": failures,
            "criteria": [
                "py_compile doraise=True",
                "no duplicate top-level def/class names",
                "no plaintext credentials (redaction effective)",
            ],
        }
        if failures:
            result.status = GateStatus.FAIL
            result.detail = "; ".join(failures)[:800]
        else:
            result.detail = f"{len(files)} file(s) compiled, no duplicate defs, no plaintext credentials"
    return result