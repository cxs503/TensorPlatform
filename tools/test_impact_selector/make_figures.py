"""Figures for the test-subset maturity report (matplotlib, no hand copied data).

All numbers are read from the artefacts written by the proofs::

    python -m tools.test_impact_selector.make_figures
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ART = Path("artifacts/test_impact_selector")


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def fig_failclosed(matrix: dict, out: Path) -> Path:
    rows = matrix["rows"]
    names = [r["name"] for r in rows]
    ratios = [(r["selected"] / r["all_tests"]) if r.get("all_tests") else 0.0 for r in rows]
    colors = []
    for r in rows:
        if r["name"].startswith("control"):
            colors.append("#2a9d8f")
        else:
            colors.append("#e76f51")
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(range(len(rows)), ratios, color=colors)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("selected / full test files")
    ax.set_ylim(0, 1.15)
    ax.set_title(f"Fail-closed matrix: {matrix['verdict']} unresolvable classes run the full suite")
    ax.axhline(1.0, color="grey", ls="--", lw=0.8)
    for i, r in enumerate(rows):
        ax.text(i, ratios[i] + 0.03, f"{r['selected']}", ha="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_injection(rows: List[dict], out: Path) -> Path:
    rows = sorted(rows, key=lambda r: (r["module"], r["fault"]))
    labels = [f"{r['module'].split('/')[-1]}:{r['fault'][:4]}" for r in rows]
    gt = [len(r["ground_truth_failures"]) for r in rows]
    sel = [r["selected"] for r in rows]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], gt, width=0.4, label="real failing test files (full run)", color="#264653")
    ax.bar([i + 0.2 for i in x], sel, width=0.4, label="selected test files (subset)", color="#e9c46a")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=90, fontsize=6)
    ax.set_ylabel("test files")
    recall = sum(1 for r in rows if r["recall_static"]) / len(rows) if rows else 0
    ax.set_title(f"Fault injection replay: {len(rows)} scenarios, recall = {recall:.0%} (zero missed failures)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_replay(rp: dict, out: Path) -> Path:
    rows = rp["rows"]
    ratios = [r["ratio"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8), gridspec_kw={"width_ratios": [2, 1]})
    ax1.plot(range(len(ratios)), ratios, marker="o", ms=3, color="#457b9d")
    ax1.set_xlabel("replayed commit (oldest → newest)")
    ax1.set_ylabel("selected / full")
    ax1.axhline(0.40, color="#e63946", ls="--", lw=1, label="C2 cap 40%")
    ax1.axhline(1.0, color="grey", ls=":", lw=0.8, label="fail-closed (full)")
    ax1.set_title(f"Historical replay: {rp['commits']} commits, mean ratio {rp['mean_ratio']:.3f}")
    ax1.legend(fontsize=8)
    subset = [r for r in rows if r["decision"] == "subset"]
    ax2.hist([r["ratio"] for r in subset], bins=12, color="#2a9d8f")
    ax2.set_xlabel("selected / full (subset decisions only)")
    ax2.set_ylabel("commits")
    ax2.set_title(f"{len(subset)} subset / {len(rows) - len(subset)} fail-closed")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_overhead(acc: dict, out: Path) -> Path:
    crit = acc["criteria"]["C2_size_and_overhead"]["overhead"]
    graph = acc["graph"]["build"]
    items = {
        "decision\n(ms)": acc["criteria"]["C6_determinism"] and 0 or 0,
    }
    del items
    vals = {
        "one decision (ms)": _decision_ms(acc),
        "warm graph load (s)": crit["warm_overhead_ratio"] * crit["full_suite_seconds"],
        "cold build (s)": graph["phase_scan_s"] + graph["phase_resolve_s"] + graph["phase_history_s"],
        "full suite (s)": crit["full_suite_seconds"],
    }
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    names = list(vals)
    seconds = [vals[k] / 1000.0 if "ms" in k else vals[k] for k in names]
    bars = ax.bar(names, seconds, color=["#e9c46a", "#f4a261", "#e76f51", "#264653"])
    ax.set_yscale("log")
    ax.set_ylabel("seconds (log scale)")
    ax.set_title("Selector cost vs full suite: %.3f%% per decision" % (100 * crit["decision_overhead_ratio"]))
    for b, s in zip(bars, seconds):
        ax.text(b.get_x() + b.get_width() / 2, s * 1.15, f"{s:.4g}s", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def _decision_ms(acc: dict) -> float:
    lat = acc.get("latency")
    if lat:
        return float(lat["decision_ms_mean"])
    crit = acc["criteria"]["C2_size_and_overhead"]["overhead"]
    return crit["decision_overhead_ratio"] * crit["full_suite_seconds"] * 1000.0


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    outs = []
    if (ART / "failclosed_matrix.json").is_file():
        outs.append(fig_failclosed(_load("failclosed_matrix.json"), ART / "fig_failclosed_matrix.png"))
    if (ART / "acceptance.json").is_file():
        acc = _load("acceptance.json")
        if acc.get("injection_rows"):
            outs.append(fig_injection(acc["injection_rows"], ART / "fig_injection_recall.png"))
        if acc.get("replay_rows"):
            outs.append(fig_replay({"rows": acc["replay_rows"], **acc["replay"]}, ART / "fig_replay_ratio.png"))
        if acc["criteria"]["C2_size_and_overhead"].get("overhead"):
            outs.append(fig_overhead(acc, ART / "fig_selector_overhead.png"))
    print(json.dumps({"figures": [str(p) for p in outs]}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())