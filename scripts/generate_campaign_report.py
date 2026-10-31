#!/usr/bin/env python3
"""Generate campaign plan report figures for m644.

Reads the master_plan.json, computes statistics programmatically,
and produces two PNG figures:
  1. Milestone progress bar chart (nodes per milestone + risk)
  2. DAG dependency graph
"""
import json
import os
import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

PLAN_PATH = "/srv/tensor/agent/work/m611/artifacts/master_plan.json"
OUT_DIR = "/srv/tensor/agent/work/m644/artifacts"

os.makedirs(OUT_DIR, exist_ok=True)


def load_plan():
    with open(PLAN_PATH) as f:
        return json.load(f)


# English short names for milestones
MS_SHORT = {
    "M0": "Foundation\nVerification",
    "M1": "Single-Physics\nCalibration",
    "M2": "Two-Way\nCoupling",
    "M3": "Full-Chain\nIntegration",
    "M4": "Scale-Up\n& HPC",
    "M5": "Experimental\nValidation",
}


def fig1_milestone_progress(plan):
    """Bar chart: nodes per milestone with risk distribution."""
    ms_nodes = {}
    for node in plan["nodes"]:
        ms = node.get("milestone", "?")
        if ms not in ms_nodes:
            ms_nodes[ms] = {"low": 0, "medium": 0, "high": 0, "total_budget": 0, "criteria_count": 0}
        risk = node.get("risk", "medium")
        ms_nodes[ms][risk] = ms_nodes[ms].get(risk, 0) + 1
        ms_nodes[ms]["total_budget"] += node.get("budget_hours", 0)
        ms_nodes[ms]["criteria_count"] += len(node.get("acceptance_criteria", []))

    ms_order = sorted(ms_nodes.keys())

    fig, ax1 = plt.subplots(figsize=(12, 6))

    x = np.arange(len(ms_order))
    width = 0.25

    low_vals = [ms_nodes[m]["low"] for m in ms_order]
    med_vals = [ms_nodes[m]["medium"] for m in ms_order]
    high_vals = [ms_nodes[m]["high"] for m in ms_order]

    bars1 = ax1.bar(x - width, low_vals, width, label='Low Risk', color='#4CAF50', edgecolor='white')
    bars2 = ax1.bar(x, med_vals, width, label='Medium Risk', color='#FF9800', edgecolor='white')
    bars3 = ax1.bar(x + width, high_vals, width, label='High Risk', color='#F44336', edgecolor='white')

    ax1.set_xlabel('Milestone', fontsize=12)
    ax1.set_ylabel('Number of Nodes', fontsize=12)
    ax1.set_title('Ice Navigation Campaign (m611): Nodes per Milestone by Risk Level', fontsize=14)
    ax1.set_xticks(x)
    ax1.set_xticklabels([MS_SHORT.get(m, m) for m in ms_order], fontsize=9)
    ax1.legend()

    for i, m in enumerate(ms_order):
        total = low_vals[i] + med_vals[i] + high_vals[i]
        criteria = ms_nodes[m]["criteria_count"]
        budget = ms_nodes[m]["total_budget"]
        ax1.text(i, total + 0.3, f'{criteria} criteria\n{budget:.0f}h budget',
                ha='center', va='bottom', fontsize=8, color='#333')

    ax1.set_ylim(0, max(sum(v) for v in zip(low_vals, med_vals, high_vals)) + 3)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "fig1_milestone_progress.png")
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Figure 1 saved: {path}")
    return path


