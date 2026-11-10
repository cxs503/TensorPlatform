"""CLI: ``python -m tools.caliber_reader <table.json> [...]``

Exit codes (fail-closed): 0 = a definitive caliber judgement was reached,
2 = withheld (corrupt / insufficient / out of band), 3 = degraded (judgement
refused).  ``--json`` prints the full verdict.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .lines import load_lineset
from .reader import interpret


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tables", nargs="+", help="benchmark table JSON files")
    ap.add_argument("--lines", default=None, help="correlation-line config JSON")
    ap.add_argument("--config", default=None, help="reader config JSON")
    ap.add_argument("--json", action="store_true", help="print the full verdict")
    args = ap.parse_args(argv)

    root = _repo_root()
    lines_path = args.lines or root / "benchmarks/verified/data_assimilation/config/lines.json"
    cfg_path = (args.config
                or root / "benchmarks/verified/data_assimilation/config/reader_config.json")
    lines = load_lineset(lines_path)
    with open(cfg_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    worst = 0
    for table_path in args.tables:
        with open(table_path, "r", encoding="utf-8") as fh:
            table = json.load(fh)
        verdict = interpret(table, lines, config)
        code = int(verdict.get("exit_code", 2))
        worst = max(worst, 0 if code == 0 else (2 if code == 2 else 3))
        if args.json:
            print(json.dumps({"table": str(table_path), "verdict": verdict},
                             indent=2, ensure_ascii=False))
        else:
            print(f"{pathlib.Path(table_path).name}: {verdict['status']} "
                  f"(exit {code}) -- {verdict.get('reason', '')}")
    return worst


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())