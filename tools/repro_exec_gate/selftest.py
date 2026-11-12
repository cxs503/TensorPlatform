#!/usr/bin/env python3
"""Self-check: run the gate against **this repository's own** reproducibility command.

A gate that has never been pointed at its own delivery is a claim, not a capability.
This module builds a contract whose ``repro_cmd`` is the acceptance test of the
gate itself, executes it through the same code path a delivery would use, and
persists a sealed artifact (verdict + log digest + declared artifact digest).

Usage::

    python3 -m tools.repro_exec_gate.selftest [--worktree DIR] [--timeout S]
    # writes artifacts/repro_exec_gate/selftest_contract.json and selftest.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, report

SELFTEST_SUITE = "tests/test_repro_exec_gate.py"
DEFAULT_OUT_DIR = "artifacts/repro_exec_gate"


def build_contract(worktree: Path) -> dict:
    """Contract used for the self-check (declares the suite it will execute)."""
    suite = worktree / SELFTEST_SUITE
    digest = core.sha256_file(suite) if suite.is_file() else None
    artifacts = [{"path": SELFTEST_SUITE, "sha256": digest}] if digest else []
    return {
        "schema_version": core.SCHEMA_VERSION,
        "purpose": "self-check: the gate executes its own acceptance suite",
        "repro_cmd": f"{sys.executable} -m pytest -q {SELFTEST_SUITE}",
        "artifacts": artifacts,
        "timeout_s": 600,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tools.repro_exec_gate.selftest",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--worktree", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--out-dir", default=None,
                        help=f"artifact directory (default <worktree>/{DEFAULT_OUT_DIR})")
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args(argv)

    worktree = Path(args.worktree).resolve()
    out_dir = Path(args.out_dir) if args.out_dir else worktree / DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    contract = build_contract(worktree)
    contract_path = out_dir / "selftest_contract.json"
    contract_path.write_text(report.canonical_json(contract) + "\n", encoding="utf-8")

    spec = core.normalize_contract(contract)
    verdict, run, details = core.run_and_judge(spec, worktree, timeout_s=args.timeout)

    body = report.build_artifact(
        verdict, contract_path=contract_path, worktree=worktree,
        repro_cmd=spec["repro_cmd"], artifact_details=details,
        log_sha256=(run.log_sha256 if run else ""), log_bytes=(run.log_bytes if run else 0),
        timeout_s=args.timeout or spec["timeout_s"],
        command=["python3", "-m", "tools.repro_exec_gate.selftest",
                 "--worktree", str(worktree)])
    payload = report.sealed(body)
    out_path = report.write_json_out(out_dir / "selftest.json", payload)

    sys.stdout.write(report.render_stdout(verdict) + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"self-check artifact: {out_path} (payload_sha256="
                     f"{payload['payload_sha256'][:16]}...)\n")
    return 0 if verdict["status"] == core.STATUS_PASS else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))