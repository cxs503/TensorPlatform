#!/usr/bin/env python3
"""CLI for the WITHHELD reference-activation mechanism.

    python -m services.withheld_activation.cli seed   --state-dir DIR [--with-references]
    python -m services.withheld_activation.cli scan   --state-dir DIR [--dry-run]
    python -m services.withheld_activation.cli watch  --state-dir DIR [--interval 60] [--timeout 300]
    python -m services.withheld_activation.cli gate   --state-dir DIR
    python -m services.withheld_activation.cli recount --state-dir DIR [--repair]
    python -m services.withheld_activation.cli show   --state-dir DIR
    python -m services.withheld_activation.cli acceptance [--out-dir DIR] [--figures]

Exit codes
----------
``0``   pass (including "nothing to do": an idempotent pass is a success)
``1``   usage / unexpected error
``2``   write-time recompute gate refused the write (counter block tampered);
        the per-field delta is printed and no state is written
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _pair(state_dir: Path):
    from .fixtures import open_pair

    ledger, store = open_pair(state_dir)
    if not ledger.state_path.exists():
        raise SystemExit(f"no ledger at {ledger.state_path} (run `seed` first)")
    return ledger, store


def _print(payload) -> None:
    print(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        return sub.add_parser(name, help=help_text)

    p_seed = add("seed", "register synthetic subjects (WITHHELD) and optionally reference rows")
    p_seed.add_argument("--state-dir", required=True)
    p_seed.add_argument("--with-references", action="store_true")

    p_scan = add("scan", "run one activation-detector pass")
    p_scan.add_argument("--state-dir", required=True)
    p_scan.add_argument("--dry-run", action="store_true")

    p_watch = add("watch", "poll the reference table and flip verdicts as data arrives")
    p_watch.add_argument("--state-dir", required=True)
    p_watch.add_argument("--interval", type=float, default=60.0)
    p_watch.add_argument("--timeout", type=float, default=None)
    p_watch.add_argument("--max-passes", type=int, default=None)
    p_watch.add_argument("--stop-on-event", action="store_true")

    p_gate = add("gate", "verify the stored counters against the base records")
    p_gate.add_argument("--state-dir", required=True)

    p_recount = add("recount", "recompute counters (optionally repair a tampered block)")
    p_recount.add_argument("--state-dir", required=True)
    p_recount.add_argument("--repair", action="store_true")

    p_show = add("show", "dump verdict state and audit summary")
    p_show.add_argument("--state-dir", required=True)

    p_acc = add("acceptance", "run machine-checkable criteria A..F")
    p_acc.add_argument("--out-dir", default=None)
    p_acc.add_argument("--figures", action="store_true")

    args = parser.parse_args(argv)

    from .detector import scan, watch
    from .ledger import CounterMismatch

    try:
        if args.command == "seed":
            from . import fixtures
            _print(fixtures.seed(Path(args.state_dir),
                                 with_references=args.with_references))
            return 0

        if args.command == "scan":
            ledger, store = _pair(Path(args.state_dir))
            report = scan(ledger, store, dry_run=args.dry_run)
            _print(report)
            print(f"audit_appended={report['audit_appended']} noop={len(report['noop_subjects'])}",
                  file=sys.stderr)
            return 0

        if args.command == "watch":
            ledger, store = _pair(Path(args.state_dir))
            report = watch(ledger, store, interval_s=args.interval, timeout_s=args.timeout,
                           max_passes=args.max_passes, stop_on_event=args.stop_on_event)
            _print(report)
            return 0

        if args.command == "gate":
            ledger, _ = _pair(Path(args.state_dir))
            report = ledger.gate_report()
            _print(report)
            if not report["ok"]:
                print(f"REFUSED: counter delta {report['deltas']}", file=sys.stderr)
                return 2
            return 0

        if args.command == "recount":
            ledger, _ = _pair(Path(args.state_dir))
            if args.repair:
                _print(ledger.repair_counters())
            else:
                _print(ledger.gate_report())
            return 0

        if args.command == "show":
            ledger, store = _pair(Path(args.state_dir))
            _print({
                "criteria": ledger.criteria,
                "counters_declared": ledger.declared_counters,
                "counters_recomputed": ledger.recomputed_counters(),
                "audit_entries": len(ledger.audit_records()),
                "reference_table_digest": store.table_digest(),
                "reference_rows": sorted(store.rows()),
            })
            return 0

        if args.command == "acceptance":
            from .acceptance import run

            report = run(Path(args.out_dir) if args.out_dir else None,
                         figures=args.figures)
            _print({k: v["passed"] for k, v in report["criteria"].items()})
            print(f"all_passed={report['all_passed']} out_dir={report['out_dir']}",
                  file=sys.stderr)
            return 0 if report["all_passed"] else 1

    except CounterMismatch as exc:
        _print({"ok": False, "refused": True, "reason": str(exc),
                "declared": exc.declared, "recomputed": exc.recomputed, "deltas": exc.deltas})
        return 2

    parser.error("unhandled command")  # pragma: no cover
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))