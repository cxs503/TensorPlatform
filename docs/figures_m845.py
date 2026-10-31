#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m845 图件：从 artifacts/audit_report_m845.json 直接绘制，无手写数值。

产物： artifacts/fig1_proposal_aggregation_m845.png （提案聚合谱系）
       artifacts/fig2_dag_corrected_m845.png       （按 ARRAY 实际值重绘的 DAG）
"""
import json
import os

WORK = "/srv/tensor/agent/work/m845"
ART = os.path.join(WORK, "artifacts")
a = json.load(open(os.path.join(ART, "audit_report_m845.json"), encoding="utf-8"))
nodes, g, acc = a["nodes"], a["graph"], a["proposal_accounting"]
ms = a["milestones"]
ext2ms = {n["ext_id"]: n["milestone_id"] for n in nodes}
ms_band = {m["id"]: (m["name"].split("(")[1].rstrip(")") if "(" in m["name"] else m["name"]) for m in ms}


def fig1():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seeded = sorted(acc["seeded_native_ids"] + acc["seeded_arbiter_slugs"], key=lambda d: d["key"])
    uns = acc["unseeded"]
    names = [n["ext_id"] for n in nodes]
    row = 1.0
    top = len(seeded) + len(uns) + 2.0
    y_of, y = {}, top
    for d in seeded:
        y_of[d["key"]] = y
        y -= row
    y -= row * 1.2
    divider = y + row * 0.6
    for d in uns:
        y_of[d["key"]] = y
        y -= row
    bottom = y

    def ny(ext):
        i = names.index(ext)
        return top - (i + 0.5) * (top - bottom) / len(names)

    fig, ax = plt.subplots(figsize=(12.5, 7.6))
    for d in seeded:
        yy = y_of[d["key"]]
        ax.scatter(0.06, yy, s=46, color="#2b6cb0", zorder=3)
        ax.text(0.045, yy, d["key"], ha="right", va="center", fontsize=8.2)
    for d in uns:
        yy = y_of[d["key"]]
        ax.scatter(0.06, yy, s=52, color="#c53030", marker="X", zorder=3)
        ax.text(0.045, yy, d["key"], ha="right", va="center", fontsize=7.8, color="#c53030")
    for ext in names:
        yy = ny(ext)
        mat = ext in g["maturity_nodes"]
        ax.scatter(0.94, yy, s=150, color="#c6f6d5" if mat else "#bee3f8",
                   edgecolor="#2f855a" if mat else "#2b6cb0", linewidth=1.4, zorder=3)
        ax.text(0.955, yy, ext, ha="left", va="center", fontsize=9)
    for d in seeded:
        for tgt in d["nodes"]:
            if tgt in names:
                ax.plot([0.07, 0.93], [y_of[d["key"]], ny(tgt)],
                        color="#2b6cb0", alpha=0.45, lw=1.1, zorder=1)
    ax.axhline(divider, color="#cbd5e0", lw=1.2, ls="--", zorder=0)
    ax.text(0.5, divider + 0.30, "%d unseeded proposals (= %d - %d; see Table 3-6 / D4)"
            % (len(uns), acc["total"], acc["identifiable_refs"]), ha="center", fontsize=8.5,
            color="#c53030")
    ax.set_xlim(-0.02, 1.10)
    ax.set_ylim(bottom - 0.6, top + 0.8)
    ax.set_yticks([])
    ax.set_xticks([0.06, 0.94])
    ax.set_xticklabels([
        "Original proposals: %d (%s)\ntraceable: %d" % (
            acc["total"], " / ".join("M%s:%s" % (m, c) for m, c in acc["per_mission"].items()),
            acc["identifiable_refs"]),
        "Plan 4 campaign nodes: %d\n(maturity: %d)" % (len(nodes), len(g["maturity_nodes"]))], fontsize=9)
    ax.set_title("Fig.1  Proposal aggregation lineage: %d raw proposals -> %d traceable (blue) -> "
                 "%d campaign nodes; %d unseeded (red x)"
                 % (acc["total"], acc["identifiable_refs"], len(nodes), len(uns)), fontsize=10)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    p = os.path.join(ART, "fig1_proposal_aggregation_m845.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig2():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chain = g["longest_chain"]
    chain_edges = set(zip(chain, chain[1:]))
    ms_ids = sorted(set(ext2ms.values()))
    band = {m: i for i, m in enumerate(ms_ids)}
    pos = {}
    for m in ms_ids:
        group = sorted([e for e in ext2ms if ext2ms[e] == m])
        for i, e in enumerate(group):
            x = 0.5 if len(group) == 1 else 0.07 + i / (len(group) - 1) * 0.86
            pos[e] = (x, band[m])
    fig, ax = plt.subplots(figsize=(12.0, 6.8))
    for u, v in g["edges"]:
        hl = (u, v) in chain_edges
        ax.annotate("", xy=pos[v], xytext=pos[u],
                    arrowprops=dict(arrowstyle="-|>", lw=3.2 if hl else 1.6,
                                    color="#c53030" if hl else "#4a5568",
                                    alpha=0.95 if hl else 0.55, shrinkA=26, shrinkB=26))
    for ext, (x, yy) in pos.items():
        mat = ext in g["maturity_nodes"]
        ax.scatter(x, yy, s=2100, color="#c6f6d5" if mat else "#bee3f8",
                   edgecolor="#2f855a" if mat else "#2b6cb0", linewidth=1.8, zorder=3)
        deps = g["deps_by_ext"][ext]
        ax.text(x, yy, ext.replace("W4-N", "N") + "\ndeps: " +
                (",".join(d.replace("W4-N", "") for d in deps) if deps else "—"),
                ha="center", va="center", fontsize=8.6, zorder=4)
    for m in ms_ids:
        yy = band[m]
        ax.axhline(yy - 0.5, color="#e2e8f0", lw=1, zorder=0)
        ax.text(1.02, yy, "M%d  %s\n%d nodes: %s" % (
            m, ms_band[m], len(g["ms_coverage"]["M%d" % m]),
            ", ".join(x.replace("W4-N", "N") for x in g["ms_coverage"]["M%d" % m])),
            fontsize=8.6, va="center")
    ax.set_xlim(-0.05, 1.32)
    ax.set_ylim(-0.75, len(ms_ids) - 0.35)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.set_title("Fig.2  Plan 4 dependency DAG redrawn from the actual ARRAY values in seed_plan4.sql "
                 "(%d edges); longest chain %s = %d nodes / %d steps (red arrows)"
                 % (g["edge_count"], " -> ".join(chain), g["longest_chain_len_nodes"],
                    g["longest_chain_len_steps"]), fontsize=10)
    for s in ("top", "right", "left", "bottom"):
        ax.spines[s].set_visible(False)
    ax.text(-0.03, -0.70, "green = maturity node (is_maturity=true), blue = frontier node; "
                          "arrow = dep -> node; text inside node = its actual dependencies",
            fontsize=8, color="#4a5568")
    fig.tight_layout()
    p = os.path.join(ART, "fig2_dag_corrected_m845.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


if __name__ == "__main__":
    print(fig1())
    print(fig2())