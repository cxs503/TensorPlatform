"""CLI for the artifact sha256 indexer.

Usage (from the repo root):
    python3 -m tools.artifact_indexer build  --root artifacts [--root runs] \
        [--index artifacts/.artifact_index.json] [--dry-run]
    python3 -m tools.artifact_indexer check  --index PATH --n 20 [--seed 42]
    python3 -m tools.artifact_indexer verify --index PATH
    python3 -m tools.artifact_indexer show   --index PATH [--limit 10]

Exit codes: 0 = ok / all sampled hashes match, 1 = mismatch or missing file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import indexer as ix


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="artifact_indexer", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build or incrementally update the index")
    p_build.add_argument("--root", action="append", required=True,
                         help="artifact root directory (repeatable, cross-repo)")
    p_build.add_argument("--index", default=ix.DEFAULT_INDEX_NAME)
    p_build.add_argument("--dry-run", action="store_true",
                         help="report planned work, write nothing")

    p_check = sub.add_parser("check", help="spot check: random N entries, recompute sha256")
    p_check.add_argument("--index", required=True)
    p_check.add_argument("--n", type=int, default=20)
    p_check.add_argument("--seed", type=int, default=None,
                         help="RNG seed for reproducible sampling")

    p_verify = sub.add_parser("verify", help="recompute sha256 for every entry")
    p_verify.add_argument("--index", required=True)

    p_show = sub.add_parser("show", help="print index summary")
    p_show.add_argument("--index", required=True)
    p_show.add_argument("--limit", type=int, default=10)

    args = ap.parse_args(argv)

    if args.cmd == "build":
        roots = [Path(r) for r in args.root]
        stats = ix.build_index(roots, Path(args.index), dry_run=args.dry_run)
        _print_json(stats)
        return 1 if stats["errors"] else 0

    if args.cmd == "check":
        res = ix.spot_check(Path(args.index), n=args.n, seed=args.seed)
        _print_json(res)
        return 0 if not res["failed"] else 1

    if args.cmd == "verify":
        res = ix.verify_all(Path(args.index))
        _print_json(res)
        return 0 if not res["failed"] else 1

    if args.cmd == "show":
        s = ix.index_summary(Path(args.index))
        sizes = s.pop("sizes")
        s["size_min"] = min(sizes) if sizes else 0
        s["size_max"] = max(sizes) if sizes else 0
        _print_json(s)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
