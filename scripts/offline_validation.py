#!/usr/bin/env python3
"""Manage isolated foundation offline validation runs without production writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quality.offline_validation import RunRegistry, ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/tmp/foundation-offline-validation")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("scenario_id")
    create.add_argument("--scenario-version", default="1.0.0")
    create.add_argument("--environment", choices=("test", "staging"), default="test")
    create.add_argument(
        "--asset-type",
        choices=("fixture", "synthetic", "replay", "anonymized"),
        default="synthetic",
    )
    create.add_argument("--ttl-hours", type=int, default=24)
    create.add_argument("--lease-seconds", type=int, default=900)

    show = sub.add_parser("show")
    show.add_argument("run_id")

    register = sub.add_parser("register")
    register.add_argument("run_id")
    register.add_argument("kind")
    register.add_argument("uri")
    register.add_argument("--checksum")
    register.add_argument(
        "--cleanup",
        choices=("registry-owned", "metadata-only", "read-only"),
        default="registry-owned",
        help="external URIs must use metadata-only or read-only",
    )

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("run_id")
    heartbeat.add_argument("--lease-seconds", type=int, default=900)

    finish = sub.add_parser("finish")
    finish.add_argument("run_id")
    finish.add_argument("status", choices=("passed", "failed", "cancelled"))

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("run_id")
    cleanup.add_argument("--force", action="store_true")
    sub.add_parser("janitor")
    sub.add_parser("cleanup-expired", help="compatibility alias for janitor")
    sub.add_parser("list")

    args = parser.parse_args()
    registry = RunRegistry(args.root)
    try:
        if args.command == "create":
            result = registry.create(
                args.scenario_id,
                scenario_version=args.scenario_version,
                environment=args.environment,
                asset_type=args.asset_type,
                ttl_hours=args.ttl_hours,
                lease_seconds=args.lease_seconds,
            )
        elif args.command == "show":
            result = registry.load(args.run_id)
        elif args.command == "register":
            result = registry.register_asset(
                args.run_id, kind=args.kind, uri=args.uri, checksum=args.checksum,
                cleanup=args.cleanup,
            )
        elif args.command == "heartbeat":
            result = registry.heartbeat(args.run_id, lease_seconds=args.lease_seconds)
        elif args.command == "finish":
            result = registry.finish(args.run_id, args.status)
        elif args.command == "cleanup":
            result = {"cleaned": registry.cleanup(args.run_id, force=args.force)}
        elif args.command in {"janitor", "cleanup-expired"}:
            result = registry.janitor()
        else:
            result = registry.list()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ValidationError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
