"""CLI helper: export history, run the backtest, audit domain freedom.

Usage (from the repository root)::

    python -m tools.dispatch_readiness.cli export  --snapshot artifacts/dispatch_readiness/history_snapshot.json
    python -m tools.dispatch_readiness.cli backtest --snapshot ... --report artifacts/dispatch_readiness/backtest_report.json
    python -m tools.dispatch_readiness.cli audit --root backend/tools/dispatch_readiness
    python -m tools.dispatch_readiness.cli check --snapshot artifacts/dispatch_readiness/history_snapshot.json

``check`` is the CI-friendly entry point: it runs the backtest and returns exit
code 1 when a frozen acceptance threshold is violated.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _default(*parts):
    return os.path.join(REPO_ROOT, *parts)

from .audit_zero_domain import audit
from .backtest import run_backtest, write_report
from .history_export import export_history


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dispatch_readiness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export")
    p_export.add_argument("--snapshot", default=_default("artifacts", "dispatch_readiness", "history_snapshot.json"))

    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--snapshot", default=_default("artifacts", "dispatch_readiness", "history_snapshot.json"))
    p_bt.add_argument("--report", default=_default("artifacts", "dispatch_readiness", "backtest_report.json"))
    p_bt.add_argument("--iterations", type=int, default=200)
    p_bt.add_argument("--seed", type=int, default=20261110)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--root", action="append", default=None)

    p_check = sub.add_parser("check")
    p_check.add_argument("--snapshot", default=_default("artifacts", "dispatch_readiness", "history_snapshot.json"))
    p_check.add_argument("--iterations", type=int, default=200)

    args = parser.parse_args(argv)

    if args.command == "export":
        snapshot = export_history(args.snapshot)
        print(json.dumps(snapshot["counts"], ensure_ascii=False))
        return 0

    if args.command == "backtest":
        body = run_backtest(_load(args.snapshot), permutation_iterations=args.iterations, seed=args.seed)
        write_report(body, args.report)
        print(json.dumps(body["ac"], ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit":
        result = audit(args.root or [
            _default("backend", "tools", "dispatch_readiness"),
            _default("backend", "tests", "test_dispatch_readiness.py"),
            _default("backend", "tests", "test_dispatch_readiness_zero_domain.py"),
        ])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("clean", "degraded_identity") else 1

    if args.command == "check":
        body = run_backtest(_load(args.snapshot), permutation_iterations=args.iterations)
        failed = [name for name, ac in body["ac"].items() if not ac.get("passed")]
        print(json.dumps({"failed": failed, "ac": body["ac"]}, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())