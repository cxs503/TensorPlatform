"""Figure generation: calibration curves, ROC curves, feature importance.

Generates publication-quality PNG figures for the risk calibrator report.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import roc_curve, auc


def _fig_to_base64(fig) -> str:
    """Convert matplotlib figure to base64 PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def plot_calibration_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    model_name: str,
    n_bins: int = 10,
) -> tuple[plt.Figure, dict]:
    """Plot reliability diagram (calibration curve)."""
    fig, ax = plt.subplots(1, 1, figsize=(8, 6))

    # Perfect calibration line
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration", linewidth=2)

    # Bin the predictions
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    bin_centers = []
    bin_accs = []
    bin_counts = []

    for i in range(n_bins):
        lo, hi = bin_boundaries[i], bin_boundaries[i + 1]
        if i < n_bins - 1:
            mask = (y_prob >= lo) & (y_prob < hi)
        else:
            mask = (y_prob >= lo) & (y_prob <= hi)

        if mask.sum() > 0:
            bin_centers.append((lo + hi) / 2)
            bin_accs.append(y_true[mask].mean())
            bin_counts.append(int(mask.sum()))

    # Plot calibration points with size proportional to count
    max_count = max(bin_counts) if bin_counts else 1
    sizes = [50 + 200 * (c / max_count) for c in bin_counts]
    ax.scatter(bin_centers, bin_accs, s=sizes, alpha=0.7, label=model_name, zorder=3)

    # ECE annotation
    ece = sum(
        (c / len(y_true)) * abs(acc - conf)
        for c, acc, conf in zip(
            bin_counts,
            bin_accs,
            bin_centers,
        )
    )

    ax.set_xlabel("Mean Predicted Probability", fontsize=12)
    ax.set_ylabel("Fraction of Positives", fontsize=12)
    ax.set_title(f"Calibration Curve — {model_name}\nECE = {ece:.4f}", fontsize=14)
    ax.legend(loc="upper left")
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1])
    ax.grid(True, alpha=0.3)

    return fig, {"ece": float(ece), "n_bins": n_bins, "model": model_name}


def plot_roc_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    model_name: str,
) -> tuple[plt.Figure, dict]:
    """Plot ROC curve with AUC annotation."""
    fig, ax = plt.subplots(1, 1, figsize=(8, 6))

    fpr, tpr, _ = roc_curve(y_true, y_prob)
    roc_auc = auc(fpr, tpr)

    ax.plot(fpr, tpr, linewidth=2, label=f"{model_name} (AUC = {roc_auc:.3f})")
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="Random classifier")

    ax.set_xlabel("False Positive Rate", fontsize=12)
    ax.set_ylabel("True Positive Rate", fontsize=12)
    ax.set_title(f"ROC Curve — {model_name}", fontsize=14)
    ax.legend(loc="lower right")
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1])
    ax.grid(True, alpha=0.3)

    return fig, {"auc": float(roc_auc), "model": model_name}


def plot_feature_importance(
    feature_names: list[str],
    importances: np.ndarray,
    model_name: str,
) -> tuple[plt.Figure, dict]:
    """Plot horizontal bar chart of feature importances."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 6))

    # Sort by importance
    sorted_idx = np.argsort(importances)
    sorted_names = [feature_names[i] for i in sorted_idx]
    sorted_importances = importances[sorted_idx]

    y_pos = np.arange(len(sorted_names))
    ax.barh(y_pos, sorted_importances, color="steelblue", alpha=0.8)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(sorted_names, fontsize=10)
    ax.set_xlabel("Feature Importance", fontsize=12)
    ax.set_title(f"Feature Importance — {model_name}", fontsize=14)
    ax.grid(True, alpha=0.3, axis="x")

    return fig, {"model": model_name}


def generate_all_figures(
    y_test: np.ndarray,
    lr_proba: np.ndarray,
    gbt_proba: np.ndarray,
    feature_names: list[str],
    lr_coefficients: np.ndarray,
    gbt_importances: np.ndarray,
    output_dir: str | Path = "artifacts/m9912/figures",
) -> list[dict]:
    """Generate all figures and return list of figure metadata."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = []

    # 1. Calibration curves
    for proba, name in [(lr_proba, "Logistic Regression"), (gbt_proba, "Gradient Boosting")]:
        fig, meta = plot_calibration_curve(y_test, proba, name)
        png_path = output_dir / f"calibration_{name.lower().replace(' ', '_')}.png"
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        figures.append({
            "name": f"calibration_{name.lower().replace(' ', '_')}",
            "caption": f"Reliability diagram for {name} calibrator",
            "png_path": str(png_path),
            "metrics": meta,
        })

    # 2. ROC curves
    for proba, name in [(lr_proba, "Logistic Regression"), (gbt_proba, "Gradient Boosting")]:
        fig, meta = plot_roc_curve(y_test, proba, name)
        png_path = output_dir / f"roc_{name.lower().replace(' ', '_')}.png"
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        figures.append({
            "name": f"roc_{name.lower().replace(' ', '_')}",
            "caption": f"ROC curve for {name} calibrator",
            "png_path": str(png_path),
            "metrics": meta,
        })

    # 3. Feature importance (GBT)
    fig, meta = plot_feature_importance(feature_names, gbt_importances, "Gradient Boosting")
    png_path = output_dir / "feature_importance_gbt.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "feature_importance_gbt",
        "caption": "Feature importance from gradient boosting calibrator",
        "png_path": str(png_path),
        "metrics": meta,
    })

    # 4. LR coefficients
    fig, meta = plot_feature_importance(feature_names, np.abs(lr_coefficients), "Logistic Regression")
    png_path = output_dir / "lr_coefficients.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "lr_coefficients",
        "caption": "Absolute coefficient magnitudes from logistic regression calibrator",
        "png_path": str(png_path),
        "metrics": meta,
    })

    return figures


if __name__ == "__main__":
    # Demo with synthetic data
    np.random.seed(42)
    n = 200
    y = np.random.binomial(1, 0.2, n)
    lr_p = np.clip(y * 0.6 + np.random.beta(2, 5, n), 0, 1)
    gbt_p = np.clip(y * 0.7 + np.random.beta(2, 4, n), 0, 1)

    features = ["feat1", "feat2", "feat3", "feat4", "feat5"]
    lr_coefs = np.array([0.5, -0.3, 0.8, -0.1, 0.2])
    gbt_imps = np.array([0.25, 0.15, 0.35, 0.10, 0.15])

    figs = generate_all_figures(y, lr_p, gbt_p, features, lr_coefs, gbt_imps)
    for f in figs:
        print(f"Generated: {f['name']} -> {f['png_path']}")
