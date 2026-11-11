"""CLI: python3 -m tools.bench_norm.cli <command>."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import measure, pipeline
from .schema import read_json, read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[2]


def cmd_physics(args) -> int:
    cfg = read_json(args.config or ROOT / "tools" / "bench_norm" / "configs" / "workloads.json")
    built = measure.build_kernel(args.outdir or ROOT / "artifacts" / "bench_norm" / "bin")
    res = pipeline.physics_check(built["binary"], cfg)
    print(json.dumps({"build": {k: v for k, v in built.items() if k != "cmd"}, "physics": res}, indent=2))
    return 0 if (res["l2_pass"] and res["mass_pass"]) else 1


def cmd_measure(args) -> int:
    cfg = read_json(args.config or ROOT / "tools" / "bench_norm" / "configs" / "workloads.json")
    built = measure.build_kernel(args.outdir or ROOT / "artifacts" / "bench_norm" / "bin",
                                 flags=args.flags)
    fp = measure.host_fingerprint()
    arm = args.arm or pipeline.arm_label(fp["host"], fp["isa"], fp["cpu_model"], args.tag)
    recs = measure.measure_grid(built["binary"], cfg["workloads"], args.reps, arm=arm,
                                host=fp["host"], isa=fp["isa"], progress=True)
    out = Path(args.out or ROOT / "artifacts" / "bench_norm" / "measurements" /
               (f"{fp['host']}{('_' + args.tag) if args.tag else ''}.jsonl"))
    write_jsonl(recs, out)
    print(json.dumps({"out": str(out), "n": len(recs), "arm": arm,
                      "host": fp, "noise": measure.summarise_noise(recs)}, indent=2))
    return 0


def cmd_pipeline(args) -> int:
    res = pipeline.run(external=args.external or (), reps=args.reps, ref=args.ref,
                       do_measure=not args.no_measure, n_replicates=args.replicates,
                       outdir=args.outdir, make_figures=not args.no_figures,
                       kernel_flags=args.flags, tag=args.tag)
    print(json.dumps({"criteria": res["criteria"], "figures": res["figures"],
                      "studies": {k: {"alpha_true": v["alpha_true"],
                                      "alpha_rel_err": v["alpha_rel_err"],
                                      "r2_log": v["r2_log"],
                                      "coverage": v["coverage"],
                                      "diff_norm_pct": v["diff_norm_pct"],
                                      "diff_raw_pct": v["diff_raw_pct"],
                                      "target_thresholds": v["target_thresholds"]}
                                  for k, v in res["studies"].items()},
                      "real": {"arms": res["real"]["arms"],
                               "lmm": {k: res["real"]["lmm"][k] for k in
                                       ("r2_log", "r2_log_fixed_only", "icc", "beta", "se")},
                               "noise": res["real"]["noise"],
                               "physics_check": res["real"]["physics_check"]}},
                     indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.bench_norm.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("physics", help="run the steady-state physics verification")
    p.add_argument("--config")
    p.add_argument("--outdir")
    p.set_defaults(func=cmd_physics)

    m = sub.add_parser("measure", help="measure the timing grid on this host")
    m.add_argument("--config")
    m.add_argument("--outdir")
    m.add_argument("--out")
    m.add_argument("--arm")
    m.add_argument("--reps", type=int, default=9)
    m.add_argument("--flags", default="-O2", help="compiler flags for the timing kernel")
    m.add_argument("--tag", default="", help="arm tag, e.g. build variant")
    m.set_defaults(func=cmd_measure)

    q = sub.add_parser("pipeline", help="full pipeline")
    q.add_argument("--external", action="append", help="jsonl records from other hosts")
    q.add_argument("--ref")
    q.add_argument("--reps", type=int)
    q.add_argument("--replicates", type=int, help="Monte-Carlo replicates per scenario")
    q.add_argument("--outdir")
    q.add_argument("--no-measure", action="store_true")
    q.add_argument("--no-figures", action="store_true")
    q.add_argument("--flags", default="-O2")
    q.add_argument("--tag", default="")
    q.set_defaults(func=cmd_pipeline)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())