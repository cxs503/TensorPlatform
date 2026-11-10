"""Build the consistency report (and its figure) from the arm records on disk.

    python scripts/consistency/build_report.py \
        --gate-config config/consistency/bluff-body-2d-re100.json \
        --arms artifacts/consistency/bluff-body-2d-re100/arm_*.json

Writes ``consistency_report.json`` (digest inside) and ``consistency_report.png``
next to the arm records.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.services.consistency import ConsistencyGate, verify_report, write_report  # noqa: E402


def richardson(values, factor):
    """Grid extrapolation x(h->0) = x_f + (x_f - x_c)/(r^p - 1) with p from two levels.

    ``values`` are ordered coarse -> fine.  With three levels the observed order is
    estimated from the two differences, which is reported alongside the
    extrapolated value so a reader can judge whether the series is in its
    asymptotic range.
    """
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return None, None, None
    if len(vals) == 2:
        d1 = vals[-1] - vals[-2]
        ext = vals[-1] + d1 / (factor - 1.0)
        return ext, 1.0, None
    d1 = vals[-2] - vals[-3]
    d2 = vals[-1] - vals[-2]
    if abs(d2) < 1e-14 or abs(d1) < 1e-14:
        return vals[-1], None, None
    ratio = d1 / d2
    p = float(np.log(abs(ratio)) / np.log(factor)) if ratio > 0 else None
    if not p or p <= 0:
        p = 1.0
    ext = vals[-1] + d2 / (factor ** p - 1.0)
    return ext, p, ratio


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-config", required=True)
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--figure", default=None)
    args = ap.parse_args(argv)

    gate = ConsistencyGate.from_path(args.gate_config)
    paths = []
    for pattern in args.arms:
        paths.extend(sorted(glob.glob(pattern)))
    arms = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sorted(set(paths))]
    if not arms:
        raise SystemExit("no arm records found")

    report = gate.run(arms, run_meta={"arm_files": sorted(set(paths))})

    # grid refinement: for every scalar dimension, extrapolate the arms that
    # share a solver family (the family is the only dimension along which a
    # refinement sequence is meaningful)
    refinement = {}
    for dim in report["dimensions"]:
        if dim["dimension"] != "scalar":
            continue
        by_family = {}
        for aid, val in dim["values"].items():
            fam = next(a["solver_family"] for a in report["arms"] if a["arm_id"] == aid)
            by_family.setdefault(fam, []).append((aid, val))
        summary = {}
        for fam, items in by_family.items():
            if len(items) < 2:
                continue
            ordered = sorted(items, key=lambda kv: kv[0])
            ext, p, ratio = richardson([v for _, v in ordered], 1.5)
            summary[fam] = {"arms": [a for a, _ in ordered],
                            "values": [v for _, v in ordered],
                            "extrapolated": ext, "observed_order": p,
                            "difference_ratio": ratio}
        if summary:
            refinement[dim["name"]] = summary
    report["grid_refinement"] = refinement

    out_dir = Path(args.out_dir) if args.out_dir else Path(paths[0]).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "consistency_report.json"
    digest = write_report(report, report_path)
    check = verify_report(report_path)

    fig_path = Path(args.figure) if args.figure else out_dir / "consistency_report.png"
    make_figure(report, arms, fig_path)

    print(json.dumps({"report": str(report_path), "sha256": digest,
                      "digest_ok": check["ok"], "verdict": report["verdict"],
                      "figure": str(fig_path),
                      "deviation_summary": report["deviation_summary"]}, indent=2))
    return 0


def make_figure(report, arms, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    colours = ["#1b6ca8", "#c2185b", "#2e7d32", "#6a1b9a", "#ef6c00"]

    # (a) surface pressure distribution per arm
    ax = axes[0]
    for k, arm in enumerate(arms):
        prof = (arm.get("profiles") or {}).get("cp_surface")
        if not prof:
            continue
        ax.plot(prof["coordinate"], prof["value"], "-", lw=1.6,
                color=colours[k % len(colours)], label=arm["arm_id"])
    ax.set_xlabel("arc length from the upstream stagnation point / D")
    ax.set_ylabel("$C_p$")
    ax.set_title("(a) surface pressure distribution per arm")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax.invert_yaxis()

    # (b) force coefficient per arm against the injected reference band
    ax = axes[1]
    dim = next((d for d in report["dimensions"]
                if d["dimension"] == "scalar" and d["name"] == "cd"), None)
    if dim:
        ids = [a for a in dim["values"] if dim["values"][a] is not None]
        vals = [dim["values"][a] for a in ids]
        ax.bar(range(len(ids)), vals, color=[colours[i % len(colours)] for i in range(len(ids))])
        ax.set_xticks(range(len(ids)))
        ax.set_xticklabels(ids, rotation=20, ha="right", fontsize=8)
        ref = dim.get("reference")
        if ref:
            ax.axhline(ref["value"], color="k", ls="--", lw=1.2,
                       label=f"reference {ref['value']:.3f}")
            ax.axhspan(ref["value"] * (1 - ref["tolerance"]),
                       ref["value"] * (1 + ref["tolerance"]),
                       color="k", alpha=0.08, label="reference band")
        ax.set_ylabel("streamwise force coefficient")
        ax.set_title(f"(b) force coefficient, max spread {100*dim['max_dev']:.1f} %")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")

    # (c) energy ledger: input power vs dissipation per arm
    ax = axes[2]
    edim = next((d for d in report["dimensions"] if d["dimension"] == "energy"), None)
    if edim:
        ids = list(edim["per_arm"])
        pin = [edim["per_arm"][a]["terms"].get(edim["input_term"]) or np.nan for a in ids]
        dis = [edim["per_arm"][a]["terms"].get(edim["dissipation_term"]) or np.nan for a in ids]
        x = np.arange(len(ids))
        ax.bar(x - 0.2, pin, width=0.4, label="input power", color="#1b6ca8")
        ax.bar(x + 0.2, dis, width=0.4, label="dissipation", color="#c2185b")
        ax.set_xticks(x)
        ax.set_xticklabels(ids, rotation=20, ha="right", fontsize=8)
        for i, a in enumerate(ids):
            r = edim["per_arm"][a]["closure_residual"]
            if r is not None:
                ax.text(i, max(pin[i], dis[i]) * 1.03, f"{100*r:+.0f}%",
                        ha="center", fontsize=7)
        ax.set_ylabel("non-dimensional power (rho=U=D=1)")
        verdict = edim["verdict"]
        ax.set_title(f"(c) energy ledger (closure {verdict})")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")

    fig.suptitle(f"cross-solver consistency gate -- verdict: {report['verdict']}",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())