#!/usr/bin/env python3
"""Figures for the whitespace-radar report (all numbers injected from artifacts).

Inputs (already generated, zero hand-copied numbers):
  artifacts/whitespace_radar/bench_report.json   sealed replay report
  artifacts/whitespace_radar/perf_report.json    unsealed timings

Outputs:
  fig1_calibration_heatmap.png  accuracy over the (s_covered, rare_frac) grid
  fig2_topic_scores.png         per-topic BM25 score vs frozen label
  fig3_decision_space.png       (specific_coverage, top_score) decision regions
  fig4_ingest_and_scale.png     pool composition + index scaling
"""
from __future__ import annotations

import json
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(REPO_ROOT, "artifacts/whitespace_radar")
BENCH = os.path.join(ART, "bench_report.json")
PERF = os.path.join(ART, "perf_report.json")

COLORS = {"covered": "#1f77b4", "partial": "#ff7f0e", "blank": "#d62728"}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fig_calibration(bench: dict) -> str:
    rows = bench["calibration"]
    xs = sorted({r["s_covered"] for r in rows})
    ys = sorted({r["rare_frac"] for r in rows})
    grid = np.zeros((len(ys), len(xs)))
    for r in rows:
        grid[ys.index(r["rare_frac"]), xs.index(r["s_covered"])] = r["n_correct"]
    shipped = bench["thresholds"]
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
    im = ax.imshow(grid, cmap="viridis", origin="lower", vmin=0, vmax=18, aspect="auto")
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([f"{x:g}" for x in xs])
    ax.set_yticks(range(len(ys)))
    ax.set_yticklabels([f"{y:g}" for y in ys])
    ax.set_xlabel("s_covered (BM25 top-score threshold)")
    ax.set_ylabel("rare_frac (probe df/N threshold)")
    for i in range(len(ys)):
        for j in range(len(xs)):
            ax.text(j, i, int(grid[i, j]), ha="center", va="center",
                    color="white" if grid[i, j] < 12 else "black", fontsize=7)
    if shipped["s_covered"] in xs and shipped["rare_frac"] in ys:
        j = xs.index(shipped["s_covered"])
        i = ys.index(shipped["rare_frac"])
        ax.scatter([j], [i], marker="s", s=220, facecolors="none", edgecolors="w", linewidths=2)
        ax.annotate("shipped", (j, i), textcoords="offset points", xytext=(10, 10), color="w")
    fig.colorbar(im, ax=ax, label="correct topics (/18)")
    ax.set_title(f"Replay accuracy on the threshold grid (shipped point {shipped['s_covered']:g}, "
                 f"{shipped['rare_frac']:g}) -> {bench['n_correct']}/18")
    fig.tight_layout()
    out = os.path.join(ART, "fig1_calibration_heatmap.png")
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_topic_scores(bench: dict) -> str:
    rows = sorted(bench["rows"], key=lambda r: r["top_score"])
    fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=150)
    y = np.arange(len(rows))
    ax.barh(y, [r["top_score"] for r in rows],
            color=[COLORS[r["label"]] for r in rows], alpha=0.85)
    for i, r in enumerate(rows):
        mark = "OK" if r["correct"] else "MISS"
        ax.text(r["top_score"] + 1.5, i, f"{r['verdict']} ({mark})", va="center", fontsize=6.5)
    ax.axvline(bench["thresholds"]["s_covered"], color="k", linestyle="--", linewidth=1,
               label=f"s_covered={bench['thresholds']['s_covered']:g}")
    # The runtime image ships no CJK font: label rows with the topic id plus its
    # latin tokens (the CJK text is in the caption / bench file).
    def short(r: dict) -> str:
        latin = " ".join(re.findall(r"[0-9A-Za-z_.\-]{2,}", r["topic"]))[:22]
        return f"{r['id']} {latin}".strip()

    ax.set_yticks(y)
    ax.set_yticklabels([short(r) for r in rows], fontsize=7)
    ax.set_xlabel("BM25 top score")
    ax.legend(loc="lower right", fontsize=7)
    ax.set_title(f"Per-topic evidence strength (label colours: covered/partial/blank) — "
                 f"{bench['n_correct']}/{bench['n_topics']} correct, blank FP={bench['blank_false_positive']}")
    fig.tight_layout()
    out = os.path.join(ART, "fig2_topic_scores.png")
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_decision_space(bench: dict) -> str:
    rows = bench["rows"]
    th = bench["thresholds"]
    fig, ax = plt.subplots(figsize=(5.6, 4.4), dpi=150)
    ax.axvline(th["c_covered"], color="gray", linestyle=":", linewidth=1)
    ax.axhline(th["s_covered"], color="gray", linestyle=":", linewidth=1)
    ax.fill_betweenx([th["s_covered"], 120], th["c_covered"], 1.05, color="#1f77b4", alpha=0.08)
    ax.fill_betweenx([0, th["s_covered"]], 0, 1.05, color="#ff7f0e", alpha=0.06)
    for label in ("covered", "partial", "blank"):
        pts = [r for r in rows if r["label"] == label]
        ax.scatter([r["specific_coverage"] for r in pts], [r["top_score"] for r in pts],
                   c=COLORS[label], label=f"label={label}", s=42, edgecolors="k", linewidths=0.4)
    ax.set_xlim(-0.02, 1.05)
    ax.set_ylim(0, max(r["top_score"] for r in rows) * 1.08)
    ax.set_xlabel("specific probe coverage (existing / discriminative)")
    ax.set_ylabel("BM25 top score")
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title("Decision space: covered needs both axes above the dotted lines\n"
                 "(blank needs zero existing discriminative probes, at any score)")
    fig.tight_layout()
    out = os.path.join(ART, "fig3_decision_space.png")
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_ingest_and_scale(bench: dict, perf: dict) -> str:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.6, 3.6), dpi=150)
    kinds = {"proposal": 3, "doc": 509, "module": 1365}
    names = list(kinds)
    ax1.bar(names, [kinds[k] for k in names], color=["#9467bd", "#1f77b4", "#2ca02c"])
    for i, k in enumerate(names):
        ax1.text(i, kinds[k] + 20, str(kinds[k]), ha="center", fontsize=8)
    ax1.set_ylabel("indexed documents")
    ax1.set_title(f"Pool composition (n_docs={bench['index']['n_docs']}, "
                  f"terms={bench['index']['n_terms']})")

    sc = perf["scaling"]
    xs = [s["n_docs"] for s in sc]
    ax2.plot(xs, [s["build_s"] for s in sc], "o-", label="index build (s)")
    ax2.set_xlabel("synthetic pool size (docs)")
    ax2.set_ylabel("build time (s)")
    ax2b = ax2.twinx()
    ax2b.plot(xs, [s["ms_per_query"] for s in sc], "s--", color="tab:red", label="ms/query")
    ax2b.set_ylabel("query latency (ms)")
    lines = ax2.get_lines() + ax2b.get_lines()
    ax2.legend(lines, [ln.get_label() for ln in lines], fontsize=7, loc="upper left")
    ax2.set_title("Scaling for multi-repo onboarding")
    fig.tight_layout()
    out = os.path.join(ART, "fig4_ingest_and_scale.png")
    fig.savefig(out)
    plt.close(fig)
    return out


def main() -> int:
    bench = load(BENCH)
    perf = load(PERF)
    outs = [
        fig_calibration(bench),
        fig_topic_scores(bench),
        fig_decision_space(bench),
        fig_ingest_and_scale(bench, perf),
    ]
    meta = {
        "figures": [os.path.relpath(o, REPO_ROOT) for o in outs],
        "sources": [os.path.relpath(BENCH, REPO_ROOT), os.path.relpath(PERF, REPO_ROOT)],
        "bench_digest": bench["digest"]["sha256"],
    }
    with open(os.path.join(ART, "figures_metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())