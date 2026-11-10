#!/usr/bin/env python3
"""报告图：覆盖/检出矩阵/检出渠道（matplotlib，纯本地数据，无网络）。

三张图（落 ``artifacts/mech_replay/``）：
1. ``fig1_fixture_coverage.png``  —— 机制 × fixture 条数（real / probe 分层栈）
2. ``fig2_mutation_matrix.png``   —— 8 机制 × 6 退化类 检出矩阵（色=检出率，标注 k/n）
3. ``fig3_detection_channels.png``—— 每类退化由哪条渠道检出（决策翻转/幂等/审计面）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.mech_replay import mechanisms, mutate, replay  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "artifacts" / "mech_replay"


def _fig1(fixtures: list[dict], out: Path) -> Path:
    names = list(mechanisms.MECHANISMS)
    real = [sum(1 for f in fixtures if f["mechanism"] == n and f["origin"] == "real")
            for n in names]
    probe = [sum(1 for f in fixtures if f["mechanism"] == n and f["origin"] == "probe")
             for n in names]
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=160)
    labels = [f"{n}\n({mechanisms.TITLES[n]})" for n in names]
    ax.bar(range(len(names)), real, color="#2b6cb0", label="real-derived (真实记录投影)")
    ax.bar(range(len(names)), probe, bottom=real, color="#f6ad55",
           label="probe (确定性合成探针)")
    for i, (r, p) in enumerate(zip(real, probe)):
        ax.text(i, r + p + 0.2, f"{r + p}", ha="center", fontsize=9)
    ax.axhline(5, color="#c53030", linestyle="--", linewidth=1,
               label="C3 门槛 ≥5 条/机制")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel("fixture 条数")
    ax.set_title("图1  golden fixture 覆盖：8 机制 × (真实投影 + 确定性探针)", fontsize=11)
    ax.legend(fontsize=7.5, loc="upper left")
    ax.set_ylim(0, max(r + p for r, p in zip(real, probe)) + 3)
    fig.tight_layout()
    path = out / "fig1_fixture_coverage.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def _fig2(matrix: dict, out: Path) -> Path:
    names = list(mechanisms.MECHANISMS)
    classes = list(mutate.MUTATION_CLASSES)
    grid = []
    annot = []
    for name in names:
        row, arow = [], []
        for klass in classes:
            cell = matrix.get(name, {}).get(klass)
            if not cell:
                row.append(float("nan"))
                arow.append("—")
            else:
                row.append(cell["detected"] / cell["samples"])
                arow.append(f"{cell['detected']}/{cell['samples']}")
        grid.append(row)
        annot.append(arow)

    fig, ax = plt.subplots(figsize=(10.5, 5.2), dpi=160)
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([f"{c.split('_', 1)[0]}\n{c.split('_', 1)[1]}" for c in classes],
                       fontsize=7.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([f"{n} ({mechanisms.TITLES[n]})" for n in names], fontsize=7.5)
    for i in range(len(names)):
        for j in range(len(classes)):
            ax.text(j, i, annot[i][j], ha="center", va="center", fontsize=8,
                    color="black")
    ax.set_title("图2  退化检出矩阵（k/n = 检出/样本；— 表示该机制无此类靶面）", fontsize=11)
    fig.colorbar(im, ax=ax, label="检出率", fraction=0.025)
    fig.tight_layout()
    path = out / "fig2_mutation_matrix.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def _fig3(report: dict, out: Path) -> Path:
    classes = list(mutate.MUTATION_CLASSES)
    channels = ["decision_flip", "idempotency", "audit_diff", "permutation"]
    colors = ["#2f855a", "#2b6cb0", "#b7791f", "#805ad5"]
    bottoms = [0] * len(classes)
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=160)
    for channel, color in zip(channels, colors):
        values = [report["classes"][c]["channel_detected"][channel] for c in classes]
        ax.bar(range(len(classes)), values, bottom=bottoms, color=color, label=channel)
        bottoms = [b + v for b, v in zip(bottoms, values)]
    for i, c in enumerate(classes):
        row = report["classes"][c]
        ax.text(i, bottoms[i] + 0.3, f"{row['detected']}/{row['samples']}\nreq={row['required_channel']}",
                ha="center", fontsize=7.5)
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([c.replace("_", "\n", 1) for c in classes], fontsize=7.5)
    ax.set_ylabel("检出样本数（可多渠道并计）")
    ax.set_ylim(0, max(bottoms) + 4)
    ax.set_title("图3  检出渠道分解：语义退化走决策/理由码，状态泄漏走幂等，审计丢失走审计面",
                 fontsize=10.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = out / "fig3_detection_channels.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def build_all(out_dir: Path | str = DEFAULT_OUT) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fixtures = replay.load_fixtures(out / "fixtures")
    suite = mutate.run_suite(fixtures)
    paths = {
        "fig1": str(_fig1(fixtures, out)),
        "fig2": str(_fig2(mutate.matrix(suite["samples"]), out)),
        "fig3": str(_fig3(suite, out)),
    }
    (out / "figures_manifest.json").write_text(
        json.dumps(paths, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mech_replay 报告图")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    print(json.dumps(build_all(args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())