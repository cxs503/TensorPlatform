"""出图：门链时序 / 红队矩阵 / psql 超时上限实验。数据全部读 MISSION_EVIDENCE.json（零手抄）。

用法：python3 -m harness_gateway.experiments.make_figures --artifacts artifacts/harness_gateway
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

COLOR = {"PASS": "#2e7d32", "FAIL": "#c62828", "SKIP": "#9e9e9e"}


def fig_gate_chain(ev: dict, out: Path) -> Path:
    runs = list(ev["runs"].items())
    order = ["preflight_mirror_verify", "git_rollback_drill", "checkpoint", "apply_patch",
             "py_compile", "canary_single_process", "commit_mirror", "git_rollback"]
    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    for row, (run_id, run) in enumerate(runs):
        y = len(runs) - row
        left = 0.0
        for stage in run["stages"]:
            w = max(stage["duration_ms"], 3) / 1000.0
            ax.barh(y, w, left=left, height=0.62,
                    color=COLOR.get(stage["status"], "#607d8b"),
                    edgecolor="white", linewidth=0.8)
            left += w
        verdict = run["verdict"]
        ax.text(left + 0.03, y, verdict + (f"  caught_by={run['caught_by']}" if run["caught_by"] else ""),
                va="center", fontsize=8, color=COLOR["PASS"] if verdict == "PASS" else COLOR["FAIL"])
    ax.set_yticks(range(1, len(runs) + 1))
    ax.set_yticklabels([rid.replace("_", " ") for rid, _ in runs][::-1], fontsize=7.5)
    ax.set_xlabel("cumulative wall time inside the gate chain (s)")
    ax.set_title("Gate chain per run: stage durations and verdicts (8 gated units)", fontsize=10)
    ax.set_xlim(0, max(1.1, ax.get_xlim()[1] * 1.35))
    ax.grid(axis="x", alpha=0.25)
    ax.legend(handles=[Patch(color=COLOR[k], label=k) for k in ("PASS", "FAIL", "SKIP")],
              loc="lower right", fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(out, dpi=104)
    plt.close(fig)
    return out


def fig_redteam(ev: dict, out: Path) -> Path:
    ncs = [("0004_redteam_NC1", "inject syntax error"), ("0004_redteam_NC2", "inject runtime crash"),
           ("0004_redteam_NC3", "re-inject plaintext credential")]
    gates = ["py_compile", "canary_single_process", "git_rollback"]
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    for r, (run_id, label) in enumerate(ncs):
        run = ev["runs"].get(run_id)
        caught = run["caught_by"] if run else None
        for c, gate in enumerate(gates):
            hit = caught == gate
            ax.add_patch(plt.Rectangle((c, r), 1, 1,
                                       facecolor="#c62828" if hit else "#f5f5f5",
                                       edgecolor="white", linewidth=2))
            if hit:
                ax.text(c + 0.5, r + 0.5, "CAUGHT", ha="center", va="center", color="white",
                        fontsize=8.5, weight="bold")
        matched = run["rollback_files_matched"] if run else 0
        total = run["rollback_files_total"] if run else 0
        verdict = run["verdict"] if run else "?"
        ax.text(len(gates) + 0.15, r + 0.5,
                f"{verdict}  rollback {matched}/{total} files byte-identical", va="center", fontsize=7.5)
    ax.set_xlim(0, len(gates) + 3.6)
    ax.set_ylim(0, len(ncs))
    ax.set_xticks([c + 0.5 for c in range(len(gates))])
    ax.set_xticklabels(["gate1 py_compile", "gate2 canary", "gate3 git rollback"], fontsize=8)
    ax.set_yticks([r + 0.5 for r in range(len(ncs))])
    ax.set_yticklabels([label for _, label in ncs], fontsize=8)
    ax.set_title("Red-team negative controls: which gate stopped the injected fault", fontsize=10)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(out, dpi=104)
    plt.close(fig)
    return out


def fig_timeout(ev: dict, out: Path) -> Path:
    t = ev["timeout_experiment"]
    base = t["baseline_no_timeout"]["wall_s"]
    patched = t["patched_with_timeout"]["wall_s"]
    sleep = t["shim_sleep_s"]
    cap = float(t["timeout_cap_s"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.0, 3.4), gridspec_kw={"width_ratios": [1, 1.25]})

    bars = a1.bar(["baseline\n(no timeout)", "patched\n(cap = %.0fs)" % cap], [base, patched],
                  color=["#c62828", "#2e7d32"], width=0.55)
    a1.axhline(3 * sleep, ls="--", lw=1, color="#424242")
    a1.axhline(3 * cap, ls=":", lw=1, color="#2e7d32")
    a1.text(1.42, 3 * sleep, "model: 3 calls x %.0fs" % sleep, fontsize=7, color="#424242", va="bottom", ha="right")
    a1.text(1.42, 3 * cap, "model: 3 calls x %.0fs" % cap, fontsize=7, color="#2e7d32", va="bottom", ha="right")
    for b, v in zip(bars, [base, patched]):
        a1.text(b.get_x() + b.get_width() / 2, v + 0.15, "%.2fs" % v, ha="center", fontsize=8)
    a1.set_ylabel("process wall time (s)")
    a1.set_title("psql wait is now bounded per call", fontsize=9)
    a1.set_ylim(0, 3 * sleep * 1.25)
    a1.grid(axis="y", alpha=0.25)

    modes = ev["canary_units"]
    names = list(modes)
    ys = range(len(names))
    a2.barh(list(ys), [1 if modes[n]["mode"] == "exec_dry_run" else 0.5 for n in names],
            color=["#1565c0" if modes[n]["mode"] == "exec_dry_run" else "#90a4ae" for n in names], height=0.6)
    a2.set_yticks(list(ys))
    a2.set_yticklabels([f"{n} ({modes[n]['mode']})" for n in names], fontsize=7)
    a2.set_xticks([0, 0.5, 1])
    a2.set_xticklabels(["symbol probe", "", "single-process dry run"], fontsize=7)
    a2.set_xlim(0, 1.25)
    a2.set_title("Canary coverage per harness unit (honest degradation)", fontsize=9)
    for s in ("top", "right"):
        a2.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=104)
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts/harness_gateway")
    args = ap.parse_args()
    art = Path(args.artifacts)
    ev = json.loads((art / "MISSION_EVIDENCE.json").read_text(encoding="utf-8"))
    figs = art / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    made = [fig_gate_chain(ev, figs / "fig1_gate_chain.png"),
            fig_redteam(ev, figs / "fig2_redteam_matrix.png"),
            fig_timeout(ev, figs / "fig3_timeout_and_canary.png")]
    for p in made:
        print(p, p.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())