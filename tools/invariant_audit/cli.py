#!/usr/bin/env python3
"""CLI: ``python3 -m tools.invariant_audit run|verify`` (domain-neutral)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .bundle import verify_bundle, write_bundle
from .engine import ledger_markdown, load_plan_file, run_audit
from .model import round_floats


def _cmd_run(args: argparse.Namespace) -> int:
    plan = load_plan_file(args.plan)
    report = run_audit(plan)
    out_dir = Path(args.out)
    manifest = write_bundle(report, plan, out_dir)
    if not args.quiet:
        sys.stdout.write(ledger_markdown(report))
        sys.stdout.write(f"\nledger root_digest: {manifest['root_digest']}\n")
        sys.stdout.write(f"bundle: {out_dir}\n")
    print(json.dumps(round_floats({
        "plan_id": report.plan_id,
        "counts": report.counts(),
        "pair_counts": report.pair_counts(),
        "bundle": str(out_dir),
        "root_digest": manifest["root_digest"],
    }), sort_keys=True))
    if args.fail_on_fail and report.pair_counts().get("FAIL", 0):
        return 1
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    verdict = verify_bundle(args.bundle)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.invariant_audit",
        description="Cross-solver physical-invariant audit (domain-neutral).")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="execute a plan and freeze an evidence bundle")
    run.add_argument("--plan", required=True, help="path to an invariant-audit/1.0 plan")
    run.add_argument("--out", required=True, help="output bundle directory")
    run.add_argument("--quiet", action="store_true", help="suppress the markdown ledger")
    run.add_argument("--fail-on-fail", action="store_true",
                     help="exit 1 when any pair FAILs (CI gate on a clean matrix)")
    run.set_defaults(func=_cmd_run)

    verify = sub.add_parser("verify", help="re-hash a bundle and classify tampering")
    verify.add_argument("--bundle", required=True)
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())