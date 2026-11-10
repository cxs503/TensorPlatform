"""Command-line entry point for the cross-architecture reproduction gate.

    python3 -m tools.cross_arch_gate.cli run        --arm loong64-n315 --out arm.json
    python3 -m tools.cross_arch_gate.cli compare    --ref a.json --cand b.json --out pair.json
    python3 -m tools.cross_arch_gate.cli selftest   --ref a.json --cand name=b.json --out m.json
    python3 -m tools.cross_arch_gate.cli audit      --out audit.json
    python3 -m tools.cross_arch_gate.cli pipeline   --outdir artifacts/cross_arch_gate \\
                                                    --report-md docs/reports/...md

The ``run`` subcommand is the only one that has to execute on the architecture
under test; the other four are pure functions of records and can run anywhere.
That split is what lets a single judge consume records produced by the loong64
fleet and by an x86 CI runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import audit, gate, pipeline, record as record_mod, report_md, selftest


def _parse_arms(pairs: List[str]) -> Dict[str, Path]:
    arms: Dict[str, Path] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--cand expects label=path, got {item!r}")
        label, path = item.split("=", 1)
        arms[label] = Path(path)
    return arms


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cross_arch_gate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="measure one arm on this machine")
    p_run.add_argument("--arm", required=True)
    p_run.add_argument("--arith", default="native", choices=("native", "exact", "fma"))
    p_run.add_argument("--sum", default="naive", choices=("naive", "fsum", "reverse", "kahan"))
    p_run.add_argument("--libm", default="stdlib", choices=("stdlib", "alt"))
    p_run.add_argument("--out", required=True)

    p_cmp = sub.add_parser("compare", help="compare two arm records")
    p_cmp.add_argument("--ref", required=True)
    p_cmp.add_argument("--cand", required=True)
    p_cmp.add_argument("--policy", default=None)
    p_cmp.add_argument("--out", required=True)

    p_self = sub.add_parser("selftest", help="mutation matrix against one reference arm")
    p_self.add_argument("--ref", required=True)
    p_self.add_argument("--cand", action="append", default=[])
    p_self.add_argument("--policy", default=None)
    p_self.add_argument("--width", type=int, default=6)
    p_self.add_argument("--out", required=True)

    p_audit = sub.add_parser("audit", help="zero-domain-semantics scan")
    p_audit.add_argument("--out", required=True)

    p_pipe = sub.add_parser("pipeline", help="full run: arms -> pairs -> selftest -> audit -> criteria")
    p_pipe.add_argument("--outdir", default="artifacts/cross_arch_gate")
    p_pipe.add_argument("--policy", default=None)
    p_pipe.add_argument("--external-arm", action="append", default=[],
                        help="label=record.json produced on another architecture")
    p_pipe.add_argument("--report-md", default=None)
    p_pipe.add_argument("--no-figures", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "run":
        rec = record_mod.build_record(args.arm, arith=args.arith,
                                      sum_variant=args.sum, libm=args.libm)
        path = record_mod.save_record(rec, Path(args.out))
        print(json.dumps({"arm": args.arm, "out": str(path), "digest": rec["digest"],
                          "machine": rec["provenance"]["machine"],
                          "fields": rec["field_count"], "elapsed_s": rec["elapsed_s"]}))
        return 0

    if args.command == "compare":
        policy = gate.load_policy(Path(args.policy) if args.policy else None)
        report = gate.compare_files(Path(args.ref), Path(args.cand), policy)
        gate.save_report(report, Path(args.out))
        print(json.dumps(gate.compact(report)))
        return 0 if report["verdict"] == "PASS" else 1

    if args.command == "selftest":
        policy = gate.load_policy(Path(args.policy) if args.policy else None)
        ref = record_mod.load_record(Path(args.ref))
        cands = {label: record_mod.load_record(path)
                 for label, path in _parse_arms(args.cand).items()}
        result = selftest.run_matrix(ref, cands, policy, width=args.width)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result["metrics"]))
        return 0

    if args.command == "audit":
        report = audit.scan()
        audit.save(report, Path(args.out))
        print(json.dumps({"hit_count": report["hit_count"], "files": report["files_scanned"]}))
        return 0 if report["pass"] else 1

    # pipeline
    external = _parse_arms(args.external_arm)
    result = pipeline.run(Path(args.outdir), Path(args.policy) if args.policy else None,
                          external_arms=external, make_figures=not args.no_figures)
    if args.report_md:
        outdir = Path(args.outdir)
        records = {}
        for label, path in {**result["arms"], **result["external_arms"]}.items():
            records[label] = record_mod.load_record(Path(path))
        pairs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in result["pairs"].values()]
        mutation = json.loads(Path(result["mutation"]).read_text(encoding="utf-8"))
        criteria_report = json.loads(Path(result["criteria"]).read_text(encoding="utf-8"))
        audit_report = json.loads(Path(result["audit"]).read_text(encoding="utf-8"))
        markdown = report_md.render(outdir, records, pairs, mutation, criteria_report,
                                    audit_report, result["figures"], result["latency_s"])
        report_md.write(markdown, Path(args.report_md))
        result["report_md"] = args.report_md
    print(json.dumps(result, indent=2))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())