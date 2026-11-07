"""评价运维产物的图（读 artifacts/IN38-N09/*.json，不重新算数）。

四张图：
1. ``fig1_cohort_coverage.png``   逐 cohort 的 mission 规模 / 已判定数与覆盖率
2. ``fig2_cohort_latency_backlog.png`` cohort 的 P90 评价时延与积压行数（带 SLO 线）
3. ``fig3_window_independence.png`` 7/30/90 视图下同一 cohort 数值完全重合（差异=0），
                                   与旧口径（最近 N 单）随窗口漂移的对照
4. ``fig4_inflight_cycles.png``   in-flight 三轮：pending 行数与已判定 mission 数

图上的数全部来自 JSON 产物；没有任何手抄数字。标签用英文（容器内无中文字体，
避免出现豆腐块），图注在报告里用中文写。
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ART = pathlib.Path("artifacts/IN38-N09")
DPI = 74   # 报告内嵌 base64 有总量上限（~250 KB）→ 每图控制在 ~25 KB 量级


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_coverage(capture: Mapping[str, Any], out: pathlib.Path) -> pathlib.Path:
    cohorts = capture["cohorts"]
    keys = sorted(cohorts)
    total = [cohorts[k]["missions_total"] for k in keys]
    decided = [cohorts[k]["missions_decided"] for k in keys]
    cov = [cohorts[k]["coverage"] for k in keys]

    fig, ax = plt.subplots(figsize=(8.2, 4.7))
    x = range(len(keys))
    ax.bar([i - 0.2 for i in x], total, width=0.4, label="missions in cohort", color="#9ecae1")
    ax.bar([i + 0.2 for i in x], decided, width=0.4, label="missions decided", color="#08519c")
    ax.set_xticks(list(x))
    ax.set_xticklabels(keys)
    ax.set_ylabel("missions")
    ax.set_title("Cohort size vs decided missions (membership = ISO week of finished_at)")
    ax.grid(axis="y", alpha=0.3)
    for i, k in enumerate(keys):
        c = cov[i]
        label = "undefined" if c is None else f"{c:.1%}"
        ax.annotate(f"coverage {label}", (i, max(total[i], decided[i]) * 1.02),
                    ha="center", fontsize=9)
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_latency_backlog(capture: Mapping[str, Any], out: pathlib.Path,
                        thresholds: Mapping[str, float]) -> pathlib.Path:
    slo = capture["slo"]["views"]["all"]
    cohorts = capture["cohorts"]
    keys = sorted(cohorts)
    p90 = [cohorts[k]["p90_latency_hours"] for k in keys]
    backlog = [cohorts[k]["backlog_rows"] for k in keys]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2))
    x = range(len(keys))
    vals = [0 if v is None else v for v in p90]
    ax1.bar(list(x), vals, color="#cb181d")
    ax1.axhline(thresholds["latency_p90_hours_max"], color="k", ls="--", lw=1,
                label=f"SLO {thresholds['latency_p90_hours_max']:.0f} h")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(keys)
    ax1.set_ylabel("P90 evaluation latency [h]")
    ax1.set_title(f"P90 latency (global {slo['p90_latency_hours']:.1f} h)")
    for i, v in enumerate(p90):
        ax1.annotate("undefined" if v is None else f"{v:.1f}", (i, (0 if v is None else v) * 1.02),
                     ha="center", fontsize=9)
    ax1.legend()
    ax1.grid(axis="y", alpha=0.3)

    ax2.bar(list(x), backlog, color="#f16913")
    ax2.axhline(thresholds["backlog_rows_max"], color="k", ls="--", lw=1,
                label=f"SLO {int(thresholds['backlog_rows_max'])} rows")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(keys)
    ax2.set_ylabel("pending evaluation rows")
    ax2.set_title(f"Backlog ({slo['backlog_rows']} rows, age origin = finished_at)")
    for i, v in enumerate(backlog):
        ax2.annotate(str(v), (i, v * 1.02 if v else 0.3), ha="center", fontsize=9)
    ax2.legend()
    ax2.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_window(capture: Mapping[str, Any], out: pathlib.Path) -> pathlib.Path:
    wi = capture["window_independence"]
    details = wi["details"]
    cohorts = capture["cohorts"]
    keys = sorted(cohorts)
    days = details["views_days"]
    legacy = wi["legacy_window_dependent"]["rows"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.3))
    width = 0.8 / len(days)
    for j, d in enumerate(days):
        vals = []
        for k in keys:
            in_view = k in details["cohorts_per_view"][str(d)]
            vals.append(cohorts[k]["coverage"] if in_view else 0.0)
        ax1.bar([i + j * width - 0.4 + width / 2 for i in range(len(keys))], vals,
                width=width, label=f"{d}-day view")
    ax1.set_xticks(range(len(keys)))
    ax1.set_xticklabels(keys)
    ax1.set_ylabel("cohort coverage")
    ax1.set_title(f"Identical per cohort across 7/30/90 (max abs diff = "
                  f"{wi['cohort_invariant']['max_abs_diff']:g})")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    windows = [r["window"] for r in legacy]
    cov = [r["coverage"] for r in legacy]
    ax2.plot(windows, cov, "o-", color="#31a354")
    ax2.set_xscale("log")
    ax2.set_xlabel("legacy window N (recent missions)")
    ax2.set_ylabel("coverage")
    ax2.set_title(f"Legacy window-based coverage drifts (spread "
                  f"{wi['legacy_window_dependent']['spread']:.3f})")
    for w, c in zip(windows, cov):
        ax2.annotate(f"{c:.2f}", (w, c), textcoords="offset points", xytext=(0, 6), fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def fig_inflight(replay: Mapping[str, Any], out: pathlib.Path) -> pathlib.Path:
    cycles = replay["cycles"]
    hist = cycles["history"]
    labels = ["start"] + [f"cycle {h['cycle']}" for h in hist]
    pending = [cycles["before"]["pending_rows"]] + [h["counts"]["pending_rows"] for h in hist]
    decided = [cycles["before"]["missions_decided"]] + [h["counts"]["missions_decided"] for h in hist]
    cov = [cycles["before"]["coverage"]] + [h["counts"]["coverage"] for h in hist]

    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    x = range(len(labels))
    ax.bar(list(x), pending, color="#f16913", label="pending rows")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("pending rows")
    ax.grid(axis="y", alpha=0.3)
    ax2 = ax.twinx()
    ax2.plot(list(x), decided, "o-", color="#08519c", label="missions decided")
    ax2.plot(list(x), [0 if c is None else c * 10000 for c in cov], "s--", color="#31a354",
             label="coverage x 1e4")
    ax2.set_ylabel("missions decided  /  coverage x 1e4")
    for i, p in enumerate(pending):
        ax.annotate(str(p), (i, p + 1), ha="center", fontsize=9)
    for i, c in enumerate(cov):
        ax2.annotate("undefined" if c is None else f"{c:.1%}", (i, c * 10000),
                     textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8)
    ax.set_title("in-flight evaluation cycles: backlog -> 0, coverage rises "
                 f"(drop {cycles['pending_drop_share']:.0%})")
    lines1, lab1 = ax.get_legend_handles_labels()
    lines2, lab2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, lab1 + lab2, loc="center right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ART))
    args = ap.parse_args(argv)
    art = pathlib.Path(args.dir)
    capture = _load(art / "capture_prod_post_inflight.json")
    replay = _load(art / "replay_cycles_snapshot.json")
    thresholds = capture["thresholds"]
    made = [
        fig_coverage(capture, art / "fig1_cohort_coverage.png"),
        fig_latency_backlog(capture, art / "fig2_cohort_latency_backlog.png", thresholds),
        fig_window(capture, art / "fig3_window_independence.png"),
        fig_inflight(replay, art / "fig4_inflight_cycles.png"),
    ]
    print(json.dumps({"figures": [str(p) for p in made],
                      "bytes": {p.name: p.stat().st_size for p in made}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())