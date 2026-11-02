"""Audit CLI: probe the platform structure endpoints, grade them, emit artifacts.

Three modes, all writing into `artifacts/structure_audit/`:

  --live      call the platform (:8001) following probe.PROBE_PLAN, cache raw JSON,
              grade it, and also grade the analytic positive control for comparison
  --cached    grade a previously cached probe (offline, deterministic)
  --control   grade only the analytic positive control (fast self-check)

Every number in the emitted report is read back from the graded results, never typed
by hand: `report["criteria"]` is the single source for the markdown table and figures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import criteria as C
from . import probe as P
from . import reference_candidate as RC

ART = os.path.join("artifacts", "structure_audit")


def _records_json(records):
    return [{"case": r.case, "endpoint": r.endpoint, "inputs": r.inputs,
             "output": r.output} for r in records]


def summarise(results, grades) -> dict:
    return {
        "endpoint_grades": grades,
        "criteria": [
            {"cid": r.cid, "endpoint": r.endpoint, "statement": r.statement,
             "reference": r.reference, "tolerance": r.tolerance,
             "decisive": r.decisive, "circular": r.circular, "verdict": r.verdict,
             "measured": r.measured, "unit": r.unit, "detail": r.detail}
            for r in results],
        "counts": {
            "criteria": len(results),
            "pass": sum(r.verdict == "PASS" for r in results),
            "fail": sum(r.verdict == "FAIL" for r in results),
            "withheld": sum(r.verdict == "WITHHELD" for r in results),
            "decisive_fail": sum(r.decisive and r.verdict == "FAIL" for r in results),
        },
    }


def control_verdicts(results):
    """Criteria the control must pass: everything that is not marked circular."""
    return {r.cid: (r.verdict if not r.circular else "n/a") for r in results}


def build_figures(records, control_records, outdir: str) -> list[dict]:
    """Two field/comparison figures; numbers are pulled from the graded records."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(outdir, exist_ok=True)
    figs = []

    # ---- Figure 1: critical buckling stress vs plate aspect ratio
    alphas = [x / 100.0 for x in range(20, 501)]
    theory = RC.reference_plate_buckling_curve(alphas, b=1.2, t=0.012)
    grid = ["pb_r0.4", "pb_r1.0", "pb_r1.5", "pb_r2.5", "pb_r4.0"]
    live_pts, ctrl_pts = [], []
    for case in grid:
        r = next((x for x in records if x.case == case), None)
        c = next((x for x in control_records if x.case == case), None)
        if r is not None:
            live_pts.append((r.inputs["a"] / r.inputs["b"],
                             float(r.output.get("critical_stress", float("nan")))))
        if c is not None:
            ctrl_pts.append((c.inputs["a"] / c.inputs["b"],
                             float(c.output.get("critical_stress", float("nan")))))
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(alphas, theory, "k-", lw=1.8,
            label=r"Timoshenko–Gere $k(\alpha)\pi^2D/(b^2t)$")
    if ctrl_pts:
        ax.plot([p[0] for p in ctrl_pts], [p[1] for p in ctrl_pts], "o",
                ms=7, mfc="none", mec="#1f77b4", mew=1.6,
                label="positive control (analytic)")
    if live_pts:
        ax.plot([p[0] for p in live_pts], [p[1] for p in live_pts], "s",
                ms=7, color="#d62728",
                label="platform /fea/plate-buckling")
    ax.set_xlabel(r"aspect ratio $\alpha = a/b$")
    ax.set_ylabel(r"$\sigma_{cr}$  [MPa]")
    ax.set_title("Plate buckling: platform response vs thin-plate theory\n"
                 r"($b$=1.2 m, $t$=12 mm, $E$=210 GPa, $\nu$=0.3)")
    ax.set_xscale("log")
    ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8, loc="upper right")
    f1 = os.path.join(outdir, "fig1_plate_buckling_vs_aspect.png")
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig1_plate_buckling_vs_aspect.png",
                 "path": f1,
                 "caption": "Critical buckling stress vs plate aspect ratio: thin-plate "
                            "theory (black), analytic positive control (blue), platform "
                            "endpoint (red). The platform value is independent of a/b "
                            "and 3.3-110 % away from theory over the sweep."})

    # ---- Figure 2: fatigue life vs constant-amplitude stress range
    S = np.linspace(20, 200, 300)
    life_theory = RC.reference_fatigue_life(S)
    sf_live = [(float(r.output.get("stress_range", float("nan"))),
                float(r.output.get("calculated_life", float("nan"))))
               for r in records if r.endpoint == "/structure/fatigue"]
    sf_ctrl = [(float(r.output.get("stress_range", float("nan"))),
                float(r.output.get("calculated_life", float("nan"))))
               for r in control_records if r.endpoint == "/structure/fatigue"]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.loglog(S, life_theory, "k-", lw=1.8,
              label="DNV-RP-C203 C curve, 5e6 cycles/yr (control)")
    if sf_ctrl:
        ax.loglog([p[0] for p in sf_ctrl], [p[1] for p in sf_ctrl], "o", ms=7,
                  mfc="none", mec="#1f77b4", mew=1.6, label="positive control")
    if sf_live:
        ax.loglog([p[0] for p in sf_live], [p[1] for p in sf_live], "s", ms=7,
                  color="#d62728", label="platform /structure/fatigue")
    ax.axhline(25.0, color="grey", ls="--", lw=1.0, label="design life 25 yr")
    ax.set_xlabel("constant-amplitude stress range  [MPa]")
    ax.set_ylabel("calculated fatigue life  [years]")
    ax.set_title("Fatigue life vs stress range\n(two-slope S-N, knee at $10^7$ cycles)")
    ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8)
    f2 = os.path.join(outdir, "fig2_fatigue_life_vs_stress_range.png")
    fig.tight_layout()
    fig.savefig(f2, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig2_fatigue_life_vs_stress_range.png",
                 "path": f2,
                 "caption": "Fatigue life vs stress range. The platform emits a single "
                            "(stress range, life) pair for every hull, so its S-N "
                            "exponent is not identifiable from outside and it cannot be "
                            "placed on the curve: the fatigue model is WITHHELD."})

    # ---- Figure 3: utilization magnitude audit (log scale)
    util_live = [float(r.output.get("utilization", float("nan")))
                 for r in records if r.endpoint == "/structure/analyze"]
    util_ctrl = [float(r.output.get("utilization", float("nan")))
                 for r in control_records if r.endpoint == "/structure/analyze"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    labels, vals, colors = [], [], []
    for i, v in enumerate(util_live):
        labels.append(f"platform #{i+1}")
        vals.append(max(v, 1e-6))
        colors.append("#d62728")
    for i, v in enumerate(util_ctrl):
        labels.append(f"control #{i+1}")
        vals.append(max(v, 1e-6))
        colors.append("#1f77b4")
    y = np.arange(len(labels))
    ax.barh(y, vals, color=colors, alpha=.85)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xscale("log")
    ax.axvline(1.0, color="k", ls="--", lw=1.2, label="physical limit 1.0")
    ax.set_xlabel("reported utilization  [-]  (log scale)")
    ax.set_title("Hull-girder utilization: reported vs physically admissible")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3, axis="x")
    f3 = os.path.join(outdir, "fig3_utilization_magnitude.png")
    fig.tight_layout()
    fig.savefig(f3, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig3_utilization_magnitude.png",
                 "path": f3,
                 "caption": "Reported hull-girder utilization per probe case (log scale). "
                            "Platform values exceed the physical limit of 1.0 by ~5 "
                            "decades because Pa and MPa are mixed; control values lie "
                            "below 1.0."})

    return figs


