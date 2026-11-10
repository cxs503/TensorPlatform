"""Dashboard report generator for cross-solver regression.

Generates:
- Case status table (per-case pass/fail with deviations)
- Regression trend chart (deviation over time/runs)
- Deviation distribution histogram
- Summary statistics by solver

All figures generated with matplotlib, saved as PNG.
"""
from __future__ import annotations

import base64
import io
import json
import os
from typing import Any, Dict, List, Optional

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

__all__ = [
    "generate_dashboard", "DashboardReport",
    "fig_status_table", "fig_deviation_histogram",
    "fig_regression_trend", "fig_solver_comparison",
]

# Color palette (colorblind-friendly)
SOLVER_COLORS = {
    "TensorLBM": "#0072B2",
    "TensorFEM": "#E69F00",
    "TensorDEM": "#009E73",
    "TensorFVM": "#CC79A7",
}
DEFAULT_COLOR = "#999999"


class DashboardReport:
    """Container for dashboard report data and figures."""

    def __init__(self, report_data: Dict[str, Any], figures: Dict[str, bytes]):
        self.report_data = report_data
        self.figures = figures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_data": self.report_data,
            "figure_names": list(self.figures.keys()),
        }

    def save(self, output_dir: str) -> Dict[str, str]:
        """Save report data and figures to disk. Returns paths."""
        os.makedirs(output_dir, exist_ok=True)

        # Save JSON report
        json_path = os.path.join(output_dir, "report.json")
        with open(json_path, "w") as f:
            json.dump(self.report_data, f, indent=2, default=str)

        # Save figures
        figure_paths = {}
        for name, png_data in self.figures.items():
            fig_path = os.path.join(output_dir, f"{name}.png")
            with open(fig_path, "wb") as f:
                f.write(png_data)
            figure_paths[name] = fig_path

        return {"json": json_path, "figures": figure_paths}


