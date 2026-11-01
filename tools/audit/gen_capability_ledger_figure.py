#!/usr/bin/env python3
"""生成能力证据台账图（数据全部从 artifacts/capability_ledger/capability_ledger_resolved.json 读，
零手抄；中文域名为躲避 matplotlib 缺字形，图内统一用 ASCII 别名）。"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "artifacts" / "capability_ledger" / "capability_ledger_resolved.json"
OUT = ROOT / "artifacts" / "capability_ledger" / "fig_capability_ledger.png"
LEVELS = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}
DOMAIN_ASCII = {
    "耐波/seakeeping": "seakeeping (unregistered)",
    "绿色航行/green-ship": "green-ship (unregistered)",
    "基准参照数据/benchmark-reference": "benchmark-reference (unregistered)",
}


def main() -> int:
    r = json.loads(SRC.read_text(encoding="utf-8"))
    caps = r["capabilities"]
    fig, axs = plt.subplots(1, 2, figsize=(13.6, 7.2), gridspec_kw={"width_ratios": [1.35, 1]})

    ax = axs[0]
    m = np.array([[LEVELS[c["claimed_level"]], LEVELS[c["reconciled_level"]]] for c in caps], dtype=float)
    cmap = matplotlib.colors.ListedColormap(["#8b0000", "#c0392b", "#e67e22", "#f1c40f", "#2e8b57"])
    ax.imshow(m, cmap=cmap, vmin=0, vmax=4, aspect="auto")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["registry self-claim", "ledger reconciled"], fontsize=10)
    ax.set_yticks(range(len(caps)))
    ax.set_yticklabels([c["id"] for c in caps], fontsize=8.5)
    for i, c in enumerate(caps):
        for j, lvl in enumerate([c["claimed_level"], c["reconciled_level"]]):
            ax.text(j, i, lvl, ha="center", va="center", fontsize=7.5,
                    color="white" if LEVELS[lvl] in (0, 4) else "black")
        if c["divergence"] != "OK":
            ax.text(2.05, i, c["divergence"], ha="left", va="center", fontsize=7.5,
                    color={"OVERCLAIM": "#c0392b", "REVERIFY": "#e67e22", "UNVERIFIED": "#7f8c8d"}[c["divergence"]])
    ax.set_xlim(-0.5, 3.6)
    ax.set_title("21 capabilities: registry self-claim vs report-backed reconciliation\n"
                 "(mission 1567; evidence = 10 audited reports, verbatim-quote checked)", fontsize=10)

    ax2 = axs[1]
    tally = r["summary"]["divergence_tally"]
    keys = list(tally)
    cols = {"OVERCLAIM": "#c0392b", "REVERIFY": "#e67e22", "UNVERIFIED": "#7f8c8d", "OK": "#2e8b57"}
    bars = ax2.bar(keys, [tally[k] for k in keys], color=[cols[k] for k in keys])
    ax2.bar_label(bars, fontsize=10)
    ax2.set_ylim(0, max(tally.values()) * 1.25)
    ax2.set_ylabel("capabilities")
    ax2.tick_params(axis="x", labelsize=9)
    ax2.set_title("divergence tally (UNVERIFIED = claimed REAL, zero audit evidence)", fontsize=10)

    gaps = r["unregistered_domains"]
    txt = "registry coverage gaps (audited domain, no capability entry):\n" + "\n".join(
        f"  - {DOMAIN_ASCII.get(g['domain'], g['domain'])} <- report {g['latest_evidence_report']}"
        f" ({g['latest_asserted_level']})" for g in gaps
    )
    fig.text(0.012, 0.015, txt, fontsize=8.5, va="bottom", family="monospace")
    plt.tight_layout(rect=[0, 0.10, 1, 1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT, dpi=150)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())