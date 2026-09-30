"""Command line interface for bounded fixture generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .lib.core import MODES, SCENARIO_IDS, fingerprint, generate, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic foundation industry fixtures")
    parser.add_argument("scenario", choices=SCENARIO_IDS)
    parser.add_argument("--mode", choices=MODES, default="baseline")
    parser.add_argument("--rows", type=int, help="row count; defaults to the manifest's small fixture size")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV destination; omitted output stays in memory and only a summary is printed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = generate(args.scenario, args.mode, args.rows, args.seed)
    destination = str(write_csv(records, args.output)) if args.output else None
    print(json.dumps({
        "scenario": args.scenario,
        "mode": args.mode,
        "rows": len(records),
        "fingerprint": fingerprint(records),
        "output": destination,
    }, ensure_ascii=False, sort_keys=True))
    return 0
