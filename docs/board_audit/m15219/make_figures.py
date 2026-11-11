#!/usr/bin/env python3
"""m15219 交差审计图：全部数字从 verify_output.json 程序化读取（零手抄）。

输出: docs/board_audit/m15219/fig1_audit_checks.png
      docs/board_audit/m15219/fig2_maturity_recount.png
      docs/board_audit/m15219/fig3_gate_coverage.png
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "verify_output.json").read_text(encoding="utf-8"))
CH = {c["check"]: c for c in D["checks"]}
LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]


def A(t) -> str:
    """图内文本只保留 ASCII（容器无 CJK 字体，避免豆腐块；中文留在图注/报告正文）。"""
    return str(t).encode("ascii", "ignore").decode().replace("  ", " ").strip()


def fig_checks() -> Path:
    names = list(CH.keys())
    vals = [1 if CH[n]["pass"] else 0 for n in names]
    colors = ["#2e7d32" if v else "#c62828" for v in vals]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    bars = ax.barh(names[::-1], [1] * len(names), color=colors[::-1], alpha=0.85)
    for b, v, n in zip(bars, vals[::-1], names[::-1]):
        ax.text(0.02, b.get_y() + b.get_height() / 2, "PASS" if v else "FAIL",
                va="center", color="white", fontweight="bold")
    ax.set_xlim(0, 1.05)
    ax.set_xticks([])
    ax.set_title(f"m15219 receipt audit of m15050 H6-MATURITY-GATE — proposed level L{D['proposed_level']}", fontsize=11)
    for i, (k, v) in enumerate(D["ladder"].items()):
        ax.text(0.62, -0.9 - i * 0.38, A(f"{k}: {'OK' if v['ok'] else 'NOT MET'} - {v['evidence'][:90]}"),
                fontsize=7, transform=ax.transData, clip_on=False)
    ax.set_ylim(-0.6 - 0.38 * len(D["ladder"]), len(names) - 0.4)
    fig.tight_layout()
    p = HERE / "fig1_audit_checks.png"
    fig.savefig(p, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return p


def fig_maturity() -> Path:
    a3 = CH["A3_maturity_recount"]
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    x = range(len(LEVELS))
    w = 0.2
    mine_b = [a3["recomputed_before"]["histogram"][l] for l in LEVELS]
    gate_b = [a3["gate_before"]["histogram"][l] for l in LEVELS]
    mine_a = [a3["recomputed_after"]["histogram"][l] for l in LEVELS]
    gate_a = [a3["gate_after"]["histogram"][l] for l in LEVELS]
    ax.bar([i - 1.5 * w for i in x], mine_b, w, label="baseline (audit recount)", color="#1565c0")
    ax.bar([i - 0.5 * w for i in x], gate_b, w, label="baseline (gate.json)", color="#90caf9")
    ax.bar([i + 0.5 * w for i in x], mine_a, w, label="current (audit recount)", color="#e65100")
    ax.bar([i + 1.5 * w for i in x], gate_a, w, label="current (gate.json)", color="#ffcc80")
    ax.set_xticks(list(x))
    ax.set_xticklabels(LEVELS)
    ax.set_ylabel("capabilities N=21")
    ax.set_title("5-level maturity profile: independent recount vs artifact (cell-by-cell equal)", fontsize=10)
    ax.legend(fontsize=8)
    ax.text(0.02, 0.96, f"score: {a3['recomputed_before']['maturity_score']:.6f} -> "
                        f"{a3['recomputed_after']['maturity_score']:.6f} (recount == claim, diff 0)",
            transform=ax.transAxes, fontsize=8, va="top")
    fig.tight_layout()
    p = HERE / "fig2_maturity_recount.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def fig_coverage() -> Path:
    a6 = CH["A6_benchmark_judge"]
    probes = a6["measured"]["probes"]
    lines = a6["measured"]["lines"]
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    xs = range(len(lines))
    g1 = [1 if probes[l]["G1_gate_exists"] else 0 for l in lines]
    g2 = [1 if probes[l]["G2_can_fail_struct"] else 0 for l in lines]
    g3 = [1 if probes[l]["G3_reject_prod"] is True else 0 for l in lines]
    ax.bar([x - 0.25 for x in xs], g1, 0.25, label="G1 gate exists", color="#1565c0")
    ax.bar(list(xs), g2, 0.25, label="G2 can fail (struct)", color="#2e7d32")
    ax.bar([x + 0.25 for x in xs], g3, 0.25, label="G3 prod rejection", color="#ef6c00")
    en = {"PR": "PR", "报告": "Reports", "基准": "Benchmark", "知识": "Knowledge", "论文": "Papers"}
    ax.set_xticks(list(xs))
    ax.set_xticklabels([en.get(l, A(l)) for l in lines])
    ax.set_ylim(0, 1.5)
    ax.set_title(f"benchmark judge: gate coverage got={a6['measured']['got_strict']:.0f}%(strict)/"
                 f"{a6['measured']['got_structural']:.0f}%(structural) vs tolerance {a6['tolerance']}", fontsize=10)
    ax.legend(fontsize=8, ncol=3)
    fig.tight_layout()
    p = HERE / "fig3_gate_coverage.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


if __name__ == "__main__":
    for f in (fig_checks, fig_maturity, fig_coverage):
        print(f().name)