#!/usr/bin/env python3
"""Figure for the capability evidence reconciliation audit (matplotlib, Agg)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = Path("artifacts/audit/capability_evidence_reconcile.json")
OUT_DIR = Path("artifacts/audit")

COLOR = {
    "SUBSTANTIATED": "#2b8a3e",
    "UNSUBSTANTIATED": "#c92a2a",
    "CONTRADICTED": "#862e9c",
    "PARTIAL": "#e8590c",
    "SUB-BAR_GRADE": "#adb5bd",
}


def main() -> None:
    res = json.loads(SRC.read_text(encoding="utf-8"))
    rows = [r for r in res["rows"] if r["tier"] == "NUMERIC"][::-1]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.2),
                                   gridspec_kw={"width_ratios": [2.4, 1.0]})

    names = [r["capability"] for r in rows]
    vals = [1 if r["status"] == "SUBSTANTIATED" else 0 for r in rows]
    colors = [COLOR.get(r["status"], "#495057") for r in rows]
    ax1.barh(names, [1] * len(rows), color=colors, alpha=0.85, height=0.68)
    for i, r in enumerate(rows):
        n = r["pass_entries"]
        ax1.text(0.02, i, f"{r['status']}  (pass={n}, fail={r['fail_entries']})",
                 va="center", ha="left", fontsize=8.5, color="white")
    ax1.set_xlim(0, 1)
    ax1.set_xticks([])
    ax1.set_title("NUMERIC capability evidence bar ($n=%d$)" % len(rows), fontsize=10)
    ax1.tick_params(axis="y", labelsize=8)

    s = res["summary"]
    labels = ["NUMERIC\nREAL", "with independent\nadjudication", "OPERATIONAL\n(unadjudicated)"]
    values = [s["numeric_real"], s["numeric_substantiated"], s["operational"]]
    bars = ax2.bar(labels, values, color=["#1c7ed6", "#2b8a3e", "#f59f00"], alpha=0.9)
    for b, v in zip(bars, values):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v), ha="center", fontsize=10)
    cov = s["numeric_evidence_coverage"]
    ax2.set_title(f"numeric evidence coverage = {cov:.1%}", fontsize=10)
    ax2.set_ylabel("capabilities")
    ax2.set_ylim(0, max(values) + 2)

    fig.suptitle("Capability grade vs credit-ledger reconciliation "
                 f"({res['generated_at']})", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = OUT_DIR / "capability_evidence_coverage.png"
    fig.savefig(out, dpi=150)
    print(out)


if __name__ == "__main__":
    main()