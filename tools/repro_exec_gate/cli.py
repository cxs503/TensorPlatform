#!/usr/bin/env python3
"""CLI for the reproducibility execution gate.

Frozen interface::

    python3 -m tools.repro_exec_gate --contract CONTRACT.json --worktree DIR \
        [--timeout SECONDS] [--json-out PATH]

Exit codes: 0 = PASS, 1 = FAIL, 2 = usage error (missing argument, contract
absent or not valid JSON, structurally invalid contract).
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import core, report

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

USAGE_REASON = core.USAGE_REASON


class _Parser(argparse.ArgumentParser):
    """argparse that reports usage errors as one JSON line on stdout."""

    def error(self, message: str):  # noqa: D401 - argparse hook
        _emit_usage_error(message)
        raise SystemExit(EXIT_USAGE)


def _emit_usage_error(message: str) -> None:
    sys.stdout.write(report.render_stdout(
        core.make_verdict(USAGE_REASON, log_tail=message,
                          status=core.STATUS_FAIL)) + "\n")
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python3 -m tools.repro_exec_gate",
        description="Execute a delivery contract's repro_cmd in a worktree and judge it.")
    parser.add_argument("--contract", required=True,
                        help="path to the delivery contract JSON")
    parser.add_argument("--worktree", required=True,
                        help="directory used as cwd for repro_cmd (and artifact root)")
    parser.add_argument("--timeout", type=float, default=None,
                        help=f"hard timeout in seconds (default {core.DEFAULT_TIMEOUT_S:g})")
    parser.add_argument("--json-out", default=None,
                        help="optional path for a sealed report artifact")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.timeout is not None and not (args.timeout > 0):
        _emit_usage_error(f"--timeout must be > 0, got {args.timeout!r}")
        return EXIT_USAGE

    try:
        contract = core.load_contract(args.contract)
        spec = core.normalize_contract(contract)
    except core.ContractInvalid as exc:
        _emit_usage_error(str(exc))
        return EXIT_USAGE

    try:
        verdict, run, details = core.run_and_judge(spec, args.worktree, timeout_s=args.timeout)
    except core.ContractInvalid as exc:  # timeout from the contract itself
        _emit_usage_error(str(exc))
        return EXIT_USAGE

    sys.stdout.write(report.render_stdout(verdict) + "\n")
    sys.stdout.flush()

    if args.json_out:
        _persist(args, spec, verdict, run, details)

    return EXIT_PASS if verdict["status"] == core.STATUS_PASS else EXIT_FAIL


def _persist(args, spec: dict, verdict: dict, run: core.ExecResult | None,
             details: list[dict]) -> None:
    """Write the optional sealed artifact; never changes the verdict/exit code."""
    body = report.build_artifact(
        verdict, contract_path=args.contract, worktree=args.worktree,
        repro_cmd=spec["repro_cmd"], artifact_details=details,
        log_sha256=getattr(run, "log_sha256", "") or "",
        log_bytes=getattr(run, "log_bytes", 0) or 0,
        timeout_s=args.timeout if args.timeout is not None else spec["timeout_s"],
        command=["python3", "-m", "tools.repro_exec_gate",
                 "--contract", args.contract, "--worktree", args.worktree])
    try:
        report.write_json_out(args.json_out, report.sealed(body))
    except OSError as exc:  # keep the verdict authoritative
        sys.stderr.write(f"warning: could not write --json-out {args.json_out}: {exc}\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
