"""外部效度报告图（matplotlib，Agg 后端，无显示依赖）。

产出 5 张图，对应报告 §结果 的四个判据族：

1. ``fig1_outcome_composition.png`` —— cohort 下游结果构成 + 各 S 级的实测合并率
2. ``fig2_calibration.png``          —— 校准曲线（predicted vs actual）+ Brier
3. ``fig3_walkforward_auc.png``      —— 逐窗 walk-forward AUC（合并/回退返工）
4. ``fig4_cusum_inflation.png``      —— S 高分占比时序 + 双侧 CUSUM 控制图
5. ``fig5_negative_control.png``     —— 随机标签负控 AUC 分布 vs 真实 AUC
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

STYLE = {
    "figure.dpi": 130,
    "font.size": 9,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.spines.top": False,
    "axes.spines.right": False,
}


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_outcome_composition(result: dict[str, Any], out_dir: Path) -> Path:
    cohort = result["cohort"]
    counts = cohort["outcome_counts"]
    order = ["merged", "rework", "reverted", "discarded", "pending"]
    labels = [k for k in order if k in counts]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.6))
    ax = axes[0]
    vals = [counts[k] for k in labels]
    ax.bar(labels, vals, color=["#2a7f62", "#d08c34", "#b3384a", "#7a7a7a", "#9bb7d4"])
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.set_title(f"(a) downstream outcome mix  n={cohort['n']}")
    ax.set_ylabel("missions")

    ax = axes[1]
    rows = result["empirical_merge_rate_by_grade"]
    xs = [r["grade"] for r in rows]
    ys = [r["merge_rate"] for r in rows]
    ns = [r["n"] for r in rows]
    ax.bar(xs, ys, color="#3f6f9f")
    for i, (y, n) in enumerate(zip(ys, ns)):
        ax.text(i, y, f"{y:.2f}\nn={n}", ha="center", va="bottom", fontsize=7)
    ax.axhline(result["cohort"]["y_merge_positives"] / max(cohort["n"], 1),
               color="#b3384a", ls="--", lw=1, label="base rate")
    ax.set_ylim(0, 1.15)
    ax.set_title("(b) observed merge rate by S grade (grade directionality)")
    ax.set_ylabel("merge rate")
    ax.legend(fontsize=7)
    return _save(fig, out_dir / "fig1_outcome_composition.png")


def fig_calibration(result: dict[str, Any], out_dir: Path) -> Path:
    curve = [b for b in result["calibration_curve"] if b["n"] > 0]
    fig, ax = plt.subplots(figsize=(4.8, 4.4))
    ax.plot([0, 1], [0, 1], color="#999", ls="--", lw=1, label="perfect (y=x)")
    xs = [b["pred"] for b in curve]
    ys = [b["actual"] for b in curve]
    sizes = [max(18, 6 * b["n"] ** 0.5) for b in curve]
    ax.scatter(xs, ys, s=sizes, color="#2a7f62", alpha=0.85, zorder=3,
               label="bins (size ∝ n)")
    ax.plot(xs, ys, color="#2a7f62", lw=1, alpha=0.6)
    for b in curve:
        ax.annotate(f"n={b['n']}", (b["pred"], b["actual"]), fontsize=6,
                    xytext=(3, -8), textcoords="offset points")
    ax.set_xlabel("predicted P(merge)  [walk-forward]")
    ax.set_ylabel("actual merge rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.set_title(f"reliability diagram  Brier={result['brier']:.4f}  "
                 f"max|dev|={result['calibration_max_dev']:.4f}")
    ax.legend(fontsize=7, loc="upper left")
    return _save(fig, out_dir / "fig2_calibration.png")


def fig_walkforward_auc(result: dict[str, Any], out_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for key, label, color, thr in (
        ("walk_forward_merge", "S grade -> merged (J1)", "#2a7f62", 0.70),
        ("walk_forward_defect", "E grade -> revert/rework (J2)", "#b3384a", 0.65),
    ):
        wf = result[key]
        days = [w["day"] for w in wf["windows"]]
        aucs = [w["auc"] for w in wf["windows"]]
        if not days:
            continue
        xs = np.arange(len(days))
        ax.plot(xs, aucs, marker="o", ms=4, lw=1.4, color=color, label=f"{label}")
        ax.axhline(thr, color=color, ls=":", lw=1)
        ax.annotate(f"thr {thr:.2f}", (xs[-1], thr), fontsize=6, color=color,
                    xytext=(-26, 3), textcoords="offset points")
        ax.set_xticks(xs)
        ax.set_xticklabels(days, rotation=45, ha="right", fontsize=7)
    ax.axhline(0.5, color="#666", ls="--", lw=0.8, label="chance 0.5")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("AUC (test-day)")
    ax.set_title(f"walk-forward discrimination   J1 pooled={result['checks']['J1_auc_merge']['value']:.3f}"
                 f"   J2 pooled={result['checks']['J2_auc_defect']['value']:.3f}")
    ax.legend(fontsize=7, loc="lower left")
    return _save(fig, out_dir / "fig3_walkforward_auc.png")


def fig_cusum(result: dict[str, Any], out_dir: Path) -> Path:
    shares = result["grade_share_series"]
    cus = result["cusum"]
    days = shares["days"]
    fig, axes = plt.subplots(2, 1, figsize=(7.6, 5.0), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.3]})
    ax = axes[0]
    ax.plot(shares["s_high_share"], marker="o", ms=4, color="#2a7f62",
            label="share of high grade (>=S2)")
    ax.plot(shares["s_any_share"], marker="s", ms=3, color="#9bb7d4",
            label="share of S grade (>=S1)")
    if cus["target"] is not None:
        ax.axhline(cus["target"], color="#b3384a", ls="--", lw=1,
                   label=f"target={cus['target']:.3f}")
    ax.set_ylabel("share")
    ax.set_title("grade-inflation watch: S-share series and two-sided CUSUM")
    ax.legend(fontsize=7)

    ax = axes[1]
    xs = np.arange(len(days))
    ax.plot(xs, cus["s_pos"], color="#d08c34", marker="^", ms=3, label="S+ (upward drift)")
    ax.plot(xs, cus["s_neg"], color="#3f6f9f", marker="v", ms=3, label="S- (downward drift)")
    if cus["threshold"] is not None:
        ax.axhline(cus["threshold"], color="#b3384a", ls="--", lw=1,
                   label=f"h={cus['threshold']:.4f}")
    for a in cus["alarms"]:
        ax.axvspan(a - 0.4, a + 0.4, color="#b3384a", alpha=0.12)
    ax.set_xticks(xs)
    ax.set_xticklabels(days, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("CUSUM")
    ax.set_xlabel("test day (UTC)")
    ax.legend(fontsize=7)
    return _save(fig, out_dir / "fig4_cusum_inflation.png")


def fig_negative_control(result: dict[str, Any], out_dir: Path) -> Path:
    nc = result["negative_control"]
    aucs = nc["aucs"]
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    if aucs:
        ax.hist(aucs, bins=10, color="#9bb7d4", edgecolor="#3f6f9f")
    ax.axvline(0.5, color="#666", ls="--", lw=1, label="chance 0.5")
    real = result["checks"]["J1_auc_merge"]["value"]
    ax.axvline(real, color="#2a7f62", lw=1.6, label=f"real AUC={real:.3f}")
    ax.axvspan(0.45, 0.55, color="#d08c34", alpha=0.15, label="accept band [0.45,0.55]")
    ax.set_xlim(0.2, 0.9)
    ax.set_xlabel("AUC with shuffled labels")
    ax.set_ylabel("draws")
    ax.set_title(f"negative control: {nc['n_draws']} shuffled-label draws\n"
                 f"mean={nc['mean_auc']:.3f}  sd={nc['sd_auc']:.3f}")
    ax.legend(fontsize=7)
    return _save(fig, out_dir / "fig5_negative_control.png")


def make_all(result: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    plt.rcParams.update(STYLE)
    out_dir = Path(out_dir)
    return {
        "fig1": fig_outcome_composition(result, out_dir),
        "fig2": fig_calibration(result, out_dir),
        "fig3": fig_walkforward_auc(result, out_dir),
        "fig4": fig_cusum(result, out_dir),
        "fig5": fig_negative_control(result, out_dir),
    }


def _finite(values) -> list[float]:
    return [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]