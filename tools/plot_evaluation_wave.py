"""评价波出图：S×E 分布、理由长度分布、按仓覆盖率、回路增量。

只读 ``artifacts/<wave>/`` 下已生成的 JSON，不重算判级 —— 图与报告数字同源，
避免「图用手抄」这一类假证据。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

E_ORDER = ["E0", "E1", "E2", "E3", "E4"]
G_ORDER = ["S-N", "S1", "S2", "S3", "S4", "S5"]


def _matrix(rows, status="confirmed"):
    m = {g: {e: 0 for e in E_ORDER} for g in G_ORDER}
    for r in rows:
        if r["status"] != status:
            continue
        g, e = r["grade"], r["e_level"]
        if g in m and e in m[g]:
            m[g][e] += 1
    return m


def fig_matrix(rows, out: Path) -> None:
    m = _matrix(rows)
    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    data = [[m[g][e] for e in E_ORDER] for g in G_ORDER]
    im = ax.imshow(data, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(range(len(E_ORDER)), E_ORDER)
    ax.set_yticks(range(len(G_ORDER)), G_ORDER)
    for i, g in enumerate(G_ORDER):
        for j, e in enumerate(E_ORDER):
            v = data[i][j]
            ax.text(j, i, str(v), ha="center", va="center",
                    color="white" if v > max(max(r) for r in data) * 0.5 else "black",
                    fontsize=9)
    ax.set_xlabel("evidence level E")
    ax.set_ylabel("value grade S")
    ax.set_title(f"S x E confirmed distribution (n={sum(sum(r) for r in data)})")
    fig.colorbar(im, ax=ax, label="rows")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def fig_reason_length(report, out: Path) -> None:
    rl = report["metrics"]["reason_length"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 3.6))
    buckets = rl["buckets"]
    ax1.bar(list(buckets.keys()), list(buckets.values()), color="#3b7dd8")
    ax1.axhline(rl["n"] * 0.0, color="k", lw=0)
    ax1.set_xlabel("reason length (chars, whitespace stripped)")
    ax1.set_ylabel("rows")
    ax1.set_title(f"distribution n={rl['n']} median={rl['median']} min={rl['min']}")
    for i, v in enumerate(buckets.values()):
        ax1.text(i, v, str(v), ha="center", va="bottom", fontsize=8)

    marks = {"min": rl["min"], "p10": rl["p10"], "median": rl["median"],
             "p90": rl["p90"], "max": rl["max"]}
    ax2.boxplot([[rl["min"], rl["p10"], rl["median"], rl["p90"], rl["max"]]], vert=False,
                widths=0.4)
    ax2.set_yticklabels([])
    ax2.set_xlabel("chars")
    ax2.set_title("percentile summary (threshold = 50)")
    ax2.axvline(50, color="r", ls="--", lw=1)
    for k, v in marks.items():
        ax2.annotate(f"{k}={v}", (v, 1), textcoords="offset points", xytext=(0, 8),
                     fontsize=7, ha="center")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def fig_repo(rows, out: Path) -> None:
    by_repo = Counter(r["repo"] for r in rows)
    s2 = Counter(r["repo"] for r in rows if r["grade"] in ("S2", "S3", "S4", "S5")
                 and r["status"] == "confirmed")
    cand = Counter(r["repo"] for r in rows if r["status"] == "candidate")
    repos = [k for k, _ in by_repo.most_common()]
    x = range(len(repos))
    fig, ax = plt.subplots(figsize=(9.0, 3.8))
    ax.bar(x, [by_repo[r] for r in repos], color="#c9d8ef", label="evaluated")
    ax.bar(x, [s2[r] for r in repos], color="#2f6f4f", label="S2+ confirmed")
    ax.bar(x, [cand[r] for r in repos], bottom=[s2[r] for r in repos],
           color="#e0a33a", label="candidate", alpha=0.85)
    ax.set_xticks(list(x), repos, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("missions")
    ax.set_title("per-repo evaluation outcome")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def fig_loop(effect, out: Path) -> None:
    mc = effect["measured_change"]
    labels = ["batch position\nchanged", "extra 4th seat", "grade/E/status\nchanged",
              "reason median\n+chars"]
    vals = [mc["batch_position_changed"], mc["missions_with_extra_seat"],
            mc["grade_e_status_changed"],
            mc["reason_median_r2"] - mc["reason_median_r1"]]
    colors = ["#3b7dd8", "#2f6f4f", "#e0a33a", "#8a5cd1"]
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    bars = ax.bar(labels, vals, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("count / chars")
    ax.set_title("guidance loop effect: round1 -> round2 (same window, read-only replay)")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="artifacts/m5180_evaluation_wave")
    args = ap.parse_args()
    d = Path(args.dir)
    rows = [json.loads(line) for line in (d / "rows.jsonl").read_text().splitlines() if line]
    report = json.loads((d / "wave_report.json").read_text())
    effect = json.loads((d / "round2_effect.json").read_text())

    fig_matrix(rows, d / "fig_sxe_matrix.png")
    fig_reason_length(report, d / "fig_reason_length.png")
    fig_repo(rows, d / "fig_repo_outcome.png")
    fig_loop(effect, d / "fig_loop_effect.png")
    sizes = {p.name: p.stat().st_size for p in sorted(d.glob("fig_*.png"))}
    print(json.dumps(sizes, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())