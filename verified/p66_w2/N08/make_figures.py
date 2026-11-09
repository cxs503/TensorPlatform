#!/usr/bin/env python3
"""make_figures —— 把蒸馏账本（coverage/graph/gaps/manifest）画成核验图（域无关）。

图 1 覆盖分解：每节点判据按状态堆叠 + 证据文件数 + 探针退出码（左/右双轴）
图 2 知识图谱：实体按类型着色、边按关系分色（规模与连通性一眼可核）
图 3 判据对阈值：图谱规模 / 缺口条数 / 判据覆盖 与 spec 阈值对比

用法：
    python3 tools/distill/make_figures.py --out-dir artifacts/x/out --fig-dir artifacts/x/figures
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

STATUS_COLORS = {
    "PASS_CLAIMED": "#2e7d32",
    "FAIL_CLAIMED": "#c62828",
    "EVIDENCE_ONLY": "#f9a825",
    "NO_EVIDENCE": "#9e9e9e",
}
TYPE_COLORS = {
    "node": "#1e88e5",
    "criterion": "#8e24aa",
    "artifact": "#00897b",
    "repo": "#6d4c41",
    "gap": "#e53935",
}


def load(out_dir: Path) -> tuple[dict, dict, dict, dict]:
    cov = json.loads((out_dir / "coverage_matrix.json").read_text(encoding="utf-8"))
    graph = json.loads((out_dir / "knowledge_graph.json").read_text(encoding="utf-8"))
    gaps = json.loads((out_dir / "gaps.json").read_text(encoding="utf-8"))
    summary = json.loads((out_dir / "distill_summary.json").read_text(encoding="utf-8"))
    return cov, graph, gaps, summary


def fig_coverage(cov: dict, summary: dict, fig_dir: Path, bundle: dict) -> Path:
    nodes = [n["node_key"] for n in cov["nodes"]]
    order = ["PASS_CLAIMED", "FAIL_CLAIMED", "EVIDENCE_ONLY", "NO_EVIDENCE"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    bottom = [0] * len(nodes)
    for status in order:
        vals = [
            sum(1 for r in cov["rows"] if r["node_key"] == k and r["status"] == status)
            for k in nodes
        ]
        ax.bar(nodes, vals, bottom=bottom, color=STATUS_COLORS[status], label=status)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_ylabel("criteria count")
    ax.set_title("Criterion coverage by node (status stacked)")
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    ax2 = axes[1]
    ev = {n["node_key"]: (len(n["evidence"]["files"]), n["evidence"]["probes"]) for n in bundle["nodes"]}
    xs = range(len(nodes))
    ax2.bar(xs, [ev[k][0] for k in nodes], width=0.55, color="#546e7a", label="evidence files")
    for i, k in enumerate(nodes):
        for j, probe in enumerate(ev[k][1]):
            ok = probe.get("exit_code") == 0
            ax2.scatter(i + (j - 0.5) * 0.18, -0.9, marker="o", s=90,
                        facecolors="#2e7d32" if ok else "none",
                        edgecolors="#2e7d32" if ok else "#c62828", zorder=5)
    ax2.set_xticks(list(xs))
    ax2.set_xticklabels(nodes)
    ax2.set_ylabel("evidence files")
    ax2.set_title("Evidence files (bars); test probes (dots: pass=solid, fail=hollow)")
    ax2.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = fig_dir / "fig1_coverage_and_evidence.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_graph(graph_doc: dict, fig_dir: Path) -> Path:
    graph = graph_doc["graph"]
    g = nx.Graph()
    for ent in graph["entities"]:
        g.add_node(ent["id"], etype=ent["type"])
    for edge in graph["edges"]:
        g.add_edge(edge["source"], edge["target"], relation=edge["relation"])
    pos = nx.spring_layout(g, seed=7, k=0.32, iterations=120)
    fig, ax = plt.subplots(figsize=(11, 8))
    nodes_by_type: dict[str, list[str]] = {}
    for ent in graph["entities"]:
        nodes_by_type.setdefault(ent["type"], []).append(ent["id"])
    for etype, ids in nodes_by_type.items():
        nx.draw_networkx_nodes(g, pos, nodelist=ids, node_size=90 if etype != "node" else 300,
                               node_color=TYPE_COLORS.get(etype, "#757575"), label=f"{etype} ({len(ids)})",
                               ax=ax, alpha=0.92)
    rel_ids = {}
    for edge in graph["edges"]:
        rel_ids.setdefault(edge["relation"], []).append((edge["source"], edge["target"]))
    palette = ["#90a4ae", "#4db6ac", "#ffb74d", "#b39ddb", "#ef9a9a", "#a5d6a7", "#f48fb1"]
    for i, (rel, pairs) in enumerate(sorted(rel_ids.items())):
        nx.draw_networkx_edges(g, pos, edgelist=pairs, edge_color=palette[i % len(palette)],
                               width=0.6, alpha=0.5, ax=ax, label=f"{rel} ({len(pairs)})")
    nx.draw_networkx_labels(g, pos, labels={n: n.replace("node:", "").replace("crit:", "")[:12]
                                            for n in g.nodes if n.startswith(("node:", "repo:", "gap:"))},
                            font_size=7, ax=ax)
    ax.set_title(f"Knowledge graph: {len(g.nodes)} entities / {g.number_of_edges()} edges")
    ax.legend(fontsize=7, loc="lower left")
    ax.axis("off")
    fig.tight_layout()
    path = fig_dir / "fig2_knowledge_graph.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_thresholds(summary: dict, gaps: dict, fig_dir: Path) -> Path:
    spec = summary["spec"]
    stats = summary["graph_stats"]
    cov = summary["coverage"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    items = [
        ("entities", stats["entities"], spec["graph_min_entities"]),
        ("edges", stats["edges"], spec["graph_min_edges"]),
        ("gaps", gaps["count"], spec["gaps_min"]),
    ]
    ax = axes[0]
    labels = [i[0] for i in items]
    actual = [i[1] for i in items]
    target = [i[2] for i in items]
    ax.bar(labels, actual, color="#1e88e5", label="actual")
    ax.plot(labels, target, "r_", markersize=40, label="threshold")
    for i, (a, t) in enumerate(zip(actual, target)):
        ax.text(i, a, f"{a}", ha="center", va="bottom", fontsize=9)
    ax.set_yscale("log")
    ax.set_title("Scale criteria vs threshold")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    counts = cov["counts"]
    keys = list(counts)
    ax.bar(keys, [counts[k] for k in keys], color=[STATUS_COLORS.get(k, "#757575") for k in keys])
    ax.set_title(f"Criterion status ({cov['total']} total)")
    ax.tick_params(axis="x", rotation=25, labelsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[2]
    rules: dict[str, int] = {}
    for gap in gaps["gaps"]:
        rules[gap["rule"]] = rules.get(gap["rule"], 0) + 1
    ax.barh(list(rules), list(rules.values()), color="#e53935")
    ax.set_title("Gap rules fired")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    path = fig_dir / "fig3_criteria_vs_threshold.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--fig-dir", required=True)
    args = ap.parse_args(argv)
    out_dir, fig_dir = Path(args.out_dir), Path(args.fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    cov, graph, gaps, summary = load(out_dir)
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    paths = [
        fig_coverage(cov, summary, fig_dir, bundle),
        fig_graph(graph, fig_dir),
        fig_thresholds(summary, gaps, fig_dir),
    ]
    print(json.dumps({"figures": [str(p) for p in paths]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())