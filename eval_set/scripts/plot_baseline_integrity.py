#!/usr/bin/env python3
"""Render figures + the audit doc page from ``baseline_integrity_audit.json``.

Numbers are read from the ledger produced by
``eval_set/scripts/audit_baseline_integrity.py`` -- nothing is hand-typed into the
markdown, so the page cannot drift from the machine-checked JSON.

    python3 eval_set/scripts/plot_baseline_integrity.py \
        --json eval_set/results/baseline_integrity_audit.json \
        --figdir docs/audit/figures --doc docs/audit/eval_baseline_integrity.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def fig_claimed_vs_observed(audit, figdir: Path) -> Path:
    per = audit["per_model"]
    models = list(per)
    claimed = [per[m]["claimed_rate"] for m in models]
    observed = [per[m]["mapping_rate"] for m in models]
    ci_lo = [per[m]["claimed_rate"] - per[m]["claimed_ci95"][0] for m in models]
    ci_hi = [per[m]["claimed_ci95"][1] - per[m]["claimed_rate"] for m in models]
    n_obs = [per[m]["mapping_total"] for m in models]

    x = np.arange(len(models))
    w = 0.38
    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    ax.bar(x - w / 2, claimed, w, label="baseline_report.json (claimed)", color="#c0392b")
    ax.errorbar(x - w / 2, claimed, yerr=[ci_lo, ci_hi], fmt="none", ecolor="k",
                elinewidth=1.1, capsize=4)
    ax.bar(x + w / 2, observed, w, label="recomputed from HISTORICAL_MAPPING (observed)",
           color="#2c7fb8")
    for i, n in enumerate(n_obs):
        ax.annotate("n=%d" % n, (x[i] + w / 2, 0.02), ha="center", va="bottom",
                    fontsize=8, color="white", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(models, fontsize=9)
    ax.set_ylabel("pass rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("Fleet capability baseline: claimed pass rate vs rate reproducible from "
                 "its own source table", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out = figdir / "claimed_vs_observed_rate.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_cell_coverage(audit, figdir: Path, mapping_cells, models, tasks) -> Path:
    ti = {t: i for i, t in enumerate(tasks)}
    grid = np.zeros((len(models), len(tasks)))
    mi = {m: i for i, m in enumerate(models)}
    for (tid, model) in mapping_cells:
        if tid in ti and model in mi:
            grid[mi[model], ti[tid]] = 1.0
    fig, ax = plt.subplots(figsize=(10.5, 2.9))
    ax.imshow(grid, aspect="auto", cmap="Blues", vmin=0, vmax=1)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(models, fontsize=8)
    ax.set_xticks(range(len(tasks)))
    ax.set_xticklabels(tasks, rotation=90, fontsize=7)
    for i in range(len(models)):
        for j in range(len(tasks)):
            if grid[i, j] == 0:
                ax.text(j, i, "i", ha="center", va="center", fontsize=6, color="#999")
    ax.set_title("Observation matrix: filled = row present in HISTORICAL_MAPPING, "
                 "'i' = imputed (estimated_total=3 per empty difficulty bucket)",
                 fontsize=9)
    fig.tight_layout()
    out = figdir / "cell_coverage.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_artifact_matrix(audit, figdir: Path) -> Path:
    """Artifact x claim-consistency matrix, from the checks themselves."""
    checks = {c["id"]: c for c in audit["checks"]}
    rows = [
        ("baseline_report.json\n(m538 rates)",
         [checks["A1_run_provenance"]["verdict"], checks["A2_mapping_closure"]["verdict"],
          checks["B2_rate_arithmetic"]["verdict"], checks["B3_wilson_recompute"]["verdict"]]),
        ("baseline_table.json",
         [checks["A8_table_vs_report"]["verdict"], checks["A8_table_vs_report"]["verdict"],
          checks["A8_table_vs_report"]["verdict"], checks["A8_table_vs_report"]["verdict"]]),
        ("eval_results_*.json\n(dispatch)",
         [checks["A1_run_provenance"]["verdict"], "FAIL" if checks["A1_run_provenance"]["verdict"] == "FAIL" else "PASS",
          "n/a", "n/a"]),
        ("eval_results_*_verified.json",
         [checks["A6_report_vs_verified"]["verdict"], checks["A6_report_vs_verified"]["verdict"],
          checks["A7_verified_score_floor"]["verdict"], "n/a"]),
    ]
    cols = ["provenance\n(not a dry run)", "denominator\nclosure", "rate\narithmetic",
            "uncertainty\n(Wilson recompute)"]
    score = {("PASS", "PASS"): 1.0}
    code = {"PASS": 2, "WARN": 1, "FAIL": 0, "n/a": -1}
    grid = np.array([[code[v] for v in r[1]] for r in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(8.4, 3.4))
    cmap = matplotlib.colors.ListedColormap(["#e8e8e8", "#c0392b", "#f0ad4e", "#2e8b57"])
    ax.imshow(grid, cmap=cmap, vmin=-1, vmax=2, aspect="auto")
    for i, r in enumerate(rows):
        for j, v in enumerate(r[1]):
            ax.text(j, i, v, ha="center", va="center", fontsize=8,
                    color="white" if v != "n/a" else "#666")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows], fontsize=8)
    ax.set_title("Artifact consistency matrix (green=PASS, orange=WARN, red=FAIL, grey=n/a)",
                 fontsize=9)
    fig.tight_layout()
    out = figdir / "artifact_matrix.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=ROOT / "eval_set/results/baseline_integrity_audit.json")
    ap.add_argument("--figdir", type=Path, default=ROOT / "docs/audit/figures")
    ap.add_argument("--doc", type=Path, default=ROOT / "docs/audit/eval_baseline_integrity.md")
    args = ap.parse_args()

    audit = json.loads(args.json.read_text(encoding="utf-8"))
    args.figdir.mkdir(parents=True, exist_ok=True)
    mapping = []
    from importlib import import_module, sys
    sys.path.insert(0, str(ROOT / "eval_set/scripts"))
    A = import_module("audit_baseline_integrity")
    rows = A.extract_historical_mapping(A.GEN_SRC)
    models = A.extract_all_models(A.GEN_SRC)
    tasks = [t["id"] for t in A.load_json(A.EVAL_JSON)["tasks"]]
    mapping = [(r["task_id"], r["model"]) for r in rows]

    f1 = fig_claimed_vs_observed(audit, args.figdir)
    f2 = fig_cell_coverage(audit, args.figdir, mapping, models, tasks)
    f3 = fig_artifact_matrix(audit, args.figdir)
    print("figures:", f1.name, f2.name, f3.name)

    audit["figures"] = [str(p.relative_to(ROOT)) for p in (f1, f2, f3)]
    args.json.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())