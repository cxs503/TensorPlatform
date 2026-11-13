"""产出报告图（零手抄：所有数字从 artifacts/compute_meter/*.json 读）。"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = REPO_ROOT / "artifacts" / "compute_meter"


def fig_ab(data: dict, out: pathlib.Path) -> None:
    a = data["arm_A"]
    b = data["arm_B"]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    names = ["A status quo\n(forced local)", "B policy\n(submitted)"]
    vals = [a["local_cpu_seconds_mean"], b["local_cpu_seconds_mean"]]
    bars = ax.bar(names, vals, color=["#c0392b", "#27ae60"], width=0.55)
    ax.set_ylabel("CPU seconds burned on control node 99 (per run)")
    ax.set_title("Placement A/B: n=%d per arm, %.0f CPU-s requested each"
                 % (a["n"], data["design"]["burn_seconds"]))
    ax.axhline(data["design"]["target_local_share"] * a["local_cpu_seconds_mean"],
               ls="--", color="#7f8c8d", label="20% target line")
    for bar, v in zip(bars, vals):
        ax.annotate("%.2f s" % v, (bar.get_x() + bar.get_width() / 2, v),
                    ha="center", va="bottom", fontsize=10)
    ax.text(0.02, 0.95, "ratio B/A = %.3f (target <=0.20)" % (data["ratio_B_over_A"] or 0),
            transform=ax.transAxes, fontsize=9, va="top")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_placement(m: dict, out: pathlib.Path) -> None:
    by_node = m["summary"]["by_node"]
    names = list(by_node)[:6]
    hours = [by_node[k]["cpu_hours"] for k in names]
    colors = ["#c0392b" if by_node[k]["placement"] == "local" else "#2980b9" for k in names]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bars = ax.bar(names, hours, color=colors, width=0.6)
    ax.set_ylabel("CPU hours (cpu_seconds/3600)")
    ax.set_title("CPU hours by machine (local share %.1f%%, target <=20%%)"
                 % (m["summary"]["local_share"] * 100))
    for bar, v in zip(bars, hours):
        ax.annotate("%.1f" % v, (bar.get_x() + bar.get_width() / 2, v),
                    ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_queue(m: dict, out: pathlib.Path) -> None:
    q = m["queue"]
    labels = ["p50", "p90", "p99", "max"]
    vals = [q["wait_p50_seconds"], q["wait_p90_seconds"], q["wait_p99_seconds"],
            q["wait_max_seconds"]]
    hours = [(v or 0) / 3600.0 for v in vals]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bars = ax.bar(labels, hours, color="#8e44ad", width=0.55)
    ax.set_ylabel("queue wait (hours)")
    ax.set_title("Cluster queue wait percentiles (n=%d, depth=%d)"
                 % (q["wait_n"], q["depth"]))
    for bar, v in zip(bars, hours):
        ax.annotate("%.1f h" % v, (bar.get_x() + bar.get_width() / 2, v),
                    ha="center", va="bottom", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    made = []
    ab = ART / "ab_result.json"
    if ab.exists():
        fig_ab(json.loads(ab.read_text()), ART / "fig_ab_local_cpu.png")
        made.append("fig_ab_local_cpu.png")
    rep = ART / "report_metrics.json"
    if rep.exists():
        m = json.loads(rep.read_text())
        fig_placement(m, ART / "fig_placement_cpu_hours.png")
        fig_queue(m, ART / "fig_queue_wait.png")
        made += ["fig_placement_cpu_hours.png", "fig_queue_wait.png"]
    print(json.dumps({"figures": made}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())