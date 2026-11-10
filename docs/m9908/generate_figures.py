"""Generate figures for the m9908 mission report.

Produces:
1. PCE vs MC convergence comparison
2. Sobol sensitivity indices for Ishigami function
3. Drift detection conflict matrix
"""
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from backend.services.pce_uq import Marginal, fit
from backend.services.physical_constants.drift_scanner import ConstantScanner
from backend.services.uq_pipeline.sobol import sobol_from_pce, sobol_mc_saltelli


def ishigami(x1: float = 0.0, x2: float = 0.0, x3: float = 0.0,
             a: float = 7.0, b: float = 0.1) -> float:
    return math.sin(x1) + a * math.sin(x2) ** 2 + b * x3 ** 4 * math.sin(x1)


def figure_pce_vs_mc_convergence(output_path: Path) -> Path:
    """Compare PCE and MC convergence for a quadratic model."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # Model: f(x) = x², x ~ U(-1,1). Analytic: E=1/3, Var=8/45
    marginals = [Marginal(name="x", family="uniform", lower=-1.0, upper=1.0)]

    def quadratic(x: float = 0.0) -> float:
        return x * x

    # PCE convergence (vary order)
    orders = list(range(1, 11))
    pce_means = []
    pce_stds = []
    pce_n_evals = []
    for order in orders:
        pce = fit(func=quadratic, marginals=marginals, method="projection", order=order)
        pce_means.append(pce.mean)
        pce_stds.append(pce.std)
        pce_n_evals.append(pce.n_model_evaluations)

    # MC convergence (vary N)
    mc_ns = [100, 500, 1000, 5000, 10000, 50000]
    mc_means = []
    mc_stds = []
    for n in mc_ns:
        from backend.services.pce_uq import monte_carlo
        mc = monte_carlo(func=quadratic, marginals=marginals, n=n, sampler="lhs", seed=42)
        mc_means.append(mc.mean)
        mc_stds.append(mc.std)

    # Analytic values
    E_analytic = 1.0 / 3.0
    Var_analytic = 8.0 / 45.0
    std_analytic = math.sqrt(Var_analytic)

    # Plot mean convergence
    ax = axes[0]
    ax.semilogy(orders, [abs(m - E_analytic) for m in pce_means], "o-", label="PCE (projection)", lw=2)
    ax.semilogy(mc_ns, [abs(m - E_analytic) for m in mc_means], "s-", label="MC (LHS)", lw=2)
    ax.axhline(1e-10, color="gray", ls="--", label="machine precision")
    ax.set_xlabel("PCE order / MC sample size N", fontsize=11)
    ax.set_ylabel("|E[y] - E_analytic|", fontsize=11)
    ax.set_title("Mean convergence: PCE vs Monte Carlo", fontsize=12)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    # Plot std convergence
    ax = axes[1]
    ax.semilogy(orders, [abs(s - std_analytic) for s in pce_stds], "o-", label="PCE (projection)", lw=2)
    ax.semilogy(mc_ns, [abs(s - std_analytic) for s in mc_stds], "s-", label="MC (LHS)", lw=2)
    ax.axhline(1e-10, color="gray", ls="--", label="machine precision")
    ax.set_xlabel("PCE order / MC sample size N", fontsize=11)
    ax.set_ylabel("|σ[y] - σ_analytic|", fontsize=11)
    ax.set_title("Standard deviation convergence", fontsize=12)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return output_path


def figure_sobol_ishigami(output_path: Path) -> Path:
    """Sobol indices for the Ishigami test function."""
    pi = math.pi
    marginals = [
        Marginal(name="x1", family="uniform", lower=-pi, upper=pi),
        Marginal(name="x2", family="uniform", lower=-pi, upper=pi),
        Marginal(name="x3", family="uniform", lower=-pi, upper=pi),
    ]

    # PCE-based Sobol
    pce = fit(func=ishigami, marginals=marginals, method="projection", order=8)
    sobol_pce = sobol_from_pce(pce)

    # Analytic Sobol indices for Ishigami (a=7, b=0.1)
    # From Saltelli & Anill (2010)
    a, b = 7.0, 0.1
    V1 = 0.5 * (1 + b * pi**4 / 5)**2  # approx
    V2 = a**2 / 8
    V3 = 0
    V13 = b**2 * pi**8 / 18 * 0.5  # interaction term (approx)
    # Simplified: use the PCE result as reference

    fig, ax = plt.subplots(figsize=(8, 5))
    names = ["x1", "x2", "x3"]
    s1 = [sobol_pce.first_order[n] for n in names]
    st = [sobol_pce.total_order[n] for n in names]

    x = np.arange(len(names))
    width = 0.35
    bars1 = ax.bar(x - width/2, s1, width, label="First-order $S_i$", color="#1f4e79")
    bars2 = ax.bar(x + width/2, st, width, label="Total-order $S_{Ti}$", color="#e08a1e")

    ax.set_xlabel("Input variable", fontsize=11)
    ax.set_ylabel("Sobol index", fontsize=11)
    ax.set_title("Ishigami function: variance-based sensitivity indices", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)

    # Annotate bars
    for bar in bars1:
        height = bar.get_height()
        ax.annotate(f"{height:.3f}", xy=(bar.get_x() + bar.get_width()/2, height),
                    xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9)
    for bar in bars2:
        height = bar.get_height()
        ax.annotate(f"{height:.3f}", xy=(bar.get_x() + bar.get_width()/2, height),
                    xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9)

    fig.tight_layout()
    fig.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return output_path


def figure_drift_detection(output_path: Path) -> Path:
    """Visualize drift detection results from the codebase scan."""
    scanner = ConstantScanner(relative_tolerance=1e-3)
    report = scanner.scan_and_report(
        Path("backend/services"),
        exclude_patterns=["__pycache__", ".git", "node_modules", "tests"],
    )

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # Left: summary pie chart
    ax = axes[0]
    summary = report["summary"]
    labels = ["OK", "DRIFT", "CONFLICT"]
    sizes = [summary["ok"], summary["drift"], summary["conflict"]]
    colors = ["#2ca02c", "#ff7f0e", "#d62728"]
    if sum(sizes) > 0:
        ax.pie(sizes, labels=labels, colors=colors, autopct="%1.0f%%", startangle=90, textprops={"fontsize": 11})
    ax.set_title(f"Constant drift status\n({summary['total_extractions']} extractions, {summary['constants_scanned']} constants)", fontsize=11)

    # Right: bar chart of max relative deviation per constant
    ax = axes[1]
    conflicts = report["conflicts"]
    if conflicts:
        keys = [c["canonical_key"] for c in conflicts]
        devs = [c["max_relative_deviation"] for c in conflicts]
        statuses = [c["status"] for c in conflicts]
        colors_bar = {"OK": "#2ca02c", "DRIFT": "#ff7f0e", "CONFLICT": "#d62728"}
        bar_colors = [colors_bar[s] for s in statuses]
        y = np.arange(len(keys))
        ax.barh(y, devs, color=bar_colors)
        ax.set_yticks(y)
        ax.set_yticklabels(keys, fontsize=9)
        ax.set_xlabel("Max relative deviation from SSOT", fontsize=10)
        ax.set_title("Drift magnitude per constant", fontsize=11)
        ax.axvline(1e-3, color="red", ls="--", lw=1.5, label="tolerance (0.1%)")
        ax.legend(fontsize=9)
        ax.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    out_dir = Path("docs/m9908")
    out_dir.mkdir(parents=True, exist_ok=True)

    p1 = figure_pce_vs_mc_convergence(out_dir / "fig1_pce_vs_mc_convergence.png")
    print(f"Generated: {p1}")

    p2 = figure_sobol_ishigami(out_dir / "fig2_sobol_ishigami.png")
    print(f"Generated: {p2}")

    p3 = figure_drift_detection(out_dir / "fig3_drift_detection.png")
    print(f"Generated: {p3}")
