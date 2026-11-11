"""Command line entry point: ``python -m services.capability_auto_upgrader.cli accept``."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from .acceptance import run_acceptance
from .model import EvidenceRun
from .policy import UpgradePolicy
from .upgrader import CapabilityAutoUpgrader


def _cmd_accept(args: argparse.Namespace) -> int:
    data = run_acceptance(args.out, run_pytest=not args.skip_pytest)
    print(
        json.dumps(
            {
                "all_passed": data["all_passed"],
                "criteria": {k: v["passed"] for k, v in data["criteria"].items()},
                "acceptance": data["acceptance_path"],
                "report": data["report"]["doc_path"],
                "figures": data["figures"],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0 if data["all_passed"] else 2


def _cmd_status(args: argparse.Namespace) -> int:
    with CapabilityAutoUpgrader(args.dir, policy=UpgradePolicy()) as up:
        print(json.dumps({"levels": up.levels(), "status": up.status(), "verify": up.verify()},
                         ensure_ascii=False, indent=1))
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    payload = json.loads(open(args.run_file, encoding="utf-8").read())
    run = EvidenceRun.from_dict(payload)
    with CapabilityAutoUpgrader(args.dir, policy=UpgradePolicy()) as up:
        print(json.dumps(up.record_run(run), ensure_ascii=False, indent=1, default=str))
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    payload = json.loads(open(args.snapshot_file, encoding="utf-8").read())
    with CapabilityAutoUpgrader(args.dir, policy=UpgradePolicy()) as up:
        print(json.dumps(up.ingest_audit_snapshot(payload), ensure_ascii=False, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capability_auto_upgrader", description=__doc__)
    parser.add_argument("--dir", default="artifacts/capability_auto_upgrader",
                        help="state directory (ledger + evidence store)")
    sub = parser.add_subparsers(dest="command", required=True)

    accept = sub.add_parser("accept", help="run the full acceptance pipeline")
    accept.add_argument("--out", default=None)
    accept.add_argument("--skip-pytest", action="store_true")
    accept.set_defaults(func=_cmd_accept)

    status = sub.add_parser("status", help="print levels, streaks and chain verification")
    status.set_defaults(func=_cmd_status)

    record = sub.add_parser("record", help="record one evidence run from a JSON file")
    record.add_argument("run_file")
    record.set_defaults(func=_cmd_record)

    snapshot = sub.add_parser("snapshot", help="ingest one audit_capabilities payload from a JSON file")
    snapshot.add_argument("snapshot_file")
    snapshot.set_defaults(func=_cmd_snapshot)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())