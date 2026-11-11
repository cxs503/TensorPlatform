"""CLI: run a scaling plan, write the ledger, figures and markdown summary.

Usage::

    python -m tools.scaling.cli --plan tools/scaling/configs/full.json \
        --out artifacts/scaling_ledger/results.json \
        --figdir artifacts/scaling_ledger/figures
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.scaling.report import make_figures, summary_markdown
from tools.scaling.runner import (
    build_ledger,
    evaluate_acceptance,
    host_precheck,
    recompute_ledger,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parallel scalability ledger harness")
    ap.add_argument("--plan", required=True, help="measurement plan JSON")
    ap.add_argument("--out", required=True, help="ledger JSON output path")
    ap.add_argument("--figdir", default=None, help="figure output directory")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--recompute", default=None,
                    help="re-derive statistics from an existing results.json (no measurement)")
    ap.add_argument("--skip-host-check", action="store_true",
                    help="do not refuse to measure on a saturated host")
    ap.add_argument("--allow-contended-host", action="store_true",
                    help="measure anyway on a saturated host (numbers describe the queue)")
    args = ap.parse_args(argv)

    if args.recompute:
        prev = json.loads(Path(args.recompute).read_text())
        ledger = recompute_ledger(prev["ledger"], seed=int(prev["ledger"]["protocol"]["reps"]))
        figures = make_figures(ledger, Path(args.figdir)) if args.figdir else prev.get("figures", [])
        acceptance = evaluate_acceptance(ledger, figures=[f["path"] for f in figures])
        doc = {"ledger": ledger, "acceptance": acceptance, "figures": figures}
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2) + "\n")
        md = summary_markdown(ledger, acceptance)
        out.with_suffix(".md").write_text(md + "\n")
        if args.figdir:
            (Path(args.figdir) / "summary.md").write_text(md + "\n")
        print(md, flush=True)
        return 0 if acceptance["all_pass"] else 1

    plan = json.loads(Path(args.plan).read_text())
    progress = None if args.quiet else (lambda msg: print(msg, flush=True))
    check = None
    if not args.skip_host_check:
        check = host_precheck()
        print(
            "host precheck: speedup@%d procs = %.2f (gate %.1f) load=%s -> %s"
            % (
                check["n_max"],
                check["speedup"][str(check["n_max"])],
                check["min_speedup"],
                check.get("load_1_5_15"),
                check["verdict"],
            ),
            flush=True,
        )
        if not check["host_ok"] and not args.allow_contended_host:
            print(
                "REFUSING to emit a ledger: this host cannot run parallel processes. "
                "Use a dedicated compute node, or pass --allow-contended-host to record "
                "the (invalid for scaling) numbers anyway.",
                flush=True,
            )
            return 2
    ledger = build_ledger(plan, progress=progress, host_check=check)
    figures = []
    if args.figdir:
        figures = make_figures(ledger, Path(args.figdir))
    acceptance = evaluate_acceptance(ledger, figures=[f["path"] for f in figures])
    doc = {
        "ledger": ledger,
        "acceptance": acceptance,
        "figures": figures,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    md = summary_markdown(ledger, acceptance)
    out.with_suffix(".md").write_text(md + "\n")
    if args.figdir:
        (Path(args.figdir) / "summary.md").write_text(md + "\n")
    print(md, flush=True)
    return 0 if acceptance["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())