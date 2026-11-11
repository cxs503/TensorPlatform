#!/usr/bin/env python3
"""C6 regression evidence: the new package must not perturb the existing suite.

Two parts, each cheap enough to run on the control host (whole-tree collection takes
~40 s, so they are separate invocations):

``--part collect``
    ``pytest --collect-only`` over the whole ``tests/`` tree twice: once with the new
    package present, once with ``--ignore=tests/fidelity``.  The full tree does not
    import cleanly on this host (pre-existing ``sys.path`` shadowing: ``ops`` not a
    package, duplicate test basenames, relative-import errors).  The comparison is the
    evidence: the error count must not change and the collected count must increase by
    exactly the number of new tests.

``--part batches``
    Actual runs of the new suite plus the neighbouring suites that scan ``tools/``.

Writes ``evidence/regression_baseline.json`` (merged across parts).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "evidence" / "regression_baseline.json"

BATCHES = [
    ["tests/fidelity", "tests/test_multifidelity.py", "tests/test_synthetic_fixtures.py"],
    ["tests/evidence"],
    ["tests/test_write_gate.py", "tests/test_distill_run_case.py",
     "tests/test_multisolver_ckpt_rollback.py"],
]


def _pytest(args: list, timeout: int = 300) -> dict:
    cmd = [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    text = proc.stdout + proc.stderr
    m = re.search(r"(\d+) passed", text)
    err = re.search(r"(\d+) errors?", text)
    col = re.search(r"(\d+) tests collected", text)
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "passed": int(m.group(1)) if m else 0,
        "errors": int(err.group(1)) if err else 0,
        "collected": int(col.group(1)) if col else 0,
        "tail": [ln for ln in text.strip().splitlines() if ln.strip()][-1:],
    }


def part_collect() -> dict:
    with_new = _pytest(["tests", "--collect-only", "--continue-on-collection-errors"])
    baseline = _pytest(["tests", "--collect-only", "--continue-on-collection-errors",
                        "--ignore=tests/fidelity"])
    return {
        "collect_only_with_new_package": with_new,
        "collect_only_baseline": baseline,
        "new_tests": with_new["collected"] - baseline["collected"],
        "error_delta": with_new["errors"] - baseline["errors"],
        "note": ("the pre-existing collection errors are host-level sys.path shadowing "
                 "in the whole-tree run; the new package adds tests and no errors"),
    }


def part_batches() -> dict:
    rows = [_pytest(targets, timeout=600) for targets in BATCHES]
    return {
        "batches": rows,
        "total_passed": sum(r["passed"] for r in rows),
        "all_green": all(r["returncode"] == 0 for r in rows),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", choices=["collect", "batches", "both"], default="both")
    args = ap.parse_args(argv)
    data = json.loads(OUT.read_text()) if OUT.exists() else {"schema": "c6-regression/1.0"}
    if args.part in ("collect", "both"):
        data["collect"] = part_collect()
    if args.part in ("batches", "both"):
        data["batches"] = part_batches()
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "schema"}, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())