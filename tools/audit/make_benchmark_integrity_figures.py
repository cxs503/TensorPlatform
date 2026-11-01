#!/usr/bin/env python3
"""生成基准真值完整性审计图（reproducible, 读的是审计产物 audit.json）。"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def main(out_dir: str = "artifacts/benchmark_integrity") -> None:
    out = Path(out_dir)
    audit = json.loads((out / "audit.json").read_text())
    cases = audit["cases"]
    ids = [c["case_id"] for c in cases]
    ratio = np.array([c["checks"]["C2_resistance"]["implied_over_ref"] for c in cases])
    fr_rep = np.array([c["checks"]["C1_froude"]["froude_reported"] for c in cases])
    fr_geo = np.array([c["checks"]["C1_froude"]["froude_from_geometry"] for c in cases])
    colors = ["#c0392b" if c["verdict"] == "WITHHELD" else "#27ae60" for c in cases]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))

    ax = axes[0]
    x = np.arange(len(ids))
    ax.bar(x, ratio, color=colors)
    ax.axhline(1.0, color="k", lw=1, label="identity holds: Rt = Ct*0.5*rho*V^2*S")
    ax.axhspan(0.75, 1.25, color="green", alpha=0.12, label="+/-25% tolerance band")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("implied wetted surface / reference wetted surface")
    ax.set_title("C2 resistance identity: 12/12 cases inconsistent")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1]
    ax.loglog(fr_geo, fr_rep, "o", color="#c0392b", label="reported in dataset")
    lims = [0.03, 3.0]
    ax.plot(lims, lims, "k--", lw=1, label="y = x (identity)")
    ax.fill_between(lims, [v * 0.98 for v in lims], [v * 1.02 for v in lims], color="green", alpha=0.25)
    for cid, a, b in zip(ids, fr_geo, fr_rep):
        if abs(a - b) / a > 0.02:
            ax.annotate(cid, (a, b), fontsize=7, xytext=(4, -8), textcoords="offset points")
    ax.set_xlabel("Fr recomputed from geometry: V/sqrt(gL)")
    ax.set_ylabel("Fr reported")
    ax.set_title("C1 Froude identity: DTMB-5415 unit/scale mix")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    fig.suptitle("Benchmark reference integrity gate — 12 cases, 0 usable as ground truth", fontsize=11)
    fig.tight_layout()
    png = out / "benchmark_integrity.png"
    fig.savefig(png, dpi=150)
    print(f"wrote {png}")


if __name__ == "__main__":
    main()