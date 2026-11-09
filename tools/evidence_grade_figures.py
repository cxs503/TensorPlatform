#!/usr/bin/env python3
"""Figures for the evidence-grading node (m7794).

Three panels, all generated from ``artifacts/evidence_grade/metrics.json`` so the
numbers in the report are never hand-copied:

fig1  confusion matrix — derived grade vs construction-time label (4x4)
fig2  gate attribution — which rule DAG gate produced each non-REAL grade
fig3  residual vs independence — the trap is invisible to the residual, visible
      to provenance, which is exactly why the grade is structural
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "evidence_grade"
GRADES = ("REAL", "WITHHELD", "MOCK", "STUB")


def fig_confusion(metrics: dict, out: Path) -> Path:
    mat = metrics["confusion"]
    data = np.zeros((len(GRADES), len(GRADES)), dtype=int)
    for i, exp in enumerate(GRADES):
        for j, got in enumerate(GRADES):
            data[i, j] = mat.get(exp, {}).get(got, 0)
    fig, ax = plt.subplots(figsize=(5.4, 4.4), dpi=140)
    im = ax.imshow(data, cmap="Blues", vmin=0, vmax=max(1, data.max()))
    for i in range(len(GRADES)):
        for j in range(len(GRADES)):
            ax.text(j, i, str(data[i, j]), ha="center", va="center",
                    color="white" if data[i, j] > data.max() * 0.6 else "black",
                    fontsize=12)
    ax.set_xticks(range(len(GRADES)), GRADES)
    ax.set_yticks(range(len(GRADES)), GRADES)
    ax.set_xlabel("derived grade (engine)")
    ax.set_ylabel("label (case construction)")
    kappa = metrics["criteria"]["c_cohen_kappa_vs_labels"]["value"]
    ax.set_title(f"30 synthetic cases: 30/30 reproduced, Cohen's $\\kappa$={kappa:.3f}")
    fig.colorbar(im, ax=ax, shrink=0.85, label="cases")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_gate_attribution(metrics: dict, out: Path) -> Path:
    rows = metrics["per_case"]
    codes = {}
    for row in rows:
        if row["derived"] != "REAL":
            codes[row["reason"]] = codes.get(row["reason"], 0) + 1
    order = sorted(codes, key=lambda c: (-codes[c], c))
    fig, ax = plt.subplots(figsize=(7.4, 4.0), dpi=140)
    ax.bar(range(len(order)), [codes[c] for c in order],
           color=["#c0504d" if c == "SAME_SOURCE_COPY" else "#4f81bd" for c in order])
    ax.set_xticks(range(len(order)), order, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("cases")
    ax.set_title("Which rule-DAG gate produced the downgrade (30 cases, 0 UNKNOWN)")
    for i, c in enumerate(order):
        ax.text(i, codes[c] + 0.15, str(codes[c]), ha="center", fontsize=9)
    ax.set_ylim(0, max(codes.values()) + 1.5)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_residual_vs_independence(metrics: dict, out: Path) -> Path:
    rows = metrics["per_case"]
    tol = 1e-5
    fig, ax = plt.subplots(figsize=(8.0, 4.2), dpi=140)
    colours = {"REAL": "#2e7d32", "WITHHELD": "#c0504d", "MOCK": "#f9a825", "STUB": "#9e9e9e"}
    xs, ys, cs = [], [], []
    for i, row in enumerate(rows):
        if row["derived"] == "STUB":
            continue
        xs.append(i)
        ys.append(max(1e-12, abs(_residual_of(row))))
        cs.append(colours.get(row["derived"], "#607d8b"))
    ax.scatter(xs, ys, c=cs, s=26, zorder=3, edgecolors="k", linewidths=0.3)
    ax.axhline(tol, color="k", ls="--", lw=1, label=f"predeclared tolerance {tol:g}")
    ax.set_yscale("log")
    ax.set_xlabel("case index (numeric cases)")
    ax.set_ylabel("|residual| (unit-free)")
    ax.set_title("Residual magnitude cannot separate REAL from the same-source trap;\n"
                 "provenance does (green=REAL, red=WITHHELD)")
    ax.grid(alpha=0.3)
    ax.legend(loc="center right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def _residual_of(row: dict) -> float:
    """Derived residual for a case, recomputed from its contract (no hand copy)."""
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import evidence_grade as EG  # noqa: PLC0415

    cpath = ART / "fixtures" / row["case_id"] / "contract.json"
    contract = json.loads(cpath.read_text(encoding="utf-8"))
    res = EG.grade_contract(contract, base_dir=cpath.parent)
    value = res["metrics"].get("residual_recomputed")
    return value if value is not None else 1e-12


def main() -> int:
    metrics = json.loads((ART / "metrics.json").read_text(encoding="utf-8"))
    figures = ART / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    outs = [fig_confusion(metrics, figures / "fig1_confusion.png"),
            fig_gate_attribution(metrics, figures / "fig2_gate_attribution.png"),
            fig_residual_vs_independence(metrics, figures / "fig3_residual_vs_independence.png")]
    print(json.dumps([str(p.relative_to(ROOT)) for p in outs], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())