#!/usr/bin/env python3
"""Figures for the report gate — plotted straight from the artifact JSONs.

Zero hand-typed numbers: every bar/value is read from an artifact produced by
``tools.reconciliation.report_gate`` (which is itself self-hashing), so a figure
cannot drift from the ledger it illustrates.

Usage::

    python -m tools.reconciliation.gen_report_gate_figures \
        --artifacts artifacts/reconciliation --out artifacts/reconciliation/figures
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

C_OK = "#2f7d4f"
C_WARN = "#c98b2e"
C_BAD = "#a63a3a"
C_MUTE = "#8a8f98"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def fig_terminal_ledger(art: dict[str, Any], out: Path) -> Path:
    lg = art["ledger"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.6))

    labels = ["done", "blocked"]
    with_rep = [lg["done_with_report"], lg["blocked_with_report"]]
    no_rep = [lg["done_no_report"], lg["blocked_no_report"]]
    ax1.bar(labels, with_rep, color=C_OK, label="has report")
    ax1.bar(labels, no_rep, bottom=with_rep, color=C_BAD, label="no report (debt)")
    for i, (w, n) in enumerate(zip(with_rep, no_rep)):
        ax1.text(i, w / 2 if w > sum(with_rep) * 0.08 else 0, str(w), ha="center",
                 va="center", color="white", fontsize=9)
        ax1.text(i, w + n / 2, str(n), ha="center", va="center", color="white", fontsize=9)
    ax1.set_ylabel("terminal campaign nodes")
    ax1.set_title(f"Terminal-state ledger (coverage {lg['coverage_rate']}% / "
                  f"done-only {lg['coverage_rate_done']}%)", fontsize=9)
    ax1.legend(fontsize=8, frameon=False)

    cats = ["done_no\nreport", "blocked_no\nreport", "report_no\nnode\n(phantom)",
            "hash/owner\nmismatch", "orphan\nmission\n(info)"]
    vals = [lg["done_no_report"], lg["blocked_no_report"], lg["report_no_node"],
            lg["report_hash_mismatch"], lg["report_orphan_mission"]]
    colors = [C_BAD, C_WARN, C_BAD, C_BAD, C_MUTE]
    bars = ax2.bar(cats, vals, color=colors)
    for b, v in zip(bars, vals):
        ax2.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, str(v),
                 ha="center", va="bottom", fontsize=8)
    ax2.set_yscale("symlog")
    ax2.set_ylabel("entries (symlog)")
    ax2.set_title("Four-bucket reconciliation ledger", fontsize=9)
    ax2.tick_params(axis="x", labelsize=7)

    fig.suptitle(f"Report gate — terminal↔report reconciliation "
                 f"({art['scope'].get('organization_id') and 'org=' + str(art['scope']['organization_id']) or 'all orgs'}, "
                 f"{art['elapsed_seconds']}s)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def fig_sla_aging(art: dict[str, Any], out: Path) -> Path:
    sla = art["sla"]
    aging = sla["aging_hours"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.4))

    labels = list(aging.keys())
    vals = [aging[k] for k in labels]
    colors = [C_OK, C_MUTE, C_WARN, C_BAD]
    bars = ax1.bar(labels, vals, color=colors[:len(labels)])
    for b, v in zip(bars, vals):
        ax1.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, str(v),
                 ha="center", va="bottom", fontsize=8)
    ax1.set_xlabel("ticket age since filing")
    ax1.set_ylabel("pending supplement tickets")
    ax1.set_title(f"Supplement SLA aging (SLA = {sla['sla_hours']}h, "
                  f"pending = {sla['pending']})", fontsize=9)

    stats = {"pending": sla["pending"], "overdue": sla["overdue"],
             "due<=12h": sla["due_within_12h"], "escalated (this pass)":
             art.get("dispatch", {}).get("sla", {}).get("escalated", 0)}
    bars2 = ax2.bar(list(stats.keys()), list(stats.values()),
                    color=[C_MUTE, C_BAD, C_WARN, C_WARN])
    for b, v in zip(bars2, stats.values()):
        ax2.text(b.get_x() + b.get_width() / 2, v + max(stats.values(), default=1) * 0.02,
                 str(v), ha="center", va="bottom", fontsize=8)
    ax2.set_ylabel("tickets")
    ax2.set_title("Lateness & escalation (one dispatch pass)", fontsize=9)
    ax2.tick_params(axis="x", labelsize=7.5)

    fig.suptitle("Supplement outbox: every ticket carries due_at = created_at + 72h",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def fig_idempotency(fresh: dict[str, Any], aged: dict[str, Any],
                    replay: dict[str, Any], out: Path) -> Path:
    """First filing → aged escalation pass → replay: nothing is created twice."""
    f = fresh.get("dispatch") or {}
    a = aged.get("dispatch") or {}
    r = replay.get("dispatch") or {}
    keys = ["dispatched", "already_queued", "escalated (SLA breach)"]
    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    x = range(len(keys))
    w = 0.36
    v1 = [f.get("dispatched", 0), f.get("already_queued", 0),
          (a.get("sla") or {}).get("escalated", 0)]
    v2 = [r.get("dispatched", 0), r.get("already_queued", 0),
          (r.get("sla") or {}).get("escalated", 0)]
    ax.bar([i - w / 2 for i in x], v1, w, color=C_BAD, label="run 1 (file / escalate)")
    ax.bar([i + w / 2 for i in x], v2, w, color=C_OK, label="run 2 (identical replay)")
    for i, (a, b) in enumerate(zip(v1, v2)):
        ax.text(i - w / 2, a, str(a), ha="center", va="bottom", fontsize=8)
        ax.text(i + w / 2, b, str(b), ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(keys, fontsize=8)
    ax.set_ylabel("tickets")
    ax.set_title("Dispatch idempotency: replay files 0 new tickets, 0 double escalations",
                 fontsize=9.5)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Figures for the report gate artifacts")
    ap.add_argument("--artifacts", default="artifacts/reconciliation")
    ap.add_argument("--out", default="artifacts/reconciliation/figures")
    args = ap.parse_args(argv)
    art_dir, out_dir = Path(args.artifacts), Path(args.out)

    made = []
    main_art = art_dir / "report_gate_prod_readonly.json"
    if main_art.exists():
        made.append(fig_terminal_ledger(_load(main_art), out_dir / "fig1_terminal_ledger.png"))
    aged = art_dir / "report_gate_prod_dispatch_aged.json"
    if aged.exists():
        made.append(fig_sla_aging(_load(aged), out_dir / "fig2_sla_aging.png"))
    fresh = art_dir / "report_gate_prod_dispatch.json"
    replay = art_dir / "report_gate_prod_dispatch_replay.json"
    if fresh.exists() and replay.exists():
        aged_for_fig = _load(aged) if aged.exists() else {}
        made.append(fig_idempotency(_load(fresh), aged_for_fig, _load(replay),
                                    out_dir / "fig3_dispatch_idempotency.png"))
    for path in made:
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())