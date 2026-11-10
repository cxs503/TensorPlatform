"""Regression recording: run existing suites and store their verdict as evidence.

The integration node needs the answer to "did adding this harness break anything?"
to be *recorded*, not remembered.  This module runs existing test targets in a
subprocess, parses pytest's own summary line into counts, and returns one record per
target:

    {"target": "tests/test_coupling_remap.py", "returncode": 0,
     "passed": 24, "failed": 0, "skipped": 1, "errors": 0, "duration_s": 3.1,
     "result": "PASS", "summary": "24 passed, 1 skipped in 3.10s"}

Design notes:

* **Subprocess isolation** -- a suite that hangs or crashes the interpreter cannot
  take the harness down with it, and a timeout is reported as its own result.
* **Counts, not just an exit code** -- zero collected tests is treated as a
  failure of the check itself (`result="EMPTY"`), because "nothing ran" is the
  most common way a green regression claim is wrong.
* **No shell** -- targets are passed as argv, so a path with spaces cannot become
  a second command.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|deselected)")
_RESULT = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)", re.MULTILINE)


@dataclass
class SuiteResult:
    target: str
    returncode: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    summary: str = ""
    timed_out: bool = False

    @property
    def result(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        if self.passed == 0 and self.failed == 0 and self.errors == 0:
            return "EMPTY"
        if self.failed or self.errors or self.returncode != 0:
            return "FAIL"
        return "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": f"pytest {self.target}",
            "target": self.target,
            "result": self.result,
            "returncode": self.returncode,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "duration_s": self.duration_s,
            "summary": self.summary,
        }


def run_suite(target: str, timeout: float = 900.0, extra_args: Sequence[str] = ()) -> SuiteResult:
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-p", "no:randomly", *extra_args]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=None,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") or ""
        rc = -1
        timed_out = True
    duration = time.perf_counter() - t0

    counts = {"passed": 0, "failed": 0, "error": 0, "errors": 0, "skipped": 0,
              "xfailed": 0, "xpassed": 0, "deselected": 0}
    for n, word in _SUMMARY.findall(out):
        counts[word] = int(n)
    summary_lines = [ln for ln in out.strip().splitlines() if " in " in ln or " no tests ran" in ln]
    summary = summary_lines[-1].strip() if summary_lines else ""
    return SuiteResult(
        target=target,
        returncode=rc,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"] + counts["errors"],
        skipped=counts["skipped"] + counts["xfailed"] + counts["xpassed"],
        duration_s=duration,
        summary=summary[:200],
        timed_out=timed_out,
    )


def run_suites(targets: Sequence[str], timeout: float = 900.0) -> List[Dict[str, Any]]:
    return [run_suite(t, timeout=timeout).to_dict() for t in targets]


def aggregate(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll several suite records into one verdict (any FAIL/EMPTY/TIMEOUT fails)."""
    bad = [r for r in records if r["result"] != "PASS"]
    return {
        "n_targets": len(records),
        "n_pass": sum(1 for r in records if r["result"] == "PASS"),
        "n_bad": len(bad),
        "total_passed": sum(int(r["passed"]) for r in records),
        "total_failed": sum(int(r["failed"]) for r in records),
        "total_errors": sum(int(r["errors"]) for r in records),
        "total_skipped": sum(int(r["skipped"]) for r in records),
        "total_duration_s": sum(float(r["duration_s"]) for r in records),
        "verdict": "PASS" if not bad else "FAIL",
        "bad_targets": [r["target"] for r in bad],
    }