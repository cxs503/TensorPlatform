"""CLI for the evidence regression gate.

Exit-code contract (CI friendly)::

    0  OK   (or ACK: accepted with a recorded acknowledgement)
    1  usage / registry error
    3  RED     (degradation detected / unsubstantiated downgrade / stale ack)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gate, ledger as ledger_mod, mutation, report
from .registry import ProbeRegistry, load_levels

EXIT_OK, EXIT_USAGE, EXIT_RED = 0, 1, 3


def _load(args) -> ProbeRegistry:
    return ProbeRegistry.load(args.registry, workspace_root=Path(args.workspace))


def cmd_probe(args) -> int:
    registry = _load(args)
    ids = args.capabilities or list(registry.registered_capabilities())
    payload = {"registry": registry.summary(), "results": []}
    failed = 0
    for cid in ids:
        rep = registry.run(cid)
        payload["results"].append(rep.to_dict())
        failed += 0 if rep.passed else 1
        print(f"{cid:32s} {'PASS' if rep.passed else 'FAIL':4s} {rep.elapsed_ms:8.1f} ms "
              f"{'' if rep.passed else rep.failed_kinds or rep.error}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(ids) - failed}/{len(ids)} entries pass")
    return EXIT_OK if failed == 0 else EXIT_RED


def cmd_gate(args) -> int:
    registry = _load(args)
    levels = load_levels(args.ledger, args.field)
    if args.changes:
        changes = json.loads(Path(args.changes).read_text(encoding="utf-8"))
    else:
        changes = {}
    before = dict(levels)
    after = dict(levels)
    after.update({str(k).upper() if False else str(k): str(v).upper() for k, v in changes.items()})
    result = gate.evaluate_changes(registry, before, after)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2)[: args.max_print])
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nverdict={result.verdict} red={len(result.red_findings)} latency={result.latency_ms:.1f} ms", file=sys.stderr)
    return EXIT_RED if result.verdict == gate.RED else EXIT_OK


def cmd_audit(args) -> int:
    registry = _load(args)
    levels = load_levels(args.ledger, args.field)
    claims = {cid: levels.get(cid, args.assume_level) for cid in registry.registered_capabilities()}
    result = gate.audit_claims(registry, claims)
    print(f"verdict={result.verdict} red={len(result.red_findings)} latency={result.latency_ms:.1f} ms")
    for finding in result.findings:
        print(f"  {finding.capability_id:32s} {finding.severity:6s} {finding.code}: {finding.detail}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return EXIT_RED if result.verdict == gate.RED else EXIT_OK


def cmd_drill(args) -> int:
    registry = _load(args)
    payload = {"drill": mutation.drill(registry, injections=args.injections)}
    if args.matrix:
        payload["matrix"] = mutation.drill_matrix(registry)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    drill = payload["drill"]
    print(f"injections={drill['injections']} detected={drill['detected']} rate={drill['detection_rate']:.3f}")
    ok = drill["detected"] == drill["injections"]
    if "matrix" in payload:
        m = payload["matrix"]
        print(f"matrix cases={m['cases']} detected={m['detected']} rate={m['detection_rate']:.3f}")
        ok = ok and m["detected"] == m["cases"]
    return EXIT_OK if ok else EXIT_RED


def cmd_apply(args) -> int:
    registry = _load(args)
    changes = json.loads(Path(args.changes).read_text(encoding="utf-8"))
    ack = json.loads(Path(args.ack).read_text(encoding="utf-8")) if args.ack else None
    try:
        out = ledger_mod.apply_changes(args.ledger, changes, registry, out_path=args.out,
                                       audit_path=args.audit, ack=ack, field_name=args.field,
                                       actor=args.actor)
    except ledger_mod.GateBlocked as exc:
        print(json.dumps(ledger_mod.blocked_summary(exc), ensure_ascii=False, indent=2))
        return EXIT_RED
    print(json.dumps({"verdict": out["gate"]["verdict"], "out_path": out["out_path"],
                      "ledger_digest": out["ledger_digest"]}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_acceptance(args) -> int:
    result = report.run_acceptance(registry_path=args.registry, ledger_path=args.ledger,
                                   out_dir=args.out_dir, injections=args.injections,
                                   fp_runs=args.fp_runs, with_pytest=not args.no_pytest,
                                   with_figures=not args.no_figures,
                                   workspace=Path(args.workspace))
    for name, crit in result["criteria"].items():
        print(f"{name:28s} pass={crit['pass']}")
    for name, crit in result["negative_controls"].items():
        print(f"neg:{name:24s} pass={crit['pass']}")
    print(f"all_pass={result['all_pass']} -> {result['paths']['acceptance']}")
    return EXIT_OK if result["all_pass"] else EXIT_RED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="capability evidence regression gate (probe registry + REAL->MOCK alarm)")
    parser.add_argument("--registry", default="config/evidence_regression/probes.json")
    parser.add_argument("--ledger", default="artifacts/capability_ledger/capability_ledger_resolved.json")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--field", default="claimed_level")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="run every registered probe (claim audit)")
    p.add_argument("--capabilities", nargs="*", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("audit", help="compare ledger claims with live probe evidence")
    p.add_argument("--assume-level", default="REAL", help="level assumed for capabilities missing from the ledger")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("gate", help="evaluate a level-change file")
    p.add_argument("--changes", default=None, help="JSON {capability_id: new_level}")
    p.add_argument("--out", default=None)
    p.add_argument("--max-print", type=int, default=4000)
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("drill", help="inject REAL->MOCK degradations and measure detection")
    p.add_argument("--injections", type=int, default=20)
    p.add_argument("--matrix", action="store_true", help="also run the full entries x families matrix")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_drill)

    p = sub.add_parser("apply", help="gate then write a ledger level edit")
    p.add_argument("--changes", required=True)
    p.add_argument("--ack", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--audit", default=ledger_mod.DEFAULT_AUDIT)
    p.add_argument("--actor", default="cli")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("acceptance", help="run the full acceptance harness (criteria + artifacts + figures)")
    p.add_argument("--out-dir", default=str(report.DEFAULT_OUT))
    p.add_argument("--injections", type=int, default=20)
    p.add_argument("--fp-runs", type=int, default=100)
    p.add_argument("--no-pytest", action="store_true")
    p.add_argument("--no-figures", action="store_true")
    p.set_defaults(func=cmd_acceptance)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())