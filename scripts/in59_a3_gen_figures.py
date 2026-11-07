#!/usr/bin/env python3
"""IN59-A3 报告图生成（读 artifacts/in59_a3/results.json，零手抄）。

图 1 ``fig_ec_cap_matrix.png``：申报 E × 链形态 → 授予 E（S3 面）的阶梯矩阵，
     直观看到「缺件一律落到 E1」；
图 2 ``fig_ec_status_by_variant.png``：S3 声明在各链形态下的 confirmed/candidate/rejected 计数；
图 3 ``fig_ec_completeness_vs_granted.png``：五元完整度 → 授予 E 的散点（全部 S 级），
     证明「只降不升」与封顶线 E1 的位置。

用法： python3 scripts/in59_a3_gen_figures.py
"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
RESULTS = REPO / "artifacts" / "in59_a3" / "results.json"
OUT = REPO / "artifacts" / "in59_a3"
E_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
VARIANTS = ["complete", "miss_theory", "bad_code", "miss_data", "absent"]
VARIANT_LABEL = {
    "complete": "五元齐全",
    "miss_theory": "缺 theory",
    "bad_code": "code 非 sha",
    "miss_data": "缺 data（红队）",
    "absent": "未给链",
}


def _matrix(rows, grade: str):
    """→ (申报 E 列表, 链形态列表, 授予 E 秩矩阵)"""
    e_levels = sorted({r["e_level_declared"] for r in rows}, key=lambda e: E_ORDER[e])
    grid = []
    for variant in VARIANTS:
        line = []
        for e in e_levels:
            hit = [r for r in rows if r["grade"] == grade and r["e_level_declared"] == e
                   and r["chain_variant"] == variant]
            line.append(E_ORDER[hit[0]["e_level_granted"]] if hit else 0)
        grid.append(line)
    return e_levels, grid


def fig_cap_matrix(rows, path: pathlib.Path):
    e_levels, grid = _matrix(rows, "S3")
    fig, ax = plt.subplots(figsize=(6.6, 4.2), dpi=150)
    im = ax.imshow(grid, cmap="viridis", vmin=0, vmax=4, aspect="auto")
    ax.set_xticks(range(len(e_levels)), e_levels)
    ax.set_yticks(range(len(VARIANTS)), [VARIANT_LABEL[v] for v in VARIANTS])
    for i in range(len(VARIANTS)):
        for j in range(len(e_levels)):
            ax.text(j, i, f"E{grid[i][j]}", ha="center", va="center",
                    color="white" if grid[i][j] <= 2 else "black", fontsize=9)
    ax.set_xlabel("申报 E 级")
    ax.set_ylabel("五元链形态")
    ax.set_title("S3 声明授予 E 级矩阵：缺件一律封顶 E1（只降不升）")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("授予 E 秩（1=E1 … 4=E4）")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_status_counts(rows, path: pathlib.Path):
    statuses = ["confirmed", "candidate", "rejected"]
    counts = {v: [] for v in VARIANTS}
    for variant in VARIANTS:
        for st in statuses:
            counts[variant].append(sum(
                1 for r in rows
                if r["grade"] == "S3" and r["chain_variant"] == variant and r["status"] == st))
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=150)
    width = 0.26
    xs = range(len(VARIANTS))
    colors = {"confirmed": "#2a9d8f", "candidate": "#e9c46a", "rejected": "#e76f51"}
    for k, st in enumerate(statuses):
        ax.bar([x + (k - 1) * width for x in xs],
               [counts[v][k] for v in VARIANTS], width, label=st, color=colors[st])
    ax.set_xticks(list(xs), [VARIANT_LABEL[v] for v in VARIANTS])
    ax.set_ylabel("S3 声明计数（E1–E4 四档）")
    ax.set_title("S3 声明在链形态下的判定分布（红队：缺 data 全部落 candidate）")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_completeness(rows, path: pathlib.Path):
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=150)
    palette = {"confirmed": "#2a9d8f", "candidate": "#e9c46a", "rejected": "#e76f51"}
    for st, color in palette.items():
        xs = [r["completeness"] for r in rows if r["status"] == st]
        ys = [E_ORDER[r["e_level_granted"]] for r in rows if r["status"] == st]
        ax.scatter(xs, ys, s=22, alpha=0.7, label=st, color=color)
    ax.axvline(1.0, ls="--", color="#6c757d", lw=1)
    ax.axhline(1.0, ls=":", color="#6c757d", lw=1)
    ax.set_xlabel("五元完整度（可解析元数 / 5）")
    ax.set_ylabel("授予 E 秩")
    ax.set_yticks([0, 1, 2, 3, 4], ["E0", "E1", "E2", "E3", "E4"])
    ax.set_title("完整度 → 授予 E：完整度<1 的点全部压在 E1 线（封顶）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = data["matrix"]
    OUT.mkdir(parents=True, exist_ok=True)
    fig_cap_matrix(rows, OUT / "fig_ec_cap_matrix.png")
    fig_status_counts(rows, OUT / "fig_ec_status_by_variant.png")
    fig_completeness(rows, OUT / "fig_ec_completeness_vs_granted.png")
    print(json.dumps({"figures": sorted(p.name for p in OUT.glob("*.png")),
                      "rows": len(rows), "summary": data["summary"],
                      "config_hash": data["config_hash"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())