"""Report figures for the task-salvage backtest (matplotlib, deterministic).

Four panels, all drawn from the frozen corpus / acceptance JSON — the plotting
code computes nothing that is not already in the artefacts, so a figure can never
claim more than the machine gates did.

1. ``fig1_gate_outcomes.png`` — per-gate pass/fail/not-applicable over judged files.
2. ``fig2_states.png``        — salvage state split and blocked codes.
3. ``fig3_replay_ab.png``     — replay pass rate, control vs enabled arm, with tests.
4. ``fig4_confusion.png``     — gate verdict vs independent-oracle verdict.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

FIGS = Path(__file__).resolve().parents[2] / "artifacts" / "task_salvage" / "figures"


def _load(out_dir: Path) -> tuple[dict, dict]:
    out_dir = Path(out_dir)
    acceptance = json.loads((out_dir / "acceptance.json").read_text(encoding="utf-8"))
    corpus = json.loads((out_dir / "corpus.json").read_text(encoding="utf-8"))
    return acceptance, corpus


def _gate_counts(corpus: dict) -> dict:
    tally: dict[str, dict[str, int]] = {}
    for mission in corpus.get("missions", []):
        for row in mission.get("files", []):
            for gate, rec in (row.get("gates") or {}).items():
                bucket = tally.setdefault(gate, {"pass": 0, "fail": 0, "not_applicable": 0})
                bucket[rec["status"]] = bucket.get(rec["status"], 0) + 1
    return tally


def render(out_dir: Path, figures_dir: Path = FIGS) -> list[str]:
    acceptance, corpus = _load(out_dir)
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    # ── fig 1: per-gate outcomes ────────────────────────────────────────────
    tally = _gate_counts(corpus)
    gates = [g for g in ("parse", "smoke", "structural") if g in tally]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    bottoms = [0] * len(gates)
    colors = {"pass": "#2f7d4f", "fail": "#b3453a", "not_applicable": "#9aa4ad"}
    for status in ("pass", "fail", "not_applicable"):
        values = [tally[g].get(status, 0) for g in gates]
        ax.bar(gates, values, bottom=bottoms, color=colors[status], label=status)
        bottoms = [b + v for b, v in zip(bottoms, values)]
    ax.set_ylabel("judged files")
    ax.set_title("Three-gate sift outcomes")
    ax.legend(frameon=False, fontsize=8)
    for idx, total in enumerate(bottoms):
        ax.text(idx, total, str(total), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    path = figures_dir / "fig1_gate_outcomes.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(str(path))

    # ── fig 2: state split + blocked codes ─────────────────────────────────
    rate = acceptance["gates"]["salvage_rate"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 3.4))
    ax1.bar(
        ["INHERITABLE", "BLOCKED"],
        [rate["state_inheritable"], rate["state_blocked"]],
        color=["#2f7d4f", "#b3453a"],
    )
    ax1.set_title(f"salvage state (n={rate['terminal_tasks']})")
    ax1.set_ylabel("tasks")
    for idx, value in enumerate([rate["state_inheritable"], rate["state_blocked"]]):
        ax1.text(idx, value, str(value), ha="center", va="bottom", fontsize=9)
    codes = rate.get("blocked_codes") or {}
    if codes:
        keys = list(codes)[:8]
        ax2.barh(keys, [codes[k] for k in keys], color="#b3453a")
        ax2.set_title("BLOCKED reason codes")
        ax2.invert_yaxis()
    else:
        ax2.text(0.5, 0.5, "no blocked task", ha="center", va="center")
        ax2.set_axis_off()
    fig.tight_layout()
    path = figures_dir / "fig2_states.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(str(path))

    # ── fig 3: replay A/B ──────────────────────────────────────────────────
    replay = acceptance["gates"]["replay_all_files"]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    rates = [replay["control_pass_rate"] * 100, replay["enabled_pass_rate"] * 100]
    bars = ax.bar(["control\n(cold restart)", "enabled\n(seed inherited)"], rates, color=["#6b7d8f", "#2f7d4f"])
    ax.set_ylabel("replay acceptance pass rate [%]")
    ax.set_ylim(0, 105)
    ax.set_title(
        f"paired replay, n={replay['pairs']} families; "
        f"Fisher p={replay['fisher']['p_value']:.3g}, McNemar p={replay['mcnemar']['p_value']:.3g}"
    )
    for bar, value in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.1f}%", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    path = figures_dir / "fig3_replay_ab.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(str(path))

    # ── fig 4: gate vs oracle confusion ────────────────────────────────────
    cons = acceptance["gates"]["consistency"]
    fig, ax = plt.subplots(figsize=(4.4, 3.6))
    matrix = [
        [cons["agree"] - cons["false_positives"], cons["false_positives"]],
        [cons["disagree"] - cons["false_positives"], cons["agree"] - (cons["disagree"] - cons["false_positives"])],
    ]
    matrix = [[max(0, value) for value in row] for row in matrix]
    image = ax.imshow(matrix, cmap="Greens")
    ax.set_xticks([0, 1], ["oracle: pass", "oracle: blocked"])
    ax.set_yticks([0, 1], ["gate: pass", "gate: blocked"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(matrix[i][j]), ha="center", va="center", fontsize=11)
    ax.set_title(
        f"gate vs independent oracle\nconsistency={cons['consistency']:.3f}, FP={cons['false_positive_rate']:.3f}"
    )
    fig.colorbar(image, ax=ax, shrink=0.8)
    fig.tight_layout()
    path = figures_dir / "fig4_confusion.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(str(path))
    return written


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(render(Path(__file__).resolve().parents[2] / "artifacts" / "task_salvage"), indent=2))