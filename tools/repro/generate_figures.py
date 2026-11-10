"""Generate audit figures for P71-N10 repro pipeline.

Produces:
  - repro_rate_by_repo.png   (bar chart with error bars)
  - drift_types_pie.png      (pie chart of drift classifications)
  - ci_forest_plot.png       (forest plot of confidence intervals)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


def load_summary(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def plot_repro_rate_by_repo(summary: dict, output: Path) -> str:
    """Bar chart of repro_rate by repo with Wilson CI error bars."""
    repo_data = summary.get("repo_breakdown", {})
    if not repo_data:
        return ""

    repos = sorted(repo_data.keys())
    rates = [repo_data[r]["repro_rate"]["proportion"] for r in repos]
    lowers = [repo_data[r]["repro_rate"]["lower"] for r in repos]
    uppers = [repo_data[r]["repro_rate"]["upper"] for r in repos]
    errors = [(np.array(rates) - np.array(lowers)).tolist(),
              (np.array(uppers) - np.array(rates)).tolist()]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(repos))
    bars = ax.bar(x, rates, yerr=errors, capsize=5, color="#2196F3",
                  edgecolor="white", linewidth=0.5, alpha=0.85)

    # Add value labels
    for i, (r, lo, hi) in enumerate(zip(rates, lowers, uppers)):
        ax.text(i, r + 0.02, f"{r:.3f}", ha="center", va="bottom", fontsize=9,
                fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(repos, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Reproduction Rate", fontsize=11)
    ax.set_title("Reproduction Rate by Repository (95% Wilson CI)", fontsize=13, fontweight="bold")
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
    ax.axhline(y=summary["repro_rate"]["proportion"], color="red", linestyle="--",
               linewidth=1.5, alpha=0.7, label=f"Overall: {summary['repro_rate']['proportion']:.3f}")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(str(output), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def plot_drift_types_pie(summary: dict, output: Path) -> str:
    """Pie chart of drift type classifications."""
    drift = summary.get("drift_summary", {})
    if not drift:
        return ""

    labels = list(drift.keys())
    sizes = list(drift.values())

    # Human-readable labels
    label_map = {
        "code_change": "Code Change",
        "dependency_change": "Dependency Change",
        "environment_change": "Environment Change",
        "original_verdict_error": "Original Verdict Error",
    }
    display_labels = [label_map.get(l, l) for l in labels]

    colors = ["#FF6384", "#36A2EB", "#FFCE56", "#4BC0C0"]

    fig, ax = plt.subplots(figsize=(8, 6))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=display_labels, autopct="%1.1f%%",
        colors=colors[:len(labels)], startangle=90,
        textprops={"fontsize": 10},
    )
    for t in autotexts:
        t.set_fontweight("bold")

    ax.set_title("Drift Type Classification", fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(str(output), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def plot_ci_forest(summary: dict, output: Path) -> str:
    """Forest plot of Wilson confidence intervals by repo."""
    repo_data = summary.get("repo_breakdown", {})
    if not repo_data:
        return ""

    repos = sorted(repo_data.keys())
    rates = [repo_data[r]["repro_rate"]["proportion"] for r in repos]
    lowers = [repo_data[r]["repro_rate"]["lower"] for r in repos]
    uppers = [repo_data[r]["repro_rate"]["upper"] for r in repos]

    fig, ax = plt.subplots(figsize=(10, max(4, len(repos) * 0.5)))
    y = np.arange(len(repos))

    # CI lines
    ax.errorbar(rates, y, xerr=[np.array(rates) - np.array(lowers),
                                 np.array(uppers) - np.array(rates)],
                fmt="o", color="#2196F3", capsize=4, markersize=8,
                elinewidth=2, label="95% Wilson CI")

    # Overall rate line
    overall = summary["repro_rate"]["proportion"]
    overall_lo = summary["repro_rate"]["lower"]
    overall_hi = summary["repro_rate"]["upper"]
    ax.axvline(x=overall, color="red", linestyle="--", linewidth=1.5, alpha=0.7,
               label=f"Overall ({overall:.3f})")
    ax.axvspan(overall_lo, overall_hi, alpha=0.1, color="red")

    ax.set_yticks(y)
    ax.set_yticklabels(repos, fontsize=10)
    ax.set_xlabel("Reproduction Rate", fontsize=11)
    ax.set_title("Forest Plot: Reproduction Rate by Repository", fontsize=13, fontweight="bold")
    ax.set_xlim(0, 1.05)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
    ax.legend(fontsize=9, loc="lower right")
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    fig.savefig(str(output), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(output)


def main():
    summary_path = sys.argv[1] if len(sys.argv) > 1 else "artifacts/runs/m7141/summary.json"
    output_dir = Path(summary_path).parent / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = load_summary(summary_path)

    paths = {}
    paths["bar_chart"] = plot_repro_rate_by_repo(summary, output_dir / "repro_rate_by_repo.png")
    paths["pie_chart"] = plot_drift_types_pie(summary, output_dir / "drift_types_pie.png")
    paths["forest_plot"] = plot_ci_forest(summary, output_dir / "ci_forest_plot.png")

    print(f"Figures generated in {output_dir}:")
    for name, path in paths.items():
        if path:
            print(f"  {name}: {path}")

    # Update summary with figure paths
    summary["figures"] = {
        "bar_chart": "figures/repro_rate_by_repo.png",
        "pie_chart": "figures/drift_types_pie.png",
        "forest_plot": "figures/ci_forest_plot.png",
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
