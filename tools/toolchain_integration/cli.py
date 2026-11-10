"""CLI: run a toolchain pipeline and emit its evidence bundle.

    python -m tools.toolchain_integration.cli \
        --spec tools/toolchain_integration/specs/synthetic_toolchain_v1.json \
        --out artifacts/toolchain_integration/latest --repeats 3

Exit code is 0 only when the required criteria pass *and* the run is reproducible;
``--allow-fail`` exists so a deliberately failing pipeline can still be recorded
as evidence (an honest FAIL that is written down beats an unnoticed one).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .dag import build_plan, run_pipeline
from .perf import measure
from .regression import aggregate, run_suites
from .registry import ExecutionContext
from .report import build_artifact, write_bundle, write_json
from .schema import PipelineSpec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="toolchain integration harness")
    ap.add_argument("--spec", required=True, help="pipeline spec JSON")
    ap.add_argument("--out", default="artifacts/toolchain_integration/latest",
                    help="output directory for the evidence bundle")
    ap.add_argument("--repeats", type=int, default=3, help="timed repeats (warm-up excluded)")
    ap.add_argument("--seed", type=int, default=0, help="run seed injected into adapters")
    ap.add_argument("--no-perf", action="store_true", help="skip the timing study")
    ap.add_argument("--allow-fail", action="store_true",
                    help="write the bundle and exit 0 even when criteria fail")
    ap.add_argument("--regression", nargs="*", default=None, metavar="TARGET",
                    help="existing test targets to run and record in the bundle")
    ap.add_argument("--regression-timeout", type=float, default=900.0,
                    help="per-target timeout for --regression (seconds)")
    args = ap.parse_args(argv)

    spec = PipelineSpec.load(args.spec)
    plan = build_plan(spec)
    ctx = ExecutionContext(seed=args.seed)
    run = run_pipeline(spec, ctx, plan=plan)
    overhead = None if args.no_perf else measure(spec, repeats=args.repeats, ctx=ctx, plan=plan)

    extra_checks = []
    regression = None
    if args.regression:
        records = run_suites(args.regression, timeout=args.regression_timeout)
        regression = {"suites": records, "aggregate": aggregate(records)}
        extra_checks = list(records)

    rollup = run.values.get("rollup")
    verdict = bool(rollup["verdict"].value) if rollup else None
    paths = write_bundle(args.out, spec, run, overhead, extra_checks=extra_checks)
    summary = build_artifact(spec, run, overhead)
    write_json(f"{args.out}/summary.json", {
        "status": run.status,
        "evidence": run.evidence,
        "verdict": verdict,
        "chain_digest": run.chain_digest,
        "overhead": {
            "orchestration_ratio": overhead.orchestration_ratio if overhead else None,
            "overhead_ratio": overhead.overhead_ratio if overhead else None,
            "determinism": overhead.determinism if overhead else None,
        } if overhead else None,
        "files": {k: str(v) for k, v in paths.items()},
        "criteria": summary.get("criteria", {}),
        "regression": regression,
    })
    print(json.dumps({
        "run_status": run.status,
        "evidence": run.evidence,
        "verdict": verdict,
        "chain_digest": run.chain_digest,
        "overhead_ratio": overhead.orchestration_ratio if overhead else None,
        "determinism": overhead.determinism if overhead else None,
    }, indent=2))

    ok = run.status == "OK" and (verdict is not False)
    if regression is not None and regression["aggregate"]["verdict"] != "PASS":
        ok = False
    if overhead is not None and not overhead.determinism:
        ok = False
    if args.allow_fail:
        return 0
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())