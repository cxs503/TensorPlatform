"""KPI-REPRO-95 结果图生成模块。

生成四张图：
  fig1: 四口径通过率 vs 95% 目标
  fig2: 失败根因分布
  fig3: 证据链阶梯
  fig4: 逐日进账与累计通过率
"""
from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _fig_to_base64(fig) -> str:
    """将 matplotlib figure 转为 base64 PNG。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def generate_figures(
    result_path: str,
    rows_path: str,
    output_dir: Optional[str] = None,
) -> list[dict]:
    """生成结果图。

    Returns:
        list of {name, caption, png_base64}
    """
    if not HAS_MATPLOTLIB:
        logger.warning("matplotlib not available, skipping figure generation")
        return []

    with open(result_path, "r", encoding="utf-8") as f:
        result = json.load(f)

    missions = []
    with open(rows_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                missions.append(json.loads(line))

    out_dir = Path(output_dir) if output_dir else Path("runs/m15200_kpi_repro/figures")
    out_dir.mkdir(parents=True, exist_ok=True)

    figures = []

    # ─── Fig1: 四口径通过率 vs 95% 目标 ───
    fig, ax = plt.subplots(figsize=(10, 6))
    labels = ["主口径", "上界", "S2", "S1"]
    values = [
        result.get("current", 0),
        result.get("current_upper", 0),
        result.get("s2_current", 0),
        result.get("s1_current", 0),
    ]
    colors = ["#e74c3c" if v < 95 else "#2ecc71" for v in values]
    bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=1.5)
    ax.axhline(y=95, color="#3498db", linestyle="--", linewidth=2, label="目标 95%")
    ax.set_ylim(0, 110)
    ax.set_ylabel("通过率 (%)")
    ax.set_title("KPI-REPRO-95: 四口径通过率 vs 目标")
    ax.legend()
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"{val:.1f}%", ha="center", va="bottom", fontweight="bold")
    plt.tight_layout()
    png_b64 = _fig_to_base64(fig)
    fig1_path = out_dir / "fig1_pass_rate_vs_target.png"
    fig.savefig(fig1_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "fig1_pass_rate_vs_target",
        "caption": "四口径通过率对比：主口径/上界/S2/S1 vs 95% 目标线",
        "png_base64": png_b64,
    })

    # ─── Fig2: 失败根因分布 ───
    fig, ax = plt.subplots(figsize=(10, 6))
    root_causes = result.get("failure_root_causes", {})
    if root_causes:
        causes = list(root_causes.keys())
        counts = list(root_causes.values())
        total_fail = sum(counts)
        percentages = [c / total_fail * 100 for c in counts]
        # 缩短标签
        short_causes = []
        for c in causes:
            if len(c) > 30:
                short_causes.append(c[:27] + "...")
            else:
                short_causes.append(c)
        colors2 = plt.cm.Set2(range(len(causes)))
        bars2 = ax.barh(short_causes[::-1], percentages[::-1], color=colors2[::-1], edgecolor="white")
        ax.set_xlabel("占比 (%)")
        ax.set_title(f"失败根因分布（共 {total_fail} 例）")
        for bar, pct in zip(bars2, percentages[::-1]):
            ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                    f"{pct:.1f}%", ha="left", va="center")
    else:
        ax.text(0.5, 0.5, "无失败根因数据", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("失败根因分布")
    plt.tight_layout()
    png_b64 = _fig_to_base64(fig)
    fig2_path = out_dir / "fig2_failure_root_causes.png"
    fig.savefig(fig2_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "fig2_failure_root_causes",
        "caption": "非通过任务的失败根因分布与占比",
        "png_base64": png_b64,
    })

    # ─── Fig3: 证据链阶梯 ───
    fig, ax = plt.subplots(figsize=(10, 6))
    ladder_labels = [
        "succeeded率",
        "报告工件覆盖",
        "worktree留存",
        "指针可解析",
        "复现官记录覆盖",
        "指针级可解析率",
    ]
    ladder_values = [
        result.get("succeeded_rate", 0),
        result.get("report_coverage", 0),
        result.get("worktree_retention", 0),
        result.get("artifact_parse_rate", 0),
        result.get("verdict_coverage", 0),
        result.get("artifact_parse_rate", 0),
    ]
    x = range(len(ladder_labels))
    ax.bar(x, ladder_values, color="#3498db", edgecolor="white", linewidth=1.5)
    ax.axhline(y=95, color="#e74c3c", linestyle="--", linewidth=2, label="目标 95%")
    ax.set_xticks(x)
    ax.set_xticklabels(ladder_labels, rotation=30, ha="right")
    ax.set_ylim(0, 110)
    ax.set_ylabel("比率 (%)")
    ax.set_title("证据链阶梯：95% 在哪一环断？")
    ax.legend()
    for i, v in enumerate(ladder_values):
        ax.text(i, v + 1, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    png_b64 = _fig_to_base64(fig)
    fig3_path = out_dir / "fig3_evidence_ladder.png"
    fig.savefig(fig3_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "fig3_evidence_ladder",
        "caption": "证据链阶梯：succeeded → 报告 → worktree → 指针 → 复现官记录 → 指针级可解析",
        "png_base64": png_b64,
    })

    # ─── Fig4: 逐日进账与累计通过率 ───
    fig, ax1 = plt.subplots(figsize=(12, 6))
    # 从 missions 提取日期
    from collections import defaultdict
    daily_counts = defaultdict(int)
    daily_passed = defaultdict(int)
    for m in missions:
        created = m.get("created_at", "")
        if created:
            date_str = str(created)[:10]  # YYYY-MM-DD
            daily_counts[date_str] += 1
            if m["status"] == "succeeded" and m["artifact_level"] in ("strong", "medium"):
                daily_passed[date_str] += 1

    if daily_counts:
        sorted_dates = sorted(daily_counts.keys())
        daily_vals = [daily_counts[d] for d in sorted_dates]
        # 累计通过率
        cum_total = 0
        cum_passed = 0
        cum_rates = []
        for d in sorted_dates:
            cum_total += daily_counts[d]
            cum_passed += daily_passed.get(d, 0)
            cum_rates.append(cum_passed / cum_total * 100 if cum_total > 0 else 0)

        ax1.bar(sorted_dates, daily_vals, color="#3498db", alpha=0.7, label="每日进账")
        ax1.set_xlabel("日期")
        ax1.set_ylabel("任务数", color="#3498db")
        ax1.tick_params(axis="y", labelcolor="#3498db")

        ax2 = ax1.twinx()
        ax2.plot(sorted_dates, cum_rates, color="#e74c3c", marker="o", linewidth=2, label="累计通过率")
        ax2.axhline(y=95, color="#2ecc71", linestyle="--", linewidth=2, label="目标 95%")
        ax2.set_ylabel("累计通过率 (%)", color="#e74c3c")
        ax2.tick_params(axis="y", labelcolor="#e74c3c")
        ax2.set_ylim(0, 110)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
        ax1.set_title("逐日复现任务进账与累计通过率")
        plt.xticks(rotation=45)
    else:
        ax1.text(0.5, 0.5, "无日期数据", ha="center", va="center", transform=ax1.transAxes)
        ax1.set_title("逐日进账与累计通过率")

    plt.tight_layout()
    png_b64 = _fig_to_base64(fig)
    fig4_path = out_dir / "fig4_daily_inflow.png"
    fig.savefig(fig4_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append({
        "name": "fig4_daily_inflow",
        "caption": "逐日复现任务进账数与累计通过率趋势",
        "png_base64": png_b64,
    })

    logger.info("Generated %d figures in %s", len(figures), out_dir)
    return figures
