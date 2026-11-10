"""Command line interface of the unified optimizer toolchain.

    python -m tools.optimizer.unified.cli probe
    python -m tools.optimizer.unified.cli solve --problem g06 --engine nlopt --algorithm COBYLA
    python -m tools.optimizer.unified.cli crosscheck --problem g08
    python -m tools.optimizer.unified.cli dsl --problem g09 --out /tmp/g09.json
    python -m tools.optimizer.unified.cli audit --outdir artifacts/p304w2_n10_optimizer

``solve`` writes the standardised JSON result (validated against the published
schema) to ``--out`` or stdout, so a shell pipeline can consume optimisation
results without importing Python objects.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .adapters import algorithm_matrix, probe_all, solve as engine_solve
from .compiled import CompiledProblem
from .crosscheck import crosscheck as run_crosscheck
from .dsl import dump_problem, dump_result, load_problem
from .problems import PROBLEM_IDS, get_problem


def _load(args) -> Any:
    if args.problem in PROBLEM_IDS:
        return get_problem(args.problem)
    path = Path(args.problem)
    if not path.exists():
        raise SystemExit(f"unknown problem {args.problem!r}; built-ins: {list(PROBLEM_IDS)}")
    return load_problem(path)


def cmd_probe(args) -> int:
    payload = {
        "version": __version__,
        "engines": [p.to_dict() for p in probe_all()],
        "algorithm_matrix": algorithm_matrix(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for probe in payload["engines"]:
        state = "available" if probe["available"] else "UNAVAILABLE"
        print(f"{probe['engine']:<12} {state:<12} version={probe['version']}")
        print(f"  algorithms: {', '.join(probe['algorithms']) or '-'}")
        if probe["missing_algorithms"]:
            print(f"  missing:    {', '.join(probe['missing_algorithms'])}")
        if probe["reason"]:
            print(f"  reason:     {probe['reason']}")
    return 0


def cmd_solve(args) -> int:
    spec = _load(args)
    options: Dict[str, Any] = {}
    if args.max_iter is not None:
        options["max_iter"] = args.max_iter
    if args.max_eval is not None:
        options["max_eval"] = args.max_eval
    result = engine_solve(spec, engine=args.engine, algorithm=args.algorithm, options=options)
    if args.out:
        dump_result(result, args.out, history_limit=args.history_limit)
        print(
            f"{result.engine}:{result.algorithm} {result.problem_id} -> {result.status} "
            f"f={result.objective!r} violation={result.constraint_violation!r} "
            f"(written to {args.out})",
            file=sys.stderr,
        )
    else:
        print(json.dumps(result.to_dict(history_limit=args.history_limit), indent=2))
    return 0 if result.status in ("converged", "max_iterations") else 1


def cmd_crosscheck(args) -> int:
    spec = _load(args)
    pairs = [tuple(p.split(":")) for p in args.pair] if args.pair else None
    report = run_crosscheck(spec, pairs=pairs, tol=args.tol)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(report["verdict"], file=sys.stderr)
    else:
        print(text)
    return 0 if report["meets_tolerance"] else 1


def cmd_dsl(args) -> int:
    spec = _load(args)
    if args.out:
        path = dump_problem(spec, args.out)
        print(f"wrote schema-valid problem definition to {path}", file=sys.stderr)
    else:
        print(spec.to_json())
    return 0


def cmd_audit(args) -> int:
    from .audit import main as audit_main

    return audit_main(["--outdir", args.outdir] + (["--problems", *args.problems] if args.problems else []))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.optimizer.unified.cli",
        description="unified optimisation toolchain (SciPy / NLopt / pyoptsparse)",
    )
    parser.add_argument("--version", action="version", version=f"unified-optimizer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="list engines, versions and algorithms")
    p_probe.add_argument("--json", action="store_true")
    p_probe.set_defaults(func=cmd_probe)

    def add_problem(p):
        p.add_argument("--problem", required=True, help="built-in problem id or a DSL JSON file")

    p_solve = sub.add_parser("solve", help="solve one problem with one engine")
    add_problem(p_solve)
    p_solve.add_argument("--engine", required=True, choices=["scipy", "nlopt", "pyoptsparse"])
    p_solve.add_argument("--algorithm", default=None)
    p_solve.add_argument("--max-iter", type=int, default=None)
    p_solve.add_argument("--max-eval", type=int, default=None)
    p_solve.add_argument("--history-limit", type=int, default=200)
    p_solve.add_argument("--out", default=None)
    p_solve.set_defaults(func=cmd_solve)

    p_cc = sub.add_parser("crosscheck", help="solve one problem with several engines and adjudicate")
    add_problem(p_cc)
    p_cc.add_argument("--pair", action="append", help="engine:algorithm (repeatable)")
    p_cc.add_argument("--tol", type=float, default=0.01)
    p_cc.add_argument("--out", default=None)
    p_cc.set_defaults(func=cmd_crosscheck)

    p_dsl = sub.add_parser("dsl", help="emit a schema-valid problem definition")
    add_problem(p_dsl)
    p_dsl.add_argument("--out", default=None)
    p_dsl.set_defaults(func=cmd_dsl)

    p_audit = sub.add_parser("audit", help="run the full criteria audit and write artifacts")
    p_audit.add_argument("--outdir", default="artifacts/p304w2_n10_optimizer")
    p_audit.add_argument("--problems", nargs="*", default=None)
    p_audit.set_defaults(func=cmd_audit)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())