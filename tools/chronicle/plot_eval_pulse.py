#!/usr/bin/env python3
"""评价面快照 → 报告图（数字全部从 artifacts JSON 读，零手抄）。

用法:
    python3 tools/chronicle/plot_eval_pulse.py \
        --snapshot-1 artifacts/chronicle_eval_pulse/snapshot_1.json \
        --snapshot-2 artifacts/chronicle_eval_pulse/snapshot_2.json \
        --window-sensitivity artifacts/chronicle_eval_pulse/window_sensitivity.json \
        --drill artifacts/chronicle_eval_pulse/drill_alert.json \
        --out-dir artifacts/chronicle_eval_pulse/figures
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TH = {"coverage_min": 0.60, "pending_max": 200.0, "independent_share_min": 0.50}
ALERT_ORDER = ("coverage_low", "pending_backlog_high", "independent_share_low")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def figure_thresholds(facet: dict, out: Path) -> Path:
    cov = facet["coverage"]
    pb = facet["pending_backlog"]
    th = facet.get("thresholds") or TH
    ratio = cov["ratio"] or 0.0
    indep = facet["independent_share"]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))

    ax = axes[0]
    ax.bar(["coverage\n(confirmed/succeeded)"], [ratio * 100], color="#2b7bba", width=0.5)
    ax.axhline(float(th["coverage_min"]) * 100, color="#c0392b", ls="--", lw=1.6,
               label=f"threshold {float(th['coverage_min']):.0%}")
    ax.set_ylim(0, 100)
    ax.set_ylabel("percent")
    ax.set_title("Coverage (window)")
    ax.annotate(f"{ratio:.1%}\n({cov['missions_confirmed']}/{cov['missions_succeeded']})",
                xy=(0, ratio * 100), xytext=(0.05, min(ratio * 100 + 14, 92)),
                ha="center", fontsize=10, color="#1b4f72")
    ax.legend(loc="upper right", fontsize=9)

    ax = axes[1]
    vals = [0.0 if indep is None else indep * 100,
            0.0 if facet["auto_scan_share"] is None else facet["auto_scan_share"] * 100]
    ax.bar(["independent\nreview", "auto_scan"], vals, color=["#27ae60", "#e67e22"], width=0.55)
    ax.axhline(float(th["independent_share_min"]) * 100, color="#c0392b", ls="--", lw=1.6,
               label=f"threshold {float(th['independent_share_min']):.0%}")
    ax.set_ylim(0, 105)
    ax.set_ylabel("share of confirmed records [%]")
    ax.set_title("Review structure")
    ax.annotate("undefined" if indep is None else f"{indep:.1%}", xy=(0, vals[0]),
                xytext=(0.0, min(vals[0] + 12, 72)), ha="center", fontsize=10, color="#1e8449")
    ax.annotate(f"{vals[1]:.1f}%", xy=(1, vals[1]), xytext=(1.0, min(vals[1] + 12, 95)),
                ha="center", fontsize=10, color="#a04000")
    ax.legend(loc="upper left", fontsize=9)

    ax = axes[2]
    ax.bar(["pending backlog"], [pb["count"]], color="#8e44ad", width=0.45)
    ax.axhline(float(th["pending_max"]), color="#c0392b", ls="--", lw=1.6,
               label=f"threshold {int(float(th['pending_max']))}")
    ax.set_ylim(0, max(float(th["pending_max"]) * 1.15, pb["count"] * 1.3))
    ax.set_ylabel("records")
    ax.set_title("Pending backlog & age")
    ax.annotate(f"{pb['count']} records\noldest {pb['oldest_age_hours']} h\nmedian {pb['median_age_hours']} h",
                xy=(0, pb["count"]), xytext=(0.05, pb["count"] + 0.12 * ax.get_ylim()[1]),
                ha="center", fontsize=9, color="#4a235a")
    ax.legend(loc="upper right", fontsize=9)

    subj = facet.get("subject")
    fig.suptitle(f"Evaluation pulse of chronicle snapshots — subject={subj}, window={facet.get('window')}, "
                 f"as_of={facet.get('as_of')}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def figure_distributions(facet: dict, out: Path) -> Path:
    s = facet["s_distribution"]["counts"]
    e = facet["e_distribution"]["counts"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.0))
    ax = axes[0]
    keys = list(s.keys())
    ax.bar(keys, [s[k] for k in keys], color="#2c3e50")
    ax.set_title(f"S-grade distribution (confirmed, unweighted; total={facet['s_distribution']['total']})")
    ax.set_ylabel("records")
    ax.set_xlabel("grade bucket (S-N kept separate, never merged into S1–S5)")
    for i, k in enumerate(keys):
        ax.annotate(str(s[k]), xy=(i, s[k]), xytext=(i, s[k] + 0.4), ha="center", fontsize=9)
    ax.set_ylim(0, max(list(s.values()) + [1]) * 1.25)

    ax = axes[1]
    ekeys = list(e.keys())
    ax.bar(ekeys, [e[k] for k in ekeys], color="#1f6f8b")
    ax.set_title(f"E-level distribution (confirmed, total={facet['e_distribution']['total']})")
    ax.set_ylabel("records")
    ax.set_xlabel("evidence level E0–E4")
    for i, k in enumerate(ekeys):
        ax.annotate(str(e[k]), xy=(i, e[k]), xytext=(i, e[k] + 0.4), ha="center", fontsize=9)
    ax.set_ylim(0, max(list(e.values()) + [1]) * 1.25)
    fig.suptitle(f"No weighting, no ranking: layered counts only — subject={facet.get('subject')}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def figure_ticks(snap1: dict, snap2: dict, out: Path) -> Path:
    f1, f2 = snap1["facet"], snap2["facet"]
    e1 = snap1.get("alert_edge") or {}
    e2 = snap2.get("alert_edge") or {}
    emitted1 = {a["key"] for a in e1.get("emitted", [])}
    emitted2 = {a["key"] for a in e2.get("emitted", [])}
    suppressed2 = {a["key"] for a in e2.get("suppressed", [])}

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.2), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    labels = ["coverage", "independent\nshare", "pending/200"]
    v1 = [f1["coverage"]["ratio"] or 0.0, f1["independent_share"] or 0.0,
          f1["pending_backlog"]["count"] / float(f1["thresholds"]["pending_max"])]
    v2 = [f2["coverage"]["ratio"] or 0.0, f2["independent_share"] or 0.0,
          f2["pending_backlog"]["count"] / float(f2["thresholds"]["pending_max"])]
    x = range(len(labels))
    ax.bar([i - 0.18 for i in x], v1, width=0.34, label=f"tick1 {f1['as_of']}", color="#5dade2")
    ax.bar([i + 0.18 for i in x], v2, width=0.34, label=f"tick2 {f2['as_of']}", color="#1a5276")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("normalised value (pending scaled by its threshold)")
    ax.set_title("Two consecutive chronicle ticks (full evaluation facet each)")
    ax.legend(fontsize=8)

    ax = axes[1]
    for i, key in enumerate(ALERT_ORDER):
        st = (f2.get("alert_states") or {}).get(key, "undefined")
        color = {"breach": "#c0392b", "ok": "#27ae60", "undefined": "#7f8c8d"}.get(st, "#7f8c8d")
        ax.scatter([0], [i], marker="o", s=260, color=color, zorder=3)
        tag = "EMITTED" if key in emitted1 else ("dedup" if key in suppressed2 else "-")
        ax.scatter([1], [i], marker="o", s=260, color=color, zorder=3)
        ax.annotate(tag, xy=(1, i), xytext=(1.16, i), va="center", fontsize=9)
        ax.annotate("EMITTED" if key in emitted1 else "-", xy=(0, i), xytext=(-0.42, i),
                    va="center", fontsize=9)
    ax.set_xlim(-0.6, 1.7)
    ax.set_ylim(-0.6, len(ALERT_ORDER) - 0.4)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["tick1", "tick2"])
    ax.set_yticks(range(len(ALERT_ORDER)))
    ax.set_yticklabels(list(ALERT_ORDER), fontsize=9)
    ax.set_title(f"Alert rise/edge: tick1 emitted={len(emitted1)}, tick2 emitted={len(emitted2)} "
                 f"(suppressed={len(suppressed2)})")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.suptitle("Rising-edge digest alerts: one alert per breach, no repeat on the next tick", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def figure_window_sensitivity(sens: dict, out: Path) -> Path:
    rows = sens["rows"]
    w = [r["window"] for r in rows]
    cov = [(r["coverage_ratio"] or 0.0) * 100 for r in rows]
    ind = [44 if r["independent_share"] is None else r["independent_share"] * 100 for r in rows]
    undef = [r["independent_share"] is None for r in rows]
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.plot(w, cov, "o-", color="#2b7bba", label="coverage [%] = confirmed/succeeded")
    ax.plot(w, ind, "s--", color="#27ae60", label="independent share [%] (hatched = undefined)")
    ax.axhline(60, color="#c0392b", ls=":", lw=1.5, label="coverage threshold 60%")
    ax.axhline(50, color="#e67e22", ls=":", lw=1.5, label="independent threshold 50%")
    for xi, yi, u in zip(w, ind, undef):
        if u:
            ax.annotate("undefined\n(0 confirmed)", xy=(xi, yi), xytext=(xi, yi + 7),
                        ha="center", fontsize=8, color="#27ae60")
    ax.set_xscale("log")
    ax.set_xticks(w)
    ax.set_xticklabels([str(x) for x in w])
    ax.set_xlabel("evaluation window (last N succeeded missions, log scale)")
    ax.set_ylabel("percent")
    ax.set_title(f"Window sensitivity of the coverage facet — subject={sens.get('subject')}")
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-1", default="artifacts/chronicle_eval_pulse/snapshot_1.json")
    ap.add_argument("--snapshot-2", default="artifacts/chronicle_eval_pulse/snapshot_2.json")
    ap.add_argument("--window-sensitivity", default="artifacts/chronicle_eval_pulse/window_sensitivity.json")
    ap.add_argument("--out-dir", default="artifacts/chronicle_eval_pulse/figures")
    args = ap.parse_args()
    out = Path(args.out_dir)
    s1, s2 = _load(args.snapshot_1), _load(args.snapshot_2)
    made = [
        figure_thresholds(s2["facet"], out / "fig1_thresholds.png"),
        figure_distributions(s2["facet"], out / "fig2_distributions.png"),
        figure_ticks(s1, s2, out / "fig3_two_ticks_alerts.png"),
        figure_window_sensitivity(_load(args.window_sensitivity), out / "fig4_window_sensitivity.png"),
    ]
    for p in made:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())