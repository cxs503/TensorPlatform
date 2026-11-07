#!/usr/bin/env python3
"""合并口自动化证据图：判决分布 / 留因归因 / 审计链增长。

只读 ``artifacts/merge_master/`` 下的运行产物，不联网、不猜数。
    python3 scripts/gen_merge_master_figures.py
"""

from __future__ import annotations

import collections
import json
import os
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(HERE, "artifacts", "merge_master")
FIGS = os.path.join(ART, "figs")


def load(name: str) -> Dict[str, Any]:
    with open(os.path.join(ART, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def audit_kinds() -> "collections.Counter":
    path = os.path.join(ART, "audit.jsonl")
    c = collections.Counter()
    if os.path.exists(path):
        for line in open(path, "r", encoding="utf-8"):
            line = line.strip()
            if line:
                c[json.loads(line)["kind"]] += 1
    return c


def fig_decisions(runs: List[Dict[str, Any]]) -> str:
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))
    names = [r["label"] for r in runs]
    merged = [r["counts"]["merged"] for r in runs]
    left = [r["counts"]["left"] for r in runs]
    skip = [r["counts"]["skipped"] for r in runs]
    x = range(len(runs))
    ax[0].bar(x, merged, label="merge (executed/planned)", color="#2e7d32")
    ax[0].bar(x, left, bottom=merged, label="left with reason",
              color="#c62828")
    ax[0].bar(x, skip, bottom=[m + l for m, l in zip(merged, left)],
              label="skip (already merged)", color="#90a4ae")
    ax[0].set_xticks(list(x))
    ax[0].set_xticklabels(names, fontsize=9)
    ax[0].set_ylabel("PR count")
    ax[0].set_title("Reconcile outcome per run (real queued PRs)")
    ax[0].legend(fontsize=8, loc="lower right")
    for i, r in enumerate(runs):
        ax[0].text(i, r["counts"]["total"] + 0.3,
                   "n=%d" % r["counts"]["total"], ha="center", fontsize=8)

    reasons = collections.Counter()
    for r in runs:
        for row in r["decisions"]:
            if row["action"] == "LEAVE":
                for reason in row["reasons"]:
                    reasons[reason] += 1
    if reasons:
        labels = list(reasons)
        ax[1].barh(labels, [reasons[k] for k in labels], color="#ef6c00")
        ax[1].set_xlabel("occurrences across runs")
        ax[1].set_title("Left-in-queue reason attribution")
        for i, k in enumerate(labels):
            ax[1].text(reasons[k] + 0.05, i, str(reasons[k]), va="center",
                       fontsize=8)
    ax[1].grid(axis="x", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIGS, "fig1_outcomes_and_reasons.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_audit(kinds: "collections.Counter") -> str:
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.0))
    labels = list(kinds)
    ax[0].bar(labels, [kinds[k] for k in labels], color="#1565c0")
    ax[0].set_title("Audit chain records by kind (hash-chained JSONL)")
    ax[0].tick_params(axis="x", rotation=20, labelsize=9)
    ax[0].set_ylabel("records")
    for i, k in enumerate(labels):
        ax[0].text(i, kinds[k] + 0.5, str(kinds[k]), ha="center", fontsize=8)

    gates = collections.Counter()
    fails = collections.Counter()
    for name in ("plan_live.json", "rehearsal.json", "ablation_no_boundary.json"):
        p = os.path.join(ART, name)
        if not os.path.exists(p):
            continue
        for dec in load(name)["decisions"]:
            for g in dec["gates"]:
                gates[g["gate"]] += 1
                if not g["ok"]:
                    fails[g["gate"]] += 1
    order = ["G1_acceptance", "G2_quality", "G3_tests", "G4_conflict",
             "G2b_boundary"]
    seen = [g for g in order if g in gates]
    ax[1].bar(seen, [gates[g] - fails[g] for g in seen], label="pass",
              color="#2e7d32")
    ax[1].bar(seen, [fails[g] for g in seen],
              bottom=[gates[g] - fails[g] for g in seen], label="fail",
              color="#c62828")
    ax[1].set_title("Gate verdicts over all evaluated PRs")
    ax[1].tick_params(axis="x", rotation=20, labelsize=8)
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    out = os.path.join(FIGS, "fig2_audit_and_gates.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> int:
    os.makedirs(FIGS, exist_ok=True)
    runs = [
        {"label": "plan (live queue)", "counts": load("plan_live.json")["counts"],
         "decisions": load("plan_live.json")["decisions"]},
    ]
    for name, label in (("rehearsal.json", "rehearsal (execute path)"),
                        ("ablation_no_boundary.json", "ablation: no boundary")):
        p = os.path.join(ART, name)
        if os.path.exists(p):
            d = load(name)
            runs.append({"label": label, "counts": d["counts"],
                         "decisions": d["decisions"]})
    outs = [fig_decisions(runs), fig_audit(audit_kinds())]
    print(json.dumps({"figures": outs}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())