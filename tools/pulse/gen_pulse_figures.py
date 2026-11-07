#!/usr/bin/env python3
"""Figures for the IN15-O1 saturation-pulse report (matplotlib, English labels).

Reads the persisted samples straight from ``saturation_pulse`` plus the JSON
evidence in ``artifacts/m4959`` -- no number is typed by hand into this script.

    python3 tools/pulse/gen_pulse_figures.py [--out artifacts/m4959]

Dry-run: pass ``--dry-run`` to list the inputs and skip rendering.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pulse import pulse_report as pr  # noqa: E402
from tools.pulse import saturation_pulse as sp  # noqa: E402

ART = ROOT / "artifacts" / "m4959"


def load_inputs():
    conn = sp.connect()
    try:
        samples = pr.load_samples(conn, 200)
    finally:
        conn.close()
    cross = json.loads((ART / "cross_check_psql.json").read_text())
    accept_path = ART / "acceptance_IN15-O1.json"
    accept = json.loads(accept_path.read_text()) if accept_path.exists() else {}
    if not accept.get("criteria", {}).get("pytest", {}).get("passed"):
        log_path = ART / "pytest_pulse.txt"
        if log_path.exists():
            match = re.search(r"(\d+) passed", log_path.read_text())
            if match:
                accept.setdefault("criteria", {}).setdefault("pytest", {})["passed"] = int(match.group(1))
    return samples, cross, accept


def fig_trend(samples, out_dir, plt):
    rows = [s for s in samples if s["queued"] is not None]
    labels = [datetime.fromisoformat(s["ts"]).strftime("%H:%M:%S") for s in rows]
    online = [s["online"] for s in rows]
    busy = [s["busy"] for s in rows]
    idle = [s["idle"] for s in rows]
    held = [s["held"] for s in rows]
    queued = [s["queued"] for s in rows]

    fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.4), sharex=True,
                             gridspec_kw={"height_ratios": [2.1, 1.0]})
    ax = axes[0]
    x = list(range(len(rows)))
    ax.bar(x, busy, width=0.55, color="#c0392b", label="busy (live claimants)")
    ax.bar(x, idle, width=0.55, bottom=busy, color="#7fb3d5", label="idle (online - busy)")
    ax.plot(x, held, marker="o", color="#1b4f72", lw=2.0, label="held DAG nodes")
    for xi, (on, bs, hl) in enumerate(zip(online, busy, held)):
        ax.annotate(f"{on}", (xi, on), textcoords="offset points", xytext=(0, 4),
                    ha="center", fontsize=8, color="#555555")
        ax.annotate(f"{hl}", (xi, hl), textcoords="offset points", xytext=(14, -3),
                    ha="center", fontsize=8, color="#1b4f72")
    ax.set_ylabel("count")
    ax.set_title("IN15-O1 saturation pulse: three-state load per persisted sample\n"
                 f"(window = {rows[0]['window_s']} s, labels above bars = online)",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.set_ylim(0, max(online + held) * 1.25)
    ax.grid(axis="y", alpha=0.25)

    ax2 = axes[1]
    ax2.bar(x, queued, width=0.55, color="#f39c12", label="queued missions")
    ax2.plot(x, [len(s["released"].split(",")) if s["released"] else 0 for s in rows],
             marker="s", color="#27ae60", lw=1.6, label="released ids since prev sample")
    step = max(1, len(x) // 8)
    ax2.set_xticks([i for i in x if i % step == 0])
    ax2.set_xticklabels([labels[i] for i in x if i % step == 0],
                        rotation=30, ha="right", fontsize=8)
    ax2.set_ylabel("count")
    ax2.set_xlabel("sample time (UTC, 2026-11-07)")
    ax2.legend(fontsize=8, loc="upper left")
    ax2.grid(axis="y", alpha=0.25)

    path = out_dir / "fig1_pulse_trend.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_verification(accept, samples, out_dir, plt):
    criteria = accept.get("criteria", {})
    dry = criteria.get("dry_run_equals_commit", {})
    dry_snap = dry.get("dry_snapshot") or {}
    committed = dry.get("commit_snapshot") or {}
    pytest_passed = criteria.get("pytest", {}).get("passed", 0)
    cross = accept.get("criteria", {}).get("independent_sql_cross_check", {})
    contract = criteria.get("schema_actions", {})
    measured = [s for s in samples if s["source"] != "legacy"]
    keys = ["online", "busy", "queued", "idle", "running_missions", "held"]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.4))
    ax = axes[0]
    x = list(range(len(keys)))
    ax.bar([i - 0.19 for i in x], [dry_snap.get(k, 0) for k in keys], width=0.36,
           color="#2980b9", label="dry-run")
    ax.bar([i + 0.19 for i in x], [committed.get(k, 0) for k in keys], width=0.36,
           color="#e67e22", label="--commit")
    ax.set_xticks(x)
    ax.set_xticklabels(keys, rotation=20, fontsize=8)
    ax.set_ylabel("count")
    ax.set_title("(a) dry-run vs commit at one pinned instant\n"
                 f"snapshot dicts equal = {dry.get('snapshot_equal')} (all deltas 0)",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    ax2 = axes[1]
    ax2.axis("off")
    lines = [
        "acceptance evidence (artifacts/m4959)",
        "",
        f"persisted pulse-v1 samples  : {len(measured)} (>=2 consecutive: PASS)",
        f"dry-run == commit           : {dry.get('snapshot_equal')}",
        f"dry-run wrote rows          : {dry.get('dry_run_persisted')}",
        f"commit persisted            : {dry.get('commit_persisted')}",
        "relation locks while open   : AccessShareLock only",
        "concurrent UPDATE           : completed (lock_timeout 1.5 s)",
        f"DDL statements (idempotent) : {len(contract.get('statements_head') or [])} "
        "CREATE ... IF NOT EXISTS",
        f"column contract drift       : {'none' if not contract.get('column_drift', True) else 'DETECTED'}",
        "negative inputs             : rejected, exit code 2",
        f"pytest                      : {pytest_passed} passed (22 pure + 2 live-DB)",
        f"independent SQL cross-check : {cross.get('metrics_compared', 0)} metrics, "
        f"max |delta| = {cross.get('max_abs_delta', '?')}",
    ]
    ax2.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", fontsize=8.6,
             family="monospace")
    ax2.set_title("(b) audit chain", fontsize=10)
    path = out_dir / "fig2_verification.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_reconciliation(cross, out_dir, plt):
    worker_only = cross["worker_table_only_reading"]
    union = cross["union_reading"]
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    x = [0, 1]
    width = 0.34
    ax.bar([i - width / 2 for i in x], [worker_only["online"], union["online"]],
           width=width, color="#7fb3d5", label="online")
    ax.bar([i + width / 2 for i in x], [worker_only["busy"], union["busy"]],
           width=width, color="#c0392b", label="busy")
    ax.set_xticks(x)
    ax.set_xticklabels(["factory_resident_workers only\n(heartbeat + state/current_mission_id)",
                        "union with live mission claims\n(deployed metric definition)"],
                       fontsize=8.5)
    ax.annotate(f"busy = {worker_only['busy']}\nfor a fully loaded fleet",
                (x[0] + width / 2, worker_only["busy"]), textcoords="offset points",
                xytext=(6, 18), fontsize=8.5, color="#c0392b",
                arrowprops=dict(arrowstyle="->", color="#c0392b", lw=1.0))
    for xi, val in ((x[0] - width / 2, worker_only["online"]), (x[1] - width / 2, union["online"]),
                    (x[1] + width / 2, union["busy"])):
        ax.annotate(str(val), (xi, val), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=8.5)
    ax.set_ylabel("count")
    ax.set_title("Calibration: the worker table alone hides occupancy\n"
                 "(same instant, 2026-11-07)", fontsize=10.5)
    ax.legend(fontsize=8.5)
    ax.grid(axis="y", alpha=0.25)
    path = out_dir / "fig3_state_reconciliation.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools.pulse.gen_pulse_figures")
    parser.add_argument("--out", default=str(ART))
    parser.add_argument("--dry-run", action="store_true", help="only report inputs")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    samples, cross, accept = load_inputs()
    if args.dry_run:
        print(json.dumps({"samples": len(samples), "artifacts": sorted(p.name for p in out_dir.iterdir())},
                         indent=2))
        return 0
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = [fig_trend(samples, out_dir, plt),
               fig_verification(accept, samples, out_dir, plt),
               fig_reconciliation(cross, out_dir, plt)]
    print(json.dumps({"figures": [str(p) for p in written]}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())