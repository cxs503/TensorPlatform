"""Harvest a ledger when the host is momentarily fit for parallel measurement.

On a shared control plane the load fluctuates by an order of magnitude.  A scaling
run started during a busy window produces numbers that describe the queue, not the
code (see :func:`tools.scaling.runner.host_precheck`).  This wrapper polls the host
fitness and starts the full measurement only when the precheck passes, then writes
the ledger and exits.

    python -m tools.scaling.harvest --plan tools/scaling/configs/full.json \
        --out artifacts/scaling_ledger/results.json \
        --figdir artifacts/scaling_ledger/figures \
        --max-wait-min 90 --min-speedup 3.0

Exit codes: 0 ledger written; 3 host never became fit within ``--max-wait-min``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tools.scaling.report import make_figures, summary_markdown
from tools.scaling.runner import build_ledger, evaluate_acceptance, host_precheck

__all__ = ["main"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--figdir", default=None)
    ap.add_argument("--max-wait-min", type=float, default=90.0)
    ap.add_argument("--poll-s", type=float, default=120.0)
    ap.add_argument("--min-speedup", type=float, default=3.0)
    ap.add_argument("--n-max", type=int, default=4)
    args = ap.parse_args(argv)

    deadline = time.time() + 60.0 * args.max_wait_min
    attempt = 0
    while True:
        attempt += 1
        chk = host_precheck(n_max=args.n_max, min_speedup=args.min_speedup)
        got = chk["speedup"][str(args.n_max)]
        print(
            f"[{time.strftime('%H:%M:%S')}] attempt {attempt}: "
            f"speedup@{args.n_max}={got:.2f} load={chk.get('load_1_5_15')} -> {'FIT' if chk['host_ok'] else 'busy'}",
            flush=True,
        )
        if chk["host_ok"]:
            break
        if time.time() >= deadline:
            print("host never became fit within the wait budget", flush=True)
            return 3
        time.sleep(args.poll_s)

    plan = json.loads(Path(args.plan).read_text())
    print("host fit -> running the full ledger", flush=True)
    ledger = build_ledger(plan, progress=lambda m: print(m, flush=True), host_check=chk)
    figures = make_figures(ledger, Path(args.figdir)) if args.figdir else []
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


if __name__ == "__main__":
    sys.exit(main())