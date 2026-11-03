#!/usr/bin/env python3
"""Red ratchet: the failing-test baseline may only shrink.

R6 of the TensorPlatform refactor. ``main`` carries a large inherited red
tail (~300 in backend/tests alone, triaged in PR #581); until it is burned
down, "CI must be green" is not actionable. What IS actionable is a ratchet:

  - ``measure``  run the suites, write the failing-node baseline JSON.
  - ``check``    run the suites again; any failure NOT in the baseline fails
                 the gate. Failures that disappeared are reported as fixed.

Re-baselining policy: ``measure`` may only be run to SHRINK the baseline
(after fixes land). If it grows, the PR that grew it must fix its own reds
first — the check gate enforces this mechanically since CI runs ``check``
against the committed baseline.

Usage:
    python tools/ops/red_ratchet.py measure [--suite backend/tests] [...]
    python tools/ops/red_ratchet.py check   [--suite backend/tests] [...]

Multiple ``--suite`` arguments are measured as separate pytest invocations
(the repository has two disjoint test trees; running them in one pytest
process breaks collection, see PR history).

Collection errors are continued past (``--continue-on-collection-errors``)
so a few unimportable legacy files cannot mask the whole suite -- they are
ratcheted as file-level ERROR entries like any other red.

Output format of the baseline JSON is intentionally boring (sorted node ids)
so that baseline diffs in PRs are reviewable line by line.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPO / "tools" / "ops" / "red_baseline.json"
# Default gate scope = what CI actually runs (the "backend suite" job).
# The repo-root tests/ tree (heavy ship-physics integration, 40+ min single
# run) is NOT part of CI; measure/watch it explicitly with --suite tests.
DEFAULT_SUITES = ("backend/tests",)
SUITE_TIMEOUT_S = 3600

_SUMMARY = re.compile(r"^(FAILED|ERROR) (\S+)")


def _run_suite(suite: str) -> tuple[set[str], str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "--tb=no",
             "-p", "no:warnings", "--continue-on-collection-errors"],
            cwd=REPO, capture_output=True, text=True, timeout=SUITE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"[ratchet] suite {suite} exceeded {SUITE_TIMEOUT_S}s -- a gate "
            f"that can hang is not a gate; split the suite or fix the hang")
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        m = _SUMMARY.match(line)
        if m:
            found.add(m.group(2))
    return found, proc.stdout


def measure(suites: list[str], baseline_path: Path) -> int:
    doc: dict = {"generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                 "suites": {}}
    total = 0
    for suite in suites:
        fails, out = _run_suite(suite)
        doc["suites"][suite] = sorted(fails)
        total += len(fails)
        print(f"[measure] {suite}: {len(fails)} red")
    doc["total_red"] = total
    old = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if old and total > old.get("total_red", 0):
        print(f"[measure] REFUSING to write a larger baseline "
              f"({total} > {old['total_red']}); the new reds must be fixed, "
              f"not adopted. Override by deleting the baseline file.")
        return 1
    baseline_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"[measure] baseline -> {baseline_path} (total_red={total})")
    return 0


def check(suites: list[str], baseline_path: Path) -> int:
    if not baseline_path.exists():
        print("[check] no baseline; run measure first")
        return 2
    base = json.loads(baseline_path.read_text())
    rc = 0
    for suite in suites:
        known = set(base["suites"].get(suite, []))
        current, _ = _run_suite(suite)
        fresh = current - known
        fixed = known - current
        if fresh:
            rc = 1
            print(f"[check] {suite}: {len(fresh)} NEW RED (gate fails):")
            for f in sorted(fresh):
                print("  +", f)
        print(f"[check] {suite}: {len(current)} red ({len(fixed)} fixed since baseline)")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("measure", "check"))
    ap.add_argument("--suite", action="append", default=None,
                    help="test path relative to repo root (repeatable)")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = ap.parse_args()
    suites = args.suite or list(DEFAULT_SUITES)
    return (measure if args.mode == "measure" else check)(suites, args.baseline)


if __name__ == "__main__":
    raise SystemExit(main())
