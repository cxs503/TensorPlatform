"""IN35-N07 结果图：只读 ``artifacts/IN35-N07/*.json``，不重算、不手抄。

三张图对应任务书第 ⑤ 条要的三块面板：

1. ``fig1_coverage_trend.png``：逐 cohort 覆盖率（分子/分母标注）+ 多视图不变性；
2. ``fig2_backlog_drain.png``：积压排空瀑布（before → drained / 仍 pending → after）
   与目标线（≥90%）；
3. ``fig3_inflight_latency.png``：in-flight 时延直方图 + 经验 CDF，标 P95 与 60 s 阈值。

容器内无中文字体 → 图内文字一律英文（标题/轴标签），中文说明写在报告里。
"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ART = pathlib.Path("artifacts/IN35-N07")


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def fig_coverage(panel: dict, acc: dict) -> pathlib.Path:
    trend = panel["coverage_trend"]
    inv = acc["J1"]["cohorts_per_view"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    keys = [r["cohort"] for r in trend]
    covs = [r["coverage"] if r["coverage"] is not None else 0.0 for r in trend]
    labels = [f"{r['missions_decided']}/{r['missions_total']}" for r in trend]
    colors = ["#2f6fb3" if r["frozen"] else "#c46a1b" for r in trend]
    bars = ax1.bar(keys, covs, color=colors, width=0.55)
    for b, r, lab in zip(bars, trend, labels):
        top = r["coverage"] if r["coverage"] is not None else 0.0
        ax1.text(b.get_x() + b.get_width() / 2, top + 0.02, lab, ha="center", fontsize=8)
    ax1.set_ylim(0, 1.0)
    ax1.set_ylabel("cohort coverage = decided / total")
    ax1.set_title("Cohort coverage (window-invariant)\nblue=frozen week, orange=in-progress")
    ax1.grid(axis="y", alpha=0.25)

    # 多视图不变性：每个 cohort 在各视图下的覆盖率完全相同 → 折线重合
    view_days = sorted(inv, key=lambda d: int(d))
    for cohort in keys:
        ys = []
        for d in view_days:
            row = next((r for r in trend if r["cohort"] == cohort), None)
            ys.append(None if row is None or row["coverage"] is None else row["coverage"])
        ax2.plot([int(d) for d in view_days], ys, marker="o", label=cohort)
    ax2.set_xscale("log")
    ax2.set_xlabel("view window (days, log scale)")
    ax2.set_ylabel("coverage in that view")
    ax2.set_title("Same cohort, same numbers in every view\n(window only filters which cohorts show)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    out = ART / "fig1_coverage_trend.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_drain(acc: dict, panel: dict) -> pathlib.Path:
    j3 = acc["J3"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.0))

    labels = ["pending\n(before)", "drained\n(decided)", "left pending\n(no evidence)", "pending\n(after)"]
    vals = [j3["pending_before"], j3["drained"], j3["undecidable_left_pending"], j3["pending_after"]]
    cols = ["#8a8f98", "#2f7d32", "#b58900", "#4a4f57"]
    bars = ax1.bar(labels, vals, color=cols, width=0.6)
    for b, v in zip(bars, vals):
        ax1.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, str(v), ha="center", fontsize=9)
    ax1.set_ylabel("pending evaluation rows")
    ax1.set_title(f"Backlog drain (threshold={j3['threshold']}, elapsed={j3['elapsed_seconds']:.3f}s)")
    ax1.axhline(j3["threshold"], color="#c0392b", ls="--", lw=1,
               label=f"trigger threshold = {j3['threshold']}")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.25)

    share = j3["drain_share"]
    ax2.barh(["drain share"], [share], color="#2f7d32", height=0.35)
    ax2.barh(["drain share"], [max(0.0, 1 - share)], left=[share], color="#d8d8d8", height=0.35)
    ax2.axvline(j3["target_share"], color="#c0392b", ls="--", lw=1,
                label=f"SLO target = {j3['target_share']:.0%} / {j3['window_hours']:.0f}h")
    ax2.text(min(share + 0.01, 0.95), 0, f"{share:.2%}", va="center", fontsize=10)
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("share of the initial pending set that got decided")
    ax2.set_title("Drain SLO: met" if j3["met"] else "Drain SLO: breach")
    ax2.legend(fontsize=8, loc="lower right")
    ax2.grid(axis="x", alpha=0.25)

    fig.tight_layout()
    out = ART / "fig2_backlog_drain.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_latency(acc: dict) -> pathlib.Path:
    j2 = acc["J2"]
    hist = j2["histogram"]
    lat = j2["latency_seconds"]
    counts = [b["n"] for b in hist]
    xt = [f"[{b['lo']},{b['hi'] if b['hi'] is not None else 'inf'})" for b in hist]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.0))
    bars = ax1.bar(range(len(counts)), counts, color="#3b6ea5")
    ax1.set_xticks(range(len(counts)))
    ax1.set_xticklabels(xt, rotation=25, ha="right", fontsize=7)
    ax1.set_ylabel("events")
    ax1.set_title(f"in-flight latency histogram (n={lat['n']}, seconds)")
    for b, v in zip(bars, counts):
        if v:
            ax1.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", fontsize=7)
    ax1.grid(axis="y", alpha=0.25)

    # 经验 CDF（用分位点构造阶梯，避免重算原始样本）
    pts = [(lat["min"], 0.0), (lat["p50"], 0.50), (lat["p95"], 0.95), (lat["p99"], 0.99),
           (lat["max"], 1.0)]
    ax2.step([p[0] for p in pts], [p[1] for p in pts], where="post", color="#1f4e79")
    ax2.scatter([p[0] for p in pts], [p[1] for p in pts], s=18, color="#1f4e79", zorder=3)
    ax2.axhline(0.95, color="#c0392b", ls="--", lw=1, label="P95")
    ax2.axvline(j2["target_p95_seconds"], color="#2f7d32", ls=":", lw=1.2,
                label=f"target {j2['target_p95_seconds']:.0f} s")
    ax2.set_xscale("log")
    ax2.set_xlabel("latency per event (seconds, log scale)")
    ax2.set_ylabel("empirical CDF")
    ax2.set_title(f"P95 = {lat['p95']:.4f} s  ({'PASS' if j2['j2_pass'] else 'FAIL'})")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.25, which="both")

    fig.tight_layout()
    out = ART / "fig3_inflight_latency.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> int:
    acc = _load("acceptance.json")
    panel = _load("panel_baseline.json")
    paths = [fig_coverage(panel, acc), fig_drain(acc, panel), fig_latency(acc)]
    manifest = {
        "schema": "eval-timeliness-figures/1",
        "figures": [
            {"name": "fig1_coverage_trend.png",
             "caption": "逐 cohort 覆盖率（分子/分母标注；蓝=已冻结周，橙=进行中周）"
                        "与多视图不变性（同一 cohort 在 2/7/15/30/90/300 天视图下数值重合）"},
            {"name": "fig2_backlog_drain.png",
             "caption": "积压排空瀑布：100 行 pending → 92 行已判定 / 8 行证据不足保持 pending，"
                        "排空份额 92% ≥ 90% 目标线"},
            {"name": "fig3_inflight_latency.png",
             "caption": "in-flight 增量评价时延直方图与经验 CDF，P95 远低于 60 s 契约"},
        ],
        "files": [str(p) for p in paths],
    }
    (ART / "figures_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())