#!/usr/bin/env python3
"""统一 CLI：``python -m tools.mech_replay --mode {collect,replay,mutate,gate,figures}``。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.mech_replay import collector, gate, mutate, replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.mech_replay",
        description="机制重放一致性套件（golden replay + 6 类退化检出 + CI 门）")
    parser.add_argument("--mode", default="replay",
                        choices=["collect", "replay", "mutate", "gate", "figures"])
    parser.add_argument("--mechanism", default=None, help="只重放/变异单个机制")
    parser.add_argument("--fixtures", default=str(replay.DEFAULT_FIXTURES))
    parser.add_argument("--out", default=str(collector.DEFAULT_OUT))
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--no-pytest", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "collect":
        return collector.main(["--out", args.out]
                              + (["--snapshot", args.snapshot] if args.snapshot else []))
    if args.mode == "replay":
        return replay.main(["--fixtures", args.fixtures]
                           + (["--mechanism", args.mechanism] if args.mechanism else [])
                           + (["--json-out", args.json_out] if args.json_out else []))
    if args.mode == "mutate":
        return mutate.main(["--fixtures", args.fixtures]
                           + (["--mechanism", args.mechanism] if args.mechanism else [])
                           + (["--json-out", args.json_out] if args.json_out else []))
    if args.mode == "gate":
        out = Path(args.out)
        report = gate.run_gate(out / "fixtures", out, include_pytest=not args.no_pytest)
        path = gate.write_report(report, out)
        print(f"gate_green={report['green']} report={path}")
        return 0 if report["green"] else 1
    if args.mode == "figures":
        from tools.mech_replay import figures

        return figures.main(["--out", args.out])
    parser.error(f"unknown mode: {args.mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main())