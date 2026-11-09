"""CLI for the campaign-gate lifecycle.

    python -m tools.gate_lifecycle.cli run  --plan plan.json --out out/ \
        [--capacity 16] [--ledger ledger.jsonl] [--pool pool.jsonl]
    python -m tools.gate_lifecycle.cli dag  --plan plan.json
    python -m tools.gate_lifecycle.cli maturity --baseline baseline.json

Exit codes: 0 = OK, 1 = a node failed / criteria not met, 2 = usage/plan error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import baseline as baseline_mod
from .dag import critical_path, find_cycle, schedule, slacks, waves
from .executor import JsonCommandExecutor, StaticExecutor
from .model import CycleError, Plan, PlanError
from .pipeline import LifecycleConfig, run


def _load_plan(path: str) -> Plan:
    return Plan.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def cmd_dag(args: argparse.Namespace) -> int:
    plan = _load_plan(args.plan)
    cyc = find_cycle(plan)
    if cyc:
        print(json.dumps({"acyclic": False, "cycle": cyc}, ensure_ascii=False))
        return 1
    cp, length = critical_path(plan)
    sched = schedule(plan, capacity=args.capacity)
    print(json.dumps({
        "acyclic": True,
        "critical_path": cp,
        "critical_length": length,
        "slacks": slacks(plan),
        "waves": waves(plan),
        "makespan": sched.makespan,
        "max_parallelism": sched.max_parallelism(),
        "capacity": sched.capacity,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plan = _load_plan(args.plan)
    executor = (JsonCommandExecutor(args.cmd, timeout=args.timeout) if args.cmd
                else StaticExecutor())
    result = run(plan, executor=executor, cfg=LifecycleConfig(
        capacity=args.capacity,
        ledger_path=args.ledger,
        pool_path=args.pool,
        seed_plan_id=args.seed_plan_id,
        max_seed_nodes=args.max_seed_nodes,
    ), out_dir=args.out)
    summary = {
        "run_id": result.run_id,
        "result_digest": result.result_digest,
        "makespan": result.schedule["makespan"],
        "critical_path": result.schedule["critical_path"],
        "failures": result.failures,
        "completeness": result.completeness,
        "gaps": result.gaps["counts"],
        "proposals_generated": result.proposals["generated"],
        "injection": result.injection,
        "seeded_plan_nodes": len(result.seeded_plan["nodes"]),
        "schedule_problems": result.invariants["schedule_problems"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    bad = result.failures["failed"] or result.invariants["schedule_problems"]
    return 1 if bad else 0


def cmd_maturity(args: argparse.Namespace) -> int:
    base = baseline_mod.load_baseline(args.baseline)
    out = baseline_mod.evaluate(base, capacity=args.capacity)
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if out["comparison"]["decision"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gate_lifecycle")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dag", help="acyclicity / critical path / schedule")
    p.add_argument("--plan", required=True)
    p.add_argument("--capacity", type=int, default=16)
    p.set_defaults(func=cmd_dag)

    p = sub.add_parser("run", help="full lifecycle run")
    p.add_argument("--plan", required=True)
    p.add_argument("--out")
    p.add_argument("--capacity", type=int, default=16)
    p.add_argument("--ledger")
    p.add_argument("--pool")
    p.add_argument("--seed-plan-id")
    p.add_argument("--max-seed-nodes", type=int, default=10)
    p.add_argument("--cmd", help="per-node command emitting JSON (optional)")
    p.add_argument("--timeout", type=float, default=300.0)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("maturity", help="compare against a frozen baseline")
    p.add_argument("--baseline", required=True)
    p.add_argument("--capacity", type=int, default=16)
    p.set_defaults(func=cmd_maturity)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (PlanError, CycleError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())