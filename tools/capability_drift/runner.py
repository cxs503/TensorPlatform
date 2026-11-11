"""验证案例重跑器：每个案例一个 pytest 子进程，指标经 plugin 落 JSON。

为什么子进程而不是同进程跑测试：验证案例可能 import 领域仓、改全局状态、崩溃；
隔离后单案例失败/超时只让该能力变黄，不会污染整轮检测。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .model import Case
from .plugin import OUT_ENV

PLUGIN_MODULE = "tools.capability_drift.plugin"
TP_ROOT = Path(__file__).resolve().parents[2]

PASSED = "passed"
FAILED = "failed"
TIMEOUT = "timeout"
ERROR = "error"


@dataclass
class CaseRun:
    case_id: str
    node: str
    status: str
    metrics: Dict[str, float] = field(default_factory=dict)
    duration_s: float = 0.0
    returncode: Optional[int] = None
    stdout_tail: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASSED

    def as_dict(self) -> Dict[str, object]:
        return {
            "case_id": self.case_id,
            "node": self.node,
            "status": self.status,
            "metrics": dict(self.metrics),
            "duration_s": round(self.duration_s, 4),
            "returncode": self.returncode,
            "error": self.error,
            "stdout_tail": self.stdout_tail[-800:],
        }


def _tail(text: str, n: int = 2000) -> str:
    text = (text or "").strip()
    return text[-n:]


def run_case(
    case: Case,
    repo_root: str | Path,
    *,
    python: str = sys.executable,
    timeout: float = 300.0,
    tp_root: str | Path = TP_ROOT,
    extra_env: Optional[Mapping[str, str]] = None,
) -> CaseRun:
    """跑单个案例：``python -m pytest <node> -p tools.capability_drift.plugin``。"""
    repo_root = Path(repo_root)
    fd, out_path = tempfile.mkstemp(prefix="capdrift_", suffix=".json")
    os.close(fd)
    os.unlink(out_path)
    env = dict(os.environ)
    env[OUT_ENV] = out_path
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tp_root), str(repo_root), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    cmd = [
        python,
        "-m",
        "pytest",
        case.node,
        "-q",
        "--no-header",
        "-p",
        PLUGIN_MODULE,
        "-p",
        "no:cacheprovider",
    ]
    started = time.perf_counter()
    status = ERROR
    rc: Optional[int] = None
    stdout = ""
    err = ""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc = proc.returncode
        stdout = _tail(proc.stdout, 4000) + ("\n" + _tail(proc.stderr, 2000) if proc.stderr else "")
        if rc == 0:
            status = PASSED
        elif rc == 1:
            status = FAILED
        elif rc == 5:
            status = ERROR
            err = "no tests collected (pytest exit 5)"
        else:
            status = ERROR
            err = f"pytest exit {rc}"
    except subprocess.TimeoutExpired:
        status = TIMEOUT
        err = f"timeout after {timeout}s"
    duration = time.perf_counter() - started
    metrics: Dict[str, float] = {}
    if os.path.exists(out_path):
        try:
            payload = json.loads(Path(out_path).read_text(encoding="utf-8"))
            metrics = {str(k): float(v) for k, v in (payload.get("metrics") or {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:  # pragma: no cover
            err = err or f"metrics json unreadable: {exc}"
        finally:
            try:
                os.unlink(out_path)
            except OSError:  # pragma: no cover
                pass
    if status == PASSED and not metrics:
        status = ERROR
        err = err or "case passed but recorded no metrics"
    return CaseRun(
        case_id=case.id,
        node=case.node,
        status=status,
        metrics=metrics,
        duration_s=duration,
        returncode=rc,
        stdout_tail=stdout,
        error=err,
    )


def run_cases(
    cases: Iterable[Case],
    repo_root: str | Path,
    *,
    workers: int = 4,
    timeout: float = 300.0,
    python: str = sys.executable,
    tp_root: str | Path = TP_ROOT,
) -> Dict[str, CaseRun]:
    """并发跑一批案例（默认 4 并发，避免压垮 CI 机器）；返回 case_id → CaseRun。"""
    case_list: List[Case] = list(cases)
    if not case_list:
        return {}
    workers = max(1, min(int(workers), len(case_list)))
    results: Dict[str, CaseRun] = {}
    if workers == 1:
        for c in case_list:
            results[c.id] = run_case(c, repo_root, python=python, timeout=timeout, tp_root=tp_root)
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_case, c, repo_root, python=python, timeout=timeout, tp_root=tp_root
            ): c
            for c in case_list
        }
        for fut, c in futures.items():
            results[c.id] = fut.result()
    return results


def run_capability_cases(
    capability,
    repo_root: str | Path,
    *,
    workers: int = 4,
    timeout: float = 300.0,
    python: str = sys.executable,
) -> Dict[str, CaseRun]:
    return run_cases(
        capability.cases, repo_root, workers=workers, timeout=timeout, python=python
    )