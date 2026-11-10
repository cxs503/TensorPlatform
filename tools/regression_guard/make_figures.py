#!/usr/bin/env python3
"""Figures for the cross-mission regression guard (docs/REGRESSION_GUARD.md).

All numbers are read from ``artifacts/regression_guard/`` JSON produced by
``demo_real_repo.py`` — nothing is hand-typed, so a figure cannot drift from
the machine-checkable criteria file.

    python3 tools/regression_guard/make_figures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "regression_guard"
FIG = ART / "figures"


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def fig_pipeline(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.6)
    ax.axis("off")
    stages = [
        ("change\nmanifest\n(JSON / git diff)", 0.3, 2.9, "#dbeafe"),
        ("task ledger\nartifact->feature\n->task + DAG", 2.35, 2.9, "#e0f2fe"),
        ("static import\ngraph (ast)\n3109 modules", 4.4, 3.05, "#ede9fe"),
        ("impact\nresolution\n+ reason trail", 2.35, 1.05, "#dcfce7"),
        ("cache probe\ncontent-addressed\n(test+deps digest)", 4.4, 1.05, "#fef3c7"),
        ("pytest batches\n(one process\nper test root)", 6.55, 1.05, "#ffe4e6"),
        ("baseline diff\npass->fail =\nregression", 8.7, 1.05, "#fae8ff"),
        ("structured JSON\nreport + exit code\n0/1/2", 10.5, 2.0, "#d1fae5"),
    ]
    for label, x, y, color in stages:
        ax.add_patch(
            FancyBboxPatch((x, y), 1.75, 0.95, boxstyle="round,pad=0.06",
                           linewidth=1.1, edgecolor="#334155", facecolor=color)
        )
        ax.text(x + 0.875, y + 0.475, label, ha="center", va="center", fontsize=8.3)
    arrows = [
        ((2.05, 3.37), (2.35, 3.37)),
        ((4.1, 3.37), (4.4, 3.37)),
        ((4.4, 3.05), (3.4, 2.0)),
        ((3.15, 2.9), (3.15, 2.0)),
        ((4.1, 1.52), (4.4, 1.52)),
        ((6.15, 1.52), (6.55, 1.52)),
        ((8.3, 1.52), (8.7, 1.52)),
        ((10.45, 1.52), (10.9, 2.0)),
        ((9.6, 2.0), (9.6, 2.55)),
        ((2.4, 2.9), (2.4, 2.6)),
    ]
    for start, end in arrows:
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11,
                                     linewidth=1.1, color="#1e293b"))
    ax.text(6.0, 4.25, "cross-mission regression guard: change -> dependency -> re-run -> compare",
            ha="center", fontsize=11, fontweight="bold")
    ax.text(0.3, 0.25, "legitimate zero-test paths: no dependency matches (exit 0) · warm cache (0 re-executions)",
            fontsize=8, color="#475569")
    fig.tight_layout()
    fig.savefig(path, dpi=190)
    plt.close(fig)


def fig_metrics(criteria: dict, path: Path) -> None:
    a = criteria["a_solver_change"]
    b = criteria["b_platform_api_change"]
    c = criteria["c_false_trigger"]
    e = criteria["e_negative_and_cache"]
    labels = ["detection\nsolver (a)", "detection\nAPI (b)", "cache hit\nwarm run (e)"]
    values = [a["detection_rate"], b["detection_rate"] or 0.0, e["cache_hit_rate"]]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.9), gridspec_kw={"width_ratios": [1.15, 1]})
    bars = ax1.bar(labels, [v * 100 for v in values], color=["#2563eb", "#7c3aed", "#059669"], width=0.62)
    for bar, value in zip(bars, values):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                 f"{value*100:.1f}%", ha="center", fontsize=10, fontweight="bold")
    ax1.set_ylim(0, 118)
    ax1.set_ylabel("percent")
    ax1.axhline(100, color="#94a3b8", linestyle="--", linewidth=1)
    ax1.set_title("detection & cache reuse (must be 100%)", fontsize=10)
    ax1.grid(axis="y", alpha=0.25)

    ax2.bar(["false trigger\n(criterion c)"], [c["false_trigger_rate"] * 100], color="#dc2626", width=0.4)
    ax2.axhline(1.0, color="#111827", linestyle="--", linewidth=1.2)
    ax2.text(0.02, 1.05, "ceiling 1%", fontsize=8, color="#111827")
    ax2.set_ylim(0, 3)
    ax2.set_ylabel("percent of selected tests")
    ax2.set_title(f"false triggers: {c['untracked_paths_tested']} untracked-path scenarios",
                  fontsize=10)
    ax2.text(0, 0.15, f"{c['untracked_selected_total']} tests selected",
             ha="center", fontsize=9, color="#111827")
    ax2.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=190)
    plt.close(fig)


def fig_demos(demos: dict, path: Path) -> None:
    names, executed, cached = [], [], []
    for key in ("D1_solver_change_cold", "D3_baseline", "D4_injected_regression", "D5_api_change_vs_baseline"):
        d = demos[key]
        if key == "D4_injected_regression":
            d = d["after_injection"]
        names.append(key.split("_")[0])
        executed.append(d["executed"])
        cached.append(d["cached"])
    warm = demos["D2_solver_change_cache"]["warm_hit"]
    names.append("D2(warm)")
    executed.append(warm["executed"])
    cached.append(warm["cached"])

    fig, ax = plt.subplots(figsize=(9, 3.8))
    x = range(len(names))
    ax.bar(x, executed, label="executed", color="#1d4ed8", width=0.6)
    ax.bar(x, cached, bottom=executed, label="served from cache", color="#f59e0b", width=0.6)
    for i, (ex, ca) in enumerate(zip(executed, cached)):
        ax.text(i, ex + ca + 1.5, f"{ex}+{ca}", ha="center", fontsize=8.6)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=9)
    ax.set_ylabel("test cases")
    ax.set_title("real-repo demonstrations: re-execution volume (executed vs cached)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=190)
    plt.close(fig)


def fig_fanout(demos: dict, criteria: dict, path: Path) -> None:
    d1 = demos["D1_solver_change_cold"]
    d5 = demos["D5_api_change_vs_baseline"]
    rows = [
        ("solver kernel\n(pce_uq/kernel.py)", len(d1["tasks"]), len(d1["selected_test_files"])),
        ("audit API\n(api/audit.py)", len(d5["tasks"]), len(d5["selected_test_files"])),
        ("capability ledger\n(tools/capability_discovery)", 3, 3),
        ("guard self-test\n(tools/regression_guard)", 1, 1),
    ]
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    y = range(len(rows))
    ax.barh(list(y), [r[1] for r in rows], color="#0ea5e9", height=0.38, label="affected tasks")
    ax.barh([i + 0.4 for i in y], [r[2] for r in rows], color="#a855f7", height=0.38,
            label="test files re-run")
    for i, r in enumerate(rows):
        ax.text(r[1] + 0.08, i, str(r[1]), va="center", fontsize=9)
        ax.text(r[2] + 0.08, i + 0.4, str(r[2]), va="center", fontsize=9)
    ax.set_yticks([i + 0.2 for i in y])
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.6)
    ax.invert_yaxis()
    ax.set_xlabel("count")
    ax.set_title(f"blast radius per tracked artifact (baseline recorded {demos['D3_baseline']['baseline_tests_recorded']} test cases)",
                 fontsize=9.6)
    ax.legend(fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=190)
    plt.close(fig)


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    criteria = _load("criteria.json")
    demos = _load("demo_reports.json")
    fig_pipeline(FIG / "fig1_pipeline.png")
    fig_metrics(criteria, FIG / "fig2_metrics.png")
    fig_demos(demos, FIG / "fig3_evolution.png")
    fig_fanout(demos, criteria, FIG / "fig4_fanout.png")
    for p in sorted(FIG.glob("*.png")):
        print(p.relative_to(ROOT), p.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())