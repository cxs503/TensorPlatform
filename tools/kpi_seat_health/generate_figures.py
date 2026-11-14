#!/usr/bin/env python3
"""生成 KPI-SEAT-HEALTH 计量报告图。"""
import json
import base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path

RESULT_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "kpi_seat_health" / "result.json"
OUTDIR = Path(__file__).resolve().parents[2] / "artifacts" / "kpi_seat_health"

def load_result():
    with open(RESULT_PATH) as f:
        return json.load(f)

def fig1_health_gauge(result):
    """健康分分布柱状图。"""
    buckets = result["seat_health"]["score_buckets"]
    labels = [b["bucket"] for b in buckets]
    counts = [b["count"] for b in buckets]
    avgs = [b["avg_score"] for b in buckets]
    
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ["#e74c3c", "#e67e22", "#f39c12", "#27ae60"]
    bars = ax.bar(labels, counts, color=colors, edgecolor="white", linewidth=1.5)
    for bar, avg in zip(bars, avgs):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"avg={avg:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax.set_ylabel("席位数", fontsize=12)
    ax.set_title("KPI-SEAT-HEALTH: 健康分分布", fontsize=14, fontweight="bold")
    ax.set_ylim(0, max(counts) * 1.2)
    plt.tight_layout()
    path = OUTDIR / "fig1_health_distribution.png"
    fig.savefig(path, dpi=150)
    plt.close()
    return path

def fig2_current_vs_target(result):
    """当前值 vs 目标值对比。"""
    current = result["current"]
    target = result["target"]
    gap = result["gap"]
    
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(["当前值", "目标值"], [current, target], color=["#e74c3c", "#27ae60"], edgecolor="white", linewidth=1.5)
    ax.set_ylabel("健康席位比例 (%)", fontsize=12)
    ax.set_title(f"KPI-SEAT-HEALTH: 当前 {current}% vs 目标 {target}% (缺口 {gap} pp)", fontsize=13, fontweight="bold")
    ax.set_ylim(0, 100)
    for bar, val in zip(bars, [current, target]):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"{val:.1f}%", ha="center", va="bottom", fontsize=12, fontweight="bold")
    # 画目标线
    ax.axhline(y=80, color="#27ae60", linestyle="--", alpha=0.5, label="消账门 80%")
    ax.legend()
    plt.tight_layout()
    path = OUTDIR / "fig2_current_vs_target.png"
    fig.savefig(path, dpi=150)
    plt.close()
    return path

def fig3_marker_breakdown(result):
    """病标记分布。"""
    ss = result["seat_health"]["summary"]
    markers = ["RECUR_FAIL", "LOW_SUCCESS", "MEMORY_STARVED"]
    counts = [ss.get("recur_fail_seats", 0), ss.get("low_success_seats", 0), ss.get("memory_starved_seats", 0)]
    total_unhealthy = ss.get("unhealthy_seats", 1)
    
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ["#e74c3c", "#e67e22", "#3498db"]
    bars = ax.bar(markers, counts, color=colors, edgecolor="white", linewidth=1.5)
    for bar, cnt in zip(bars, counts):
        pct = cnt / total_unhealthy * 100
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"{cnt} ({pct:.0f}%)", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_ylabel("席位数", fontsize=12)
    ax.set_title(f"KPI-SEAT-HEALTH: 病标记分布 (共 {total_unhealthy} 病席位)", fontsize=13, fontweight="bold")
    ax.set_ylim(0, max(counts) * 1.25)
    plt.tight_layout()
    path = OUTDIR / "fig3_marker_breakdown.png"
    fig.savefig(path, dpi=150)
    plt.close()
    return path

def encode_png(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def main():
    result = load_result()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    
    paths = [fig1_health_gauge(result), fig2_current_vs_target(result), fig3_marker_breakdown(result)]
    print(f"Figures saved: {[str(p) for p in paths]}")
    
    # 生成 figures 列表 for report
    figures = []
    for p in paths:
        figures.append({
            "name": p.stem,
            "caption": {
                "fig1_health_distribution": "健康分分布：critical(<40) / poor(40-59) / warning(60-79) / healthy(≥80)",
                "fig2_current_vs_target": "当前值 vs 目标值对比（消账门 80%）",
                "fig3_marker_breakdown": "病标记分布：RECUR_FAIL / LOW_SUCCESS / MEMORY_STARVED",
            }.get(p.stem, ""),
            "png_base64": encode_png(p),
        })
    
    # 保存 figures JSON
    fig_path = OUTDIR / "figures.json"
    fig_path.write_text(json.dumps(figures, ensure_ascii=False), encoding="utf-8")
    print(f"Figures JSON saved to {fig_path}")

if __name__ == "__main__":
    main()
