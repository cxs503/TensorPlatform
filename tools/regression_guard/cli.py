"""CLI for the cross-mission regression guard.

Usage::

    python -m tools.regression_guard.cli scan   --root . --out artifacts/regression_guard/repo_graph.json
    python -m tools.regression_guard.cli plan   --ledger L.json --changes C.json [--graph G.json]
    python -m tools.regression_guard.cli run    --ledger L.json --changes C.json --report R.json \
                                                [--cache-dir D] [--baseline B.json] [--update-baseline]
    python -m tools.regression_guard.cli from-git --ledger L.json --rev-range HEAD~1..HEAD [...]

Exit code is the guard verdict (0 clean / nothing to do, 1 regression or failure, 2 error).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .guard import GuardConfig, RegressionGuard, write_report
from .impact import changes_from_git
from .model import ChangeManifest, TaskLedger
from .scanner import RepoGraph


def _load_graph(path: Optional[str]) -> Optional[RepoGraph]:
    if not path:
        return None
    return RepoGraph.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _build_guard(args) -> RegressionGuard:
    ledger = TaskLedger.load(args.ledger)
    problems = ledger.validate()
    if problems:
        raise SystemExit("ledger invalid:\n  " + "\n  ".join(problems))
    graph = _load_graph(getattr(args, "graph", None))
    if graph is None and not getattr(args, "no_scan", False):
        subdirs = tuple(args.subdirs.split(",")) if getattr(args, "subdirs", "") else ()
        graph = RepoGraph.build(args.root, subdirs)
    config = GuardConfig(
        root=args.root,
        ledger=ledger,
        graph=graph,
        test_roots=tuple(t for t in (args.test_roots or "").split(",") if t),
        include_import_closure=not getattr(args, "no_import_closure", False),
        cache_dir=getattr(args, "cache_dir", None),
        baseline_path=getattr(args, "baseline", None),
        timeout=getattr(args, "timeout", 1800),
    )
    return RegressionGuard(config)


def _print_summary(report: Dict[str, Any]) -> None:
    verdict = report["verdict"]
    sel = report["selection"]
    ex = report["execution"]
    print(
        f"[regression-guard] verdict={verdict['status']} exit={verdict['exit_code']} "
        f"selected={sel['count']} executed={ex['executed']} cached={ex['cached']} "
        f"tasks={report['metrics']['affected_tasks']} "
        f"regressions={len(report['deltas']['regressions'])}",
        file=sys.stderr,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="regression-guard", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    common.add_argument("--ledger", required=True)
    common.add_argument("--graph", default=None)
    common.add_argument("--subdirs", default="")
    common.add_argument("--test-roots", default="tests,backend/tests")
    common.add_argument("--no-import-closure", action="store_true")
    common.add_argument("--timeout", type=int, default=1800)

    p_scan = sub.add_parser("scan", parents=[common], help="build the static import graph")
    p_scan.add_argument("--out", default="artifacts/regression_guard/repo_graph.json")

    p_plan = sub.add_parser("plan", parents=[common], help="impact analysis only")
    p_plan.add_argument("--changes", required=True)
    p_plan.add_argument("--out", default=None)

    p_run = sub.add_parser("run", parents=[common], help="impact + execute + compare")
    p_run.add_argument("--changes", required=True)
    p_run.add_argument("--report", default="artifacts/regression_guard/report.json")
    p_run.add_argument("--cache-dir", default=None)
    p_run.add_argument("--baseline", default=None)
    p_run.add_argument("--update-baseline", action="store_true")

    p_git = sub.add_parser("from-git", parents=[common], help="derive manifest from git diff")
    p_git.add_argument("--rev-range", default="HEAD~1..HEAD")
    p_git.add_argument("--report", default="artifacts/regression_guard/report.json")
    p_git.add_argument("--cache-dir", default=None)
    p_git.add_argument("--baseline", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "scan":
        subdirs = tuple(args.subdirs.split(",")) if args.subdirs else ()
        graph = RepoGraph.build(args.root, subdirs)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"scanned {len(graph.files)} modules -> {out} (digest {graph.digest[:12]})")
        return 0

    if args.cmd == "from-git":
        changes, base, head = changes_from_git(args.root, args.rev_range)
        manifest = ChangeManifest(changes=changes, base_rev=base, head_rev=head)
    else:
        raw = json.loads(Path(args.changes).read_text(encoding="utf-8"))
        manifest = ChangeManifest.from_dict(raw) if isinstance(raw, dict) else ChangeManifest(
            changes=[], base_rev="", head_rev=""
        )

    try:
        guard = _build_guard(args)
    except SystemExit as exc:  # ledger invalid
        print(str(exc), file=sys.stderr)
        return 2

    if args.cmd == "plan":
        impact = guard.analyzer.analyze(manifest)
        payload = {"manifest": manifest.to_dict(), "impact": impact.to_dict()}
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    report = guard.run(manifest, update_baseline=getattr(args, "update_baseline", False))
    out = Path(args.report)
    write_report(report, out)
    _print_summary(report)
    print(json.dumps({"report": str(out), "digest": report["report_digest"]}, ensure_ascii=False))
    return int(report["verdict"]["exit_code"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())