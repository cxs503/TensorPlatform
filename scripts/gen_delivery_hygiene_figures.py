#!/usr/bin/env python3
"""出交付面卫生的报告图（全部从账本 JSON 现算，零手抄）。

    python3 scripts/gen_delivery_hygiene_figures.py \
        --ledgers artifacts/delivery_hygiene/repo-alpha/ledger.json \
                  artifacts/delivery_hygiene/repo-beta/ledger.json \
        --demo-before artifacts/delivery_hygiene/apply-demo/ledger_before.json \
        --demo-after  artifacts/delivery_hygiene/apply-demo/ledger_after.json \
        --demo-baseline artifacts/delivery_hygiene/apply-demo/baseline.json \
        --out artifacts/delivery_hygiene/figures

产出：
* ``fig_classes_compare.png``  两仓四类分布（含未合并基准线）
* ``fig_backlog_month.png``    未合并积压按分支 tip 月份堆叠（熵的累积曲线）
* ``fig_apply_demo.png``       真实仓库副本上处置前后的未合并数与 ceiling 门线
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

CLASSES = ("A", "B", "C", "D")
LABELS = {"A": "A covered-by-base", "B": "B superset-covered", "C": "C undelivered", "D": "D orphan"}
COLORS = {"A": "#2e7d32", "B": "#1565c0", "C": "#c62828", "D": "#616161"}


def load(path: str) -> Dict[str, Any]:
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig_classes_compare(ledgers: List[Dict[str, Any]], out: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    width = 0.2
    for i, led in enumerate(ledgers):
        vals = [led["stats"]["by_class"][c] for c in CLASSES]
        xs = [j + i * width for j in range(len(CLASSES))]
        bars = ax.bar(xs, vals, width=width, label=f"{led['repo']} (n={led['stats']['total_branches']})")
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=7)
    ax.set_xticks([j + width * (len(ledgers) - 1) / 2 for j in range(len(CLASSES))])
    ax.set_xticklabels([LABELS[c] for c in CLASSES], fontsize=8)
    ax.set_ylabel("branches")
    ax.set_title("Content-equivalence classes of unmerged agent/* branches "
                 "(A/D closable, B closable via superset, C needs delivery)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_backlog_month(ledgers: List[Dict[str, Any]], out: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(ledgers), 1, figsize=(9.0, 3.4 * len(ledgers)), squeeze=False)
    for ax, led in zip(axes[:, 0], ledgers):
        months = sorted(led["stats"]["by_month"])
        c = [led["stats"]["by_month"][m].get("C", 0) for m in months]
        b = [led["stats"]["by_month"][m].get("B", 0) for m in months]
        ax.bar(months, c, label="C undelivered", color=COLORS["C"])
        ax.bar(months, b, bottom=c, label="B superset-covered", color=COLORS["B"])
        ax.set_title(f"{led['repo']}: unmerged backlog by branch tip month "
                     f"(unmerged={led['stats']['unmerged']})", fontsize=9)
        ax.set_ylabel("branches")
        ax.tick_params(axis="x", labelrotation=70, labelsize=6)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_apply_demo(before: Dict[str, Any], after: Dict[str, Any], baseline: Dict[str, Any],
                   out: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label = before["repo"]
    ceiling = baseline["repos"][label]["ceiling"]
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    states = ["before disposal", "after disposal"]
    vals = [before["stats"]["unmerged"], after["stats"]["unmerged"]]
    bvals = [by for by in ({"A": before["stats"]["by_class"]["A"],
                            "B": before["stats"]["by_class"]["B"],
                            "D": before["stats"]["by_class"]["D"]},
                           {"A": after["stats"]["by_class"]["A"],
                            "B": after["stats"]["by_class"]["B"],
                            "D": after["stats"]["by_class"]["D"]})]
    bars = ax.bar(states, vals, color=COLORS["C"], width=0.45, label="unmerged (B+C)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v}", ha="center", va="bottom", fontsize=10)
    ax.axhline(ceiling, color="#000000", ls="--", lw=1.2,
               label=f"ceiling (frozen baseline) = {ceiling}")
    ax.set_ylim(0, max(ceiling, max(vals)) * 1.15)
    ax.set_ylabel("branches")
    closed = (bvals[0]["A"] + bvals[0]["B"] + bvals[0]["D"])
    ax.set_title(f"Real-repo copy `{label}`: archive+close of A/B/D refs "
                 f"(closable={closed}), C kept", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledgers", nargs="+", required=True)
    ap.add_argument("--demo-before", default=None)
    ap.add_argument("--demo-after", default=None)
    ap.add_argument("--demo-baseline", default=None)
    ap.add_argument("--out", default="artifacts/delivery_hygiene/figures")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ledgers = [load(p) for p in a.ledgers]
    paths = [
        fig_classes_compare(ledgers, os.path.join(a.out, "fig_classes_compare.png")),
        fig_backlog_month(ledgers, os.path.join(a.out, "fig_backlog_month.png")),
    ]
    if a.demo_before and a.demo_after and a.demo_baseline:
        paths.append(fig_apply_demo(load(a.demo_before), load(a.demo_after),
                                    load(a.demo_baseline),
                                    os.path.join(a.out, "fig_apply_demo.png")))
    print("[figures] " + " ".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())