"""Figures for the cross-architecture normalisation report (matplotlib, Agg)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

INK = "#1b1b1b"


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)
    return path


def alpha_bars(alpha_sets: Sequence[Dict[str, Any]], out: Path,
               title: str = "Architecture normalisation factor $\\alpha_{arch}=T_{arch}/T_{ref}$") -> Path:
    """alpha_sets: list of {"label":..., "items":[(arm, alpha, lo, hi, layer)]}."""
    rows = [(lbl, it) for s in alpha_sets for it in s["items"] for lbl in [s["label"]]]
    if not rows:
        raise ValueError("no alpha values")
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    labels = [f"{arm}" for _, (arm, *_rest) in rows]
    vals = np.array([it[1] for _, it in rows])
    lo = np.array([it[2] for _, it in rows])
    hi = np.array([it[3] for _, it in rows])
    x = np.arange(len(rows))
    colors = ["#2d6cdf" if lbl == "injected" else "#c8503b" for lbl, _ in rows]
    ax.bar(x, vals, color=colors, alpha=0.85, width=0.6)
    ax.errorbar(x, vals, yerr=[vals - lo, hi - vals], fmt="none", ecolor=INK, capsize=4, lw=1.2)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{a}\n[{lbl}]" for lbl, (a, *_r) in rows], fontsize=7.5)
    ax.axhline(1.0, color="#888888", ls="--", lw=1)
    ax.set_ylabel("$\\alpha_{arch}$  (95% CI)")
    ax.set_title(title, fontsize=10)
    handles = [plt.Rectangle((0, 0), 1, 1, color="#2d6cdf", alpha=.85),
               plt.Rectangle((0, 0), 1, 1, color="#c8503b", alpha=.85)]
    ax.legend(handles, ["injected (known truth)", "measured (real hosts)"], fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    return _save(fig, out)


def fit_scatter(obs: Sequence[float], fitted: Sequence[float], out: Path, r2: float,
                label: str = "real measurement") -> Path:
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    o = np.asarray(obs); f = np.asarray(fitted)
    ax.scatter(o, f, s=14, alpha=0.7, color="#2d6cdf", edgecolor="none", label=f"{label} (n={len(o)})")
    lo, hi = float(min(o.min(), f.min())), float(max(o.max(), f.max()))
    ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="1:1")
    ax.set_xlabel("observed  $\\log T_{raw}$")
    ax.set_ylabel("mixed-model fitted  $\\hat{x}\\beta+\\hat{u}_{host}$")
    ax.set_title(f"Linear mixed-effects fit (log scale), $R^2$ = {r2:.4f}", fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    return _save(fig, out)


def before_after_box(raw_pct: Sequence[float], norm_pct: Sequence[float], out: Path,
                     label: str = "loong64-like vs x86-like (injected)") -> Path:
    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    bp = ax.boxplot([raw_pct, norm_pct], labels=["un-normalised", "normalised (÷$\\alpha$)"],
                    widths=0.5, patch_artist=True, medianprops=dict(color=INK))
    for patch, c in zip(bp["boxes"], ["#c8503b", "#2d9a5b"]):
        patch.set_facecolor(c); patch.set_alpha(0.55)
    ax.axhline(50, color="#c8503b", ls=":", lw=1.2)
    ax.axhline(-50, color="#c8503b", ls=":", lw=1.2)
    ax.axhline(5, color="#2d9a5b", ls=":", lw=1.2)
    ax.axhline(-5, color="#2d9a5b", ls=":", lw=1.2)
    ax.set_ylabel("performance difference vs reference arm (%)")
    ax.set_title(f"Before/after normalisation — {label}", fontsize=10)
    ax.text(2.28, 50, "±50% negative-control band", fontsize=7, color="#c8503b", va="bottom", ha="right")
    ax.text(2.28, 5, "±5% criterion band", fontsize=7, color="#2d9a5b", va="bottom", ha="right")
    ax.grid(axis="y", alpha=0.25)
    return _save(fig, out)


def alpha_by_workload(per_workload: Dict[str, float], out: Path, arm: str) -> Path:
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    items = sorted(per_workload.items())
    x = np.arange(len(items))
    ax.plot(x, [v for _, v in items], "o-", color="#2d6cdf")
    ax.axhline(1.0, color="#888888", ls="--", lw=1)
    ax.set_xticks(x); ax.set_xticklabels([k for k, _ in items], rotation=30, ha="right", fontsize=7.5)
    ax.set_ylabel("$T_{arm}/T_{ref}$ per workload")
    ax.set_title(f"Per-workload ratio for {arm}: is the architecture factor constant?", fontsize=10)
    ax.grid(alpha=0.25)
    return _save(fig, out)