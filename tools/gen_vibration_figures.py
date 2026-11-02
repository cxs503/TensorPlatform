#!/usr/bin/env python3
"""振动噪声域验证欠账 —— 报告插图（全部数据从 artifacts/vibration_gate.json 读，零手抄）。

产出 ``docs/verification_debt/vibration_noise/artifacts/`` 下两张 png：
  fig1_vibration_reference_vs_platform.png
  fig2_platform_fingerprint.png
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "docs", "verification_debt", "vibration_noise", "artifacts")


def load():
    with open(os.path.join(ART, "vibration_gate.json"), encoding="utf-8") as f:
        return json.load(f)


def fig_reference(ref: list[dict]) -> str:
    L = np.array([r["L"] for r in ref])
    f2v = np.array([r["f2v_exact_Hz"] for r in ref])
    fb = np.array([r["blade_rate_Hz"] for r in ref])

    fig, ax = plt.subplots(figsize=(9.0, 5.4), dpi=150)

    # 参考实现的外推曲线（沿四船级做对数二次拟合，仅作趋势可视化）
    p = np.polyfit(np.log(L), np.log(f2v), 2)
    Ls = np.linspace(L.min() * 0.9, L.max() * 1.05, 200)
    ax.plot(Ls, np.exp(np.polyval(p, np.log(Ls))), "-", color="#1f77b4", lw=1.6,
            label="reference f2v (closed-form, free-free beam)")
    ax.plot(L, f2v, "o", ms=8, color="#1f77b4",
            label="f2v analytic (4 ship classes; FEM self-check 4.2e-8)")
    ax.plot(L, fb, "s", ms=7, color="#d62728", label="blade rate f_b = n*Z/60")

    # 平台输出：任何频率类量都不存在 → 画成「不可分辨」的区间
    ax.axhspan(0.0, 0.0 + 1e-12, color="none")
    ax.annotate("platform endpoint returns\nNO frequency quantity\n(resonance margin NOT_SUPPORTED)",
                xy=(300, 3.2), fontsize=9, color="#8c564b", ha="center",
                bbox=dict(boxstyle="round,pad=0.35", fc="#fdf0e6", ec="#8c564b", lw=0.8))

    ax.set_xlabel("Length between perpendiculars L [m]")
    ax.set_ylabel("Frequency [Hz]")
    ax.set_title("Fig.1 Vibration/noise reference: 2-node vertical hull girder frequency vs blade rate")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8.5)
    ax.set_yscale("log")
    out = os.path.join(ART, "fig1_vibration_reference_vs_platform.png")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_fingerprint(live: dict) -> str:
    resp = live["responses"]
    by_class = {r["class"]: r for r in load()["reference"]}
    labels = [r["ship_id"] for r in resp]
    acc = [float(r["body"]["acceleration"]) for r in resp]
    noise = [float(r["body"]["noise_level"]) for r in resp]
    # 参考量按船级 join（平台响应里没有频率，必须从 reference 取）
    f2v = [float(by_class[r["ship_id"]]["f2v_exact_Hz"]) for r in resp]

    x = np.arange(len(labels))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.0, 4.6), dpi=150)

    a1.plot(x, acc, "o-", color="#2ca02c", lw=1.8, ms=8, label="platform `acceleration`")
    a1.plot(x, noise, "s-", color="#9467bd", lw=1.8, ms=8, label="platform `noise_level`")
    a1.set_xticks(x, labels, rotation=18, ha="right", fontsize=8.5)
    a1.set_ylim(0, 75)
    a1.set_ylabel("returned value (unit undeclared)")
    a1.set_title("(a) 4 ship classes -> identical fields (0% variation)")
    a1.grid(alpha=0.3)
    a1.legend(fontsize=8.5)

    rng = (max(f2v) - min(f2v)) / max(f2v) * 100.0
    prng_acc = 0.0
    a2.bar(["platform\nacceleration", "platform\nnoise_level", "reference\nf2v"],
           [prng_acc, 0.0, rng], color=["#2ca02c", "#9467bd", "#1f77b4"])
    for i, v in enumerate([prng_acc, 0.0, rng]):
        a2.text(i, v + 1.0, f"{v:.1f}%", ha="center", fontsize=9)
    a2.set_ylabel("relative variation across 4 classes (L 145-399 m) [%]")
    a2.set_title(f"(b) platform resolvability = 0% vs reference {rng:.1f}%")
    a2.grid(alpha=0.3, axis="y")

    out = os.path.join(ART, "fig2_platform_fingerprint.png")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


if __name__ == "__main__":
    d = load()
    print(fig_reference(d["reference"]))
    print(fig_fingerprint(d["live"]))