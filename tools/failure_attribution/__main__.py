"""CLI: ``python3 -m tools.failure_attribution <command>``.

Commands
--------
measure    success rate + attribution coverage/accuracy from a frozen CSV
attribute  classify one JSON record (or a JSON list) and print attributions
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .engine import classify, load_config, mission_from_row
from .measure import run


def cmd_measure(args: argparse.Namespace) -> int:
    as_of = None
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    result = run(
        args.missions_csv,
        args.outdir,
        config_path=args.config,
        as_of=as_of,
        emit_sql=not args.no_sql,
    )
    print(json.dumps(
        {
            "snapshot_sha256": result["snapshot_sha256"],
            "success_all_time": result["success"]["all_time"]["success_rate"],
            "success_window": result["success"][f"last_{result['success']['window_days']}d"]["success_rate"],
            "covered": result["attribution"]["masked"]["coverage"],
            "heldout_accuracy": result["attribution"]["masked"]["heldout"]["stage_accuracy"],
            "criteria": result["criteria"],
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))
    return 0


def cmd_attribute(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else [payload]
    out = [
        classify(mission_from_row(rec, config), config, mask_declared=args.mask).as_dict()
        for rec in records
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.failure_attribution", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="success rate + attribution metrics")
    m.add_argument("--missions-csv", required=True)
    m.add_argument("--outdir", required=True)
    m.add_argument("--config", default=None)
    m.add_argument("--as-of", default=None, help="ISO timestamp of the snapshot")
    m.add_argument("--no-sql", action="store_true", help="skip backfill SQL emission")
    m.set_defaults(func=cmd_measure)

    a = sub.add_parser("attribute", help="classify record(s) from a JSON file")
    a.add_argument("--input", required=True)
    a.add_argument("--config", default=None)
    a.add_argument("--mask", action="store_true", help="mask declared stage tokens")
    a.set_defaults(func=cmd_attribute)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())