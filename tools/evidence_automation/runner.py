"""映射测试的批量执行与结果归属。

一次 pytest 调用跑完**所有**映射模块（junitxml 落盘），再按用例的 ``file``
属性把结果归属回能力 —— 比"每能力跑一遍"快一个量级，也让跨模块共享的夹具
（fixture）复用同一份通过/失败判定。

产物与措辞纪律
--------------
* 失败/错误/跳过 **分开计**：``errored``（收集期异常）与 ``failed``（断言失败）
  语义不同，前者往往是环境/工件缺失，判级时不得当作"实现造假"。
* 归属不到的用例（未在映射里）**不参与**任何能力的通过率，避免虚增。
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 - 需要在本仓跑 pytest
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List


@dataclass
class ModuleResult:
    """单个测试模块的执行统计。"""

    module: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    nodeids: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    executed: bool = False

    @property
    def pass_rate(self) -> float:
        """通过率 = passed / (passed + failed + errored)，跳过不计入分母。"""
        decisive = self.passed + self.failed + self.errored
        return self.passed / decisive if decisive else 0.0

    @property
    def decisive(self) -> int:
        return self.passed + self.failed + self.errored

    def to_dict(self) -> Dict[str, object]:
        return {
            "module": self.module,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "skipped": self.skipped,
            "decisive": self.decisive,
            "pass_rate": round(self.pass_rate, 4),
            "executed": self.executed,
            "failures": self.failures[:5],
        }


@dataclass
class TestRun:
    """一次批量执行的整体结果。"""

    exit_code: int
    modules: Dict[str, ModuleResult] = field(default_factory=dict)
    stdout_tail: str = ""
    junit_available: bool = False

    def aggregate(self, modules: Iterable[str]) -> Dict[str, object]:
        """把若干模块的统计合并成一条能力的测试证据。"""
        acc = ModuleResult(module="+".join(modules))
        for mod in modules:
            res = self.modules.get(mod)
            if res is None:
                continue
            acc.total += res.total
            acc.passed += res.passed
            acc.failed += res.failed
            acc.errored += res.errored
            acc.skipped += res.skipped
            acc.nodeids.extend(res.nodeids)
            acc.failures.extend(res.failures)
            acc.executed = acc.executed or res.executed
        return acc.to_dict()


def _norm(path: str) -> str:
    return path.replace(os.sep, "/").lstrip("./")


def _resolve_module(case: ET.Element, known: Iterable[str]) -> str:
    """把一条用例归属到映射模块。

    判据优先级：
    1. junit 的 ``file`` 属性（部分 pytest 版本不写）；
    2. ``classname`` 点号路径**自长向短**逐级去尾，与已知映射模块做后缀匹配
       —— pytest 的 ``classname`` 形如 ``tests.pkg.test_mod.TestClass``，
       直接点转斜杠会把类名当模块，导致"测试归属到不存在的模块"。
    """
    known = [_norm(k) for k in known]
    file_attr = _norm(case.get("file") or "")
    classname = _norm(case.get("classname") or "")
    candidates: List[str] = []
    if file_attr:
        candidates.append(file_attr)
    if classname:
        parts = classname.split(".")
        for cut in range(len(parts), 0, -1):
            candidates.append("/".join(parts[:cut]) + ".py")
    for cand in candidates:
        for known_mod in known:
            if cand == known_mod or cand.endswith("/" + known_mod):
                return known_mod
    return candidates[0] if candidates else ""


def parse_junit(xml_path: Path, module_hint: Iterable[str] = ()) -> Dict[str, ModuleResult]:
    """解析 junitxml → {模块路径: ModuleResult}。"""
    results: Dict[str, ModuleResult] = {}
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    known = list(module_hint)
    suites = root.findall(".//testsuite") or []
    for suite in suites:
        for case in suite.findall("testcase"):
            file_attr = _resolve_module(case, known)
            res = results.setdefault(file_attr, ModuleResult(module=file_attr))
            res.total += 1
            res.executed = True
            nodeid = f"{file_attr}::{case.get('name', '')}"
            res.nodeids.append(nodeid)
            if case.find("failure") is not None:
                res.failed += 1
                res.failures.append(nodeid)
            elif case.find("error") is not None:
                res.errored += 1
                res.failures.append(nodeid)
            elif case.find("skipped") is not None:
                res.skipped += 1
            else:
                res.passed += 1
    return results


def run_tests(root: Path, modules: Iterable[str], *, timeout: int = 3600,
              junit_path: Path | None = None, extra_args: Iterable[str] = (),
              ) -> TestRun:
    """批量执行映射测试。

    模块列表为空时返回一次 ``executed=False`` 的空运行（不调用 pytest）。
    """
    modules = [m for m in modules if m]
    if not modules:
        return TestRun(exit_code=0, junit_available=False, stdout_tail="no_tests_mapped")
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    junit_path = Path(junit_path) if junit_path else (tmp_dir / "evidence_junit_m12525.xml")
    if junit_path.exists():
        junit_path.unlink()
    cmd = [sys.executable, "-m", "pytest", *modules, "-q", "--tb=short",
           "-p", "no:cacheprovider", f"--junitxml={junit_path}", *extra_args]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(  # nosec B603 - 参数来自受控映射配置
            cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env,
        )
        exit_code = proc.returncode
        tail = "\n".join(proc.stdout.strip().splitlines()[-8:])
    except subprocess.TimeoutExpired:
        return TestRun(exit_code=124, stdout_tail=f"timeout_after_{timeout}s")
    if not junit_path.exists():
        return TestRun(exit_code=exit_code, stdout_tail=tail, junit_available=False)
    results = parse_junit(junit_path, modules)
    for mod in modules:
        results.setdefault(mod, ModuleResult(module=mod, executed=False))
    return TestRun(exit_code=exit_code, modules=results, stdout_tail=tail,
                   junit_available=True)