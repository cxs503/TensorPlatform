"""S×E 分布图（报告用）：S×E 矩阵热力图 + 记录流覆盖漏斗。

只读 ``artifacts/<dir>/backfill_report.json``（由
``tools/backfill_outcome_evaluations.py`` 程序化生成），不手抄任何数字。

用法::

    python tools/plot_outcome_evaluations.py --report artifacts/m5018_outcome_evaluations/backfill_report.json
"""
from __future__ import annotations

import argparse
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

GRADES = ["S1", "S2", "S3", "S4", "S5", "S-N"]
E_LEVELS = ["E0", "E1", "E2", "E3", "E4"]


def render(report_path: pathlib.Path, out_path: pathlib.Path) -> pathlib.Path:
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    stats = rep["stats"]
    cov = stats["coverage"]
    matrix = np.array(
        [[stats["sxe_matrix_confirmed"][g][e] for e in E_LEVELS] for g in GRADES],
        dtype=float,
    )

    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.4),
                             gridspec_kw={"width_ratios": [1.25, 1.0, 1.0]})

    ax = axes[0]
    im = ax.imshow(matrix, cmap="Blues", aspect="auto", vmin=0, vmax=max(1.0, matrix.max()))
    ax.set_xticks(range(len(E_LEVELS)), E_LEVELS)
    ax.set_yticks(range(len(GRADES)), GRADES)
    ax.set_xlabel("Evidence level E")
    ax.set_ylabel("Outcome grade S")
    ax.set_title(f"Confirmed S×E (repo={stats['repo']}, last {stats['window']})", fontsize=11)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = int(matrix[i, j])
            ax.text(j, i, str(value), ha="center", va="center", fontsize=10,
                    color="white" if value > matrix.max() * 0.55 else "#1f2937")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="records")

    ax = axes[1]
    labels = ["succeeded\nmissions", "records\ncreated", "decided\n(S/E written)", "confirmed"]
    values = [
        stats["missions_in_window"],
        cov.get("missions_with_record", 0),
        cov.get("missions_decided", 0),
        cov.get("records_confirmed", 0),
    ]
    bars = ax.bar(labels, values, color=["#94a3b8", "#60a5fa", "#38bdf8", "#0ea5e9"])
    ax.set_ylabel("missions / records")
    ax.set_title("Record-flow coverage", fontsize=11)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.4, str(v), ha="center", fontsize=10)
    ax.set_ylim(0, max(values + [1]) * 1.25)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[2]
    # 两个维度**并列成两组**，各自标签对齐，不做任何加权合成
    labels = GRADES + E_LEVELS
    values = ([stats["distribution_confirmed"]["by_grade"][g] for g in GRADES]
              + [stats["distribution_confirmed"]["by_e_level"][e] for e in E_LEVELS])
    colors = ["#0ea5e9"] * len(GRADES) + ["#f59e0b"] * len(E_LEVELS)
    x = np.arange(len(labels))
    ax.bar(x, values, color=colors, width=0.72)
    ax.set_xticks(x, labels, rotation=45, fontsize=8)
    ax.set_title("Stratified counts (no weighting / no ranking)", fontsize=11)
    ax.set_ylabel("confirmed records")
    for xi, v in zip(x, values):
        if v:
            ax.text(xi, v + 0.3, str(v), ha="center", fontsize=8)
    ax.set_ylim(0, max(values + [1]) * 1.25)
    ax.axvline(len(GRADES) - 0.5, color="#6b7280", lw=0.8, ls="--")
    ax.text(0.5, 0.94, "S grade", transform=ax.get_xaxis_transform(), fontsize=8,
            color="#0369a1", ha="left")
    ax.text((len(GRADES) + 0.5) / len(labels), 0.94, "E level",
            transform=ax.get_xaxis_transform(), fontsize=8, color="#b45309", ha="center")
    ax.grid(axis="y", alpha=0.25)

    fig.suptitle(
        "Outcome evaluation (S×E) — AOG SPEC v1.2 §7.1 machine landing · m5018",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    report_path = pathlib.Path(args.report)
    out = pathlib.Path(args.out) if args.out else report_path.parent / "fig_sxe_window30.png"
    print(render(report_path, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())