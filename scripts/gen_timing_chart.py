#!/usr/bin/env python3
"""Generate timing baseline visualization."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"
OUTPUT_PNG = ARTIFACTS_DIR / "timing_baseline_chart.png"

def main():
    with open(ARTIFACTS_DIR / "timing_baseline.json") as f:
        data = json.load(f)

    records = data["timing_records"]
    baseline = data["baseline"]

    # Group by endpoint
    by_ep = {}
    for r in records:
        by_ep.setdefault(r["endpoint"], []).append(r["wall_clock_ms"])

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Plot 1: Box plot of wall clock times per endpoint
    ep_names = sorted(by_ep.keys())
    ep_data = [by_ep[ep] for ep in ep_names]
    bp = axes[0].boxplot(ep_data, labels=ep_names, patch_artist=True)
    colors = ["#4C72B0", "#55A868"]
    for patch, color in zip(bp["boxes"], colors[:len(ep_names)]):
        patch.set_facecolor(color)
    axes[0].set_ylabel("Wall Clock Time (ms)")
    axes[0].set_title("Timing Distribution by Endpoint")
    axes[0].grid(True, alpha=0.3)

    # Plot 2: Percentile comparison
    eps = list(baseline.keys())
    p50_vals = [baseline[ep]["p50"] for ep in eps]
    p90_vals = [baseline[ep]["p90"] for ep in eps]
    p99_vals = [baseline[ep]["p99"] for ep in eps]

    x = range(len(eps))
    width = 0.25
    axes[1].bar([i - width for i in x], p50_vals, width, label="p50", color="#4C72B0")
    axes[1].bar(x, p90_vals, width, label="p90", color="#55A868")
    axes[1].bar([i + width for i in x], p99_vals, width, label="p99", color="#C44E52")
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(eps)
    axes[1].set_ylabel("Latency (ms)")
    axes[1].set_title("Percentile Baseline (p50/p90/p99)")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(str(OUTPUT_PNG), dpi=150)
    print(f"Saved chart to {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