def _fig_to_png(fig) -> bytes:
    """Convert matplotlib figure to PNG bytes."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def fig_status_table(results: List[Dict[str, Any]], title: str = "Case Status Table") -> bytes:
    """Generate a case status table as a figure.

    Shows: case_id, solver, status, max deviation, alerts count.
    """
    if not HAS_MPL:
        return b""

    fig, ax = plt.subplots(figsize=(14, max(4, len(results) * 0.4 + 2)))
    ax.axis("off")
    ax.set_title(title, fontsize=14, fontweight="bold", pad=20)

    # Table data
    col_labels = ["Case ID", "Solver", "Status", "Max Dev (%)", "Alerts", "Critical"]
    table_data = []
    for r in results:
        max_dev = max(r.get("deviations", {}).values()) * 100 if r.get("deviations") else 0.0
        n_alerts = len(r.get("alerts", []))
        n_critical = sum(1 for a in r.get("alerts", []) if a.get("severity") == "critical")
        status_str = "✓ PASS" if r.get("passed") else "✗ FAIL"
        table_data.append([
            r.get("case_id", "?")[:40],
            r.get("solver_name", "?"),
            status_str,
            f"{max_dev:.2f}",
            str(n_alerts),
            str(n_critical),
        ])

    table = ax.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.4)

    # Color cells by status
    for i, row_data in enumerate(table_data):
        color = "#d4edda" if "PASS" in row_data[2] else "#f8d7da"
        for j in range(len(col_labels)):
            table[i + 1, j].set_facecolor(color)

    # Header styling
    for j in range(len(col_labels)):
        table[0, j].set_facecolor("#4472C4")
        table[0, j].set_text_props(color="white", fontweight="bold")

    return _fig_to_png(fig)


def fig_deviation_histogram(
    results: List[Dict[str, Any]],
    drift_threshold: float = 0.05,
    title: str = "Deviation Distribution",
) -> bytes:
    """Generate deviation distribution histogram.

    Shows distribution of all metric deviations across all cases.
    Vertical line at drift threshold.
    """
    if not HAS_MPL:
        return b""

    # Collect all deviations
    all_devs = []
    devs_by_solver: Dict[str, List[float]] = {}
    for r in results:
        for metric, dev in r.get("deviations", {}).items():
            all_devs.append(dev * 100)  # convert to percent
            solver = r.get("solver_name", "unknown")
            devs_by_solver.setdefault(solver, []).append(dev * 100)

    fig, ax = plt.subplots(figsize=(10, 6))

    if all_devs:
        # Overall histogram
        ax.hist(all_devs, bins=30, alpha=0.3, color="gray", label="All solvers", density=True)

        # Per-solver histograms
        for solver, devs in devs_by_solver.items():
            color = SOLVER_COLORS.get(solver, DEFAULT_COLOR)
            ax.hist(devs, bins=20, alpha=0.5, color=color, label=solver, density=True)

    # Threshold line
    ax.axvline(x=drift_threshold * 100, color="red", linestyle="--", linewidth=2,
               label=f"Threshold ({drift_threshold*100:.0f}%)")

    ax.set_xlabel("Relative Deviation (%)", fontsize=12)
    ax.set_ylabel("Density", fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    # Add statistics annotation
    if all_devs:
        import statistics
        mean_dev = statistics.mean(all_devs)
        max_dev = max(all_devs)
        ax.text(0.95, 0.95, f"Mean: {mean_dev:.2f}%\nMax: {max_dev:.2f}%\nN: {len(all_devs)}",
                transform=ax.transAxes, fontsize=10, verticalalignment="top",
                horizontalalignment="right",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    return _fig_to_png(fig)


def fig_regression_trend(
    suite_history: List[Dict[str, Any]],
    title: str = "Regression Trend",
) -> bytes:
    """Generate regression trend chart showing deviation over multiple runs.

    Args:
        suite_history: list of suite results (each with timestamp and results)
    """
    if not HAS_MPL:
        return b""

    fig, ax = plt.subplots(figsize=(12, 6))

    if suite_history:
        # Extract max deviation per run
        run_labels = []
        max_devs = []
        for i, suite in enumerate(suite_history):
            run_labels.append(suite.get("run_id", f"Run {i+1}"))
            devs = []
            for r in suite.get("results", []):
                devs.extend(r.get("deviations", {}).values())
            max_devs.append(max(devs) * 100 if devs else 0.0)

        x = range(len(run_labels))
        ax.plot(x, max_devs, "o-", color="#4472C4", linewidth=2, markersize=8)
        ax.set_xticks(list(x))
        ax.set_xticklabels(run_labels, rotation=45, ha="right")

    # Threshold line
    ax.axhline(y=5.0, color="red", linestyle="--", linewidth=2, label="5% threshold")
    ax.axhline(y=15.0, color="darkred", linestyle=":", linewidth=1.5, label="15% critical")

    ax.set_xlabel("Run", fontsize=12)
    ax.set_ylabel("Max Deviation (%)", fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    return _fig_to_png(fig)


def fig_solver_comparison(
    results: List[Dict[str, Any]],
    title: str = "Solver Comparison",
) -> bytes:
    """Generate solver comparison chart (bar chart of pass rates and mean deviations)."""
    if not HAS_MPL:
        return b""

    # Aggregate by solver
    solver_stats: Dict[str, Dict[str, Any]] = {}
    for r in results:
        solver = r.get("solver_name", "unknown")
        stats = solver_stats.setdefault(solver, {
            "total": 0, "passed": 0, "deviations": []
        })
        stats["total"] += 1
        if r.get("passed"):
            stats["passed"] += 1
        stats["deviations"].extend(v * 100 for v in r.get("deviations", {}).values())

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    solvers = list(solver_stats.keys())
    pass_rates = [solver_stats[s]["passed"] / max(solver_stats[s]["total"], 1) * 100
                  for s in solvers]
    mean_devs = [
        sum(solver_stats[s]["deviations"]) / max(len(solver_stats[s]["deviations"]), 1)
        for s in solvers
    ]

    colors = [SOLVER_COLORS.get(s, DEFAULT_COLOR) for s in solvers]

    # Pass rate bar chart
    bars1 = ax1.bar(solvers, pass_rates, color=colors, alpha=0.8)
    ax1.set_ylabel("Pass Rate (%)", fontsize=12)
    ax1.set_title("Pass Rate by Solver", fontsize=12, fontweight="bold")
    ax1.set_ylim(0, 105)
    ax1.axhline(y=100, color="green", linestyle="--", alpha=0.5)
    ax1.grid(True, alpha=0.3, axis="y")
    for bar, rate in zip(bars1, pass_rates):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                 f"{rate:.0f}%", ha="center", fontsize=10)

    # Mean deviation bar chart
    bars2 = ax2.bar(solvers, mean_devs, color=colors, alpha=0.8)
    ax2.set_ylabel("Mean Deviation (%)", fontsize=12)
    ax2.set_title("Mean Deviation by Solver", fontsize=12, fontweight="bold")
    ax2.axhline(y=5.0, color="red", linestyle="--", alpha=0.5, label="5% threshold")
    ax2.grid(True, alpha=0.3, axis="y")
    ax2.legend()
    for bar, dev in zip(bars2, mean_devs):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                 f"{dev:.2f}%", ha="center", fontsize=10)

    fig.suptitle(title, fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()

    return _fig_to_png(fig)


def generate_dashboard(
    suite_result: Dict[str, Any],
    suite_history: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[str] = None,
) -> DashboardReport:
    """Generate complete dashboard report.

    Args:
        suite_result: current suite regression result (from detect_suite_regression)
        suite_history: optional list of past suite results for trend chart
        output_dir: optional directory to save outputs

    Returns:
        DashboardReport with JSON data and PNG figures
    """
    results = suite_result.get("results", [])
    summary = suite_result.get("summary", {})

    # Generate figures
    figures = {}

    # 1. Status table
    status_png = fig_status_table(results)
    if status_png:
        figures["status_table"] = status_png

    # 2. Deviation histogram
    hist_png = fig_deviation_histogram(results)
    if hist_png:
        figures["deviation_histogram"] = hist_png

    # 3. Regression trend (if history available)
    if suite_history:
        trend_png = fig_regression_trend(suite_history)
        if trend_png:
            figures["regression_trend"] = trend_png

    # 4. Solver comparison
    comp_png = fig_solver_comparison(results)
    if comp_png:
        figures["solver_comparison"] = comp_png

    # Build report data
    report_data = {
        "suite_id": suite_result.get("suite_id", "unknown"),
        "overall_status": suite_result.get("overall_status", "unknown"),
        "summary": summary,
        "n_cases": len(results),
        "n_passed": summary.get("n_passed", 0),
        "n_failed": summary.get("n_failed", 0),
        "pass_rate": summary.get("pass_rate", 0.0),
        "solver_stats": summary.get("solver_stats", {}),
        "deviation_stats": summary.get("deviation_stats", {}),
        "figures_generated": list(figures.keys()),
    }

    report = DashboardReport(report_data, figures)

    if output_dir:
        report.save(output_dir)

    return report
