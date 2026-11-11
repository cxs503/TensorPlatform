#!/usr/bin/env python3
"""Figures for the terminal-attribution report — every number read from the artifacts.

Outputs (PNG, 150 dpi) into ``docs/p226w3_n09/figures``:

1. ``fig1_class_distribution.png`` — terminal population by attribution class (log scale),
   with the failure-only composition inset;
2. ``fig2_evidence_ladder.png``    — which evidence rung produced the tool-call counter;
3. ``fig3_replay_yield.png``       — pre-registered replay study vs the un-gated baseline;
4. ``fig4_rule_usage.png``         — which rule fired for each class (audit of the classifier).

No number is typed by hand: each is computed from ``acceptance.json`` /
``terminal_missions.jsonl`` at generation time.
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import terminal_attribution as ta  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "p226w3_n09")
FIG = os.path.join(ROOT, "docs", "p226w3_n09", "figures")

ORDER = list(ta.FAILURE_CLASSES) + [ta.CLASS_DELIVERED_OK, ta.CLASS_UNKNOWN]
COLORS = {
    ta.CLASS_INFRA_NO_TOOLCALL: "#b2182b",
    ta.CLASS_INFRA_LOST: "#ef8a62",
    ta.CLASS_MODEL_ERROR: "#fddbc7",
    ta.CLASS_BUDGET_EXHAUSTED: "#f7d9a0",
    ta.CLASS_POLICY_BLOCKED: "#8c510a",
    ta.CLASS_PHYSICS_HONEST_FAIL: "#2166ac",
    ta.CLASS_DELIVERED_OK: "#4d9221",
    ta.CLASS_UNKNOWN: "#999999",
}


def load():
    with open(os.path.join(ART, "acceptance.json"), encoding="utf-8") as fh:
        acc = json.load(fh)
    records = ta.load_snapshot(os.path.join(ART, "terminal_missions.jsonl.gz"))
    results = ta.attribute_all(records)
    return acc, records, results


def fig_class_distribution(summary, results):
    counts = summary["class_counts"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    names = [c for c in ORDER if counts.get(c)]
    vals = [counts[c] for c in names]
    ax.barh(range(len(names)), vals, color=[COLORS[c] for c in names])
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("terminal missions (log scale)")
    ax.set_title(f"attribution of {summary['terminals']} terminal missions\n"
                 f"coverage {summary['attribution_coverage'] * 100:.2f}% "
                 f"(UNKNOWN = {counts.get(ta.CLASS_UNKNOWN, 0)})", fontsize=10)
    for i, v in enumerate(vals):
        ax.text(v * 1.05, i, f"{v}", va="center", fontsize=8)
    ax.invert_yaxis()

    infra = counts.get(ta.CLASS_INFRA_NO_TOOLCALL, 0) + counts.get(ta.CLASS_INFRA_LOST, 0)
    runtime = infra + counts.get(ta.CLASS_MODEL_ERROR, 0) + counts.get(ta.CLASS_BUDGET_EXHAUSTED, 0)
    physics = counts.get(ta.CLASS_PHYSICS_HONEST_FAIL, 0)
    policy = counts.get(ta.CLASS_POLICY_BLOCKED, 0)
    parts = [("infra idle/lost", infra, COLORS[ta.CLASS_INFRA_NO_TOOLCALL]),
             ("model error", counts.get(ta.CLASS_MODEL_ERROR, 0), COLORS[ta.CLASS_MODEL_ERROR]),
             ("budget", counts.get(ta.CLASS_BUDGET_EXHAUSTED, 0), COLORS[ta.CLASS_BUDGET_EXHAUSTED]),
             ("policy", policy, COLORS[ta.CLASS_POLICY_BLOCKED]),
             ("physics honest FAIL", physics, COLORS[ta.CLASS_PHYSICS_HONEST_FAIL])]
    parts = [p for p in parts if p[1]]
    total = sum(p[1] for p in parts)
    bottom = 0.0
    for label, value, colour in parts:
        share = value / total
        ax2.bar(["failures"], [share], bottom=bottom, color=colour, label=f"{label} ({value})")
        ax2.text(0.0, bottom + share / 2, f"{share * 100:.1f}%", ha="center", va="center",
                 fontsize=9, color="white" if share > 0.06 else "black")
        bottom += share
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("share of terminal failures")
    ax2.set_title(f"runtime-side share = {runtime / total * 100:.1f}% of {total} failures\n"
                  "attribution separates idling from falsification", fontsize=10)
    ax2.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2)
    fig.tight_layout()
    path = os.path.join(FIG, "fig1_class_distribution.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_evidence_ladder(results):
    import collections
    sources = collections.Counter(r["tool_calls_source"] for r in results)
    order = ["result.tool_calls", "session.decision", "changed_files_lower_bound",
             "finish_record_lower_bound", "session_rounds_lower_bound",
             "worker_crash_prestart", "no_session", "absent"]
    names = [s for s in order if sources.get(s)]
    vals = [sources[s] for s in names]
    fig, ax = plt.subplots(figsize=(9, 4))
    colours = ["#2166ac" if "result" in n or "session.decision" in n else
               ("#f7d9a0" if "lower_bound" in n else "#b2182b") for n in names]
    ax.bar(range(len(names)), vals, color=colours)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax.set_yscale("log")
    ax.set_ylabel("missions (log scale)")
    measured = sum(v for n, v in zip(names, vals) if "lower_bound" not in n and n != "absent"
                   and n != "worker_crash_prestart")
    lower = sum(v for n, v in zip(names, vals) if "lower_bound" in n)
    ax.set_title(f"tool-call evidence ladder: {measured} counted by the worker, "
                 f"{lower} labelled lower bounds, "
                 f"{sum(v for n, v in zip(names, vals) if n in ('worker_crash_prestart','no_session'))} "
                 f"proved zero", fontsize=10)
    fig.tight_layout()
    path = os.path.join(FIG, "fig2_evidence_ladder.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, sources


def fig_replay_yield(acc):
    exp_nodes = acc["experiment"]["nodes"]
    live = sum(1 for n in exp_nodes if n.get("reached_live"))
    total = len(exp_nodes)
    base = acc["replay_baseline"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    labels = ["attribution-gated\n(this study)", "un-gated platform\nhistory"]
    vals = [(live / total) if total else 0.0, base["requeue_yield"] or 0.0]
    ax.bar(labels, vals, color=["#2166ac", "#999999"])
    ax.axhline(4 / 6, color="crimson", ls="--", lw=1, label="pre-registered 4/6 = 66.7%")
    ax.set_ylim(0, 1)
    ax.set_ylabel("replays reaching a live worker (tool_calls > 0)")
    ax.set_title(f"pre-registered replay study: {live}/{total} nodes live", fontsize=10)
    for i, v in enumerate(vals):
        ax.text(i, v + 0.02, f"{v * 100:.1f}%", ha="center", fontsize=9)
    ax.legend(fontsize=8)

    per_node = base["per_node"]
    nodes = list(per_node)[:40]
    reach = [per_node[n]["reached_worker"] / per_node[n]["attempts"] for n in nodes]
    ax2.hist(reach, bins=10, color="#4d9221", edgecolor="white")
    ax2.set_xlabel("per-node replay reach rate (reached worker / attempts)")
    ax2.set_ylabel("nodes")
    ax2.set_title(f"un-gated replays per node (n={len(per_node)} nodes, "
                  f"{base['replays']} attempts)", fontsize=10)
    fig.tight_layout()
    path = os.path.join(FIG, "fig3_replay_yield.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_rule_usage(results):
    import collections
    rules = collections.Counter(r["rule"] for r in results)
    names = sorted(rules)
    vals = [rules[n] for n in names]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(range(len(names)), vals, color="#2166ac")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=25, ha="right", fontsize=8)
    ax.set_yscale("log")
    ax.set_ylabel("missions (log scale)")
    ax.set_title("which rule fired (rule order is fixed, so this is an audit of precedence)",
                 fontsize=10)
    fig.tight_layout()
    path = os.path.join(FIG, "fig4_rule_usage.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, rules


def main() -> int:
    os.makedirs(FIG, exist_ok=True)
    acc, records, results = load()
    summary = acc["summary"]
    out = {
        "fig1": fig_class_distribution(summary, results),
        "fig3": fig_replay_yield(acc),
    }
    out["fig2"], sources = fig_evidence_ladder(results)
    out["fig4"], rules = fig_rule_usage(results)
    manifest = {"figures": out, "tool_call_sources": sources, "rules": rules,
                "digest": ta.sha256_of({k: {kk: vv for kk, vv in v.items()} if False else v
                                        for k, v in
                                        (("sources", sources), ("rules", rules))})}
    with open(os.path.join(ART, "figures_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({k: os.path.relpath(v, ROOT) for k, v in out.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())