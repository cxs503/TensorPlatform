#!/usr/bin/env python3
"""Figures for the m1780 ship-domain defect verdicts (report + register).

Reads the committed verdict artifacts (no hand-copied numbers) and renders

    artifacts/ship_rao_defect.png     RAO physics evidence (hardcoded table vs solved curve)
    artifacts/ship_verdict_matrix.png verdict matrix (endpoint x criterion x verdict)

Usage:  python3 tools/gen_ship_defect_figures.py
"""
from __future__ import annotations

import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERDICTS = os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts.json")
OUT_DIR = os.path.join(ROOT, "artifacts")

PALETTE = {"PASS": "#2e7d32", "REAL": "#2e7d32", "FAIL": "#c62828",
           "MOCK": "#b71c1c", "STUB": "#ef6c00", "WITHHELD": "#616161"}


def load():
    with open(VERDICTS, encoding="utf-8") as fh:
        return json.load(fh)


def one_dof_rao(length, draft, cb, n=20):
    """Reference 1-DOF heave response: hull-dependent resonance, not a constant table."""
    wn = 0.55 * (length / 320.0) ** -0.5 * (cb / 0.82) ** 0.5
    zeta = 0.15 + 0.1 * (1.0 - cb)
    freqs = [0.1 + 1.9 * i / (n - 1) for i in range(n)]
    amp = [1.0 / math.sqrt((1.0 - (w / wn) ** 2) ** 2 + (2.0 * zeta * w / wn) ** 2)
           for w in freqs]
    return freqs, amp


def fig_rao(doc):
    crit = {r["criterion_id"]: r for r in doc["records"]}
    evidence = crit["C6"]["evidence"]["fingerprint"]
    freqs_platform = [0.3, 0.5, 0.7, 0.9, 1.1, 1.3]
    table_heave = evidence["heave"]["reference"]
    table_pitch = evidence["pitch"]["reference"]
    f_a, r_a = one_dof_rao(320.0, 20.8, 0.82)
    f_b, r_b = one_dof_rao(230.0, 10.8, 0.651)

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    ax = axes[0]
    ax.plot(freqs_platform, table_heave, "o-", lw=2.4, color="#c62828",
            label="platform RAO heave (6-pt constant table)")
    ax.plot(freqs_platform, table_pitch, "s--", lw=2.0, color="#e57373",
            label="platform RAO pitch (6-pt constant table)")
    ax.plot(f_a, r_a, "-", lw=1.8, color="#1565c0",
            label="1-DOF reference, hull A L320/Cb0.82")
    ax.plot(f_b, r_b, "-", lw=1.8, color="#2e7d32",
            label="1-DOF reference, hull B L230/Cb0.651")
    ax.set_xlabel(r"wave frequency $\omega$ (rad/s)")
    ax.set_ylabel("RAO (m/m or deg/m)")
    ax.set_title("SHIP-SEA-001: platform RAO is hull/frequency independent")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")

    ax = axes[1]
    dep = crit["C5"]["evidence"]["dependence"]
    labels = ["pointwise diff\n(hull A vs B)", "correlation\n(hull A vs B)",
              "peak shift\n(hull A vs B)"]
    values = [dep.get("hull_max_rel") or 0.0, dep.get("hull_corr") or 0.0,
              dep.get("peak_shift_rel") or 0.0]
    bars = ax.bar(labels, values, color=["#c62828", "#c62828", "#c62828"], width=0.55)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.02, f"{value:.3f}",
                ha="center", fontsize=10)
    ax.axhline(0.05, ls=":", color="#1565c0", lw=1.2, label="required > 0.05")
    ax.axhline(0.99, ls="--", color="#2e7d32", lw=1.2, label="correlation must be < 0.99")
    ax.set_ylim(0, 1.25)
    ax.set_title("SHIP-SEA-001: dependence evidence (C5 = %s)" % crit["C5"]["verdict"])
    ax.legend(fontsize=8, loc="center right")
    ax.grid(axis="y", alpha=0.3)

    fig.suptitle("m1780 / advanced/bem RAO physics probe (%s)" % doc["generated_at"],
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = os.path.join(OUT_DIR, "ship_rao_defect.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_matrix(doc):
    records = doc["records"]
    endpoints = sorted({r["endpoint"] for r in records})
    criteria = [c for c in ("C1", "C2", "C3", "C4", "C5", "C6", "L1", "L2")]
    grid = [[None] * len(criteria) for _ in endpoints]
    for rec in records:
        i = endpoints.index(rec["endpoint"])
        j = criteria.index(rec["criterion_id"])
        grid[i][j] = rec["verdict"]

    fig, ax = plt.subplots(figsize=(11.0, 3.4 + 0.5 * len(endpoints)))
    for i, row in enumerate(grid):
        for j, verdict in enumerate(row):
            if verdict is None:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, color="#ffffff", ec="#dddddd"))
                ax.text(j + 0.5, i + 0.5, "—", ha="center", va="center", color="#999999")
                continue
            ax.add_patch(plt.Rectangle((j, i), 1, 1,
                                       color=PALETTE.get(verdict, "#455a64"), ec="white"))
            ax.text(j + 0.5, i + 0.5, verdict, ha="center", va="center",
                    color="white", fontsize=10, fontweight="bold")
    ax.set_xlim(0, len(criteria))
    ax.set_ylim(0, len(endpoints))
    ax.set_xticks([j + 0.5 for j in range(len(criteria))])
    ax.set_xticklabels(criteria)
    ax.set_yticks([i + 0.5 for i in range(len(endpoints))])
    ax.set_yticklabels([e.replace("/api/v1/", "") for e in endpoints], fontsize=8)
    ax.invert_yaxis()
    ax.set_title("m1780 verdict matrix: %s" % doc["generated_at"], fontsize=11)
    fig.tight_layout()
    path = os.path.join(OUT_DIR, "ship_verdict_matrix.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def main():
    doc = load()
    for path in (fig_rao(doc), fig_matrix(doc)):
        print("wrote", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()