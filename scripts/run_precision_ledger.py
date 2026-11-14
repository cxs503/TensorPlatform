#!/usr/bin/env python3
"""CLI for the KPI-GATE-100 precision ledger.

    python3 scripts/run_precision_ledger.py --scan . --out artifacts/precision_ledger/ledger.json
    python3 scripts/run_precision_ledger.py --check artifacts/precision_ledger/ledger.json

``--scan``  : scan the repo for benchmark comparisons, normalise, adjudicate
              baselines, write ledger.json + summary.md and the figures.
``--check`` : machine gate over an existing ledger.json (field completeness,
              rel_err re-derivation, WITHHELD invariants, summary consistency).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precision_ledger import build_ledger, check_file, write_ledger  # noqa: E402
from precision_ledger.report import write_reports  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KPI-GATE-100 precision ledger")
    ap.add_argument("--scan", metavar="ROOT", help="scan this root for benchmark evidence")
    ap.add_argument("--out", metavar="PATH", help="ledger.json output path")
    ap.add_argument("--check", metavar="PATH", help="validate an existing ledger.json")
    ap.add_argument("--docs", metavar="PATH", default="docs/precision_ledger",
                    help="report output directory (default docs/precision_ledger)")
    ap.add_argument("--no-figures", action="store_true", help="skip figure generation")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.scan and not args.check:
        ap.error("one of --scan / --check is required")

    if args.scan:
        root = Path(args.scan).resolve()
        out = Path(args.out) if args.out else root / "artifacts/precision_ledger/ledger.json"
        if not out.is_absolute():
            out = Path.cwd() / out
        ledger = build_ledger(root)
        write_ledger(ledger, out)
        if not args.quiet:
            t = ledger["totals"]
            g = ledger["gate_results"]
            print(f"[scan] root={root}")
            print(f"[scan] entries={t['entries']} judged={t['judged']} "
                  f"grades={t['by_grade']}")
            print(f"[scan] pass_15pct={g['pass_15pct']}/{g['denominator']} "
                  f"({g['pass_15pct_rate']}%)  pass_3pct={g['pass_3pct']}/"
                  f"{g['denominator']} ({g['pass_3pct_rate']}%)")
            print(f"[scan] wrote {out}")
        rep = write_reports(ledger, out_dir=out.parent,
                            docs_dir=(Path(args.docs) if Path(args.docs).is_absolute()
                                      else root / args.docs),
                            figures=not args.no_figures)
        if not args.quiet:
            for p in rep:
                print(f"[report] {p}")
        ok, errs = check_file(out)
        if not ok:
            for e in errs:
                print(f"[gate] FAIL {e}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"[gate] ledger OK ({ledger['totals']['entries']} entries)")
        return 0

    ok, errs = check_file(Path(args.check))
    if ok:
        print(f"[gate] OK {args.check}")
        return 0
    for e in errs:
        print(f"[gate] FAIL {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())