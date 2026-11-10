#!/usr/bin/env python3
"""knowledge_sediment_figures.py — 四契约沉淀管线的证据图（数据全部来自工件，不手抄）。

输入（运行前须已生成的工件）
--------------------------
* ``artifacts/knowledge_sediment/scan_report.json``   全语料扫描汇总 + 可归属明细
* ``artifacts/knowledge_sediment/recall_report.json`` 标注集召回评估
* ``artifacts/knowledge_sediment/bench_report.json``  100/300/1000 条检索时延

输出
----
* ``docs/assets/knowledge_sediment/fig1_attribution_funnel.png``
* ``docs/assets/knowledge_sediment/fig2_recall.png``
* ``docs/assets/knowledge_sediment/fig3_latency.png``

所有数字读自 JSON 工件，脚本里没有手写常量 → 图与工件不可能对不上。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "knowledge_sediment"
OUT = ROOT / "docs" / "assets" / "knowledge_sediment"


def load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def fig1_attribution() -> Path:
    scan = load("scan_report.json")
    cont = scan["contamination"]
    tally = scan["tally"]
    store = scan["store"] or {}
    stages = [
        ("mission dirs\nwith artifacts", scan["missions_scanned"]),
        ("attributable\n(declared id == dir)", scan["missions_attributable"]),
        ("knowledge in store\n(all 4 contracts)", store.get("count", tally.get("inserted", 0))),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    names = [s[0] for s in stages]
    vals = [s[1] for s in stages]
    bars = ax.bar(names, vals, color=["#4C72B0", "#DD8452", "#55A868"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v}", ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("count")
    ax.set_title("Attribution funnel (live corpus scan)")
    ax.text(
        0.02,
        0.72,
        f"indexed artifacts: {cont['files_indexed']}\n"
        f"distinct contents: {cont['distinct_contents']}\n"
        f"contents shared by >1 mission: {cont['shared_contents']}\n"
        f"dirs carrying shared content: {cont['shared_mission_dirs']}",
        transform=ax.transAxes,
        fontsize=8.5,
        va="top",
        bbox=dict(boxstyle="round", fc="#f5f5f5", ec="#cccccc"),
    )
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    hist = scan.get("rejected_reason_histogram", {})
    reasons = list(hist.keys())
    counts = [hist[r] for r in reasons]
    bars = ax.barh(reasons, counts, color="#C44E52")
    for b, v in zip(bars, counts):
        ax.text(v, b.get_y() + b.get_height() / 2, f" {v}", va="center", fontsize=9)
    ax.set_xlabel("rejected records")
    ax.set_title("Quality / attribution gate: why records were refused")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "fig1_attribution_funnel.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig2_recall() -> Path:
    rec = load("recall_report.json")
    scan = load("scan_report.json")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    mids = [m["mission_id"] for m in rec["per_mission"]]
    vals = [m["recall"] if m["recall"] is not None else 0.0 for m in rec["per_mission"]]
    bars = ax.bar(mids, vals, color=["#55A868" if v >= 1.0 else "#DD8452" for v in vals])
    for b, m, v in zip(bars, rec["per_mission"], vals):
        if m["missed"]:
            ax.text(b.get_x() + b.get_width() / 2, v + 0.02, ",".join(m["missed"]), ha="center", fontsize=7, rotation=90)
    ax.axhline(0.8, color="#C44E52", ls="--", lw=1.2, label="acceptance 0.80")
    ax.set_ylim(0, 1.25)
    ax.set_ylabel("recall (contract slots recovered)")
    ax.set_xlabel("mission id (hand-labelled reference set)")
    ax.set_title(
        f"Recall = {rec['recall']:.3f} over {rec['missions']} missions, "
        f"fabricated slots = {rec['fabricated_slots']}"
    )
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    ax.tick_params(axis="x", labelrotation=60, labelsize=8)

    ax = axes[1]
    rows = scan["rows"]
    filled = [len(r["filled"]) for r in rows]
    bins = [i - 0.5 for i in range(0, 6)]
    ax.hist(filled, bins=bins, color="#4C72B0", edgecolor="white")
    ax.set_xticks([0, 1, 2, 3, 4])
    ax.set_xlabel("contracts extracted per attributable mission (max 4)")
    ax.set_ylabel("missions")
    ax.set_title(
        f"Fill distribution: {scan['missions_all_four']}/{scan['missions_attributable']} missions all-four, "
        f"mean fill {scan['mean_fill_ratio']:.3f}"
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = OUT / "fig2_recall.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig3_latency() -> Path:
    bench = load("bench_report.json")
    rows = bench["scaling"]
    ns = [r["n_entries"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    for key, colour, marker in (("median_ms", "#4C72B0", "o"), ("p95_ms", "#DD8452", "s"), ("max_ms", "#C44E52", "^")):
        ax.plot(ns, [r[key] for r in rows], marker=marker, color=colour, label=key.replace("_ms", ""))
    ax.axhline(bench["threshold_ms"], color="#555555", ls="--", lw=1.2, label=f"acceptance {bench['threshold_ms']:.0f} ms")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("knowledge entries in store")
    ax.set_ylabel("search latency (ms, limit=20)")
    ax.set_title("GET /knowledge/search latency vs corpus size (synthetic corpus)")
    for x, r in zip(ns, rows):
        ax.annotate(f"{r['max_ms']:.1f}", (x, r["max_ms"]), textcoords="offset points", xytext=(0, 6), fontsize=8, ha="center")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = OUT / "fig3_latency.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    for fn in (fig1_attribution, fig2_recall, fig3_latency):
        print("wrote", fn())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())