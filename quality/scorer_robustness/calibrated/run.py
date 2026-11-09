"""End-to-end runner for the p422w3-n12 scorer-robustness randomized trials.

Usage
-----
    python -m quality.scorer_robustness.calibrated.run \
        --out artifacts/m8374/scorer_robustness_trials

Produces
--------
``results.json``         full experiment payload (arms, trials, effect sizes, defense gate)
``acceptance.json``      criteria (a)-(e) verdicts with the numbers each was judged on
``per_trial.jsonl``      one line per paired trial (audit trail)
``fig1_dose_response.png``   flip rate + Cohen's d vs injected numeric deviation
``fig2_arm_effect_sizes.png`` flip rate and |delta S| per arm with criterion lines
``fig3_grade_transition.png`` grade-transition matrix at +-20 % fabrication
``fig4_v1_v2_comparison.png`` before/after: v1 vs v2 vs gap-0 ablation flip rates
``MANIFEST.json``        sha256 of every artifact + environment + command line
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from typing import List

from .acceptance import evaluate_acceptance
from .reports import SyntheticReportFactory
from .trials import PREREG, run_experiment
from .scorer_v2 import CalibratedScorer

GRADES = ["A", "B", "C", "D", "F"]


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def _figure_dose_response(results: dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    expl = results["exploratory_numeric"]
    mags = sorted(float(k) for k in expl)
    flip = [expl[f"{m:.3f}"]["flip_rate"] for m in mags]
    dvals = [abs(expl[f"{m:.3f}"]["cohens_d"]) for m in mags]

    fig, ax1 = plt.subplots(figsize=(7.2, 4.4))
    ax1.plot([m * 100 for m in mags], flip, "o-", color="#1f4e79", label="flip rate")
    ax1.set_xlabel("injected numeric deviation |Δ| (%)")
    ax1.set_ylabel("grade flip rate", color="#1f4e79")
    ax1.set_ylim(-0.03, 1.03)
    ax1.grid(alpha=0.3)
    for x, rule, lbl in [(5, 0.10, "crit ±5% <0.10"), (10, 0.30, "crit ±10% <0.30"), (20, 0.50, "crit ±20% >0.50")]:
        ax1.axvline(x, color="grey", ls=":", lw=1)
        ax1.annotate(lbl, (x, 1.0), rotation=90, fontsize=7, va="top", ha="right", color="grey")
    ax1.axhline(PREREG["flip_review_threshold"], color="crimson", ls="--", lw=1,
                label=f"human-review gate = {PREREG['flip_review_threshold']:.2f}")
    ax2 = ax1.twinx()
    ax2.plot([m * 100 for m in mags], dvals, "s--", color="#c55a11", label="|Cohen's d|")
    ax2.set_ylabel("|Cohen's d| (pooled)", color="#c55a11")
    lines = ax1.get_lines() + ax2.get_lines()
    ax1.legend(lines, [ln.get_label() for ln in lines], fontsize=7, loc="center right")
    ax1.set_title("v2 scorer dose-response to numeric fabrication")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _figure_arm_effects(results: dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arms = results["arms"]
    order = [
        ("negative", arms["negative"]),
        ("rhetorical", arms["rhetorical"]),
        ("citation", arms["citation"]),
        ("numeric@0.05", arms["numeric@0.05"]),
        ("numeric@0.10", arms["numeric@0.10"]),
        ("numeric@0.20", arms["numeric@0.20"]),
        ("positive(x3)", arms["positive"]),
    ]
    labels = [o[0] for o in order]
    flips = [o[1]["flip_rate"] for o in order]
    deltas = [o[1]["delta_s"] for o in order]
    ds = [o[1]["cohens_d"] for o in order]

    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.9))
    x = range(len(labels))
    axes[0].bar(x, flips, color="#1f4e79")
    axes[0].axhline(PREREG["flip_review_threshold"], color="crimson", ls="--", lw=1)
    axes[0].set_title("flip rate (dashed = review gate)")
    axes[0].set_ylim(0, 1.05)
    axes[1].bar(x, deltas, color="#548235")
    axes[1].axhline(0.10, color="grey", ls=":", lw=1)
    axes[1].axhline(0.05, color="grey", ls=":", lw=1)
    axes[1].set_title("|ΔS| (dotted = criteria 0.10 / 0.05)")
    axes[2].bar(x, ds, color="#c55a11")
    axes[2].axhline(0.0, color="black", lw=0.8)
    axes[2].set_title("Cohen's d (pooled)")
    for ax in axes:
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
        ax.grid(alpha=0.25, axis="y")
    fig.suptitle("randomized paired trials, n=%d reports per arm" % results["n_reports"], fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _figure_transitions(results: dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    trials = results["arms"]["numeric@0.20"]["trials"]
    mat = np.zeros((len(GRADES), len(GRADES)))
    for t in trials:
        i = GRADES.index(t["grade_original"])
        j = GRADES.index(t["grade_intervened"])
        mat[i][j] += 1
    fig, ax = plt.subplots(figsize=(4.8, 4.2))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(GRADES)))
    ax.set_xticklabels(GRADES)
    ax.set_yticks(range(len(GRADES)))
    ax.set_yticklabels(GRADES)
    ax.set_xlabel("grade after ±20% fabrication")
    ax.set_ylabel("grade before (honest report)")
    for i in range(len(GRADES)):
        for j in range(len(GRADES)):
            if mat[i][j]:
                ax.text(j, i, int(mat[i][j]), ha="center", va="center", fontsize=8,
                        color="white" if mat[i][j] > mat.max() * 0.6 else "black")
    fig.colorbar(im, ax=ax, label="count")
    ax.set_title("grade transition matrix (±20% arm)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _figure_v1_v2(results: dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    arms = ["negative", "rhetorical", "citation", "numeric@0.05", "numeric@0.10"]
    labels = list(arms)
    v2 = results["arms"]
    v1 = results["baseline_v1"]["arms"]
    abl = results.get("ablation_v2_gap0", {}).get("arms", {})

    series = {
        "v1 (base)": [v1.get(a, {}).get("flip_rate", 0.0) for a in arms],
        "v2 gap-0 ablation": [abl.get(a, {}).get("flip_rate", np.nan) for a in arms],
        "v2 (delivered)": [v2[a]["flip_rate"] for a in arms],
    }
    series["v1 (base)"].append(v1["numeric@0.20"]["flip_rate"])
    series["v2 gap-0 ablation"].append(np.nan)
    series["v2 (delivered)"].append(v2["numeric@0.20"]["flip_rate"])
    labels.append("numeric@0.20")

    x = np.arange(len(labels))
    width = 0.26
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    for i, (name, vals) in enumerate(series.items()):
        ax.bar(x + (i - 1) * width, vals, width, label=name)
    ax.axhline(PREREG["flip_review_threshold"], color="crimson", ls="--", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("grade flip rate")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.25, axis="y")
    ax.legend(fontsize=7)
    ax.set_title("before/after: v1 is blind to fabricated numbers (0 flips at ±20%)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _write_manifest(out_dir: str, files: List[str], argv: List[str]) -> None:
    import hashlib

    entries = {}
    for name in files:
        full = os.path.join(out_dir, name)
        if not os.path.exists(full):
            continue
        with open(full, "rb") as fh:
            entries[name] = {
                "sha256": hashlib.sha256(fh.read()).hexdigest(),
                "bytes": os.path.getsize(full),
            }
    manifest = {
        "node": "p422w3-n12-scorer-robustness",
        "commit": _git_head(),
        "argv": argv,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "artifacts": entries,
    }
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/m8374/scorer_robustness_trials")
    ap.add_argument("--n-reports", type=int, default=int(PREREG["n_reports"]))
    ap.add_argument("--seed", type=int, default=int(PREREG["seed"]))
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    factory = SyntheticReportFactory(seed=args.seed)
    reports = factory.build_cohort(args.n_reports)
    scorer = CalibratedScorer(seed=args.seed)

    results = run_experiment(reports, scorer=scorer, seed=args.seed)
    acceptance = evaluate_acceptance(results)

    payload = {
        "node": "p422w3-n12-scorer-robustness",
        "campaign": "p422w3",
        "repo": "TensorPlatform",
        "seed": args.seed,
        "n_reports": len(reports),
        "commit": _git_head(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "acceptance": acceptance,
        "results": results,
    }
    results_path = os.path.join(args.out, "results.json")
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "acceptance.json"), "w", encoding="utf-8") as fh:
        json.dump(acceptance, fh, indent=2, ensure_ascii=False)

    # Audit trail: one line per paired trial (all arms).
    with open(os.path.join(args.out, "per_trial.jsonl"), "w", encoding="utf-8") as fh:
        for arm_name, arm in results["arms"].items():
            for t in arm["trials"]:
                fh.write(json.dumps({"arm": arm_name, **t}, ensure_ascii=False) + "\n")

    figure_names: List[str] = []
    if not args.no_figures:
        _figure_dose_response(results, os.path.join(args.out, "fig1_dose_response.png"))
        _figure_arm_effects(results, os.path.join(args.out, "fig2_arm_effect_sizes.png"))
        _figure_transitions(results, os.path.join(args.out, "fig3_grade_transition.png"))
        _figure_v1_v2(results, os.path.join(args.out, "fig4_v1_v2_comparison.png"))
        figure_names = [
            "fig1_dose_response.png",
            "fig2_arm_effect_sizes.png",
            "fig3_grade_transition.png",
            "fig4_v1_v2_comparison.png",
        ]
    _write_manifest(
        args.out,
        ["results.json", "acceptance.json", "per_trial.jsonl"] + figure_names,
        [sys.executable, "-m", "quality.scorer_robustness.calibrated.run"] + list(sys.argv[1:]),
    )

    summary = {
        "all_criteria_pass": acceptance["all_criteria_pass"],
        "n_passed": acceptance["n_passed"],
        "n_criteria": acceptance["n_criteria"],
        "rhetorical_delta_s": results["arms"]["rhetorical"]["delta_s"],
        "citation_delta_s": results["arms"]["citation"]["delta_s"],
        "numeric_flip_rates": {
            k: v["flip_rate"] for k, v in results["arms"].items() if k.startswith("numeric@")
        },
        "negative_control_flip_rate": results["arms"]["negative"]["flip_rate"],
        "defense_gate_triggered": results["defense_gate"]["triggered_arms"],
        "out": args.out,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if acceptance["all_criteria_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())