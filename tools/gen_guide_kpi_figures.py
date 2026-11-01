#!/usr/bin/env python3
"""生成指南 KPI 台账工件图（data 席 m1596）。

输入：artifacts/m1596_guide_kpi/kpi_snapshot.json（由 tools/guide_kpi_ledger.py --json 产出）
输出：
  artifacts/m1596_guide_kpi/kpi_progress.png  —— 12 项 KPI 完成度（DB vs 台账着色）
  artifacts/m1596_guide_kpi/kpi_drift.png     —— 看板快照 vs 现场复算的漂移（相对 %）
数字零手抄：全部从 snapshot json 读。
"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "m1596_guide_kpi"
SNAP = OUT / "kpi_snapshot.json"


def load():
    return json.loads(SNAP.read_text(encoding="utf-8"))


def fig_progress(p):
    recs = p["records"]
    rows = []
    for r in recs:
        if not r["numeric"]:
            continue
        prog = 100.0 * r["progress"] if r["progress"] is not None else 0.0
        rows.append((r["key"], prog, r["value"], r["target"], r["kind"]))
    rows.sort(key=lambda x: x[1])
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    colors = ["#2e7d32" if r[4] == "db" else "#c62828" for r in rows]

    fig, ax = plt.subplots(figsize=(11, 6.2))
    bars = ax.barh(labels, vals, color=colors, alpha=0.85, height=0.62)
    ax.axvline(100, color="black", ls="--", lw=1.2, label="target line (100%)")
    for b, r in zip(bars, rows):
        v = "—" if r[2] is None else f"{r[2]:.4g}"
        ax.text(min(b.get_width() + 1.5, 108), b.get_y() + b.get_height() / 2,
                f"{r[1]:.1f}%  (live {v} / target {r[3]:.4g})", va="center", fontsize=8)
    ax.set_xlim(0, 132)
    ax.set_xlabel("completion = live value / target (%)")
    ax.set_title("Guide KPI completion (9 numeric metrics, m1596 snapshot)\n"
                 "green = DB live SQL recompute  |  red = literal ledger (not data-recomputable)",
                 fontsize=11)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    f1 = OUT / "kpi_progress.png"
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    return f1


def fig_drift(p):
    rows = [d for d in p["drift"] if d.get("status") == "drift"]
    fig, ax = plt.subplots(figsize=(9.5, 3.6))
    if rows:
        labels = [d["metric_key"] for d in rows]
        rel = [100.0 * (d.get("rel_drift") or 0.0) for d in rows]
        bars = ax.bar(labels, rel, color="#ef6c00", alpha=0.9, width=0.5)
        for b, d in zip(bars, rows):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.4,
                    f"dashboard {d['dash_in_unit']:.4g} -> live {d['live']:.4g}",
                    ha="center", fontsize=8)
        ax.set_ylabel("relative drift (%)")
    else:
        ax.text(0.5, 0.5, "no drift detected", ha="center", va="center", transform=ax.transAxes)
    ax.axhline(2.0, color="black", ls="--", lw=1.1, label="tolerance 2%")
    ax.set_title(f"Dashboard snapshot (guide_dashboard.json) vs live recompute: "
                 f"{len(rows)} metric(s) beyond tolerance", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    plt.setp(ax.get_xticklabels(), fontsize=8)
    fig.tight_layout()
    f2 = OUT / "kpi_drift.png"
    fig.savefig(f2, dpi=140)
    plt.close(fig)
    return f2


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p = load()
    print(fig_progress(p))
    print(fig_drift(p))


if __name__ == "__main__":
    main()