#!/usr/bin/env python3
"""plan 258 门证据审计图：判定分布 + 负控检出矩阵（数据全部读 summary.json）。"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gate_contract"


def main() -> int:
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    verify_all = json.loads((OUT / "verify_all.json").read_text(encoding="utf-8"))
    nc = json.loads((OUT / "negcontrol_summary.json").read_text(encoding="utf-8"))

    nodes = [g["gate_id"].split("-")[-1] for g in verify_all["gates"]]
    colors = {"PASS": "#2e7d32", "FAIL": "#c62828", "WITHHELD": "#f9a825"}
    fig, ax = plt.subplots(figsize=(9, 4.2))
    bottoms = [0] * len(nodes)
    for grade in ("PASS", "FAIL", "WITHHELD"):
        vals = [1 if g["grade"] == grade else 0 for g in verify_all["gates"]]
        ax.bar(nodes, vals, bottom=bottoms, color=colors[grade], label=grade, width=0.62)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylim(0, 1.4)
    ax.set_ylabel("gate verdict (1 = grade)")
    ax.set_title("plan 258: gate.json re-computed verdicts "
                 f"(PASS {summary['C1_backfill']['grades']['PASS']} / "
                 f"FAIL {summary['C1_backfill']['grades']['FAIL']} / "
                 f"WITHHELD {summary['C1_backfill']['grades']['WITHHELD']})")
    ax.legend(loc="upper right", ncol=3, fontsize=8)
    ax.tick_params(axis="x", labelrotation=30)
    fig.tight_layout()
    fig.savefig(OUT / "fig_verdicts.png", dpi=140)
    plt.close(fig)

    classes = sorted(nc["by_class"])
    codes = sorted({c for case in nc["cases"] for c in case["codes"]} | {"PASS"})
    matrix = []
    for cls in classes:
        row = []
        for code in codes:
            n = sum(1 for case in nc["cases"] if case["class"] == cls and code in case["codes"])
            if code == "PASS":
                n = sum(1 for case in nc["cases"] if case["class"] == cls and case["grade"] == "PASS")
            row.append(n)
        matrix.append(row)
    fig, ax = plt.subplots(figsize=(9, 4))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(codes)), codes, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(classes)), classes, fontsize=8)
    for i in range(len(classes)):
        for j in range(len(codes)):
            ax.text(j, i, str(matrix[i][j]), ha="center", va="center", fontsize=7)
    ax.set_title(f"negative controls: {nc['n_cases']} fake gates, "
                 f"false-pass = {nc['false_pass']} (rate {nc['false_pass_rate']:.3f})")
    fig.colorbar(im, ax=ax, label="cases")
    fig.tight_layout()
    fig.savefig(OUT / "fig_negcontrol_matrix.png", dpi=140)
    plt.close(fig)
    print("figures ->", OUT / "fig_verdicts.png", OUT / "fig_negcontrol_matrix.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
