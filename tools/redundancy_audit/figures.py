"""报告图（matplotlib，无交互后端）：阈值敏感性 + 操作剖面 + 簇冗余成本排序。

图的数字全部从 ``artifacts/redundancy_report.json`` 读（零手抄），文件名与报告里的
``figures`` 字段一致；生成是确定性的（固定 dpi/尺寸、无随机数、无时间戳）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .config import RedundancyConfig  # noqa: E402

DPI = 150


def _sweep_xy(report: Mapping[str, Any]):
    rows = report["thresholds"]["frozen"]["sweep_at_freeze"]
    xs = [r["threshold"] for r in rows]
    return xs, ([r["precision"] for r in rows], [r["recall"] for r in rows],
                [r["f1"] for r in rows])


def fig_threshold_sensitivity(report: Mapping[str, Any], out: Path) -> Path:
    xs, (prec, rec, f1) = _sweep_xy(report)
    stress = report["paraphrase_stress"]["recall_by_threshold"]
    sx = sorted(float(k) for k in stress)
    sy = [stress[f"{t:.2f}"]["recall"] for t in sx]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 4.4))
    ax.plot(xs, f1, "o-", label="F1 (labels n=100)", color="#1f4e79")
    ax.plot(xs, prec, "s--", label="precision", color="#2e7d32", alpha=0.8)
    ax.plot(xs, rec, "^--", label="recall", color="#c62828", alpha=0.8)
    ax.plot(sx, sy, "d-", label="paraphrase recall (n=17)", color="#6a1b9a")
    ax.axvline(float(report["thresholds"]["strong"]), color="k", lw=0.8, ls=":")
    ax.axvline(float(report["thresholds"]["frozen"]["frozen_threshold"]), color="#ff8f00",
               lw=0.8, ls=":")
    ax.text(float(report["thresholds"]["strong"]), 1.02, " strong", fontsize=8)
    ax.text(float(report["thresholds"]["frozen"]["frozen_threshold"]), -0.06,
            " frozen", fontsize=8)
    ax.set_xlabel("similarity threshold")
    ax.set_ylabel("score")
    ax.set_ylim(-0.1, 1.12)
    ax.set_title("(a) labelled-set F1 vs paraphrase recall")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.25)

    prof = report["thresholds"]["profile"]
    tx = [p["threshold"] for p in prof]
    ax2.plot(tx, [p["clusters"] for p in prof], "o-", color="#1f4e79", label="clusters")
    ax2.plot(tx, [p["nodes_in_clusters"] for p in prof], "s-", color="#2e7d32",
             label="nodes in clusters")
    ax2.set_xlabel("similarity threshold")
    ax2.set_ylabel("count")
    ax2.grid(alpha=0.25)
    ax3 = ax2.twinx()
    ax3.plot(tx, [100 * p["redundant_share"] for p in prof], "^-", color="#c62828",
             label="redundant share (%)")
    ax3.set_ylabel("redundant share of wall-clock (%)")
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax3.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right")
    ax2.set_title("(b) operational profile")
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_cluster_cost(report: Mapping[str, Any], out: Path, top: int = 12) -> Path:
    clusters = sorted(report["clusters"]["clusters"],
                      key=lambda c: -float(c["cost"]["redundant"]["wall_seconds_actual"]))[:top]
    labels = [f"{c['cluster_id']} (n={c['size']})" for c in clusters]
    merge = [float(c["cost"]["by_action"]["merge"]["wall_seconds_actual"]) / 3600.0
             for c in clusters]
    archive = [float(c["cost"]["by_action"]["archive"]["wall_seconds_actual"]) / 3600.0
               for c in clusters]
    import numpy as np

    y = np.arange(len(clusters))
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.barh(y, merge, color="#1f4e79", label="merge (already spent, consolidate)")
    ax.barh(y, archive, left=merge, color="#c62828", label="archive (cancel, zero spend)")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("redundant wall-clock (hours)")
    ax.set_title("Top redundant clusters (non-keeper spend) at strong threshold")
    ax.legend(fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def generate(report_path: Path, out_dir: Path) -> list[dict[str, str]]:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    f1 = fig_threshold_sensitivity(report, out_dir / "threshold_sensitivity.png")
    f2 = fig_cluster_cost(report, out_dir / "cluster_redundant_cost.png")
    return [
        {"name": f1.name, "path": str(f1.relative_to(out_dir.parents[1])),
         "caption": "图1 阈值敏感性：(a) 标注集 F1/precision/recall 与同题改写召回随阈值变化；"
                    "(b) 簇数/簇内节点数/冗余成本占比的操作剖面"},
        {"name": f2.name, "path": str(f2.relative_to(out_dir.parents[1])),
         "caption": "图2 强阈值下冗余成本最高的 12 个簇（非 keeper 实耗，按 merge/archive 拆分）"},
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="生成冗余审计报告图")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = RedundancyConfig.load(args.config)
    figs = generate(cfg.path_of("paths.report"), cfg.path_of("paths.figure_dir"))
    print(json.dumps({"figures": figs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())