"""Visualization: capability matrix heatmap, gap trend, scan accuracy figures.

Generates three publication-quality figures:
1. Capability matrix heatmap: domain × aspect × evidence level
2. Gap trend chart: showing gap counts over time (or severity distribution)
3. Scan accuracy: confusion matrix or accuracy metrics
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from tools.capability_annotator.registry import CAPABILITY_DOMAINS, CAPABILITY_ASPECTS
from tools.capability_annotator.scanner import ScanReport
from tools.capability_annotator.gap_ledger import GapLedgerReport

# Color scheme for evidence levels
LEVEL_COLORS = {
    "REAL": "#2ecc71",        # Green
    "MOCK": "#e74c3c",        # Red
    "STUB": "#f39c12",        # Orange
    "WITHHELD": "#95a5a6",    # Gray
    "NOT_SUPPORTED": "#34495e",  # Dark gray
}

LEVEL_ORDER = ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]


def generate_capability_heatmap(scan_report: ScanReport, output_path: str | Path,
                                 domain_map: dict[str, str] | None = None) -> str:
    """Generate capability matrix heatmap.

    Shows domain × evidence level distribution as a heatmap.

    Args:
        scan_report: Scan results
        output_path: Path to save PNG
        domain_map: Optional capability_id → domain mapping

    Returns:
        Path to generated figure
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build domain × level matrix
    domains = CAPABILITY_DOMAINS
    levels = LEVEL_ORDER

    # Count capabilities per domain per level
    matrix = np.zeros((len(domains), len(levels)))
    domain_map = domain_map or {}

    for result in scan_report.results:
        domain = domain_map.get(result.capability_id, "unknown")
        if domain in domains:
            domain_idx = domains.index(domain)
            if result.level in levels:
                level_idx = levels.index(result.level)
                matrix[domain_idx, level_idx] += 1

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot heatmap
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")

    # Set ticks
    ax.set_xticks(np.arange(len(levels)))
    ax.set_yticks(np.arange(len(domains)))
    ax.set_xticklabels(levels, fontsize=10, fontweight="bold")
    ax.set_yticklabels([d.replace("_", " ").title() for d in domains], fontsize=9)

    # Rotate x labels
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")

    # Add text annotations
    for i in range(len(domains)):
        for j in range(len(levels)):
            value = int(matrix[i, j])
            if value > 0:
                text = ax.text(j, i, str(value), ha="center", va="center",
                             color="black" if value < 3 else "white",
                             fontsize=11, fontweight="bold")

    # Colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Number of Capabilities", rotation=270, labelpad=20, fontsize=10)

    # Labels and title
    ax.set_xlabel("Evidence Level", fontsize=12, fontweight="bold", labelpad=10)
    ax.set_ylabel("Domain", fontsize=12, fontweight="bold", labelpad=10)
    ax.set_title("Platform Capability Matrix: Domain × Evidence Level",
                fontsize=14, fontweight="bold", pad=20)

    # Add legend
    legend_patches = [mpatches.Patch(color=LEVEL_COLORS[level], label=level)
                     for level in levels]
    ax.legend(handles=legend_patches, loc="upper right", bbox_to_anchor=(1.3, 1),
             title="Evidence Levels", fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return str(output_path)


def generate_gap_trend(gap_report: GapLedgerReport, output_path: str | Path) -> str:
    """Generate gap trend/severity distribution chart.

    Shows gap severity distribution and domain breakdown.

    Args:
        gap_report: Gap ledger results
        output_path: Path to save PNG

    Returns:
        Path to generated figure
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create figure with two subplots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left: Severity distribution
    severity_counts = gap_report.summary.get("severity_tally", {})
    severities = sorted(severity_counts.keys())
    counts = [severity_counts.get(s, 0) for s in severities]

    colors = ["#27ae60", "#f39c12", "#e67e22", "#e74c3c", "#c0392b"]
    colors = colors[:len(severities)]

    bars1 = ax1.bar(severities, counts, color=colors, edgecolor="black", linewidth=0.5)
    ax1.set_xlabel("Severity Level", fontsize=11, fontweight="bold")
    ax1.set_ylabel("Number of Gaps", fontsize=11, fontweight="bold")
    ax1.set_title("Gap Severity Distribution", fontsize=12, fontweight="bold")
    ax1.set_xticks(severities)

    # Add value labels on bars
    for bar, count in zip(bars1, counts):
        if count > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    str(count), ha="center", va="bottom", fontsize=10, fontweight="bold")

    # Right: Domain breakdown
    domain_counts = gap_report.summary.get("domain_tally", {})
    domains = sorted(domain_counts.keys())
    domain_vals = [domain_counts.get(d, 0) for d in domains]

    # Horizontal bar chart
    y_pos = np.arange(len(domains))
    bars2 = ax2.barh(y_pos, domain_vals, color="#3498db", edgecolor="black", linewidth=0.5)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels([d.replace("_", " ").title() for d in domains], fontsize=9)
    ax2.set_xlabel("Number of Gaps", fontsize=11, fontweight="bold")
    ax2.set_title("Gaps by Domain", fontsize=12, fontweight="bold")
    ax2.invert_yaxis()

    # Add value labels
    for bar, val in zip(bars2, domain_vals):
        if val > 0:
            ax2.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height()/2,
                    str(val), ha="left", va="center", fontsize=9, fontweight="bold")

    # Overall title
    fig.suptitle("Platform Capability Gap Analysis",
                fontsize=14, fontweight="bold", y=1.02)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return str(output_path)


def generate_scan_accuracy(scan_report: ScanReport, output_path: str | Path,
                           labeled_baseline: dict[str, str] | None = None) -> str:
    """Generate scan accuracy visualization.

    Shows confusion matrix or accuracy metrics if labeled baseline available,
    otherwise shows level distribution and confidence scores.

    Args:
        scan_report: Scan results
        output_path: Path to save PNG
        labeled_baseline: Optional dict of capability_id → expected level

    Returns:
        Path to generated figure
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    if labeled_baseline:
        # Confusion matrix
        levels = LEVEL_ORDER
        n_levels = len(levels)
        confusion = np.zeros((n_levels, n_levels))

        for result in scan_report.results:
            expected = labeled_baseline.get(result.capability_id)
            if expected and expected in levels and result.level in levels:
                true_idx = levels.index(expected)
                pred_idx = levels.index(result.level)
                confusion[true_idx, pred_idx] += 1

        im = axes[0].imshow(confusion, cmap="Blues", aspect="auto")
        axes[0].set_xticks(range(n_levels))
        axes[0].set_yticks(range(n_levels))
        axes[0].set_xticklabels(levels, rotation=45, ha="right", fontsize=9)
        axes[0].set_yticklabels(levels, fontsize=9)
        axes[0].set_xlabel("Detected Level", fontsize=11, fontweight="bold")
        axes[0].set_ylabel("Expected Level", fontsize=11, fontweight="bold")
        axes[0].set_title("Confusion Matrix", fontsize=12, fontweight="bold")

        # Add text
        for i in range(n_levels):
            for j in range(n_levels):
                val = int(confusion[i, j])
                color = "white" if val > confusion.max()/2 else "black"
                axes[0].text(j, i, str(val), ha="center", va="center",
                           fontsize=10, fontweight="bold", color=color)

        plt.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04)

        # Calculate accuracy metrics
        total = confusion.sum()
        correct = np.trace(confusion)
        accuracy = correct / total if total > 0 else 0

        axes[1].axis("off")
        metrics_text = f"""
Scan Accuracy Metrics
━━━━━━━━━━━━━━━━━━━━

Total Capabilities: {int(total)}
Correctly Classified: {int(correct)}
Accuracy: {accuracy:.1%}

Per-Level Precision:
"""
        for i, level in enumerate(levels):
            pred_count = confusion[:, i].sum()
            if pred_count > 0:
                precision = confusion[i, i] / pred_count
                metrics_text += f"  {level}: {precision:.1%}\n"

        axes[1].text(0.1, 0.5, metrics_text, fontsize=11, verticalalignment="center",
                    fontfamily="monospace",
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    else:
        # Level distribution pie chart
        level_counts = {}
        for result in scan_report.results:
            level_counts[result.level] = level_counts.get(result.level, 0) + 1

        levels_present = list(level_counts.keys())
        counts = [level_counts[l] for l in levels_present]
        colors = [LEVEL_COLORS[l] for l in levels_present]

        wedges, texts, autotexts = axes[0].pie(counts, labels=levels_present,
                                                colors=colors, autopct="%1.0f%%",
                                                startangle=90, pctdistance=0.85)
        for text in texts:
            text.set_fontsize(10)
        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_fontweight("bold")
        axes[0].set_title("Evidence Level Distribution", fontsize=12, fontweight="bold")

        # Confidence distribution
        confidences = [r.confidence for r in scan_report.results]
        axes[1].hist(confidences, bins=10, color="#3498db", edgecolor="black",
                    linewidth=0.5, alpha=0.7)
        axes[1].set_xlabel("Confidence Score", fontsize=11, fontweight="bold")
        axes[1].set_ylabel("Number of Capabilities", fontsize=11, fontweight="bold")
        axes[1].set_title("Scan Confidence Distribution", fontsize=12, fontweight="bold")
        axes[1].axvline(np.mean(confidences), color="red", linestyle="--",
                       label=f"Mean: {np.mean(confidences):.2f}")
        axes[1].legend(fontsize=9)

    fig.suptitle("Platform Capability Scan Accuracy Analysis",
                fontsize=14, fontweight="bold", y=1.02)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return str(output_path)


def generate_all_figures(scan_report: ScanReport, gap_report: GapLedgerReport,
                         output_dir: str | Path,
                         domain_map: dict[str, str] | None = None) -> dict[str, str]:
    """Generate all three figures.

    Args:
        scan_report: Scan results
        gap_report: Gap ledger results
        output_dir: Directory to save figures
        domain_map: Optional capability_id → domain mapping

    Returns:
        Dict mapping figure name to file path
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = {}

    # Figure 1: Capability matrix heatmap
    fig1_path = output_dir / "fig1_capability_matrix_heatmap.png"
    figures["capability_matrix_heatmap"] = generate_capability_heatmap(
        scan_report, fig1_path, domain_map
    )

    # Figure 2: Gap trend
    fig2_path = output_dir / "fig2_gap_trend.png"
    figures["gap_trend"] = generate_gap_trend(gap_report, fig2_path)

    # Figure 3: Scan accuracy
    fig3_path = output_dir / "fig3_scan_accuracy.png"
    figures["scan_accuracy"] = generate_scan_accuracy(scan_report, fig3_path)

    return figures
