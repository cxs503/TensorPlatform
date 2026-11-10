#!/usr/bin/env python3
"""D1–D7 判据图（matplotlib，数据全部来自 artifacts/write_lease/acceptance.json）。

产物：``docs/write_lease/figs/fig{1..4}_*.png``
  fig1 D1 线性化：期望写数 / 成功写数 / 终值 / 陈旧写拒绝
  fig2 D2 死锁：每例「打破后前行耗时」vs 2×TTL 预算（对数纵轴）
  fig3 D3 吞吐：不同载荷下的下降百分比（session 粒度 vs per-write 粒度）
  fig4 D4 崩溃回收：每例从 SIGKILL 到回收的耗时 vs TTL+1s 预算
"""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ART = os.path.join(REPO, "artifacts", "write_lease", "acceptance.json")
OUT = os.path.join(REPO, "docs", "write_lease", "figs")

C = {"ok": "#2b8a3e", "warn": "#e8590c", "bad": "#c92a2a", "base": "#495057", "gov": "#1c7ed6"}


def _load() -> dict:
    with open(ART, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig1_d1(crit: dict, path: str) -> str:
    d1 = crit["D1"]
    labels = ["expected\nwrites", "successful\nwrites", "counter\nfinal value", "stale writes\nrejected"]
    vals = [d1["expected_writes"], d1["successful_writes"], d1["final_counter_value"],
            d1["stale_probe"]["rejected_lost_update"]]
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=96)
    bars = ax.bar(labels, vals, color=[C["base"], C["ok"], C["ok"], C["gov"]], width=0.6)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, max(vals) * 1.18)
    ax.set_ylabel("count")
    ax.set_title(f"D1 linearizability — {d1['workers']} concurrent processes × "
                 f"{d1['rounds_per_worker']} rounds, lost updates = {d1['lost_updates']}")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, pil_kwargs={"optimize": True})
    plt.close(fig)
    return path


def fig2_d2(crit: dict, path: str) -> str:
    cases = crit["D2"]["cases"]
    names = [c["case"] for c in cases]
    ms = [max(c["slowest_thread_ms"], 1e-3) for c in cases]
    budget = [c["progress_budget_ms"] for c in cases]
    fig, ax = plt.subplots(figsize=(9.2, 4.2), dpi=96)
    idx = range(len(cases))
    ax.bar([i - 0.2 for i in idx], ms, width=0.4, color=C["gov"], label="progress time (ms)")
    ax.plot([i + 0.2 for i in idx], budget, "v", color=C["bad"], label="budget = 2×TTL (ms)")
    ax.set_yscale("log")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(names, rotation=60, fontsize=7)
    ax.set_ylabel("ms (log)")
    ax.set_title(f"D2 deadlock — {crit['D2']['cases_passed']}/{crit['D2']['cases_total']} cases "
                 f"detected & broken (2-cycle / 4-cycle / self / TTL-competition)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(path, pil_kwargs={"optimize": True})
    plt.close(fig)
    return path


def fig3_d3(crit: dict, path: str) -> str:
    rows = crit["D3"]["throughput"]["rows"]
    sizes = [f"{r['payload_bytes'] // 1024} KiB" if r["payload_bytes"] >= 1024 else
             f"{r['payload_bytes']} B" for r in rows]
    sess = [r["drop_pct_vs_guarded_session"] for r in rows]
    perw = [r["drop_pct_vs_guarded_perwrite"] for r in rows]
    limit = crit["D3"]["throughput"]["criterion_drop_limit_pct"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=96)
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], sess, width=0.4, color=C["gov"],
           label="governed write (session lease)")
    ax.bar([i + 0.2 for i in x], perw, width=0.4, color=C["warn"],
           label="governed write (per-write lease)")
    ax.axhline(limit, color=C["bad"], ls="--", lw=1.2, label=f"criterion ≤ {limit}%")
    ax.set_xticks(list(x))
    ax.set_xticklabels(sizes)
    ax.set_ylabel("throughput drop vs guarded baseline (%)")
    ax.set_title(f"D3 throughput — {crit['D3']['disjoint_acquires']} disjoint acquires, "
                 f"rejects = {crit['D3']['rejected']}")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, pil_kwargs={"optimize": True})
    plt.close(fig)
    return path


def fig4_d4(crit: dict, path: str) -> str:
    cases = crit["D4"]["cases"]
    idx = [str(c["case"]) for c in cases]
    ms = [c["reclaimed_after_ms"] or 0.0 for c in cases]
    budget = cases[0]["reclaim_budget_ms"]
    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=96)
    ax.bar(idx, [max(v, 0.05) for v in ms], color=C["ok"])
    ax.axhline(budget, color=C["bad"], ls="--", lw=1.2, label=f"budget = TTL+1s = {budget:.0f} ms")
    for i, v in enumerate(ms):
        ax.text(i, max(v, 0.05), f"{v:.1f}", ha="center", va="bottom", fontsize=8)
    ax.set_yscale("log")
    ax.set_xlabel("SIGKILL injection case")
    ax.set_ylabel("reclaim latency (ms, log)")
    ax.set_title(f"D4 crash reclaim — {crit['D4']['cases_passed']}/{crit['D4']['cases_total']} "
                 f"cases within budget, holder≤1 invariant held")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(path, pil_kwargs={"optimize": True})
    plt.close(fig)
    return path


def main() -> int:
    data = _load()
    crit = data["criteria"]
    os.makedirs(OUT, exist_ok=True)
    outs = [
        fig1_d1(crit, os.path.join(OUT, "fig1_d1_linearizability.png")),
        fig2_d2(crit, os.path.join(OUT, "fig2_d2_deadlock.png")),
        fig3_d3(crit, os.path.join(OUT, "fig3_d3_throughput.png")),
        fig4_d4(crit, os.path.join(OUT, "fig4_d4_crash_reclaim.png")),
    ]
    for p in outs:
        print(os.path.relpath(p, REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())