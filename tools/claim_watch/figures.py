#!/usr/bin/env python3
"""Figures for the claim-watch audit (matplotlib, no seaborn, no network).

Every number comes from ``artifacts/claim_watch/*.json`` produced by the audit
run, so the figures cannot drift away from the tables: they are drawn from the
machine-written artifacts, not retyped.

    python3 -m tools.claim_watch.figures --artifacts artifacts/claim_watch

Produces (in the same directory):
    fig1_status_by_tree.png      status mix per audited worktree
    fig2_causes.png              cause codes and grade transitions
    fig3_drift_replay.png        git-labelled categories vs checker verdicts
    fig4_acceptance.png          acceptance criteria vs thresholds
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

STATUS_ORDER = ["OK", "ENV_DRIFT", "SUSPECT", "STALE", "BROKEN", "INVALID"]
STATUS_COLORS = {
    "OK": "#2e7d32",
    "ENV_DRIFT": "#f9a825",
    "SUSPECT": "#ef6c00",
    "STALE": "#c62828",
    "BROKEN": "#6a1b9a",
    "INVALID": "#455a64",
}


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig_status_by_tree(report: Mapping[str, Any], out: str) -> str:
    rows = report["status_table"]
    repos = [r["repo"] for r in rows]
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    left = [0] * len(repos)
    for status in STATUS_ORDER:
        values = [r.get(f"n_{status}", 0) for r in rows]
        if not any(values):
            continue
        ax.barh(repos, values, left=left, color=STATUS_COLORS[status], label=status, height=0.6)
        left = [a + b for a, b in zip(left, values)]
    for i, total in enumerate(left):
        ax.text(total + max(left) * 0.01, i, str(total), va="center", fontsize=9)
    ax.set_xlabel("claims harvested + checked")
    ax.set_title("claim_watch: freshness verdict per audited worktree\n"
                 f"(n = {report['summary']['n_claims']} claims, {report['n_worktrees']} worktrees)")
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_causes(report: Mapping[str, Any], out: str) -> str:
    causes = report["summary"]["by_cause"]
    transitions = report["summary"]["downgrades"]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9))
    labels = list(causes.keys()) or ["(none)"]
    values = list(causes.values()) or [0]
    axes[0].bar(labels, values, color="#37474f")
    axes[0].set_title("cause codes (a claim may carry several)")
    axes[0].set_ylabel("count")
    axes[0].tick_params(axis="x", labelrotation=20)
    for i, v in enumerate(values):
        axes[0].text(i, v, str(v), ha="center", va="bottom", fontsize=9)

    tlabels = list(transitions.keys()) or ["(no downgrade in this slice)"]
    tvalues = list(transitions.values()) or [0]
    axes[1].bar(tlabels, tvalues, color="#c62828")
    axes[1].set_title("automatic grade downgrades")
    axes[1].set_ylabel("count")
    axes[1].tick_params(axis="x", labelrotation=20)
    for i, v in enumerate(tvalues):
        axes[1].text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    fig.suptitle(f"claim_watch fault census (batch {report['batch_seconds']} s)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_drift_replay(report: Mapping[str, Any], out: str) -> str:
    replay = report["drift_replay"]
    counts = replay["counts"]
    cats = ["drift", "stale_at_birth", "consistent"]
    values = [counts.get(c, 0) for c in cats]
    flagged = [0, 0, 0]
    flagged[0] = counts.get("stale_flagged", 0) if counts.get("drift", 0) else 0
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    x = range(len(cats))
    ax.bar([i - 0.2 for i in x], values, width=0.4, label="git-labelled cases", color="#90a4ae")
    ax.bar([i + 0.2 for i in x], flagged, width=0.4,
           label="flagged STALE (drift cases only)", color="#c62828")
    for i, v in enumerate(values):
        ax.text(i - 0.2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.text(0.2, flagged[0], str(flagged[0]), ha="center", va="bottom", fontsize=9, color="#c62828")
    ax.set_xticks(list(x))
    ax.set_xticklabels(cats)
    ax.set_ylabel("cases")
    recall = replay.get("recall")
    precision = replay.get("precision")
    ax.set_title("historical drift replay: checker verdict vs git-ground-truth\n"
                 f"recall = {recall:.3f} (n_drift = {counts.get('drift', 0)}), "
                 f"precision = {precision if precision is None else round(precision, 3)}")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_acceptance(report: Mapping[str, Any], out: str) -> str:
    acc = report["acceptance"]
    names, ratios, texts = [], [], []
    for key, item in acc.items():
        value, threshold = item.get("value"), item.get("threshold")
        names.append(key.split("_")[0] + " " + key.split("_", 1)[1].replace("_", " "))
        if key.startswith(("A2", "A3")):
            ratio = 0.0 if value is None else min(float(value) / float(threshold), 2.0) * 0.5
            text = f"{value} (limit {threshold})"
            color = "#2e7d32" if item["pass"] else "#c62828"
        else:
            ratio = 1.0 if item["pass"] else 0.4
            text = str(value)
            color = "#2e7d32" if item["pass"] else "#c62828"
        ratios.append(ratio)
        texts.append((text, color))
    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    bars = ax.barh(names, ratios, color=[t[1] for t in texts], height=0.55)
    for bar, (text, _) in zip(bars, texts):
        ax.text(min(bar.get_width(), 1.0) + 0.03, bar.get_y() + bar.get_height() / 2,
                text, va="center", fontsize=9)
    ax.axvline(0.5, color="#455a64", linestyle="--", linewidth=1)
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0, 0.5, 1.0])
    ax.set_xticklabels(["0", "threshold", "2x"], fontsize=8)
    ax.set_title("acceptance criteria: inside limits vs outside limits\n"
                 "A2/A3 bars show value/threshold on a 0..2x scale; A1/A4/A5 are pass/fail")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="claim_watch figures")
    ap.add_argument("--artifacts", default="artifacts/claim_watch")
    args = ap.parse_args(argv)
    report = _load(os.path.join(args.artifacts, "audit_report.json"))
    made = [
        fig_status_by_tree(report, os.path.join(args.artifacts, "fig1_status_by_tree.png")),
        fig_causes(report, os.path.join(args.artifacts, "fig2_causes.png")),
        fig_drift_replay(report, os.path.join(args.artifacts, "fig3_drift_replay.png")),
        fig_acceptance(report, os.path.join(args.artifacts, "fig4_acceptance.png")),
    ]
    for path in made:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())