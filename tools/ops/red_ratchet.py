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
    python tools/ops/red_ratchet.py check-from-log  --log pytest.log [--suite backend/tests]
    python tools/ops/red_ratchet.py measure-from-log --log pytest.log [--suite backend/tests] \
        [--recompute --reason "..."]

``check-from-log`` compares a *captured* pytest ``-q`` run (the CI job tees its
output) against the committed baseline instead of re-running the suite — one
run, one bill.  It also refuses a baseline that GREW in the current checkout
relative to the merge base (``RED_RATCHET_BASELINE_MAIN`` env path, else
``git show origin/main:...``/``FETCH_HEAD:...``): adopting new reds by editing
the baseline file is the ratchet's own bypass, so the gate has to catch it.
``measure-from-log`` re-baselines from a captured log; growth requires an
explicit ``--recompute --reason`` that lands in the JSON (auditable).

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
import os
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


# ---------------------------------------------------------------------------
# Log-based modes (single CI run, no re-execution)
# ---------------------------------------------------------------------------

# CI 的 backend-suite 从 backend/ 目录用 ``tests/`` 相对路径跑 pytest；
# 基线节点号是仓库根相对（measure 从根跑）。把日志侧归一到根相对再比。
_LOG_PREFIX_MAP = {"backend/tests": ("tests/", "backend/tests/")}

_CI_LINE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z?\s*")

# A completed pytest run always prints a terminal stats line.  Two shapes:
# verbose ``==== 2 failed, 1 passed in 3.5s ====`` and quiet ``2 failed, 1
# passed in 3.5s`` (no ``=`` border — pytest -q, which CI uses).  Without
# this guard an empty/truncated log parses as "0 red — everything fixed"
# and the gate would pass on a run that never happened (a green lie).
_PYTEST_BANNER = re.compile(
    r"=+ .*in [0-9.]+s"
    r"|\d+ (?:failed|passed|error|skipped|xfailed|xpassed|warnings?)\b[^\n]*"
    r"in [0-9.]+s"
    r"|no tests ran",
)
_NO_TESTS = "no tests ran"


def parse_log(log_text: str, rc: int | None = None) -> set[str]:
    """Failing node ids from a captured pytest ``-q`` summary (log lines may
    carry a CI timestamp prefix).

    ``rc`` is pytest's own exit code when the caller captured it (CI tees the
    run and writes ``PIPESTATUS[0]`` next to the log): 0/1 = a completed run,
    anything else = config error or crash.  The code matters because the
    Actions runner sometimes drops the FINAL line of a step's stdout — exactly
    the quiet stats line this guard keys on (observed on PR #597 stage 1:
    full summary section present, stats line absent from the captured log
    while the tee'd file carried it).

    Raises ValueError when the log is not a completed pytest run — callers
    turn that into rc=2 (config/infrastructure error, distinct from the
    gate's rc=1 "new red" verdict).
    """
    if rc is None:
        # No exit-code evidence: the terminal stats line is the only
        # completion proof we have.
        if not _PYTEST_BANNER.search(log_text):
            raise ValueError(
                "log does not contain a pytest terminal summary — the run "
                "crashed or the log is empty; a gate cannot judge a run that "
                "never happened")
    else:
        if rc not in (0, 1):
            raise ValueError(
                f"pytest exited {rc} (config error/crash) — not a completed "
                f"run; a gate cannot judge a run that never finished")
        if not log_text.strip():
            raise ValueError(
                "empty log despite a captured exit code — output capture "
                "broke; refusing to judge")
    if _NO_TESTS in log_text and "failed" not in log_text and "error" not in log_text:
        raise ValueError(
            "log reports 'no tests ran' — collection death is not a green "
            "state")
    found: set[str] = set()
    for line in log_text.splitlines():
        m = _SUMMARY.match(_CI_LINE_PREFIX.sub("", line))
        if m:
            found.add(m.group(2))
    if rc == 1 and not found:
        raise ValueError(
            "exit code 1 but no FAILED/ERROR summary lines captured — the "
            "log is truncated; refusing to judge")
    return found


def _read_rc(rc_path: str | None) -> int | None:
    """Pytest's exit code from the file CI writes next to the log.

    Absent/unparseable file -> None (fall back to the terminal-stats-line
    check; legacy wiring must keep working).
    """
    if rc_path is None:
        return None
    p = Path(rc_path)
    if not p.exists():
        print(f"[ratchet] rc file {rc_path} not found — falling back to the "
              f"terminal-stats-line check")
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        print(f"[ratchet] rc file {rc_path} is not an integer — ignoring")
        return None


def _normalize(nodes: set[str], suite: str) -> set[str]:
    pair = _LOG_PREFIX_MAP.get(suite)
    if not pair:
        return set(nodes)
    src_pfx, dst_pfx = pair
    return {dst_pfx + n[len(src_pfx):] if n.startswith(src_pfx) else n for n in nodes}


def _baseline_nodes(doc: dict, suite: str) -> set[str]:
    return set(doc.get("suites", {}).get(suite, []))


def _main_baseline_nodes(baseline_path: Path) -> tuple[set[str] | None, str]:
    """Node set of the baseline on the merge base, best-effort.

    Order: env override path (tests / explicit CI wiring) -> ``git show
    origin/main`` -> ``git show FETCH_HEAD`` -> None (unavailable; the growth
    guard degrades to a warning, the new-red check never degrades).
    """
    env = os.environ.get("RED_RATCHET_BASELINE_MAIN")
    if env:
        p = Path(env)
        if p.exists():
            rel = json.loads(p.read_text())
            return {n for s in rel.get("suites", {}).values() for n in s}, env
        return None, f"{env} (missing)"
    import subprocess as _sp
    for ref in ("origin/main", "FETCH_HEAD"):
        try:
            out = _sp.run(["git", "show", f"{ref}:{baseline_path}"],
                          capture_output=True, text=True, timeout=30, cwd=REPO)
        except Exception:
            continue
        if out.returncode == 0 and out.stdout.strip():
            try:
                rel = json.loads(out.stdout)
            except ValueError:
                continue
            return {n for s in rel.get("suites", {}).values() for n in s}, ref
    return None, "unavailable"


