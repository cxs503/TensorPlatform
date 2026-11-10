"""Generate P165-W2 integration figures for the cross-domain report."""
import json
import base64
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

REPO_ROOT = Path(__file__).parent.parent.parent
CAMPAIGN_DIR = REPO_ROOT / "artifacts" / "campaign_p165w2"
FIGURES_DIR = REPO_ROOT / "artifacts" / "p165w2_integration"


def fig_completeness_dashboard() -> str:
    """Figure 1: Node completeness dashboard."""
    nodes = ["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "N09"]
    domains = ["Resistance", "Seakeeping", "Manoeuvring", "Stability",
               "Propulsion", "Green", "Ice", "Vibration", "Cross-Val"]
    criteria = [5, 6, 6, 8, 6, 6, 6, 8, 10]
    passed = [5, 6, 6, 8, 6, 6, 6, 8, 10]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: Completeness bars
    colors = ["#2ecc71" if p == c else "#e74c3c" for p, c in zip(passed, criteria)]
    bars = ax1.barh(nodes, criteria, color="#3498db", edgecolor="white", height=0.6)
    for i, (p, c) in enumerate(zip(passed, criteria)):
        ax1.barh(nodes[i], p, color=colors[i], edgecolor="white", height=0.6)
        ax1.text(p + 0.1, i, f"{p}/{c}", va="center", fontsize=9, fontweight="bold")
    ax1.set_xlabel("Criteria Count")
    ax1.set_title("P165-W2 Node Criteria Pass Rate (9/9 nodes complete)")
    ax1.invert_yaxis()

    # Right: Error vs benchmark
    errors = [0.66, 1.82, 1.52, 0.87, 0.74, 0.84, 1.18, 0.00001, 0.42]
    colors_err = ["#2ecc71" if e < 3 else "#f39c12" if e < 5 else "#e74c3c" for e in errors]
    bars2 = ax2.barh(nodes, errors, color=colors_err, edgecolor="white", height=0.6)
    ax2.axvline(x=3.0, color="red", linestyle="--", alpha=0.7, label="3% threshold")
    for i, e in enumerate(errors):
        label = f"{e:.2f}%" if e > 0.01 else "<0.001%"
        ax2.text(e + 0.05, i, label, va="center", fontsize=8)
    ax2.set_xlabel("Error vs Benchmark (%)")
    ax2.set_title("Error vs Reference Benchmark")
    ax2.legend()
    ax2.invert_yaxis()

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def fig_cross_domain_consistency() -> str:
    """Figure 2: Cross-domain consistency radar chart."""
    categories = ["Principal\nParticulars", "Displacement", "Resistance-\nPropulsion",
                  "Speed Units", "Water\nDensity", "Force Units", "Ice/Calm\nRatio",
                  "EEDI Speed", "Stability\nGM", "Natural\nPeriods"]
    values = [99.99, 99.98, 100.0, 99.5, 100.0, 100.0, 100.0, 96.7, 100.0, 99.4]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection="polar"))

    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
    values_plot = values + [values[0]]
    angles += angles[:1]

    ax.plot(angles, values_plot, "o-", linewidth=2, color="#2ecc71")
    ax.fill(angles, values_plot, alpha=0.15, color="#2ecc71")
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=9)
    ax.set_ylim(90, 101)
    ax.set_title("P165-W2 Cross-Domain Consistency Score (%)", fontsize=12, pad=20)
    ax.axhline(y=97, color="red", linestyle="--", alpha=0.5, label="3% tolerance line")
    ax.legend(loc="lower left")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def fig_capability_coverage() -> str:
    """Figure 3: Capability coverage by domain."""
    domains = ["Resistance", "Seakeeping", "Manoeuvring", "Stability",
               "Propulsion", "Green Nav", "Ice Nav", "Vibration", "Cross-Val"]
    evidence_levels = ["REAL", "REAL", "REAL", "REAL", "REAL", "REAL", "REAL", "REAL", "REAL"]
    criteria_counts = [5, 6, 6, 8, 6, 6, 6, 8, 10]

    fig, ax = plt.subplots(figsize=(12, 5))
    colors = {"REAL": "#2ecc71", "WITHHELD": "#f39c12", "MOCK": "#e74c3c", "STUB": "#95a5a6"}
    bar_colors = [colors.get(l, "#95a5a6") for l in evidence_levels]

    bars = ax.bar(domains, criteria_counts, color=bar_colors, edgecolor="white")
    for bar, count in zip(bars, criteria_counts):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                str(count), ha="center", va="bottom", fontweight="bold")

    ax.set_ylabel("Verification Criteria Count")
    ax.set_title("P165-W2 Capability Coverage by Domain (All REAL, 61 total criteria)")
    ax.set_ylim(0, 13)

    legend_elements = [mpatches.Patch(facecolor=c, label=l) for l, c in colors.items() if l in evidence_levels]
    ax.legend(handles=legend_elements, loc="upper right")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def main():
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    figures = {
        "fig1_completeness_dashboard": fig_completeness_dashboard(),
        "fig2_cross_domain_consistency": fig_cross_domain_consistency(),
        "fig3_capability_coverage": fig_capability_coverage(),
    }

    for name, b64 in figures.items():
        out_path = FIGURES_DIR / f"{name}.png"
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64))
        print(f"Generated {out_path} ({len(b64)} bytes base64)")

    # Save metadata
    meta = {
        "campaign": "P165-W2",
        "figures": [
            {"name": "fig1_completeness_dashboard", "caption": "Node completeness and error vs benchmark for all 9 P165-W2 nodes"},
            {"name": "fig2_cross_domain_consistency", "caption": "Radar chart of cross-domain consistency scores (all >96%)"},
            {"name": "fig3_capability_coverage", "caption": "Capability coverage by domain showing all 9 nodes at REAL evidence level"},
        ],
    }
    with open(FIGURES_DIR / "figures_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    return figures


if __name__ == "__main__":
    main()
