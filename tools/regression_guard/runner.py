"""Test execution layer: pytest subprocess runner + deterministic fake runner.

The guard never imports the code under test; it shells out so that a crashing
or hanging acceptance test cannot take the guard down with it, and so the
same layer works for any repo (solver repos included) without modification.

Outcome vocabulary is normalised to
``{"passed","failed","error","skipped","not_run"}`` — the comparison layer
only ever needs that much.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

OUTCOMES = ("passed", "failed", "error", "skipped", "not_run")


@dataclass
class TestOutcome:
    test_id: str
    outcome: str
    duration_s: float = 0.0
    detail: str = ""
    cache_hit: bool = False
    executed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "outcome": self.outcome,
            "duration_s": round(self.duration_s, 6),
            "detail": self.detail,
            "cache_hit": self.cache_hit,
            "executed": self.executed,
        }


def _norm_outcome(raw: str) -> str:
    raw = (raw or "").lower()
    if raw in ("passed", "pass"):
        return "passed"
    if raw in ("failed", "failure", "fail"):
        return "failed"
    if raw in ("error", "errored"):
        return "error"
    if raw in ("skipped", "skip", "xfailed", "xpassed"):
        return "skipped"
    return "not_run"


class PytestRunner:
    """Runs a set of pytest selectors in one subprocess and parses JUnit XML."""

    def __init__(
        self,
        root: Path | str,
        args: Sequence[str] = ("-q", "--tb=short", "-p", "no:cacheprovider"),
        timeout: int = 1800,
        python: Optional[str] = None,
    ) -> None:
        self.root = Path(root)
        self.args = tuple(args)
        self.timeout = timeout
        self.python = python or sys.executable
        self.invocations = 0

    @staticmethod
    def batch_key(selector: str) -> str:
        """Group selectors that must not share one pytest process.

        Each test root is executed in its own subprocess because separate roots
        carry separate ``conftest.py``/``pyproject.toml`` path insertions; a
        single process lets one root's ``sys.path`` shadow the other's top-level
        packages (observed: the ``tools`` package resolving to
        ``backend/tools`` and breaking a root-level pytest plugin import).
        """
        parts = selector.split("::", 1)[0].split("/")
        for i, part in enumerate(parts[:-1]):
            if part in ("tests", "test"):
                return "/".join(parts[: i + 1])
        return parts[0] if parts else ""

    def run(self, test_ids: Sequence[str]) -> List[TestOutcome]:
        if not test_ids:
            return []
        results: List[TestOutcome] = []
        runnable: List[str] = []
        for selector in test_ids:
            rel = selector.split("::", 1)[0]
            if (self.root / rel).exists():
                runnable.append(selector)
            else:
                # An absent selector must not poison the batch: pytest aborts the
                # whole invocation on "file or directory not found".
                results.append(
                    TestOutcome(test_id=selector, outcome="not_run",
                                detail=f"selector path missing: {rel}")
                )
        batches: Dict[str, List[str]] = {}
        for selector in runnable:
            batches.setdefault(self.batch_key(selector), []).append(selector)
        for key in sorted(batches):
            results.extend(self._run_batch(batches[key]))
        return results

    def _run_batch(self, test_ids: Sequence[str]) -> List[TestOutcome]:
        self.invocations += 1
        with tempfile.TemporaryDirectory(prefix="rguard-") as tmp:
            xml_path = Path(tmp) / "junit.xml"
            cmd = [self.python, "-m", "pytest", *self.args, f"--junitxml={xml_path}", *test_ids]
            env = dict(os.environ)
            env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(self.root) + (os.pathsep + existing if existing else "")
            started = time.time()
            proc = subprocess.run(
                cmd, cwd=str(self.root), capture_output=True, text=True,
                timeout=self.timeout, env=env,
            )
            elapsed = time.time() - started
            outcomes = self._parse(xml_path, test_ids, elapsed)
            if proc.returncode not in (0, 1) and outcomes and all(o.outcome == "not_run" for o in outcomes):
                tail = (proc.stderr or proc.stdout or "").strip()[-800:]
                for o in outcomes:
                    o.detail = tail
            return outcomes

    def _parse(self, xml_path: Path, requested: Sequence[str], elapsed: float) -> List[TestOutcome]:
        if not xml_path.exists():
            return [
                TestOutcome(test_id=t, outcome="not_run", duration_s=0.0, detail="no junit xml produced")
                for t in requested
            ]
        requested_files = {req.split("::", 1)[0] for req in requested}
        dotted_to_file = {f.replace("/", ".")[:-3]: f for f in requested_files}
        tree = ET.parse(xml_path)
        by_file: Dict[str, List[TestOutcome]] = {}
        for case in tree.iter("testcase"):
            classname = case.get("classname") or ""
            name = case.get("name") or ""
            parts = classname.split(".") if classname else []
            target, consumed = "", 0
            for cut in range(len(parts), 0, -1):          # longest module prefix wins
                hit = dotted_to_file.get(".".join(parts[:cut]))
                if hit:
                    target, consumed = hit, cut
                    break
            if not target:
                # Directory selectors, and roots whose pytest put a parent dir on
                # sys.path (e.g. backend/tests -> classname "tests.test_x"): try
                # the usual source roots before giving up.
                for cut in range(len(parts), 0, -1):
                    stem = "/".join(parts[:cut]) + ".py"
                    for prefix in ("", "backend/", "src/", "tools/"):
                        if (self.root / (prefix + stem)).exists():
                            target, consumed = prefix + stem, cut
                            break
                    if target:
                        break
            if not target and parts:
                target = classname.replace(".", "/") + ".py"
                consumed = len(parts)
            remainder = parts[consumed:] + ([name] if name else [])
            node = f"{target}::" + "::".join(remainder) if remainder else target
            status, detail = "passed", ""
            for child in case:
                if child.tag in ("failure", "error", "skipped"):
                    status = {"failure": "failed", "error": "error", "skipped": "skipped"}[child.tag]
                    detail = ((child.get("message") or "") + " " + (child.text or ""))[:800]
                    break
            by_file.setdefault(target, []).append(
                TestOutcome(test_id=node, outcome=_norm_outcome(status),
                            duration_s=float(case.get("time") or 0.0), detail=detail.strip())
            )

        results: List[TestOutcome] = []
        for req in requested:
            req_file = req.split("::", 1)[0]
            matched = by_file.get(req_file, [])
            if not matched and "::" not in req:          # directory selector
                matched = [o for f, outs in sorted(by_file.items())
                           if f.startswith(req_file.rstrip("/") + "/") for o in outs]
            if "::" in req:
                exact = [o for o in matched if o.test_id.split("::")[-1] == req.split("::", 1)[1]]
                results.extend(exact or [TestOutcome(req, "not_run", detail="not collected")])
            elif matched:
                results.extend(matched)
            else:
                results.append(TestOutcome(req, "not_run", detail="not collected"))
        if not results:
            return [TestOutcome(t, "not_run", elapsed) for t in requested]
        return results


class FakeRunner:
    """Deterministic runner for self-tests: outcomes come from a mapping.

    ``script`` maps a selector -> list of outcomes consumed in order (so a test
    can be "passed" in the baseline run and "failed" after a change).  Records
    every invocation so criteria like "cached tests are not re-executed" are
    directly assertable.
    """

    def __init__(self, script: Optional[Dict[str, List[str]]] = None, discover=None) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: List[List[str]] = []
        self.discover = discover  # optional callable(selector) -> list of test ids
        self.invocations = 0

    def run(self, test_ids: Sequence[str]) -> List[TestOutcome]:
        self.invocations += 1
        self.calls.append(list(test_ids))
        out: List[TestOutcome] = []
        for tid in test_ids:
            expanded = self.discover(tid) if self.discover else [tid]
            for one in expanded:
                queue = self.script.get(one) or self.script.get(tid)
                if queue:
                    status = queue.pop(0) if len(queue) > 1 else queue[0]
                else:
                    status = "passed"
                out.append(TestOutcome(test_id=one, outcome=_norm_outcome(status)))
        return out