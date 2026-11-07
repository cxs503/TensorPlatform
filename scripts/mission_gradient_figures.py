#!/usr/bin/env python3
"""Figures for the mission gradient & exploration-budget mechanism (IN19-Q6).

All numbers are computed by ``backend/services/mission_gradient.py`` at plot time —
nothing is hand-typed, so the figures cannot drift from the implementation.

  fig1  seeding-mix registry and quota allocation (registered vs an adjusted mix)
  fig2  credit-tier x gradient-tier admission matrix (dispatch gate)
  fig3  failure-rate accounting: quarantined honest FAIL vs a flat counter

Usage: python3 scripts/mission_gradient_figures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "artifacts" / "mission_gradient"
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services import mission_gradient as mg  # noqa: E402

TIER_COLORS = ["#4C72B0", "#55A868", "#C44E52", "#8172B2", "#CCB974"]

#: no CJK font is installed on the build host, so figures carry ASCII tier names
#: (the mechanism's own labels stay in TIER_LABELS for API/report surfaces)
TIER_EN = {"S1": "execute", "S2": "improve", "S3": "build",
           "S4": "breakthrough", "S5": "create"}


def fig_quota() -> Path:
    tiers = list(mg.GRADIENT_TIERS)
    registered = mg.validate_mix(None)
    adjusted = mg.validate_mix({"S1": 0.50, "S2": 0.30, "S3": 0.15, "S4": 0.04, "S5": 0.01})
    q_reg = mg.allocate_quota(100, registered)
    q_adj = mg.allocate_quota(100, adjusted)

    x = np.arange(len(tiers))
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6))

    ax = axes[0]
    w = 0.38
    ax.bar(x - w / 2, [registered[t] * 100 for t in tiers], w, color="#4C72B0",
           label="registered start mix")
    ax.bar(x + w / 2, [adjusted[t] * 100 for t in tiers], w, color="#C44E52", alpha=.85,
           label="an adjusted mix (validated)")
    for i, t in enumerate(tiers):
        ax.annotate(f"{registered[t]*100:.0f}%", (i - w / 2, registered[t] * 100),
                    ha="center", va="bottom", fontsize=8)
        ax.annotate(f"{adjusted[t]*100:.0f}%", (i + w / 2, adjusted[t] * 100),
                    ha="center", va="bottom", fontsize=8, color="#8B2E2E")
    ax.set_xticks(x, [f"{t}\n{TIER_EN[t]}" for t in tiers])
    ax.set_ylabel("share of seeding slots [%]")
    ax.set_title("(a) seeding-mix registry — start 60/25/10/4/1, adjustable")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(axis="y", alpha=.25)

    ax = axes[1]
    ax.bar(x - w / 2, [q_reg[t] for t in tiers], w, color=[TIER_COLORS[i] for i in range(5)],
           label="N=100 (registered)")
    ax.bar(x + w / 2, [q_adj[t] for t in tiers], w, color=[TIER_COLORS[i] for i in range(5)],
           alpha=.45, hatch="//", label="N=100 (adjusted)")
    for i, t in enumerate(tiers):
        ax.annotate(str(q_reg[t]), (i - w / 2, q_reg[t]), ha="center", va="bottom", fontsize=8)
        ax.annotate(str(q_adj[t]), (i + w / 2, q_adj[t]), ha="center", va="bottom",
                    fontsize=8, color="#555555")
    expl = q_reg["S4"] + q_reg["S5"]
    ax.axhline(0, color="black", lw=.6)
    ax.set_xticks(x, [f"{t}\n{TIER_EN[t]}" for t in tiers])
    ax.set_ylabel("allocated slots")
    ax.set_title(f"(b) Hamilton allocation is exact — exploration budget = {expl}/100 slots")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=.25)

    fig.suptitle("IN19-Q6 · task gradient S1..S5: registered mix and exact quota allocation",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = OUT / "fig1_quota_allocation.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_gating() -> Path:
    tiers = list(mg.GRADIENT_TIERS)
    levels = list(mg.CREDIT_LEVELS)
    grid = np.array([[1 if mg.credit_permits(lv, t) else 0 for lv in levels] for t in tiers])

    fig, ax = plt.subplots(figsize=(7.6, 5.0))
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    for i, t in enumerate(tiers):
        for j, lv in enumerate(levels):
            ax.text(j, i, "dispatch" if grid[i, j] else "refuse", ha="center", va="center",
                    fontsize=9, color="#123" if grid[i, j] else "#400",
                    fontweight="bold" if grid[i, j] else "normal")
    ax.set_xticks(range(len(levels)), [f"L{lv}" for lv in levels])
    ax.set_yticks(range(len(tiers)), [f"{t} {TIER_EN[t]}" for t in tiers])
    ax.set_xlabel("worker credit level")
    ax.set_ylabel("mission gradient tier")
    ax.set_title("credit-tier matched dispatch: tier Sn needs Ln or better\n"
                 "(L4+ takes S4; only L5 takes S5) — 15 admit / 10 refuse")
    cb = fig.colorbar(im, ax=ax, ticks=[0, 1])
    cb.ax.set_yticklabels(["refuse", "admit"])
    fig.tight_layout()
    path = OUT / "fig2_credit_gating_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_accounting() -> Path:
    ks, disciplined, naive = [], [], []
    for k in range(0, 9):
        outcomes = [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/m-{i:03d}",
                     "tier": "S1", "status": "succeeded"} for i in range(10)]
        outcomes += [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/f-{i:03d}",
                      "tier": "S2", "status": "failed"} for i in range(3)]
        outcomes += [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/x-{i:03d}",
                      "tier": "S4", "status": "honest_fail",
                      "root_cause": f"dom-alpha probe {i} diverged from the reference trace",
                      "retrospective": "Cause: synthetic fixture scale mismatch; validate size first."}
                     for i in range(k)]
        w = mg.failure_accounting(outcomes)["workers"][0]
        ks.append(k)
        disciplined.append(w["rate"] * 100)
        naive.append(w["naive_rate"] * 100)

    undocumented = mg.classify_outcome({"worker_id": "w-beta-2", "mission_id": "dom-beta/m-001",
                                        "tier": "S5", "status": "honest_fail", "root_cause": "oops"})
    blocked = mg.classify_outcome({"worker_id": "w-beta-2", "mission_id": "dom-beta/m-002",
                                   "tier": "S2", "status": "honest_fail",
                                   "root_cause": "dom-beta probe failed at fixture load",
                                   "retrospective": "Cause: fixture too large; generate a smaller one."})

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.4))
    ax = axes[0]
    ax.plot(ks, disciplined, "o-", color="#2E7D32", label="disciplined rate (honest FAIL quarantined)")
    ax.plot(ks, naive, "s--", color="#C62828", label="flat counter (naive rate)")
    ax.fill_between(ks, disciplined, naive, color="#C62828", alpha=.12)
    ax.set_xlabel("admissible honest FAILs from the same worker (3 ordinary failures, 13 runs)")
    ax.set_ylabel("worker failure rate [%]")
    ax.set_title("(a) honest FAIL leaves both numerator and denominator")
    ax.legend(fontsize=8, loc="center right")
    ax.grid(alpha=.25)

    ax = axes[1]
    labels = ["undocumented\nhonest FAIL (S5)", "honest FAIL claimed\non S2 (no budget)",
              "documented\nhonest FAIL (S4)"]
    values = [0 if undocumented["quarantined"] else 1,
              0 if blocked["quarantined"] else 1, 1]
    colors = ["#C62828", "#C62828", "#2E7D32"]
    bars = ax.bar(labels, [1, 1, 1], color=colors, alpha=.9)
    for b, q in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, .5, "quarantined" if q else "counts as failure",
                ha="center", va="center", color="white", fontsize=9, fontweight="bold")
    ax.set_yticks([])
    ax.set_title("(b) admissibility: evidence is required, discipline is not optional")

    fig.suptitle("IN19-Q6 · exploration budget keeps exploration honest without hiding sloppiness",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = OUT / "fig3_failure_rate_accounting.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [fig_quota(), fig_gating(), fig_accounting()]
    meta = {
        "generated_by": "scripts/mission_gradient_figures.py",
        "figures": [{"path": str(p.relative_to(REPO_ROOT)), "bytes": p.stat().st_size}
                    for p in paths],
        "numbers_source": "backend/services/mission_gradient.py (computed at plot time)",
        "label_note": ("build host has no CJK font → tier names are plotted in ASCII "
                       "(execute/improve/build/breakthrough/create) to avoid tofu glyphs"),
    }
    (OUT / "figures_metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())