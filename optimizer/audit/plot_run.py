"""重跑结果作图（m1610）：收敛曲线 + 基线/最优对比。

读 ``optimizer/runs/opt_run_holtrop1984/`` 下的 JSON，写 PNG（零手抄，全部从工件读）。
用法： python3 -m optimizer.audit.plot_run
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "optimizer" / "runs" / "opt_run_holtrop1984"


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    res = json.loads((RUN / "optimization_result.json").read_text())
    conv = json.loads((RUN / "convergence_curve.json").read_text())
    gens = [h["generation"] for h in conv]
    best = [h["best_Ct"] for h in conv]
    mean = [h["mean_Ct"] for h in conv]
    base = res["baseline"]["Ct"]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(gens, best, "o-", ms=3, label="best Ct (population)")
    ax.plot(gens, mean, "s--", ms=3, alpha=0.7, label="mean Ct")
    ax.axhline(base, color="g", ls=":", lw=2,
               label="baseline Ct = %.6f (zero deformation)" % base)
    ax.set_xlabel("generation")
    ax.set_ylabel("Ct [-] (design Fn=%.2f)" % res["metadata"]["design_Fn"])
    ax.set_title("CMA-ES convergence, canonical Holtrop-1984 objective (%s)"
                 % res["objective_evidence_level"])
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out1 = RUN / "convergence_curve.png"
    fig.savefig(out1, dpi=140)
    plt.close(fig)

    # 最优解的阻力分解（正典分量）
    comp = res["top5_candidates"][0]["components"]
    labels = ["(1+k)Rf", "Rw", "Rb", "Rtr", "Ra"]
    vals = [comp["Rv_N"], comp["Rw_N"], comp["Rb_N"], comp["Rtr_N"], comp["Ra_N"]]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    bars = ax.bar(labels, vals, color="steelblue", alpha=0.85)
    ax.set_ylabel("resistance component [N]")
    ax.set_title("Best design at design Fn: Rt = %.3f N (Ct = %.6f)"
                 % (comp["Rt_N"], comp["Ct"]))
    ax.grid(alpha=0.3, axis="y")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, "%.3f" % v, ha="center", va="bottom",
                fontsize=8)
    fig.tight_layout()
    out2 = RUN / "best_components.png"
    fig.savefig(out2, dpi=140)
    plt.close(fig)

    print(f"wrote {out1.relative_to(ROOT)}, {out2.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())