"""播种报告配图 (纯 matplotlib，数据全部来自 gate 产物，零手工数字)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .model import LEVEL_RANK, Trace  # noqa: E402

_VERDICT_COLOR = {"AGREE": "#2e7d32", "DISAGREE": "#c62828",
                  "DEGENERATE": "#ef6c00", "WITHHELD": "#616161"}


def fig_trace_agreement(report: dict[str, Any], traces: dict[str, Trace],
                        path: Path) -> str | None:
    """把第一组带 rows 的 trace_agreement 检查画成"两侧数值 + 相对偏差"双轴图。"""
    for pr in report["pairs"]:
        for c in pr["checks"]:
            rows = (c.get("detail") or {}).get("rows")
            if c["check"] == "trace_agreement" and rows:
                d = c["detail"]
                params = [r["param"] for r in rows]
                a = [r["a_transformed"] for r in rows]
                b = [r["b"] for r in rows]
                dev = [r["rel_dev"] for r in rows]
                fig, ax = plt.subplots(figsize=(7.2, 4.4))
                ax.loglog(params, b, "o-", label=f"{d['b']} (reference side)", color="#1e88e5")
                ax.loglog(params, a, "s--", label=f"{d['a']} → transformed", color="#e53935",
                          mfc="none")
                ax.set_xlabel(r"parameter $\varepsilon$ (−)")
                ax.set_ylabel("measured value (−)")
                ax.grid(True, which="both", alpha=0.3)
                ax2 = ax.twinx()
                ax2.semilogy(params, [max(x, 1e-18) for x in dev], "d-", color="#6d4c41",
                             alpha=0.75, label="relative deviation")
                ax2.set_ylabel("relative deviation (|a−b|/max)")
                ax2.axhline(d["tol"], color="#6d4c41", ls=":", lw=1)
                h1, l1 = ax.get_legend_handles_labels()
                h2, l2 = ax2.get_legend_handles_labels()
                ax.legend(h1 + h2, l1 + l2, loc="lower left", fontsize=8)
                ax.set_title(f"{pr['id']} · {c['check']}: {c['verdict']} "
                             f"(max rel dev {d['max_rel_dev']:.3e} ≤ tol {d['tol']:g})",
                             fontsize=10)
                fig.tight_layout()
                out = path / "fig1_trace_agreement.png"
                path.mkdir(parents=True, exist_ok=True)
                fig.savefig(out, dpi=160)
                plt.close(fig)
                return out.name
    return None


def fig_pair_checks(report: dict[str, Any], path: Path) -> str:
    """互裁对 × 检查 的结论矩阵 (颜色编码，数值标注最大相对偏差)。"""
    pairs = report["pairs"]
    checks = sorted({c["check"] for p in pairs for c in p["checks"]})
    fig, ax = plt.subplots(figsize=(1.6 + 1.5 * len(checks), 1.2 + 0.8 * len(pairs)))
    for i, p in enumerate(pairs):
        for j, ck in enumerate(checks):
            hit = next((c for c in p["checks"] if c["check"] == ck), None)
            if hit is None:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, color="#eeeeee"))
                continue
            ax.add_patch(plt.Rectangle((j, i), 1, 1,
                                       color=_VERDICT_COLOR.get(hit["verdict"], "#999999"),
                                       alpha=0.75))
            d = hit["detail"]
            label = hit["verdict"]
            for k in ("max_rel_dev", "rel_dev", "max_ratio", "value", "max_abs_dev"):
                if isinstance(d.get(k), (int, float)):
                    label += f"\n{k}={d[k]:.2e}"
                    break
            ax.text(j + 0.5, i + 0.5, label, ha="center", va="center", fontsize=7,
                    color="white", weight="bold")
    ax.set_xlim(0, len(checks))
    ax.set_ylim(0, len(pairs))
    ax.set_xticks([j + 0.5 for j in range(len(checks))])
    ax.set_xticklabels(checks, rotation=25, ha="right", fontsize=8)
    ax.set_yticks([i + 0.5 for i in range(len(pairs))])
    ax.set_yticklabels([p["id"] for p in pairs], fontsize=8)
    ax.set_title("inter-adjudication checks (blocking + advisory)", fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path.mkdir(parents=True, exist_ok=True)
    out = path / "fig2_pair_checks.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out.name


def fig_ledger(report: dict[str, Any], path: Path) -> str:
    """每节点能力等级分布 + 节点判据通过率。"""
    nodes = report["nodes"]
    levels = list(LEVEL_RANK)
    labels = [n["node_ext_id"].replace("p158w2-", "") for n in nodes]
    fig, ax = plt.subplots(figsize=(max(6.4, 0.85 * len(nodes) + 3), 4.2))
    bottom = [0.0] * len(nodes)
    for lv in levels:
        vals = [sum(1 for c in n["capabilities"] if c["level"] == lv) for n in nodes]
        if not any(vals):
            continue
        ax.bar(range(len(nodes)), vals, bottom=bottom, label=lv)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(len(nodes)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("capability entries")
    ax2 = ax.twinx()
    pr = [n["pass_rate"] if n["pass_rate"] is not None else 0.0 for n in nodes]
    ax2.plot(range(len(nodes)), pr, "ko--", ms=5, label="criteria pass rate")
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("criteria pass rate")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper right")
    ax.set_title(f"capability ledger levels · {report['campaign']}", fontsize=10)
    fig.tight_layout()
    path.mkdir(parents=True, exist_ok=True)
    out = path / "fig3_ledger_levels.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out.name


def render_all(report: dict[str, Any], traces: dict[str, Trace], out_dir: Path) -> list[str]:
    out_dir = Path(out_dir)
    written: list[str] = []
    name = fig_trace_agreement(report, traces, out_dir)
    if name:
        written.append(name)
    written.append(fig_pair_checks(report, out_dir))
    written.append(fig_ledger(report, out_dir))
    return written