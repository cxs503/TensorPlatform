"""执行型证据采集：把绑定的 pytest 模块真跑一遍，收结果（不是数测试名）。

为什么要真跑
============
静态审计只能看到"测试文件里写了断言"；测试是红的还是绿的、被 skip 了多少，
只有执行才知道。运行结果（passed/failed/error/skipped 计数 + 逐条用例状态 +
junit-xml 摘要）才是"自证"能用的证据。

纪律
====
* 子进程执行，模块路径来自 spec；模块缺失 → ``status=MISSING``（不静默算通过）；
* 超时 → ``status=TIMEOUT``，同样不通过；
* 摘要里不含耗时，保证同一代码库重跑结果逐位相同（digest 稳定）。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _module_digest(root: Path, modules: list[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(modules):
        p = root / rel
        h.update(rel.encode("utf-8"))
        if p.exists():
            h.update(hashlib.sha256(p.read_bytes()).digest())
        else:
            h.update(b"<missing>")
    return h.hexdigest()


def _parse_junit(path: Path) -> dict:
    if not path.exists():
        return {"cases": [], "totals": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}}
    tree = ET.parse(str(path))
    root = tree.getroot()
    suite = root if root.tag == "testsuite" else (root.find("testsuite") if root.find("testsuite") is not None else root)
    cases = []
    for case in suite.iter("testcase"):
        verdict = "passed"
        if case.find("failure") is not None:
            verdict = "failed"
        elif case.find("error") is not None:
            verdict = "error"
        elif case.find("skipped") is not None:
            verdict = "skipped"
        cases.append((f"{case.get('classname', '')}::{case.get('name', '')}", verdict))
    totals = {
        "tests": len(cases),
        "failures": sum(1 for _, v in cases if v == "failed"),
        "errors": sum(1 for _, v in cases if v == "error"),
        "skipped": sum(1 for _, v in cases if v == "skipped"),
        "passed": sum(1 for _, v in cases if v == "passed"),
    }
    return {"cases": cases, "totals": totals}


def run_tests(modules: list[str], root: str | Path = ".", timeout_s: float = 600.0) -> dict:
    """执行绑定测试模块，返回可入证据工件的结构化结果。"""
    root = Path(root).resolve()
    digest = _module_digest(root, modules)
    missing = [m for m in modules if not (root / m).exists()]
    result: dict = {
        "modules": list(modules),
        "modules_digest": digest,
        "missing_modules": missing,
        "status": "MISSING",
        "totals": {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0},
        "cases": [],
        "exit_code": None,
        "stdout_tail": "",
    }
    present = [m for m in modules if (root / m).exists()]
    if not present:
        result["detail"] = "no bound test module exists on disk"
        return result

    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", *present]
        env = dict(os.environ)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        try:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                                  timeout=timeout_s, env=env)
            result["exit_code"] = proc.returncode
            result["stdout_tail"] = "\n".join(proc.stdout.strip().splitlines()[-6:])
        except subprocess.TimeoutExpired:
            result["status"] = "TIMEOUT"
            result["detail"] = f"pytest exceeded {timeout_s}s"
            return result
        parsed = _parse_junit(junit)

    result["totals"] = parsed["totals"]
    result["cases"] = [{"nodeid": n, "verdict": v} for n, v in parsed["cases"]][:400]
    if missing:
        result["status"] = "PARTIAL_MISSING"
    elif result["totals"]["tests"] == 0:
        result["status"] = "EMPTY"
    elif result["totals"]["failures"] or result["totals"]["errors"]:
        result["status"] = "FAILED"
    elif result["exit_code"] not in (0, 5):
        result["status"] = "ERROR"
    else:
        result["status"] = "PASSED"
    return result