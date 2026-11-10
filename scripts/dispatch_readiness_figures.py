"""Figures for the P233W2-N08 interface-readiness report.

Reads ``artifacts/dispatch_readiness/backtest_report.json`` (programmatic, no
hand-typed numbers) and writes PNGs into ``artifacts/dispatch_readiness/figures``.
"""

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "dispatch_readiness")
FIG = os.path.join(ART, "figures")


def _load():
    with open(os.path.join(ART, "backtest_report.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def fig_roc(body):
    fig, ax = plt.subplots(figsize=(6.0, 5.4))
    styles = {
        "delivery_gap_consult": ("primary @consult (all labelled nodes)", "-", "#1f77b4"),
        "delivery_gap_stop": ("@stop (end of work)", "--", "#2ca02c"),
        "delivery_gap_dispatch": ("@dispatch (dispatched nodes only)", ":", "#d62728"),
    }
    for key, (label, ls, color) in styles.items():
        target = body["targets"][key]
        xs = [p["fpr"] for p in target["roc"]]
        ys = [p["tpr"] for p in target["roc"]]
        ax.plot(xs, ys, ls, color=color, label=f"{label}  AUC={target['auc']:.3f}", linewidth=1.8)
    ax.plot([0, 1], [0, 1], color="grey", linewidth=0.8, linestyle="-", alpha=0.6, label="chance")
    ax.axvline(0.05, color="black", linewidth=0.8, linestyle="-.", alpha=0.7)
    ax.text(0.055, 0.05, "FPR budget 5%", fontsize=7, rotation=90, va="bottom")
    ax.set_xlabel("false positive rate (healthy / done nodes deferred)")
    ax.set_ylabel("true positive rate (upstream-gap blocks caught)")
    ax.set_title("Readiness gate ROC — reconstructed historical dispatch moments")
    ax.set_xlim(-0.01, 1.0)
    ax.set_ylim(-0.01, 1.02)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIG, "roc_curves.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_confusion(body):
    target = body["targets"]["delivery_gap_consult"]
    cm = target["confusion_matrix"]
    matrix = [[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]]
    fig, ax = plt.subplots(figsize=(4.6, 4.0))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks([0, 1], ["gate: dispatch", "gate: defer"])
    ax.set_yticks([0, 1], ["actually done", "actually blocked\n(upstream gap)"])
    for i in range(2):
        for j in range(2):
            value = matrix[i][j]
            ax.text(j, i, f"{value}", ha="center", va="center", fontsize=13,
                    color="white" if value > max(max(r) for r in matrix) * 0.5 else "black")
    ax.set_title(
        f"Confusion matrix @consult\nrecall={target['recall']:.2f}  FPR={target['fpr']:.3%}", fontsize=10
    )
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    out = os.path.join(FIG, "confusion_matrix.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_mode_sensitivity(body):
    modes = body["mode_sensitivity"]
    names = list(modes.keys())
    fpr = [modes[n]["fpr"] for n in names]
    coverage = [modes[n]["coverage"] if modes[n]["coverage"] == modes[n]["coverage"] else 0.0 for n in names]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    x = range(len(names))
    width = 0.36
    ax.bar([i - width / 2 for i in x], fpr, width, label="FPR on done nodes", color="#d62728")
    ax.bar([i + width / 2 for i in x], coverage, width, label="coverage of upstream-gap blocks", color="#1f77b4")
    ax.axhline(0.05, color="black", linestyle="-.", linewidth=0.9)
    ax.text(len(names) - 0.5, 0.055, "FPR budget 5%", fontsize=7, ha="right")
    ax.set_xticks(list(x), names)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.set_title("Requirement-mode policy frontier (contract-parallel vs strict)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIG, "mode_sensitivity.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_permutation(body):
    null = body["permutation_null_primary"]
    draws = body.get("permutation_null_primary_draws") or []
    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    ax.hist(draws, bins=24, color="#9ecae1", edgecolor="white", label="shuffled labels (200 draws)")
    low, high = null["interval_95"]
    ax.axvline(low, color="black", linestyle="--", linewidth=1.0, label=f"95% interval [{low:.2f}, {high:.2f}]")
    ax.axvline(high, color="black", linestyle="--", linewidth=1.0)
    ax.axvline(0.5, color="grey", linestyle=":", linewidth=1.2, label="chance (0.5)")
    ax.axvline(null["observed"], color="#d62728", linewidth=2.0, label=f"observed AUC={null['observed']:.3f}")
    ax.set_xlabel("AUC with permuted labels")
    ax.set_ylabel("count")
    ax.set_title("AC3 negative control: label permutation null (n=200)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIG, "permutation_null.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_strata(body):
    strata = body["targets"]["delivery_gap_consult"]["strata"]
    out_of_scope = body["out_of_scope"]["by_stratum"]
    merged = dict(strata)
    for key, value in out_of_scope.items():
        merged[key] = merged.get(key, 0) + value
    items = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k.replace("|", "\n") for k, _ in items]
    values = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    bars = ax.barh(labels, values, color="#4c72b0")
    ax.set_xscale("log")
    for bar, value in zip(bars, values):
        ax.text(value * 1.05, bar.get_y() + bar.get_height() / 2, str(value), va="center", fontsize=8)
    ax.set_xlabel("nodes (log scale)")
    ax.set_title("Label strata of the historical terminal-state universe", fontsize=10)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIG, "label_strata.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main():
    os.makedirs(FIG, exist_ok=True)
    body = _load()
    paths = [fig_roc(body), fig_confusion(body), fig_mode_sensitivity(body), fig_permutation(body), fig_strata(body)]
    for path in paths:
        print(os.path.relpath(path, ROOT), os.path.getsize(path))


if __name__ == "__main__":
    sys.exit(main())