"""出图：校准曲线 / walk-forward AUC 时间线 / 通胀趋势 / 逐层判别力森林图。

只用 matplotlib（Agg 后端，无显示环境亦可跑）。每张图都附「基准线」：
- 校准图对角线 = 完美校准；
- AUC 时间线横线 = 判据阈值 0.70 与随机线 0.50；
- 通胀图 Sen 斜率直线 = 趋势估计；
- 森林图竖线 = pooled 同日 AUC（用于识别「拖后腿」的层）。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _finite(pairs: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(x, y) for x, y in pairs if x == x and y == y]


def calibration_plot(cal: Mapping[str, Any], path: str | Path, *,
                     title: str = "Out-of-sample calibration") -> str:
    """可靠性曲线：x=预测概率，y=实际通过率，点大小∝桶样本量。"""
    fig, ax = plt.subplots(figsize=(5.2, 5.0))
    ax.plot([0, 1], [0, 1], "--", color="#888888", lw=1.0, label="ideal (y=x)")
    bins = cal.get("bins") or []
    pts = _finite([(b["pred"], b["actual"]) for b in bins if b.get("n", 0) > 0])
    if pts:
        xs, ys = zip(*pts)
        sizes = [max(12.0, 4.0 * math.sqrt(b["n"])) for b in bins if b.get("n", 0) > 0]
        ax.scatter(xs, ys, s=sizes, c="#1f77b4", alpha=0.85, edgecolors="white",
                   linewidths=0.8, zorder=3, label="bins (size ∝ n)")
        ax.plot(xs, ys, "-", color="#1f77b4", alpha=0.6, lw=1.2, zorder=2)
    ax.set_xlabel("predicted P(merged)")
    ax.set_ylabel("observed pass rate")
    ax.set_title(f"{title}\nBrier={cal.get('brier', float('nan')):.4f}  "
                 f"(threshold 0.15)   n={cal.get('n', 0)}", fontsize=9)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)


def auc_timeline_plot(walk: Mapping[str, Any], path: str | Path, *,
                      threshold: float = 0.70,
                      title: str = "Walk-forward AUC by test day") -> str:
    """逐窗 AUC（含测试样本量标注）+ 阈值线 + pooled 值。"""
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    w = [x for x in (walk.get("windows") or []) if not math.isnan(x.get("auc", float("nan")))]
    if w:
        days = [x["day"] for x in w]
        aucs = [x["auc"] for x in w]
        ax.plot(range(len(w)), aucs, "o-", color="#d62728", lw=1.6, ms=6)
        for i, x in enumerate(w):
            ax.annotate(f"n={x['n_test']}\npos={x['test_pos']}/neg={x['test_neg']}",
                        (i, x["auc"]), textcoords="offset points", xytext=(0, 9),
                        ha="center", fontsize=6.5, color="#444444")
        ax.set_xticks(range(len(w)))
        ax.set_xticklabels([d[5:] for d in days], fontsize=7, rotation=0)
        ax.axhline(float(walk.get("pooled_auc", float("nan"))), color="#d62728",
                   ls=":", lw=1.0,
                   label=f"pooled AUC={walk.get('pooled_auc', float('nan')):.3f}")
    ax.axhline(threshold, color="#2ca02c", ls="--", lw=1.0, label=f"criterion {threshold}")
    ax.axhline(0.5, color="#999999", ls="-", lw=0.8, label="random 0.5")
    ax.set_ylim(0.0, 1.02)
    ax.set_ylabel("AUC (test window)")
    ax.set_xlabel("test day (month-day)")
    ax.set_title(title, fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)


def inflation_plot(inf: Mapping[str, Any], path: str | Path, *,
                   title: str = "Grade inflation (Mann-Kendall)") -> str:
    """日度高分级占比 + Sen 趋势线；副标题带 p 值与斜率。"""
    series = inf.get("daily_series") or []
    active = [s for s in series if s["n"] >= inf.get("min_day_n", 0)]
    primary = (inf.get("metrics") or {}).get("mean_score") or {}
    mk = primary.get("mk") or {}
    ts = primary.get("theil_sen") or {}
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.6, 5.2), sharex=True)
    if active:
        x = list(range(len(active)))
        days = [s["day"][5:] for s in active]
        ax1.plot(x, [s["high_share"] for s in active], "o-", color="#9467bd", ms=4, lw=1.2)
        ax1.set_ylabel("share S>=S2", fontsize=8)
        ax1.set_title(f"{title}\nMK p={mk.get('p', float('nan')):.4f}  "
                      f"Sen slope={ts.get('slope', float('nan')):+.4f}/day  "
                      f"(p<0.05 & slope>0 = ALERT)", fontsize=9)
        ax2.plot(x, [s["mean_score"] for s in active], "o-", color="#1f77b4", ms=4, lw=1.2)
        slope = ts.get("slope", float("nan"))
        if slope == slope:
            med = primary.get("median", 0.0)
            mid = (len(x) - 1) / 2.0
            y0 = med - slope * mid
            ax2.plot([0, len(x) - 1], [y0, y0 + slope * (len(x) - 1)], "--",
                     color="#ff7f0e", lw=1.3, label="Theil-Sen")
            ax2.legend(fontsize=7, loc="best")
        ax2.set_ylabel("mean score S+0.5E", fontsize=8)
        ax2.set_xticks(x)
        ax2.set_xticklabels(days, fontsize=6.5, rotation=45)
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    ax2.set_xlabel("grade day (month-day)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)


def decomposition_plot(decomp: Mapping[str, Mapping[str, Any]], path: str | Path, *,
                       title: str = "Discriminability by stratum") -> str:
    """逐层同日 AUC 森林图（含 bootstrap 95% 区间；竖线 = 全体同日 AUC 参考）。"""
    dims = [d for d, v in decomp.items() if v.get("n_strata_usable")]
    if not dims:
        fig, ax = plt.subplots(figsize=(5, 2.4))
        ax.text(0.5, 0.5, "no usable stratum (WITHHELD)", ha="center", va="center",
                fontsize=9)
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return str(path)

    rows: list[tuple[str, float, float, float, int]] = []
    labels: list[str] = []
    for d in dims:
        for r in decomp[d].get("per_stratum", []):
            if r["status"] != "ok":
                continue
            rows.append((d, r["same_day_auc"], r["ci"]["lo"], r["ci"]["hi"], r["n"]))
            labels.append(f"{d}:{r['stratum']}  (n={r['n']}, {r['n_pos']}/{r['n_neg']})")

    fig, ax = plt.subplots(figsize=(6.8, max(3.0, 0.26 * len(rows) + 1.4)))
    ys = list(range(len(rows)))
    for y, (d, a, lo, hi, _n) in zip(ys, rows):
        ax.plot([lo, hi], [y, y], color="#bbbbbb", lw=1.0, zorder=1)
        ax.scatter([a], [y], s=34, color="#1f77b4", zorder=3)
    ax.axvline(0.5, color="#999999", ls="-", lw=0.8)
    ax.axvline(0.70, color="#2ca02c", ls="--", lw=1.0)
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xlim(0.0, 1.02)
    ax.set_xlabel("same-day AUC (95% bootstrap CI)")
    ax.set_title(title + "\n(dashed = 0.70 criterion, solid = 0.50 random)", fontsize=9)
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)