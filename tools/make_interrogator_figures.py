"""IN27-H4 质询机制图（数字全部程序化读取 verdict.json / cycle / ledger_export，零手抄）。

产出 docs/IN27-H4/figures/ 下三张 PNG：
  fig1_gate_status.png      —— 三条机器判据 实测/要求 进度（A1/A2/A3），标注真实数字
  fig2_question_portfolio.png —— 本期题目组合（族 × 严重度），题数配额带
  fig3_signal_evidence.png  —— 质询所依据的真实平台信号（预算恒零 / 实质增量 0 / 负结果未入账）

字体说明：容器内无 CJK 字体（fc-list 只到 Noto Sans Latin），故图内标注用英文，
中文说明放图注（report figures.caption）与文档正文——避免出豆腐块假图。
"""
from __future__ import annotations

import json
import os
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "runs", "IN27-H4")
OUT = os.path.join(ROOT, "docs", "IN27-H4", "figures")


def _load(name: str) -> dict:
    with open(os.path.join(RUNS, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig_gate(verdict: dict) -> str:
    crit = {c["name"]: c for c in verdict["criteria"]}
    a1 = crit["A1_每周期题数3-5"]
    a2 = crit["A2_连续7天产出"]
    a3 = crit["A3_至少2件真实转化"]
    labels = ["A1 questions\nper cycle (req 3-5)",
              "A2 consecutive\nlive days (req 7)",
              "A3 verified\nconversions (req 2)"]
    ratios = [
        min(1.0, a1["numbers"]["counts"][0] / 3.0),
        a2["numbers"]["live_days_in_window"] / a2["numbers"]["required_days"],
        a3["numbers"]["unique_converted"] / a3["numbers"]["required"],
    ]
    annotations = [
        f"{a1['numbers']['counts'][0]} q/cycle, {a1['numbers']['violations']} violations",
        f"{a2['numbers']['live_days_in_window']}/{a2['numbers']['required_days']} days, "
        f"streak={a2['numbers']['current_streak']}, status={a2['numbers']['status']}",
        f"{a3['numbers']['unique_converted']}/{a3['numbers']['required']} converted, "
        f"tampered={len(a3['numbers']['tampered'])}",
    ]
    ok = [a1["ok"], a2["ok"], a3["ok"]]
    colors = ["#2e7d32" if o else "#c62828" for o in ok]
    fig, ax = plt.subplots(figsize=(7.2, 3.0), dpi=110)
    bars = ax.barh(labels, ratios, color=colors, height=0.55)
    for bar, ann, ratio, flag in zip(bars, annotations, ratios, ok):
        ax.text(max(ratio, 0.02) + 0.015, bar.get_y() + bar.get_height() / 2,
                f"{ann}  [{'PASS' if flag else 'NOT MET'}]",
                va="center", fontsize=8)
    ax.axvline(1.0, color="#37474f", linestyle="--", linewidth=1)
    ax.set_xlim(0, 1.9)
    ax.set_xlabel("fraction of requirement met (dashed = 100%)")
    ax.set_title(f"IN27-H4 interrogation gate: {verdict['verdict']}  "
                 f"(as_of {verdict['as_of'][:16]})", fontsize=10)
    fig.tight_layout()
    path = os.path.join(OUT, "fig1_gate_status.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_portfolio(cycle: dict) -> str:
    qs = cycle["questions"]
    fam_colors = {"EXP_BUDGET": "#1565c0", "GRADE_CEIL": "#6a1b9a",
                  "SN_TRACK": "#ef6c00", "ZERO_FLOW": "#00838f", "CONVERSION": "#558b2f"}
    labels = [f"{q['key']} ({q['family']})" for q in qs]
    sev = [q["severity"] for q in qs]
    colors = [fam_colors.get(q["family"], "#546e7a") for q in qs]
    fig, ax = plt.subplots(figsize=(7.2, 2.8), dpi=110)
    ax.barh(labels, sev, color=colors, height=0.55)
    for i, q in enumerate(qs):
        key = q.get("mission_params", {}).get("task_key", "").split(":")[-1]
        ax.text(sev[i] + 0.05, i, f"task_key {key}", va="center", fontsize=7, color="#455a64")
    ax.axvspan(3, 5, color="#cfd8dc", alpha=0.35, zorder=0)
    ax.set_xlim(0, 6.5)
    ax.set_xlabel("severity (shaded band = per-cycle quota 3-5 questions)")
    ax.set_title(f"Cycle {cycle['cycle_id']}: {len(qs)} questions across "
                 f"{len(set(q['family'] for q in qs))} families, "
                 f"created_missions={cycle['created_missions']}", fontsize=10)
    fig.tight_layout()
    path = os.path.join(OUT, "fig2_question_portfolio.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_signals(cycle: dict, ledger_export: dict) -> str:
    sig = cycle["signals"]
    plans = sig["plans_budget"]["value"] or {}
    cov = (sig.get("capability_coverage") or {}).get("value") or {}
    fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.1), dpi=110)

    ax = axes[0]
    total = int(plans.get("total") or 0)
    zero = int(plans.get("zero_actual") or 0)
    ax.bar(["budget_actual>0", "budget_actual==0"], [total - zero, zero],
           color=["#2e7d32", "#c62828"])
    ax.set_title("campaign plans: actual budget booked?", fontsize=9)
    ax.text(1, zero, f"{zero}/{total}", ha="center", va="bottom", fontsize=9)

    ax = axes[1]
    sub = int(cov.get("substantive_deltas") or 0)
    nosub = int(cov.get("no_substantive_deltas") or 0)
    ax.bar(["substantive", "no_substantive"], [sub, nosub], color=["#2e7d32", "#c62828"])
    ax.set_title("capability deltas by substance", fontsize=9)
    ax.text(1, nosub, f"{nosub} total, {sub} substantive", ha="center", va="bottom", fontsize=8)

    ax = axes[2]
    failed = int(sig["missions_failed_window"]["value"] or 0)
    honest = int(sig["missions_honest_fail_window"]["value"] or 0)
    succ = int(sig["missions_succeeded_window"]["value"] or 0)
    ax.bar(["succeeded", "failed", "failed with\nhonest-FAIL"],
           [succ, failed, honest], color=["#2e7d32", "#ef6c00", "#c62828"])
    ax.set_title("7-day mission outcomes (S-N track)", fontsize=9)
    for x, v in enumerate([succ, failed, honest]):
        ax.text(x, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_yscale("symlog")

    convs = len(ledger_export.get("conversions", []))
    fig.suptitle("Interrogation evidence base (real platform signals, cycle "
                 f"{cycle['cycle_date']}) — {convs} conversions booked", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = os.path.join(OUT, "fig3_signal_evidence.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    verdict = _load("verdict.json")
    cycle = _load(f"cycle_{verdict['as_of'][:10]}.json")
    ledger = _load("ledger_export.json")
    paths = [fig_gate(verdict), fig_portfolio(cycle), fig_signals(cycle, ledger)]
    for p in paths:
        print(p, os.path.getsize(p), "bytes")
    fams = Counter(q["family"] for q in cycle["questions"])
    print("families:", dict(fams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())