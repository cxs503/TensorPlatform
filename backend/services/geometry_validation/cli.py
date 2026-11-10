"""Command-line entry point: ``python -m backend.services.geometry_validation.cli``.

Exit codes (machine-consumable by a dispatcher):

* ``0`` – every item PASSed,
* ``1`` – at least one item was BLOCKed (gate worked, the payload must not run),
* ``2`` – at least one item raised an internal ERROR (the gate itself is suspect),
* ``3`` – harness failure (bad batch file / bad profile / IO error).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .audit import AuditLog
from .pipeline import exit_code, load_batch, validate_batch
from .profile import ProfileError, load_profile
from .reason_codes import GeometryValidationError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="geometry_validation",
        description="Pre-solve geometry & mesh validation gate (domain-neutral engine)")
    ap.add_argument("--batch", required=True,
                    help="JSON batch file: list or {\"items\": [...]}")
    ap.add_argument("--profile", default=None,
                    help="validation profile JSON (thresholds / applicability envelopes); "
                         "omitting it uses the permissive neutral default")
    ap.add_argument("--out", default=None, help="write the JSON report to this path")
    ap.add_argument("--audit-log", default=None,
                    help="append a hash-chained audit record to this JSONL file")
    ap.add_argument("--strict", action="store_true",
                    help="no error recovery: abort on the first unexpected exception")
    ap.add_argument("--quiet", action="store_true", help="suppress stdout summary")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profile = load_profile(args.profile)
        items = load_batch(args.batch)
    except (ProfileError, GeometryValidationError) as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        return 3
    audit = AuditLog(args.audit_log, run_id="cli") if args.audit_log else None
    report = validate_batch(items, profile, audit=audit,
                            continue_on_error=not args.strict,
                            strict=args.strict,
                            run_metadata={"batch": os.path.abspath(args.batch),
                                          "argv": sys.argv[1:]})
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
    if not args.quiet:
        run = report["run"]
        print(f"items={run['n_items']} pass={run['n_pass']} block={run['n_block']} "
              f"error={run['n_error']} block_rate={run['block_rate']:.3f} "
              f"profile={report['profile']['profile_id']}")
        for code, count in report["summary"]["reason_code_counts"].items():
            print(f"  {code}: {count}")
        if args.audit_log:
            print(f"  audit head: {report['audit']['chain']['head_hash']} "
                  f"(chain_ok={report['audit']['chain']['ok']})")
        if args.out:
            print(f"report: {os.path.abspath(args.out)}")
    return exit_code(report)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())