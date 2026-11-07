"""Figures for IN23-N08 — four-state time band, claim-vs-truth confusion matrix, latency CDF.

All three figures are generated from committed artefacts (sampler JSONL + replay JSON), never
from hand-typed numbers:

  ① ``fig1_state_band.png``     per-worker four-state band over the sampling hour, with the
                                fleet state-time budget as a companion panel;
  ② ``fig2_confusion.png``      time-weighted 2×2 (truth × claim) in seconds and percent —
                                the divergence ledger in one picture;
  ③ ``fig3_latency_cdf.png``    silent-death detection-latency CDF: mechanism replay
                                (measured, stopwatch) vs heartbeat-staleness bound
                                (120 s + cadence) vs the platform's actual reaction.

Usage:
    python3 -m tools.worker_truth.figures --samples runs/in23_n08/samples.jsonl \
        --replay runs/in23_n08/replay --outdir runs/in23_n08/figures
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

from tools.worker_truth import ledger, states  # noqa: E402

STATE_COLORS = {"busy": "#1b7837", "suspect_hang": "#f0a202",
                "silent_dead": "#b2182b", "idle": "#c9d6df"}
STATE_ORDER = ["busy", "suspect_hang", "silent_dead", "idle"]


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def fig_state_band(samples_path: str, outdir: str, top_n: int = 36) -> str:
    records = ledger.load_records(samples_path)
    groups = ledger.split_by_worker(records)
    derived = {wid: states.derive_series(recs) for wid, recs in groups.items()}
    # rank workers by claimed time so the interesting ones are visible
    ranked = sorted(derived, key=lambda w: -sum(
        1 for r in derived[w] if r.get("claim_active")))
    ranked = ranked[:top_n]
    all_ts = sorted({float(r["ts_epoch"]) for rs in derived.values() for r in rs})
    col = {t: i for i, t in enumerate(all_ts)}

    grid = np.full((len(ranked), len(all_ts)), np.nan)
    for y, wid in enumerate(ranked):
        for r in derived[wid]:
            grid[y, col[float(r["ts_epoch"])]] = STATE_ORDER.index(str(r["state"]))

    fig, axes = plt.subplots(2, 1, figsize=(13.2, 8.6),
                             gridspec_kw={"height_ratios": [2.5, 1.0], "hspace": 0.32})
    ax = axes[0]
    cmap = ListedColormap([STATE_COLORS[s] for s in STATE_ORDER])
    xs = np.array([mdates.date2num(_dt(t)) for t in all_ts])
    ax.pcolormesh(xs, np.arange(len(ranked)), np.ma.masked_invalid(grid),
                  cmap=cmap, vmin=-0.5, vmax=3.5, shading="nearest")
    ax.set_yticks(np.arange(len(ranked)) + 0.5)
    ax.set_yticklabels(ranked, fontsize=5.5)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    ax.set_xlim(xs.min(), xs.max())
    ax.set_title("Fig 1a  four-state band from external truth (/proc trees + cgroup metering)"
                 "  —  {len(ranked)} workers x {len(all_ts)} samples "
                 f"({_dt(all_ts[0]):%H:%M}–{_dt(all_ts[-1]):%H:%M} UTC)", fontsize=10)
    ax.set_ylabel("worker (ranked by claimed time)", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=STATE_COLORS[s]) for s in STATE_ORDER]
    ax.legend(handles, STATE_ORDER, ncol=4, fontsize=7, loc="lower left",
              framealpha=0.9, bbox_to_anchor=(0.0, 1.02))

    # companion panel: fleet time budget per state + claim-window "busy"
    fl = ledger.build_ledger(records)
    st = fl["state_seconds"]
    total = sum(st.values()) or 1.0
    names = STATE_ORDER + ["claim_busy"]
    claim_s = sum(float(v["claim_busy_seconds"]) for v in fl["per_worker"].values())
    vals = [float(st.get(s, 0.0)) for s in STATE_ORDER] + [claim_s]
    colors = [STATE_COLORS[s] for s in STATE_ORDER] + ["#4c72b0"]
    bars = axes[1].barh(range(len(names)), vals, color=colors, height=0.62)
    axes[1].set_yticks(range(len(names)))
    axes[1].set_yticklabels(names, fontsize=7.5)
    axes[1].invert_yaxis()
    axes[1].bar_label(bars, labels=[f"{v:,.0f} s ({v / total:.1%})" for v in vals],
                      fontsize=7, padding=2)
    axes[1].set_xlabel("worker-seconds (time-weighted over sample intervals, gap weight capped at 3x median)", fontsize=8)
    axes[1].set_title("Fig 1b  time budget: external truth vs claim-window "
                      f"(agreement {fl['time_weighted_agreement']:.2%})", fontsize=9)
    axes[1].set_xlim(0, max(vals) * 1.35)
    axes[1].grid(axis="x", alpha=0.25)

    path = os.path.join(outdir, "fig1_state_band.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_confusion(ledger_json: Dict[str, object], outdir: str) -> str:
    conf = ledger_json["confusion_seconds"]
    m = np.array([[conf["tp"], conf["fp"]], [conf["fn"], conf["tn"]]], dtype=float)
    total = m.sum() or 1.0
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    im = ax.imshow(m, cmap="YlOrRd", vmin=0)
    labels = [["TP both busy", "FP claim-only busy"], ["FN truth-only busy", "TN both not busy"]]
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{m[i, j]:,.0f} s\n{m[i, j] / total:.1%}",
                    ha="center", va="center", fontsize=11,
                    color="black" if m[i, j] < m.max() * 0.6 else "white")
    ax.set_xticks([0, 1], ["claim: busy", "claim: not busy"])
    ax.set_yticks([0, 1], ["truth: busy", "truth: not busy"])
    ax.set_xlabel("platform: claim held AND heartbeat age <= 300 s", fontsize=9)
    ax.set_ylabel("external truth: process tree AND 60 s CPU delta", fontsize=9)
    agree = (m[0, 0] + m[1, 1]) / total
    ax.set_title("claim-window vs external truth - time-weighted confusion matrix\n"
                 f"agreement {agree:.2%} over {ledger_json['observed_worker_hours']:.2f} worker-hours",
                 fontsize=10)
    fig.colorbar(im, ax=ax, label="worker-seconds", shrink=0.85)
    for txt, xy in zip(labels[0] + labels[1], [(0, 0), (1, 0), (0, 1), (1, 1)]):
        ax.annotate(txt, xy, xytext=(0, -30), textcoords="offset points",
                    ha="center", fontsize=7, color="#333")
    path = os.path.join(outdir, "fig2_confusion.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_latency_cdf(replay_dir: str, outdir: str) -> str:
    mech_path = os.path.join(replay_dir, "replay_mechanism.json")
    hist_path = os.path.join(replay_dir, "replay_history.json")
    mech = json.load(open(mech_path, encoding="utf-8")) if os.path.exists(mech_path) else {}
    hist = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {}

    lat = sorted(float(x) for x in mech.get("latency_s", {}).get("values", []) or [])
    fig, ax = plt.subplots(figsize=(7.6, 5.2))
    if lat:
        xs = [0.0] + lat
        ys = [0.0] + [(i + 1) / len(lat) for i in range(len(lat))]
        ax.step(xs, ys, where="post", color="#b2182b", lw=2,
                label=f"mechanism replay, measured ({len(lat)} SIGKILL cases, end-to-end)")
        ax.scatter(lat, ys[1:], color="#b2182b", zorder=5, s=22)
        for x, y in zip(lat, ys[1:]):
            ax.annotate(f"{x:.0f}s", (x, y), textcoords="offset points", xytext=(4, -10),
                        fontsize=7, color="#b2182b")
    hb = float(hist.get("detection_latency_s", {}).get("by_construction", 120.0) or 120.0)
    bound = float(hist.get("detection_latency_s", {}).get("bound_with_cadence_s", hb + 10) or hb)
    ax.axvline(hb, color="#4c72b0", ls="--", lw=1.4,
               label=f"heartbeat-staleness branch: earliest possible detection at {hb:.0f}s")
    ax.axvline(bound, color="#4c72b0", ls=":", lw=1.2, label=f"+ sampling quantum: {bound:.0f}s upper bound")
    ax.axvspan(0, 120, color="#1b7837", alpha=0.07)
    ax.axvline(120, color="#1b7837", lw=1.0)
    ax.text(2, 0.06, "acceptance line 120 s", color="#1b7837", fontsize=8)
    ax.set_xlim(0, max(bound * 1.25, (max(lat) * 1.3 if lat else 60)))
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("silent-death detection latency since SIGKILL [s]", fontsize=9)
    ax.set_ylabel("cumulative share (CDF)", fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5, loc="lower right")
    opp = hist.get("platform_opportunity_window_s", {}).get("values") or []
    opp_txt = (f"2026-11-02 batch: claim stayed held {min(opp) / 3600:.1f}-{max(opp) / 3600:.1f} h "
               f"after the earliest possible detection" if opp else "")
    ax.set_title("Fig 3  silent-death detection latency CDF: process-absence vs heartbeat staleness\n" + opp_txt,
                 fontsize=10)
    path = os.path.join(outdir, "fig3_latency_cdf.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="runs/in23_n08/samples_final.jsonl.gz")
    ap.add_argument("--replay", default="runs/in23_n08/replay")
    ap.add_argument("--outdir", default="runs/in23_n08/figures")
    ap.add_argument("--top-n", type=int, default=36)
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)
    records = ledger.load_records(args.samples)
    fl = ledger.build_ledger(records)
    paths = [fig_state_band(args.samples, args.outdir, args.top_n),
             fig_confusion(fl, args.outdir),
             fig_latency_cdf(args.replay, args.outdir)]
    print(json.dumps({"figures": paths, "agreement": fl["time_weighted_agreement"],
                      "worker_hours": fl["observed_worker_hours"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())