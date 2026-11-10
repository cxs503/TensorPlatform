#!/usr/bin/env python3
"""CLI for the capability-governance acceptance run.

    python -m services.capability_governance.cli run --out-dir artifacts/capability_governance

Exit code 0 only when every criterion (C1..C7) passes; 1 otherwise.  ``--no-test-gate``
exists for local iteration and marks the run as such in the emitted criteria file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"], nargs="?", default="run")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--db-url", default=None,
                        help="SQLAlchemy URL for the sample table (default: SQLite in out-dir)")
    parser.add_argument("--sample-limit", type=int, default=None,
                        help="cap the number of generated sample rows (smoke runs)")
    parser.add_argument("--no-test-gate", action="store_true",
                        help="skip the pytest gate (local iteration; run is marked)")
    parser.add_argument("--json", action="store_true", help="print the criteria document")
    args = parser.parse_args(argv)

    from .acceptance import run

    doc = run(Path(args.out_dir) if args.out_dir else None, db_url=args.db_url,
              skip_test_gate=args.no_test_gate, sample_limit=args.sample_limit)
    if args.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        for name, crit in doc["criteria"].items():
            print(f"{name}: {'PASS' if crit['passed'] else 'FAIL'}")
        print(f"all_passed={doc['all_passed']}")
    return 0 if doc["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))