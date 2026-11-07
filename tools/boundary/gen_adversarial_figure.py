#!/usr/bin/env python3
"""对抗语料度量图（P20-N08）：召回/误报矩阵、混淆矩阵、漏检机理、词表留出探针。

数据一律从 ``artifacts/boundary/adversarial_metrics.json`` 读（程序化注入，零手抄）。
输出 ``artifacts/boundary/adversarial_recall_figure.png``。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
METRICS = REPO / "artifacts/boundary/adversarial_metrics.json"
OUT = REPO / "artifacts/boundary/adversarial_recall_figure.png"


def main() -> int:
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    classes = list(m["baseline"]["per_class"])
    base = m["baseline"]["per_class"]
    hard = m["hardened"]["per_class"]

    fig, axes = plt.subplots(2, 2, figsize=(10.6, 7.0))
    fig.suptitle("Boundary gate on adversarial corpus (216 samples, 6 mutation operators)\n"
                 f"corpus_sha256={m['corpus']['sha256'][:16]}…  seed={m['corpus']['seed']}",
                 fontsize=11)

    # (a) per-class recall
    ax = axes[0][0]
    x = np.arange(len(classes))
    w = 0.38
    b = [base[c]["recall"] for c in classes]
    h = [hard[c]["recall"] for c in classes]
    ax.bar(x - w / 2, b, w, label="baseline (term lexicon)", color="#c0504d")
    ax.bar(x + w / 2, h, w, label="hardened (+structural rules)", color="#4f81bd")
    ax.axhline(0.6, ls="--", lw=1, color="gray")
    ax.axhline(0.7, ls=":", lw=1, color="black")
    ax.text(-0.45, 0.605, "E4 floor 0.6", fontsize=7, color="gray")
    ax.text(-0.45, 0.705, "C5 floor 0.7", fontsize=7, color="black")
    for xi, (vb, vh) in enumerate(zip(b, h)):
        ax.text(xi - w / 2, vb + 0.02, f"{vb:.2f}", ha="center", fontsize=7)
        ax.text(xi + w / 2, vh + 0.02, f"{vh:.2f}", ha="center", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels([c.replace("_", "\n") for c in classes], fontsize=7)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("recall (positives caught)")
    ax.set_title("(a) recall per mutation class", fontsize=9)
    ax.legend(fontsize=7, loc="upper right")

    # (b) confusion matrices
    for k, (name, block) in enumerate((("baseline", m["baseline"]["overall"]),
                                       ("hardened", m["hardened"]["overall"]))):
        ax = axes[0][1] if k == 0 else axes[1][0]
        cm = np.array([[block["tp"], block["fn"]], [block["fp"], block["tn"]]])
        ax.imshow(cm, cmap="Blues", vmin=0, vmax=cm.sum())
        for (i, j), v in np.ndenumerate(cm):
            ax.text(j, i, str(v), ha="center", va="center", fontsize=12,
                    color="white" if v > cm.sum() / 3 else "black")
        ax.set_xticks([0, 1], ["gate FAIL", "gate PASS"], fontsize=8)
        ax.set_yticks([0, 1], ["leak", "clean"], fontsize=8)
        ax.set_title(f"({'b' if k == 0 else 'c'}) {name} confusion matrix\n"
                     f"recall={block['recall']:.3f}  FP={block['fp_rate']:.3f}  "
                     f"precision={block['precision']:.3f}", fontsize=9)

    # (d) missed by mechanism (baseline) + vocab probe
    ax = axes[1][1]
    mech = {}
    for row in m["baseline"]["mechanism_table"]:
        if row["missed"]:
            mech[row["mechanism"]] = mech.get(row["mechanism"], 0) + row["missed"]
    names = list(mech)
    vals = [mech[n] for n in names]
    order = np.argsort(vals)
    ax.barh([names[i] for i in order], [vals[i] for i in order], color="#9bbb59")
    for i, idx in enumerate(order):
        ax.text(vals[idx] + 0.2, i, str(vals[idx]), va="center", fontsize=8)
    ax.set_xlabel("missed positives (baseline)")
    ax.set_title("(d) baseline misses by failure mechanism", fontsize=9)
    probe = m["vocab_probe"]["per_class"]
    lines = []
    for cls, buckets in probe.items():
        e, hb = buckets["expanded"], buckets["held_out"]
        if e["recall"] is not None and hb["recall"] is not None:
            lines.append(f"{cls}: expanded {e['recall']:.2f} (n={e['n']}) / "
                         f"held-out {hb['recall']:.2f} (n={hb['n']})")
    if lines:
        ax.text(0.02, -0.30, "vocabulary-expansion probe (held-out split): " + " | ".join(lines),
                transform=ax.transAxes, fontsize=7, color="#444444")

    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=96)
    print(json.dumps({"out": str(OUT), "bytes": OUT.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())