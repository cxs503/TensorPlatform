#!/usr/bin/env python3
"""Command line interface of the verification harness.

Usage::

    # execute a case package, write report + evidence bundle
    python3 -m tools.verification_harness run \
        --cases tools/verification_harness/cases \
        --out artifacts/verification_harness

    # re-hash an existing bundle (tamper audit)
    python3 -m tools.verification_harness verify --bundle artifacts/verification_harness

Exit codes: ``0`` all cases PASS and bundle intact, ``1`` a case FAILED/ERRORed
or a bundle is inconsistent, ``2`` usage/registry error.

The CLI is the only place that touches the filesystem layout; the mechanism
modules stay importable as a library (see ``tests/test_verification_harness_*``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .evidence import DEFAULT_DEPENDENCY_PROBE, verify_bundle, verification_markdown, write_bundle
from .registry import CaseRegistry, RegistryError
from .runner import run_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m tools.verification_harness",
                                     description="Domain-neutral verification harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="execute a case package and archive evidence")
    run.add_argument("--cases", required=True, help="directory of *.json case declarations")
    run.add_argument("--out", required=True, help="evidence bundle output directory")
    run.add_argument("--dependencies", default=",".join(DEFAULT_DEPENDENCY_PROBE),
                     help="comma separated package names recorded in the environment fingerprint")
    run.add_argument("--stamp", action="store_true", help="embed a wall-clock timestamp (breaks byte equality)")

    verify = sub.add_parser("verify", help="recompute digests of an evidence bundle")
    verify.add_argument("--bundle", required=True, help="bundle directory written by 'run'")
    return parser


def _run(args: argparse.Namespace) -> int:
    registry = CaseRegistry()
    try:
        registry.discover(Path(args.cases))
    except RegistryError as exc:
        print("registry error:\n  " + "\n  ".join(exc.reasons), file=sys.stderr)
        return 2
    report = run_registry(registry, stamp=args.stamp)
    dependencies = [name.strip() for name in args.dependencies.split(",") if name.strip()]
    manifest = write_bundle(registry, report, args.out, dependencies=dependencies, stamp=args.stamp)
    counts = report.counts
    print(json.dumps({
        "cases": counts["total"],
        "PASS": counts["PASS"],
        "FAIL": counts["FAIL"],
        "ERROR": counts["ERROR"],
        "quantities": counts["quantities"],
        "bundle": str(Path(args.out).resolve()),
        "environment_digest": manifest["environment_digest"],
        "root_digest": manifest["root_digest"],
    }, sort_keys=True))
    return 0 if report.ok else 1


def _verify(args: argparse.Namespace) -> int:
    verdict = verify_bundle(Path(args.bundle))
    print(verification_markdown(verdict))
    return 0 if verdict.ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    return _verify(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())