#!/usr/bin/env python3
"""IN28-N06 结果图：全部数字从 artifact JSON 读，不手抄。

| 图 | 内容 |
|---|---|
| ``fig_match_ladder`` | 提议按通道×状态堆叠；匹配率（含 95% CI）vs 90% 门槛 |
| ``fig_divergence`` | SR1 分歧率：全体 / v0 写者 / v1 写者（含 CI）vs 20% 目标；匹配时滞分布 |
| ``fig_gates`` | 回填库条数 vs 50 条门槛；回滚演练判据（坏部署拦截数、回滚秒数 vs 300 s 预算） |
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLORS = {"matched": "#2e8b57", "diverged": "#c0392b", "pending": "#b3b3b3",
          "unobservable": "#7f8c8d"}


def _load(path: str) -> Mapping[str, Any]:
    with open(path) as fh:
        return json.load(fh)


def _ann(ax, x: float, y: float, text: str) -> None:
    ax.annotate(text, (x, y), textcoords="offset points", xytext=(0, 4),
                ha="center", fontsize=8)


def fig_match_ladder(report: Mapping[str, Any], out: str) -> str:
    chans = report["channels"]
    names = sorted(chans)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    states = ("matched", "diverged", "pending", "unobservable")
    bottoms = [0] * len(names)
    for state in states:
        vals = [chans[n]["counts"].get(state, 0) for n in names]
        ax.barh(names, vals, left=bottoms, color=COLORS[state], label=state)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    for i, n in enumerate(names):
        ax.text(bottoms[i] + 8, i, f"{chans[n]['total']}", va="center", fontsize=9)
    ax.set_xlabel("proposals")
    ax.set_title("(a) proposals by channel × state")
    ax.legend(fontsize=8, loc="lower right")

    ax = axes[1]
    ci = [chans[n].get("match_rate_ci95") for n in names]
    vals = [chans[n]["match_rate"] for n in names]
    exact = [chans[n]["exact_match_rate"] for n in names]
    xs = range(len(names))
    err = [[0 if c is None else v - c[0] for v, c in zip(vals, ci)],
           [0 if c is None else c[1] - v for v, c in zip(vals, ci)]]
    ax.bar([x - 0.18 for x in xs], vals, width=0.36, color="#2980b9", label="any route",
           yerr=err, capsize=3)
    ax.bar([x + 0.18 for x in xs], exact, width=0.36, color="#8e44ad", label="exact route only")
    ax.axhline(0.90, color="#e67e22", ls="--", lw=1.3, label="target ≥ 0.90")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names, fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("match rate")
    ax.set_title("(b) auto-match rate (95% Wilson CI)")
    for x, v in zip(xs, vals):
        _ann(ax, x - 0.18, v, f"{v:.3f}")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def fig_divergence(report: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                   out: str) -> str:
    coord = report["channels"]["coordinator_shadow"]
    by_writer = coord.get("by_writer") or {}
    labels = ["all writers"] + [f"writer {w}" for w in sorted(by_writer)]
    values = [coord["divergence_rate"]]
    cis = [coord.get("divergence_rate_ci95")]
    for w in sorted(by_writer):
        values.append(by_writer[w]["divergence_rate"])
        cis.append(by_writer[w].get("divergence_rate_ci95"))
    ns = [coord["judged"]] + [by_writer[w]["matched"] + by_writer[w]["diverged"]
                              for w in sorted(by_writer)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    xs = range(len(labels))
    err = [[0 if c is None else v - c[0] for v, c in zip(values, cis)],
           [0 if c is None else c[1] - v for v, c in zip(values, cis)]]
    bars = ax.bar(list(xs), values, color="#c0392b", yerr=err, capsize=4, width=0.5)
    ax.axhline(0.20, color="#e67e22", ls="--", lw=1.3, label="SR1 target < 0.20")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{lab}\n(n={k})" for lab, k in zip(labels, ns)], fontsize=8)
    ax.set_ylim(0, 0.75)
    ax.set_ylabel("divergence rate")
    ax.set_title("(a) SR1 divergence (95% Wilson CI)")
    for i, (b, v) in enumerate(zip(bars, values)):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.015, f"{100 * v:.1f}%",
                ha="center", fontsize=9)
    ax.legend(fontsize=8)

    ax = axes[1]
    lags = [float(r["lag_seconds"]) / 60.0 for r in records
            if r.get("lag_seconds") is not None and r["channel"] == "coordinator_shadow"]
    if lags:
        ax.hist(lags, bins=min(20, max(4, len(lags) // 2)), color="#2980b9", edgecolor="white")
        ax.axvline(60, color="#e67e22", ls="--", lw=1.2, label="look-ahead 60 min")
        ax.set_xlabel("proposal → disposition lag (min)")
        ax.set_ylabel("records")
        ax.set_title(f"(b) matching lag (n={len(lags)}, median={sorted(lags)[len(lags) // 2]:.0f} min)")
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def fig_gates(report: Mapping[str, Any], drill: Mapping[str, Any] | None, out: str) -> str:
    crit = report["criteria"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    chans = report["store"]["by_channel"]
    names = sorted(chans)
    vals = [chans[n] for n in names]
    bars = ax.bar(names, vals, color="#16a085", width=0.5)
    ax.axhline(50, color="#e67e22", ls="--", lw=1.3, label="backfill target ≥ 50")
    ax.set_yscale("log")
    ax.set_ylabel("records in backfill store (log)")
    ax.set_title(f"(a) historical backfill = {crit['backfill_records']} records")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.08, str(v), ha="center", fontsize=9)
    ax.legend(fontsize=8)

    ax = axes[1]
    if drill:
        c = drill["criteria"]
        labels = ["bad deploys\nblocked", "state untouched", "good deploy\naccepted",
                  "rollback\n< 300 s", f"rollback\n{c['rollback_seconds'] * 1000:.1f} ms",
                  "byte-identical", "neg-control\ncatches tamper"]
        flags = [c["bad_deploys_blocked"], c["state_untouched_by_bad"], c["good_deploy_accepted"],
                 c["rollback_within_budget"], True, c["state_identical_after_rollback"],
                 c["negative_control_detects_tamper"]]
        ypos = range(len(labels))
        ax.barh(list(ypos), [1 if f else 0 for f in flags],
                color=["#2e8b57" if f else "#c0392b" for f in flags])
        ax.set_yticks(list(ypos))
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlim(0, 1.15)
        ax.invert_yaxis()
        ax.set_xticks([])
        ax.set_title(f"(b) rollback drill: {'PASS' if c['pass'] else 'FAIL'} "
                     f"({sum(1 for f in flags if f)}/{len(flags)})")
    else:
        ax.text(0.5, 0.5, "no drill artifact", ha="center", va="center")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN28-N06 figures")
    ap.add_argument("--report", default="artifacts/coordinator_shadow/sr1_maturation.json")
    ap.add_argument("--drill", default="artifacts/coordinator_shadow/rollback_drill.json")
    ap.add_argument("--records", default=None, help="可选：records json（含 lag_seconds）")
    ap.add_argument("--outdir", default="runs/in28_n06/figs")
    args = ap.parse_args(list(argv) if argv is not None else None)
    os.makedirs(args.outdir, exist_ok=True)
    report = _load(args.report)
    records: list[Mapping[str, Any]] = []
    if args.records and os.path.exists(args.records):
        records = _load(args.records).get("records", [])
    drill = _load(args.drill) if os.path.exists(args.drill) else None
    made = [
        fig_match_ladder(report, os.path.join(args.outdir, "fig_match_ladder.png")),
        fig_divergence(report, records, os.path.join(args.outdir, "fig_divergence.png")),
        fig_gates(report, drill, os.path.join(args.outdir, "fig_gates.png")),
    ]
    for path in made:
        print(f"figure: {path} ({os.path.getsize(path)} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())