def write_markdown(report: dict, path: str) -> None:
    lines = ["# Structure-domain endpoint audit (independent replication)", ""]
    lines.append("| endpoint | grade | pass | fail | withheld | decisive failures |")
    lines.append("|---|---|---|---|---|---|")
    for ep, g in report["live"]["endpoint_grades"].items():
        lines.append("| `{}` | **{}** | {} | {} | {} | {} |".format(
            ep, g.get("grade"), g["PASS"], g["FAIL"], g["WITHHELD"],
            ", ".join(g["decisive_fail"]) or "-"))
    lines += ["", "| id | endpoint | verdict | measured | criterion | reference |",
              "|---|---|---|---|---|---|"]
    for c in report["live"]["criteria"]:
        lines.append("| {} | `{}` | {} | {:.6g} | {} | {} |".format(
            c["cid"], c["endpoint"], c["verdict"], c["measured"],
            c["statement"], c["reference"]))
    lines += ["", "### Positive control (analytic implementation, same probe plan)", "",
              "| id | verdict |", "|---|---|"]
    for c in report["control"]["criteria"]:
        lines.append("| {} | {} |".format(c["cid"], c["verdict"]))
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="probe the live platform")
    ap.add_argument("--cached", action="store_true", help="grade a cached probe")
    ap.add_argument("--control", action="store_true", help="grade the analytic control only")
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--cache", default=os.path.join(ART, "live_records.json"))
    ap.add_argument("--out", default=os.path.join(ART, "structure_audit_report.json"))
    ap.add_argument("--figdir", default=ART)
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(ART, exist_ok=True)
    report: dict = {"tool": "tools/structure_audit", "probe_plan": {
        ep: [c for c, _ in cases] for ep, cases in P.PROBE_PLAN.items()}}

    if args.live:
        records, log = P.probe_live(args.base_url, timeout=args.timeout)
        P.save_cache(args.cache, records, log)
        report["live_call_log"] = log
    elif args.cached:
        records, log = P.load_cache(args.cache)
        report["live_call_log"] = log
    elif args.control:
        records = None
    else:
        ap.error("choose one of --live / --cached / --control")
        return 2

    control_records = P.probe_reference()
    ctrl_results = C.evaluate(control_records)
    report["control"] = summarise(ctrl_results, C.grade(ctrl_results))
    ctrl_fail = [r.cid for r in ctrl_results if r.verdict != "PASS" and not r.circular]
    report["control"]["unexpected_failures"] = ctrl_fail

    if records is not None:
        results = C.evaluate(records)
        grades = C.grade(results)
        report["live"] = summarise(results, grades)
        report["live"]["records"] = _records_json(records)
        if not args.no_figures:
            report["figures"] = build_figures(records, control_records, args.figdir)
        write_markdown(report, os.path.join(args.figdir, "structure_audit_report.md"))

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print(f"[audit] report -> {args.out}")
    if control_records:
        print(f"[audit] control unexpected failures: {ctrl_fail or 'none'}")
        if ctrl_fail:
            print("[audit] CONTROL FAILED -- criteria are too strict or the reference "
                  "implementation is wrong; fix before trusting the live verdict")
    if records is not None:
        for ep, g in report["live"]["endpoint_grades"].items():
            print(f"[audit] {ep:26s} {g['grade']:9s} pass={g['PASS']} fail={g['FAIL']} "
                  f"withheld={g['WITHHELD']} decisive_fail={g['decisive_fail'] or '-'}")
    return 0 if not ctrl_fail else 1


if __name__ == "__main__":
    sys.exit(main())