def check_from_log(suites: list[str], log_paths: list[str], baseline_path: Path,
                   rc_path: str | None = None) -> int:
    if not baseline_path.exists():
        print("[check-from-log] no baseline; run measure-from-log first")
        return 2
    base = json.loads(baseline_path.read_text())
    rc = 0
    pytest_rc = _read_rc(rc_path)
    for suite, log_path in zip(suites, log_paths):
        log = Path(log_path)
        if not log.exists():
            print(f"[check-from-log] {log_path}: log not found")
            return 2
        try:
            current = _normalize(
                parse_log(log.read_text(errors="replace"), pytest_rc), suite)
        except ValueError as e:
            print(f"[check-from-log] {log_path}: {e}")
            return 2
        known = _baseline_nodes(base, suite)
        fresh = current - known
        fixed = known - current
        if fresh:
            rc = 1
            print(f"[check-from-log] {suite}: {len(fresh)} NEW RED (gate fails):")
            for f in sorted(fresh):
                print("  +", f)
        print(f"[check-from-log] {suite}: {len(current)} red "
              f"({len(fixed)} fixed since baseline)")
    # 基线私改守卫：本 PR 的基线只许缩不许涨（对 merge base）
    main_nodes, src = _main_baseline_nodes(baseline_path)
    if main_nodes is None:
        print(f"[check-from-log] growth guard SKIPPED (main baseline {src}) "
              f"— new-red check unaffected")
    else:
        here = {n for s in base.get("suites", {}).values() for n in s}
        grown = here - main_nodes
        if grown:
            rc = 1
            print(f"[check-from-log] baseline GREW vs {src} by {len(grown)} "
                  f"(adopt-by-edit forbidden; fix the reds):")
            for g in sorted(grown):
                print("  +", g)
    return rc


def measure_from_log(suites: list[str], log_paths: list[str], baseline_path: Path,
                     recompute: bool, reason: str | None,
                     rc_path: str | None = None) -> int:
    doc: dict = {"generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                 "suites": {}}
    total = 0
    pytest_rc = _read_rc(rc_path)
    for suite, log_path in zip(suites, log_paths):
        log = Path(log_path)
        if not log.exists():
            print(f"[measure-from-log] {log_path}: log not found")
            return 2
        try:
            nodes = sorted(_normalize(
                parse_log(log.read_text(errors="replace"), pytest_rc), suite))
        except ValueError as e:
            print(f"[measure-from-log] {log_path}: {e}")
            return 2
        doc["suites"][suite] = nodes
        total += len(nodes)
        print(f"[measure-from-log] {suite}: {len(nodes)} red (from log)")
    doc["total_red"] = total
    if baseline_path.exists():
        old = json.loads(baseline_path.read_text())
        old_total = old.get("total_red", 0)
        if total > old_total:
            if not recompute:
                print(f"[measure-from-log] REFUSING to write a larger baseline "
                      f"({total} > {old_total}); use --recompute --reason if this "
                      f"is an audited re-baseline (e.g. suite revived after "
                      f"collection death), otherwise fix the new reds.")
                return 1
            if not (reason and reason.strip()):
                print("[measure-from-log] --recompute requires --reason "
                      "(recorded in the JSON, auditable)")
                return 1
            doc["recomputed_reason"] = reason.strip()
            print(f"[measure-from-log] RECOMPUTE {old_total} -> {total} "
                  f"(reason recorded)")
    baseline_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"[measure-from-log] baseline -> {baseline_path} (total_red={total})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("measure", "check",
                                     "check-from-log", "measure-from-log"))
    ap.add_argument("--suite", action="append", default=None,
                    help="test path relative to repo root (repeatable)")
    ap.add_argument("--log", action="append", default=None,
                    help="captured pytest -q log (repeatable, pairs with --suite)")
    ap.add_argument("--recompute", action="store_true",
                    help="measure-from-log: allow growth with a recorded reason")
    ap.add_argument("--reason", default=None,
                    help="measure-from-log: why this re-baseline is audited")
    ap.add_argument("--rc-file", default=None,
                    help="file holding pytest's exit code (CI: PIPESTATUS[0]); "
                         "valid with exactly one --log, authoritative "
                         "completion evidence — the runner may drop the final "
                         "quiet stats line from the captured stdout")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = ap.parse_args()
    suites = args.suite or list(DEFAULT_SUITES)
    if args.mode in ("measure", "check"):
        return (measure if args.mode == "measure" else check)(suites, args.baseline)
    logs = args.log or []
    if not logs:
        ap.error(f"{args.mode} requires --log")
    # --suite 可省：默认 backend/tests 与单日志配对
    if len(logs) == 1 and len(suites) > 1:
        suites = suites[:1]
    if len(suites) != len(logs):
        ap.error("--suite and --log must pair one-to-one")
    if args.rc_file and len(logs) != 1:
        ap.error("--rc-file is only valid with exactly one --log")
    if args.mode == "check-from-log":
        return check_from_log(suites, logs, args.baseline, args.rc_file)
    return measure_from_log(suites, logs, args.baseline, args.recompute,
                            args.reason, args.rc_file)


if __name__ == "__main__":
    raise SystemExit(main())
