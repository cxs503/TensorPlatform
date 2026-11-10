"""figures —— 回测报告图（全部数字从 ``backtest_report.json`` / 语料 JSON 读取，零手抄）。

产出
----
``fig1_confusion_decidable.png``  口径 B（交付结局）可判子集混淆矩阵
``fig2_pr_curves.png``            PR 曲线（全体 / 可判子集）+ 随机基线
``fig3_fail_stage_profile.png``   blocked 节点的失败阶段剖面（回答「为什么 blocked」）
``fig4_availability_lift.png``    输入可得性 vs 实际 blocked 率（假设检验图）
``fig5_coverage_funnel.png``      从战役总体到可判子集的覆盖率漏斗

用法::

    python3 -m tools.dispatch_contract.figures --report <backtest_report.json> \\
        --corpus <corpus_v1.jsonl> --outdir artifacts/dispatch_contract
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _cm_fig(cm: dict, out: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(4.6, 4.0))
    grid = [[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]]
    im = ax.imshow(grid, cmap="Blues")
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(grid[i][j]), ha="center", va="center",
                    color="black" if grid[i][j] < max(max(r) for r in grid) else "white",
                    fontsize=13)
    ax.set_xticks([0, 1], ["gate READY", "gate BLOCK"])
    ax.set_yticks([0, 1], ["delivered", "blocked"])
    ax.set_xlabel("predicted"); ax.set_ylabel("actual")
    ax.set_title(f"{title}\nprecision={cm['precision']:.3f} recall={cm['recall']:.3f} n={cm['n']}", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def _pr_fig(report: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.6, 4.2))
    for key, style in (("population", "-o"), ("decidable", "-s")):
        pts = report["pr_curve"][key]
        rec = [p["recall"] for p in pts]
        prec = [p["precision"] for p in pts]
        auc = report[key]["auc"]
        ax.plot(rec, prec, style, ms=3, lw=1.2, label=f"{key} (AUC={auc:.3f})")
    pos = report["coverage"]["n_blocked"] / max(1, report["coverage"]["n_records"])
    ax.axhline(pos, ls=":", color="grey", lw=1, label=f"random baseline (prevalence={pos:.3f})")
    ax.set_xlabel("recall"); ax.set_ylabel("precision")
    ax.set_title("Precision-Recall: pre-dispatch gate vs node blocked", fontsize=10)
    ax.set_xlim(0, 1.02); ax.set_ylim(0, 1.02)
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(out, dpi=140); plt.close(fig)


def _failstage_fig(report: dict, out: Path) -> None:
    prof = report.get("fail_stage_profile") or {}
    items = sorted(prof.items(), key=lambda kv: -kv[1])
    labels = [k if k and k != "None" else "unattributed" for k, _ in items]
    vals = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.barh(range(len(vals))[::-1], vals, color="#4c72b0")
    ax.set_yticks(range(len(vals))[::-1], labels, fontsize=8)
    for i, v in enumerate(vals):
        ax.text(v + 0.5, len(vals) - 1 - i, str(v), va="center", fontsize=8)
    ax.set_xlabel("count of blocked nodes")
    ax.set_title("Why nodes were blocked (platform fail_stage, machine field)", fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=140); plt.close(fig)


def _availability_fig(report: dict, out: Path) -> None:
    cm = report["tier_a_satisfiability"]["confusion"]
    # 口径 B 下按「输入可得性」分层的实际 blocked 率：tp/fn 归入「不可得」组
    unavail_n = cm["tp"] + cm["fp"]
    avail_n = cm["fn"] + cm["tn"]
    unavail_rate = cm["tp"] / unavail_n if unavail_n else 0.0
    avail_rate = cm["fn"] / avail_n if avail_n else 0.0
    base = report["coverage"]["n_blocked"] / max(1, report["coverage"]["n_records"])
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    xs = ["inputs unavailable\nat dispatch", "inputs available\nat dispatch", "population\nbase rate"]
    ys = [unavail_rate, avail_rate, base]
    ax.bar(xs, ys, color=["#c44e52", "#55a868", "#8172b2"])
    for i, (y, n) in enumerate(zip(ys, [unavail_n, avail_n, report["coverage"]["n_records"]])):
        ax.text(i, y + 0.008, f"{y:.3f}\n(n={n})", ha="center", fontsize=8)
    ax.set_ylabel("observed blocked rate")
    ax.set_title("Falsification: input unavailability does NOT raise blocked rate", fontsize=10)
    ax.set_ylim(0, max(ys) * 1.35)
    fig.tight_layout(); fig.savefig(out, dpi=140); plt.close(fig)


def _funnel_fig(report: dict, out: Path) -> None:
    cov = report["coverage"]
    n_rec = cov["n_records"]
    n_dec = cov["n_decidable"]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    labels = ["terminal campaign nodes", "with machine-readable\ninput contract", "blocked among them"]
    vals = [n_rec, n_dec, cov["n_blocked_decidable"]]
    ax.barh(range(len(vals))[::-1], vals, color=["#4c72b0", "#dd8452", "#55a868"])
    ax.set_yticks(range(len(vals))[::-1], labels, fontsize=8)
    for i, v in enumerate(vals):
        ax.text(v + max(vals) * 0.01, len(vals) - 1 - i, f"{v}", va="center", fontsize=9)
    ax.set_xlabel("count")
    ax.set_title(f"Contract coverage = {cov['coverage']:.1%} (gate can only decide on this slice)", fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=140); plt.close(fig)


def make_all(report_path: str | Path, outdir: str | Path) -> list[Path]:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    outs = []
    f1 = out / "fig1_confusion_decidable.png"
    _cm_fig(report["decidable"]["verdict_block"], f1, "Gate vs outcome (decidable subset)")
    outs.append(f1)
    f2 = out / "fig2_pr_curves.png"; _pr_fig(report, f2); outs.append(f2)
    f3 = out / "fig3_fail_stage_profile.png"; _failstage_fig(report, f3); outs.append(f3)
    f4 = out / "fig4_availability_lift.png"; _availability_fig(report, f4); outs.append(f4)
    f5 = out / "fig5_coverage_funnel.png"; _funnel_fig(report, f5); outs.append(f5)
    return outs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch_contract.figures")
    ap.add_argument("--report", required=True)
    ap.add_argument("--corpus", default=None, help="保留参数：本轮图仅依赖 backtest_report.json")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args(argv)
    for p in make_all(args.report, args.outdir):
        print(p)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())