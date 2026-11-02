"""由声明-证据台账 JSON 出图（m1962，零手抄：所有数字从工件读）。

用法：
    python3 tools/audit/plot_claim_ledger.py \
        artifacts/claim_ledger/ledger.json artifacts/claim_ledger/fig_claim_ledger.png
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ORDER = ("EVIDENCED", "DELEGATED", "QUALIFIED", "REFERENCE_MENTION",
         "UNBACKED", "PROVENANCE_DRIFT")
COLORS = {"EVIDENCED": "#2e7d32", "DELEGATED": "#66bb6a", "QUALIFIED": "#0288d1",
          "REFERENCE_MENTION": "#9e9e9e", "UNBACKED": "#ef6c00",
          "PROVENANCE_DRIFT": "#c62828"}


def main(ledger_path: str, out_png: str) -> int:
    led = json.load(open(ledger_path, encoding="utf-8"))
    bv = led["by_verdict"]
    claims = led.get("claims", []) + led.get("provenance", [])

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))

    ax = axes[0]
    counts = [bv.get(k, 0) for k in ORDER]
    ax.bar(range(len(ORDER)), counts,
           color=[COLORS[k] for k in ORDER], edgecolor="black", lw=0.6)
    for i, c in enumerate(counts):
        ax.text(i, c, str(c), ha="center", va="bottom", fontsize=10)
    ax.set_xticks(range(len(ORDER)))
    ax.set_xticklabels([k.replace("_", "\n") for k in ORDER], fontsize=8)
    ax.set_ylabel("entries")
    ax.set_title(f"claim verdicts ({led['n_claim_entries']} docstring claims + "
                 f"{led['n_provenance_entries']} provenance strings, "
                 f"{led['n_modules_scanned']} modules)", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    per_mod = Counter(r["module"] for r in claims
                      if r["verdict"] in {"UNBACKED", "PROVENANCE_DRIFT"})
    top = per_mod.most_common(10)
    if top:
        mods = [m.split("/")[-1] for m, _ in top][::-1]
        vals = [v for _, v in top][::-1]
        ax.barh(range(len(top)), vals, color="#ef6c00", edgecolor="black", lw=0.6)
        ax.set_yticks(range(len(top)))
        ax.set_yticklabels(mods, fontsize=8)
        for i, v in enumerate(vals):
            ax.text(v, i, str(v), va="center", fontsize=9)
        ax.set_xlabel("open leads (UNBACKED + DRIFT)")
        ax.set_title("modules with open claim leads (top 10)", fontsize=9)
        ax.grid(axis="x", alpha=0.3)
    else:
        ax.text(0.5, 0.5, "no open leads", ha="center", va="center")

    fig.suptitle("Claim-evidence ledger — method claims vs implementation footprints "
                 f"(m1962; provenance drift = {bv.get('PROVENANCE_DRIFT', 0)})",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    fig.savefig(out_png, dpi=140)
    print(f"wrote {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "artifacts/claim_ledger/ledger.json",
                  sys.argv[2] if len(sys.argv) > 2
                  else "artifacts/claim_ledger/fig_claim_ledger.png"))