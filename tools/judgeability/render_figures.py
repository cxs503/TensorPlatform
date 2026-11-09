#!/usr/bin/env python3
"""三张图（P151W1-N10 / ④ 报告图表）。

1. ``fig_dim_hit_distribution.png``  三维命中分布（按 strong/weak 分组）
2. ``fig_repo_stacked.png``          按 repo 的四格堆叠柱（P1&P2 / only_P1 / only_P2 / 双缺）
3. ``fig_before_after.png``          派发前 vs enforce 生效后（命中率 + 漏放读数）

数据一律从 ``baseline_audit_300.json`` 读（不手抄），matplotlib Agg 无显示。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "artifacts" / "quality" / "judgeability"
FIG = OUT / "figures"

C_STRONG = "#1f6f8b"
C_WEAK = "#c1666b"
C_SERIES = ["#1f6f8b", "#c1666b", "#e0a458", "#8d99ae"]


def fig_dim_hit(audit: Dict[str, Any], path: Path) -> None:
    recs = audit["per_record"]
    dims = ["P1", "P2", "P3"]
    strong = [sum(1 for r in recs if r["verdict"] == "strong" and r["dims"][d]) for d in dims]
    weak = [sum(1 for r in recs if r["verdict"] == "weak" and r["dims"][d]) for d in dims]
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    x = range(len(dims))
    b1 = ax.bar([i - 0.19 for i in x], strong, width=0.38, label="strong criteria",
                color=C_STRONG)
    b2 = ax.bar([i + 0.19 for i in x], weak, width=0.38, label="weak criteria", color=C_WEAK)
    for bars in (b1, b2):
        for b in bars:
            ax.annotate(f"{int(b.get_height())}", (b.get_x() + b.get_width() / 2,
                        b.get_height()), ha="center", va="bottom", fontsize=8)
    n = len(recs)
    ax.set_xticks(list(x))
    ax.set_xticklabels(["P1 numeric threshold", "P2 reference kind", "P3 artifact path"])
    ax.set_ylabel("missions")
    ax.set_title(f"Three-dimension hit distribution (N={n} terminal missions; P3 advisory only)",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_repo_stacked(audit: Dict[str, Any], path: Path) -> None:
    slices = audit["slices"]["repo"]
    repos = [k for k, v in sorted(slices.items(), key=lambda kv: -kv[1]["n"]) if v["n"] >= 3]
    keys = ["P1&P2", "only_P1", "only_P2", "neither"]
    labels = ["P1&P2 (strong)", "P1 only (no reference)", "P2 only (no threshold)", "neither"]
    fig, ax = plt.subplots(figsize=(8.0, 4.4), dpi=140)
    bottoms = [0] * len(repos)
    for k, lab, col in zip(keys, labels, C_SERIES):
        vals = [slices[r]["pairs"][k] for r in repos]
        ax.barh(repos, vals, left=bottoms, label=lab, color=col)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    for i, r in enumerate(repos):
        ax.annotate(f"{bottoms[i]}", (bottoms[i], i), va="center", fontsize=7)
    ax.set_xlabel("missions")
    ax.set_title("Per-repo stacking of the three dimensions (repos with n>=3)", fontsize=10)
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_before_after(audit: Dict[str, Any], path: Path) -> None:
    ba = audit["before_after"]
    before, after = ba["before"], ba["after"]
    items = [("P1 hit", before["P1"]), ("P2 hit", before["P2"]),
             ("P3 hit", before["P3"]), ("strong", before["strong"]),
             ("slot coverage (enforce)", after["slot_coverage"]),
             ("strong (after inject)", after["strong_after"])]
    fig, ax = plt.subplots(figsize=(8.0, 4.2), dpi=140)
    xs = range(len(items))
    vals = [v * 100 for _, v in items]
    cols = [C_STRONG] * 4 + ["#e0a458", C_STRONG]
    bars = ax.bar(list(xs), vals, color=cols)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.1f}%", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=8)
    leak = audit["enforce_replay"]["leak_marker"]["leak_rate"] * 100
    naive = audit["enforce_replay"]["leak_naive_control"]["leak_rate"] * 100
    ax.axhline(leak, color="#1f6f8b", ls="--", lw=1,
               label=f"false promotion, marker injection = {leak:.1f}%")
    ax.axhline(naive, color=C_WEAK, ls=":", lw=1.2,
               label=f"false promotion, naive injection (control) = {naive:.1f}%")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([k for k, _ in items], fontsize=8, rotation=15)
    ax.set_ylabel("share of terminal missions (%)")
    ax.set_ylim(0, 105)
    ax.set_title(f"Before dispatch vs after enforce (N={ba['n']})", fontsize=10)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染判据可裁决性三张图")
    ap.add_argument("--audit", default=str(OUT / "baseline_audit_300.json"))
    ap.add_argument("--out-dir", default=str(FIG))
    a = ap.parse_args(argv)
    audit = json.loads(Path(a.audit).read_text(encoding="utf-8"))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fig_dim_hit(audit, out / "fig_dim_hit_distribution.png")
    fig_repo_stacked(audit, out / "fig_repo_stacked.png")
    fig_before_after(audit, out / "fig_before_after.png")
    for p in sorted(out.glob("*.png")):
        print(f"{p.relative_to(REPO_ROOT)} {p.stat().st_size} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())