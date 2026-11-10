"""报告图：等级分布（声明 vs 评定）、三层覆盖、依赖图。全部数字来自流水线返回值。

matplotlib 用 Agg 后端（无显示环境安全）。任何绘图失败都降级返回空列表，
不阻断流水线——图是证据的呈现层，不该成为证据链的单点故障。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence


def _mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


LEVEL_ORDER = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
COLOURS = {
    "NOT_SUPPORTED": "#8c8c8c",
    "STUB": "#d62728",
    "MOCK": "#ff7f0e",
    "WITHHELD": "#1f77b4",
    "REAL": "#2ca02c",
}


def fig_level_distribution(result: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    """图1：声明等级 vs 评定等级分布（对账一眼看清 overclaim）。"""
    try:
        plt = _mpl()
    except Exception:  # noqa: BLE001
        return []
    declared: dict[str, int] = {}
    for t in result.get("layers", {}).get("triples", []):
        lv = str(t.get("declared_level", ""))
        declared[lv] = declared.get(lv, 0) + 1
    derived = result.get("evidence", {}).get("level_distribution", {}) or {}
    fig, ax = plt.subplots(figsize=(8, 4.2))
    xs = list(LEVEL_ORDER)
    w = 0.38
    d_vals = [declared.get(k, 0) for k in xs]
    r_vals = [int(derived.get(k, 0)) for k in xs]
    ax.bar([i - w / 2 for i in range(len(xs))], d_vals, width=w, label="declared",
           color="#b0b0b0", edgecolor="black", linewidth=0.5)
    ax.bar([i + w / 2 for i in range(len(xs))], r_vals, width=w, label="derived",
           color=[COLOURS[k] for k in xs], edgecolor="black", linewidth=0.5)
    for i, (a, b) in enumerate(zip(d_vals, r_vals)):
        ax.text(i - w / 2, a + 0.2, str(a), ha="center", fontsize=9)
        ax.text(i + w / 2, b + 0.2, str(b), ha="center", fontsize=9)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([x.replace("_", "\n") for x in xs], fontsize=9)
    ax.set_ylabel("capability entries")
    ax.set_title("Declared vs derived evidence level "
                 f"(n={len(result.get('layers', {}).get('triples', []))})")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    out = out_dir / "fig1_level_distribution.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return [{"name": out.name, "caption": "声明等级与流水线评定等级分布对比", "path": str(out)}]


def fig_tier_coverage(result: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    """图2：三层填充覆盖 + 误用治理修复前后对照。"""
    try:
        plt = _mpl()
    except Exception:  # noqa: BLE001
        return []
    stats = result.get("layers", {}).get("stats", {})
    cov = stats.get("tier_coverage_ratio", {}) or {}
    labels = ["capability", "delivery", "evidence", "delivery+evidence"]
    keys = ["capability", "delivery", "evidence", "both"]
    vals = [float(cov.get(k, 0.0)) * 100 for k in keys]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    bars = ax1.bar(labels, vals, color=["#4c72b0", "#55a868", "#c44e52", "#8172b2"])
    for b, v in zip(bars, vals):
        ax1.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}%", ha="center", fontsize=9)
    ax1.set_ylim(0, 110)
    ax1.set_ylabel("tier coverage (%)")
    ax1.set_title(f"Three-tier coverage (n={stats.get('capability_entries', 0)} entries)")
    ax1.grid(axis="y", alpha=0.3)

    ver = result.get("verification", {})
    before = ver.get("before", {}) or {}
    after = ver.get("after", {}) or {}
    cats = ["NOT_SUPPORTED\nmisuse", "orphan\ndelivery"]
    b_vals = [int(before.get("not_supported_misuse_count") or 0),
              int(before.get("orphan_delivery_count") or 0)]
    a_vals = [int(after.get("not_supported_misuse_count") or 0),
              int(after.get("orphan_delivery_count") or 0)]
    idx = range(len(cats))
    w = 0.35
    ax2.bar([i - w / 2 for i in idx], b_vals, width=w, label="before", color="#d62728")
    ax2.bar([i + w / 2 for i in idx], a_vals, width=w, label="after patch", color="#2ca02c")
    for i, (b, a) in enumerate(zip(b_vals, a_vals)):
        ax2.text(i - w / 2, b + 0.15, str(b), ha="center", fontsize=9)
        ax2.text(i + w / 2, a + 0.15, str(a), ha="center", fontsize=9)
    ax2.set_xticks(list(idx))
    ax2.set_xticklabels(cats, fontsize=9)
    ax2.set_ylabel("count")
    ax2.set_title("Misuse governance: before vs after patch")
    ax2.legend()
    ax2.grid(axis="y", alpha=0.3)
    out = out_dir / "fig2_tier_coverage_and_misuse.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return [{"name": out.name, "caption": "三层覆盖率与误用治理修复前后对照", "path": str(out)}]


def fig_dependency_graph(result: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    """图3：能力依赖图（圆环布局；环/孤立节点红圈标注）。"""
    try:
        plt = _mpl()
    except Exception:  # noqa: BLE001
        return []
    g = result.get("graph", {}) or {}
    nodes: Sequence[str] = list(g.get("nodes") or [])
    if not nodes:
        return []
    edges = [(e["dep"], e["dependent"]) for e in g.get("edges") or []
             if isinstance(e, dict) and "dep" in e and "dependent" in e]
    roles = g.get("roles") or {}
    summary = g.get("summary") or {}
    orphan = set(summary.get("orphan_domain") or [])
    cycles = summary.get("cycles") or []
    cyc_nodes = {n for c in cycles for n in c}

    n = len(nodes)
    pos = {node: (math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
           for i, node in enumerate(nodes)}
    fig, ax = plt.subplots(figsize=(10, 8))
    for src, dst in edges:
        if src in pos and dst in pos:
            x1, y1 = pos[src]
            x2, y2 = pos[dst]
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                        arrowprops=dict(arrowstyle="->", color="#555555",
                                        shrinkA=12, shrinkB=12, lw=1.2))
    for node, (x, y) in pos.items():
        is_tool = roles.get(node) == "tooling"
        colour = "#b0b0b0" if is_tool else "#4c72b0"
        lw, ec = 1.0, "black"
        if node in cyc_nodes:
            colour, ec, lw = "#d62728", "red", 2.5
        elif node in orphan:
            colour, ec, lw = "#ff7f0e", "red", 2.5
        ax.scatter([x], [y], s=520 if not is_tool else 380, c=colour,
                   edgecolors=ec, linewidths=lw, zorder=3)
        ax.text(x * 1.16, y * 1.16, node, fontsize=7.5, ha="center", va="center")
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.axis("off")
    ax.set_title("Capability dependency graph "
                 f"(nodes={summary.get('node_count', 0)}, edges={summary.get('edge_count', 0)}, "
                 f"cycles={summary.get('cycle_count', 0)}, "
                 f"orphan_ratio_domain={summary.get('orphan_ratio_domain', 0):.2%})",
                 fontsize=10)
    out = out_dir / "fig3_dependency_graph.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return [{"name": out.name, "caption": "能力依赖图（灰=工具类节点，蓝=领域节点）", "path": str(out)}]


def make_figures(result: Mapping[str, Any], out_dir: Path | str) -> list[dict[str, str]]:
    """生成全部图；返回 [{name, caption, path}]（失败项自动缺席，不抛）。"""
    out = Path(out_dir)
    figs: list[dict[str, str]] = []
    for fn in (fig_level_distribution, fig_tier_coverage, fig_dependency_graph):
        try:
            figs.extend(fn(result, out))
        except Exception:  # noqa: BLE001 - 绘图失败不阻断证据链
            continue
    return figs


__all__ = ["make_figures", "fig_level_distribution", "fig_tier_coverage",
           "fig_dependency_graph"]