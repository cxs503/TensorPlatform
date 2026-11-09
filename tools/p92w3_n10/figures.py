"""Figures for the P92W3-N10 report (matplotlib, no hand-carried numbers).

All values are read from the frozen JSON artifacts in runs/p92w3-n10/.
Run: python3 tools/p92w3_n10/figures.py
"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p92w3-n10"
FIG = RUN / "figures"


def load(name: str) -> dict:
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def fig_layers(metrics: dict) -> pathlib.Path:
    dist = metrics["state_distribution"]
    n = metrics["corpus"]["n_succeeded"]
    states = ["delivered_complete", "delivered_partial", "suspect_undeclared"]
    labels = ["complete", "partial", "suspect/undeclared"]
    art = [dist["artifact_only"][s] / n * 100 for s in states]
    att = [dist["attested"][s] / n * 100 for s in states]

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = range(len(states))
    w = 0.38
    b1 = ax.bar([i - w / 2 for i in x], art, w, label="layer 1: artifacts only", color="#4C78A8")
    b2 = ax.bar([i + w / 2 for i in x], att, w, label="layer 2: artifacts x closure", color="#F58518")
    for bars in (b1, b2):
        for r in bars:
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 0.6,
                    f"{r.get_height():.1f}%", ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("share of terminal `succeeded` missions (%)")
    ax.set_title(f"Splitting `succeeded`: artifact audit vs attested layering (n={n})")
    ax.set_ylim(0, 95)
    ax.legend(frameon=False, fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    out = FIG / "fig1_state_split.png"
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_pr(metrics: dict) -> pathlib.Path:
    rules = metrics["rules"]
    names = ["R1_artifacts_only_not_complete", "R2_attested_not_complete",
             "R3_marker_replay_tautology"]
    pretty = ["R1 artifacts only", "R2 attested (artifacts x closure)", "R3 marker replay (tautology)"]
    rec = [rules[n]["recall"] for n in names]
    prec = [rules[n]["precision"] for n in names]
    rec_ci = [rules[n]["recall_ci"] for n in names]
    prec_ci = [rules[n]["precision_ci"] for n in names]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    x = range(len(names))
    w = 0.35
    for offset, vals, cis, label, color in (
        (-w / 2, rec, rec_ci, "recall (target >= 0.85)", "#4C78A8"),
        (w / 2, prec, prec_ci, "precision (target >= 0.90)", "#E45756"),
    ):
        xs = [i + offset for i in x]
        err = [[v - ci[0] for v, ci in zip(vals, cis)], [ci[1] - v for v, ci in zip(vals, cis)]]
        ax.bar(xs, vals, w, yerr=err, capsize=4, label=label, color=color)
        for xi, v in zip(xs, vals):
            ax.text(xi, v + 0.04, f"{v:.2f}", ha="center", fontsize=8)
    ax.axhline(0.85, ls="--", lw=1.0, color="#4C78A8", alpha=0.7)
    ax.axhline(0.90, ls="--", lw=1.0, color="#E45756", alpha=0.7)
    ax.set_xticks(list(x))
    ax.set_xticklabels(pretty, fontsize=8.5)
    ax.set_ylabel("score against the forced_finish weak label")
    ax.set_ylim(0, 1.15)
    ax.set_title("Pre-registered thresholds vs measured operate points\n"
                 f"(n={metrics['corpus']['n_succeeded']}, forced={metrics['corpus']['n_forced_finish']})",
                 fontsize=10)
    ax.legend(frameon=False, fontsize=8.5)
    ax.grid(axis="y", alpha=0.3)
    out = FIG / "fig2_pr_thresholds.png"
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_label_quality(adj: dict) -> pathlib.Path:
    q = adj["label_quality"]
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    bars = ax.bar(["marked forced_finish\n-> truly under-delivered",
                   "marked clean\n-> truly not complete"],
                  [q["forced_label_ppv_under_delivery"], q["non_forced_not_complete_rate"]],
                  color=["#F58518", "#54A24B"], width=0.5)
    for bar, ci, k, n in (
        (bars[0], q["forced_label_ppv_ci"], q["forced_under_delivered"], q["forced_stratum_n"]),
        (bars[1], q["non_forced_not_complete_ci"], q["non_forced_not_complete"], q["non_forced_stratum_n"]),
    ):
        ax.errorbar(bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    yerr=[[bar.get_height() - ci[0]], [ci[1] - bar.get_height()]],
                    fmt="none", ecolor="black", capsize=5)
        ax.text(bar.get_x() + bar.get_width() / 2, ci[1] + 0.03,
                f"{bar.get_height():.2f}\n({k}/{n})", ha="center", fontsize=9)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("adjudicated rate (95% Wilson CI)")
    ax.set_title("The weak label is one-sided: marker PPV vs unmarked defects", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    out = FIG / "fig3_label_quality.png"
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    metrics = load("metrics.json")
    adj = load("adjudication_metrics.json")
    paths = [fig_layers(metrics), fig_pr(metrics), fig_label_quality(adj)]
    print(json.dumps([str(p.relative_to(ROOT)) for p in paths], ensure_ascii=False))


if __name__ == "__main__":
    main()