def fig2_dag_graph(plan):
    """DAG dependency graph visualization."""
    nodes = {n["id"]: n for n in plan["nodes"]}
    edges = plan["dag_edges"]

    in_degree = {n["id"]: 0 for n in plan["nodes"]}
    for e in edges:
        if e["to"] in in_degree:
            in_degree[e["to"]] += 1

    layers = {}
    remaining = set(in_degree.keys())
    layer = 0
    while remaining:
        current = {n for n in remaining if in_degree[n] == 0}
        if not current:
            current = {min(remaining)}
        for n in current:
            layers[n] = layer
        for n in current:
            remaining.discard(n)
            for e in edges:
                if e["from"] == n and e["to"] in in_degree:
                    in_degree[e["to"]] -= 1
        layer += 1

    layer_nodes = {}
    for nid, l in layers.items():
        if l not in layer_nodes:
            layer_nodes[l] = []
        layer_nodes[l].append(nid)

    pos = {}
    for l, nids in layer_nodes.items():
        nids.sort()
        for i, nid in enumerate(nids):
            pos[nid] = (i - len(nids) / 2, -l)

    fig, ax = plt.subplots(figsize=(16, 10))

    layer_colors = {
        "L1_foundation": "#2196F3",
        "L2_validation": "#4CAF50",
        "L3_integration": "#FF9800",
    }

    for e in edges:
        if e["from"] in pos and e["to"] in pos:
            x1, y1 = pos[e["from"]]
            x2, y2 = pos[e["to"]]
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                       arrowprops=dict(arrowstyle="->", color="#999", lw=0.8, connectionstyle="arc3,rad=0.1"))

    for nid, (x, y) in pos.items():
        node = nodes.get(nid, {})
        layer_type = node.get("layer", "L1_foundation")
        color = layer_colors.get(layer_type, "#999")
        risk = node.get("risk", "medium")
        edge_color = {"low": "#4CAF50", "medium": "#FF9800", "high": "#F44336"}.get(risk, "#999")
        circle = plt.Circle((x, y), 0.35, color=color, ec=edge_color, lw=2, alpha=0.85)
        ax.add_patch(circle)
        ax.text(x, y, nid, ha='center', va='center', fontsize=7, fontweight='bold', color='white')

    legend_patches = [
        mpatches.Patch(color="#2196F3", label="L1 Foundation"),
        mpatches.Patch(color="#4CAF50", label="L2 Validation"),
        mpatches.Patch(color="#FF9800", label="L3 Integration"),
    ]
    ax.legend(handles=legend_patches, loc='upper right', fontsize=10)

    max_layer = max(layers.values()) if layers else 0
    ax.set_xlim(-8, 8)
    ax.set_ylim(-max_layer - 1, 1)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title('Ice Navigation Campaign DAG (m611): 27 Nodes, 34 Edges', fontsize=14)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "fig2_dag_graph.png")
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Figure 2 saved: {path}")
    return path


def compute_stats(plan):
    """Compute all statistics programmatically from the plan data."""
    nodes = plan["nodes"]
    milestones = plan["milestones"]
    edges = plan["dag_edges"]

    total_nodes = len(nodes)
    total_edges = len(edges)
    total_criteria = sum(len(n.get("acceptance_criteria", [])) for n in nodes)
    total_budget = sum(n.get("budget_hours", 0) for n in nodes)
    nodes_without_criteria = sum(1 for n in nodes if not n.get("acceptance_criteria", []))
    criteria_coverage = (1 - nodes_without_criteria / total_nodes) * 100 if total_nodes > 0 else 0

    risk_dist = {}
    for n in nodes:
        r = n.get("risk", "medium")
        risk_dist[r] = risk_dist.get(r, 0) + 1

    layer_dist = {}
    for n in nodes:
        l = n.get("layer", "unknown")
        layer_dist[l] = layer_dist.get(l, 0) + 1

    ms_progress = {}
    for mid, mdata in milestones.items():
        ms_nodes_list = [n for n in nodes if n.get("milestone") == mid]
        ms_progress[mid] = {
            "name": mdata.get("name", mid),
            "status": mdata.get("status", "planned"),
            "gate_criteria_count": len(mdata.get("gate_criteria", [])),
            "node_count": len(ms_nodes_list),
            "criteria_count": sum(len(n.get("acceptance_criteria", [])) for n in ms_nodes_list),
            "budget_hours": sum(n.get("budget_hours", 0) for n in ms_nodes_list),
        }

    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_criteria": total_criteria,
        "total_budget_hours": total_budget,
        "nodes_without_criteria": nodes_without_criteria,
        "criteria_coverage_pct": round(criteria_coverage, 1),
        "risk_distribution": risk_dist,
        "layer_distribution": layer_dist,
        "milestone_progress": ms_progress,
    }


def png_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


if __name__ == "__main__":
    plan = load_plan()
    stats = compute_stats(plan)
    print("=== Computed Statistics ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    fig1_path = fig1_milestone_progress(plan)
    fig2_path = fig2_dag_graph(plan)

    print(f"\nFigure 1 base64 length: {len(png_to_base64(fig1_path))}")
    print(f"Figure 2 base64 length: {len(png_to_base64(fig2_path))}")
