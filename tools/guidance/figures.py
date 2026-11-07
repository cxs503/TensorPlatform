#!/usr/bin/env python3
"""IN24-E2 图件生成：读 ``artifacts/guidance_loop/evidence.json`` 出四张图。

图 1 梯度位移（基线 vs A 轮 vs B 轮份额）
图 2 标定映射（档位产出率 → 乘子，含 DRIFT_CAP 带）
图 3 准入矩阵（worker × 档位，A/B 两期，命中即获准）
图 4 豁免记账（各档位失败率：含/不含 honest FAIL 豁免口径）

用法：``python3 tools/guidance/figures.py``（产物落 artifacts/guidance_loop/）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.guidance_loop import BASE_SHARE, TIERS  # noqa: E402
from services.guidance_loop.gradient import DRIFT_CAP  # noqa: E402

ART = ROOT / "artifacts" / "guidance_loop"
EVIDENCE = ART / "evidence.json"
DPI = 120


def _load() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def fig_gradient_shift(evidence: dict) -> Path:
    shift = evidence["gradient_shift"]
    x = range(len(TIERS))
    width = 0.26
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.bar([i - width for i in x], [BASE_SHARE[t] for t in TIERS], width, label="baseline (60/25/10/4/1)")
    ax.bar(list(x), [shift["cycle_a"][t] for t in TIERS], width, label="cycle A (evaluation stats A)")
    ax.bar([i + width for i in x], [shift["cycle_b"][t] for t in TIERS], width, label="cycle B (stats B)")
    for index, tier in enumerate(TIERS):
        delta = shift["delta"][tier]
        ax.annotate(
            f"{delta:+.4f}",
            xy=(index + width, shift["cycle_b"][tier]),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            fontsize=7,
        )
    ax.set_xticks(list(x))
    ax.set_xticklabels(TIERS)
    ax.set_ylabel("share of dispatch pool")
    ax.set_title("Dispatch gradient follows accumulated evaluation\n(S1 down, S4/S5 up; pool = %d)" % evidence["total_pool"])
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = ART / "fig1_gradient_shift.png"
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_calibration(evidence: dict) -> Path:
    shift = evidence["gradient_shift"]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.axhspan(1 - DRIFT_CAP, 1 + DRIFT_CAP, color="#dfe7f1", label=f"bounded multiplier +-{DRIFT_CAP:.0%}")
    ax.axhline(1.0, color="#888", linewidth=0.8, linestyle="--")
    for label, yields_key, multipliers_key, marker in (
        ("cycle A", "yields_a", "multipliers_a", "o"),
        ("cycle B", "yields_b", "multipliers_b", "s"),
    ):
        yields = shift[yields_key]
        multipliers = shift[multipliers_key]
        for tier in TIERS:
            ax.scatter(yields[tier], multipliers[tier], marker=marker, s=42, label=label if tier == "S1" else None)
            ax.annotate(tier, (yields[tier], multipliers[tier]), textcoords="offset points",
                        xytext=(4, 4), fontsize=7)
    ax.set_xlabel("tier yield (empirical-Bayes smoothed success rate)")
    ax.set_ylabel("share multiplier")
    ax.set_title("Calibration map: yield -> bounded multiplier -> share")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = ART / "fig2_calibration_map.png"
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_admission(evidence: dict) -> Path:
    change = evidence["admission_change"]
    workers = sorted(change["before"])
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.4), sharey=True)
    for ax, phase, title in (
        (axes[0], "before", "before (cycle A)"),
        (axes[1], "after", "after (cycle B)"),
    ):
        matrix = [
            [1 if tier in change[phase][worker]["allowed_tiers"] else 0 for tier in TIERS]
            for worker in workers
        ]
        ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1.2, aspect="auto")
        ax.set_xticks(range(len(TIERS)))
        ax.set_xticklabels(TIERS)
        ax.set_yticks(range(len(workers)))
        ax.set_yticklabels([f'{w} ({change[phase][w]["tier"]})' for w in workers])
        ax.set_title(title, fontsize=9)
        for row in range(len(workers)):
            for col in range(len(TIERS)):
                ax.text(col, row, "Y" if matrix[row][col] else "-", ha="center", va="center", fontsize=8)
    fig.suptitle("Credit tier -> admission set (L1..L5, no peer ranking)", fontsize=9)
    fig.tight_layout()
    out = ART / "fig3_admission_matrix.png"
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_exemption(evidence: dict) -> Path:
    detail = evidence["failure_detail"]["by_tier"]
    tiers = [tier for tier in TIERS if tier in detail]
    with_exempt = [detail[t]["rate"] for t in tiers]
    without_exempt = [
        (detail[t]["fails"] + detail[t]["honest_fails"])
        / max(1, detail[t]["successes"] + detail[t]["fails"] + detail[t]["honest_fails"])
        for t in tiers
    ]
    x = range(len(tiers))
    width = 0.36
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar([i - width / 2 for i in x], without_exempt, width, label="naive rate (honest FAIL counted)")
    ax.bar([i + width / 2 for i in x], with_exempt, width, label="guidance-loop rate (exempt, booked to S-N)")
    for index, tier in enumerate(tiers):
        ax.annotate(
            f"HF={detail[tier]['honest_fails']}",
            xy=(index + width / 2, with_exempt[index]),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            fontsize=7,
        )
    ax.set_xticks(list(x))
    ax.set_xticklabels(tiers)
    ax.set_ylabel("failure rate")
    ax.set_title("Honest FAIL exemption on exploration tiers (S4/S5)\nleaves core-tier rates untouched")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = ART / "fig4_honest_fail_exemption.png"
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def main() -> int:
    evidence = _load()
    ART.mkdir(parents=True, exist_ok=True)
    outs = []
    for builder in (fig_gradient_shift, fig_calibration, fig_admission, fig_exemption):
        outs.append(builder(evidence))
    for path in outs:
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())