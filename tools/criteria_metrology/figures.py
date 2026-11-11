"""出图：把判据度量学的四个核心事实画出来（matplotlib，无交互后端）。

图 1 ``fig_degeneracy_map.png``
    退化测绘：三条判据族在 (|y_ref|/scale, |y - y_ref|/|y_ref|) 平面上的位置。
    T1 沿纵轴趋无穷（分母趋零）、T2 横跨 x=1（分母失真）、T3 落在区间带里。
图 2 ``fig_adaptive_ladder.png``
    y_ref → 0 的台阶：朴素相对误差（在对数轴上发散/缺失）vs 自适应族数值（有界）。
图 3 ``fig_controls.png``
    负控混淆矩阵（12 退化 + 12 良性）：命中率与误报率。
图 4 ``fig_replay_flips.png``
    回放重判：朴素判决 vs 自适应判决的翻转矩阵 + 逐类计数。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CLASS_COLORS = {"T1": "#d62728", "T2": "#ff7f0e", "T3": "#9467bd", "none": "#2ca02c"}


def _save(fig, path: Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def fig_degeneracy_map(out: Path, replay: dict[str, Any]) -> str:
    rows = [r for r in replay["rows"] if r.get("degeneracy")]
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    xs, ys, colors = [], [], []
    for r in rows:
        ev = r.get("degeneracy_evidence") or {}
        y = r["recompute"]["inputs"].get("y")
        y_ref = r["recompute"]["inputs"].get("y_ref")
        scale = r["recompute"]["inputs"].get("scale")
        if y is None or y_ref is None or not scale:
            continue
        x = abs(y_ref) / abs(scale) if scale else 0.0
        if r["naive_relative"] is None:
            yy = 1e6
        else:
            yy = max(r["naive_relative"], 1e-12)
        xs.append(max(x, 1e-16))
        ys.append(yy)
        colors.append(CLASS_COLORS.get(r["degeneracy"], "#333333"))
    ax.scatter(xs, ys, c=colors, s=18, alpha=0.75, edgecolors="none")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.axvline(1.0, color="#888888", ls="--", lw=1, label="|y_ref| = scale (denominator boundary)")
    ax.axhline(1.0, color="#888888", ls=":", lw=1, label="relative error = 100% (distortion boundary)")
    handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=k) for k, c in CLASS_COLORS.items()]
    ax.legend(handles=handles + ax.get_legend_handles_labels()[0], fontsize=8, loc="lower right")
    ax.set_xlabel("|y_ref| / scale (reference magnitude)")
    ax.set_ylabel("|y - y_ref| / |y_ref| (naive; undefined -> 1e6)")
    ax.set_title(f"Criterion degeneracy map: {len(xs)} locatable of {len(rows)}")
    return _save(fig, out / "fig_degeneracy_map.png")


def fig_adaptive_ladder(out: Path, invariants: dict[str, Any]) -> str:
    ladder = invariants["ladder"]
    names = [f"{row['y_ref']:g}" for row in ladder]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    idx = range(len(ladder))
    naive = [row["naive_relative"] if row["naive_relative"] is not None else float("nan") for row in ladder]
    adapt = [row["adaptive_value"] if row["adaptive_value"] is not None else float("nan") for row in ladder]
    ax.plot(list(idx), naive, "o--", color="#d62728", label="naive relative error (diverges as y_ref -> 0)")
    ax.plot(list(idx), adapt, "s-", color="#1f77b4", label="adaptive family value (always bounded)")
    ax.set_yscale("log")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(names, rotation=20)
    ax.set_xlabel("reference y_ref (measured y = 1)")
    ax.set_ylabel("error value (log scale)")
    for i, row in enumerate(ladder):
        ax.annotate(row["adaptive_verdict"], (i, adapt[i]), textcoords="offset points", xytext=(0, 7), fontsize=7, ha="center")
    ax.set_title("Invariant: no unbounded relative error as reference -> 0")
    ax.legend(fontsize=8)
    return _save(fig, out / "fig_adaptive_ladder.png")


def fig_controls(out: Path, controls: dict[str, Any]) -> str:
    labels = ["flagged degenerate", "flagged benign"]
    deg_hit = controls["n_degenerate_hit"]
    deg_miss = controls["n_degenerate"] - deg_hit
    benign_clean = controls["n_benign_clean"]
    benign_fp = controls["n_benign"] - benign_clean
    mat = [[deg_hit, deg_miss], [benign_fp, benign_clean]]
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    im = ax.imshow(mat, cmap="Blues", vmin=0, vmax=max(1, max(max(r) for r in mat)))
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labels)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["degenerate controls (12)", "benign controls (12)"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(mat[i][j]), ha="center", va="center", fontsize=14,
                    color="white" if mat[i][j] > max(max(r) for r in mat) / 2 else "black")
    ax.set_title(
        f"Negative controls: degenerate hit {deg_hit}/12, benign false positive {benign_fp}/12"
    )
    fig.colorbar(im, ax=ax, shrink=0.8)
    return _save(fig, out / "fig_controls.png")


def fig_replay_flips(out: Path, replay: dict[str, Any]) -> str:
    rows = replay["rows"]
    pairs = [("PASS", "PASS"), ("PASS", "FAIL"), ("PASS", "UNDECIDABLE"), ("FAIL", "PASS"),
             ("FAIL", "FAIL"), ("FAIL", "UNDECIDABLE"), ("UNDECIDABLE", "PASS"),
             ("UNDECIDABLE", "FAIL"), ("UNDECIDABLE", "UNDECIDABLE")]
    count = {p: 0 for p in pairs}
    for r in rows:
        key = (r["naive_verdict"], r["adaptive_verdict"])
        count[key] = count.get(key, 0) + 1
    mat = [[count.get((a, b), 0) for b in ("PASS", "FAIL", "UNDECIDABLE")] for a in ("PASS", "FAIL", "UNDECIDABLE")]
    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    im = ax.imshow(mat, cmap="Oranges")
    ax.set_xticks([0, 1, 2]); ax.set_xticklabels(["PASS", "FAIL", "UNDEC."])
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["PASS", "FAIL", "UNDEC."])
    ax.set_xlabel("adaptive verdict"); ax.set_ylabel("naive verdict")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, str(mat[i][j]), ha="center", va="center", fontsize=13,
                    color="white" if mat[i][j] > max(max(r) for r in mat) / 2 else "black")
    flips = sum(v for (a, b), v in count.items() if a != b)
    ax.set_title(f"Replay of {len(rows)} criteria: {flips} verdict flips ({flips / max(len(rows),1):.1%})")
    fig.colorbar(im, ax=ax, shrink=0.8)
    return _save(fig, out / "fig_replay_flips.png")


def fig_corpus_tiers(out: Path, corpus: dict[str, Any]) -> str:
    tiers = corpus.get("tiers") or {}
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    keys = ["faithful", "derived", "inconsistent_dropped"]
    vals = [tiers.get(k, 0) for k in keys]
    ax.bar(keys, vals, color=["#2ca02c", "#ff7f0e", "#d62728"])
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("records")
    ax.set_title("Corpus extraction fidelity tiers (self-consistency gate)")
    return _save(fig, out / "fig_corpus_tiers.png")


def make_all(
    out: Path,
    *,
    summary: dict[str, Any],
    replay: dict[str, Any],
    agreement: dict[str, Any],
    invariants: dict[str, Any],
    controls: dict[str, Any],
) -> dict[str, str]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((out / "corpus.json").read_text(encoding="utf-8")) if (out / "corpus.json").exists() else {}
    paths = {
        "fig_degeneracy_map": fig_degeneracy_map(out, replay),
        "fig_adaptive_ladder": fig_adaptive_ladder(out, invariants),
        "fig_controls": fig_controls(out, controls),
        "fig_replay_flips": fig_replay_flips(out, replay),
        "fig_corpus_tiers": fig_corpus_tiers(out, corpus),
    }
    (out / "figures.json").write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
    return paths