"""Generate the P93W3-N10 figures from the frozen metrics (no hand copy).

Outputs (PNG + caption + sha256 manifest):
  runs/p93w3-n10/fig1_terminal_layering.png   legacy binary vs four-state layering
  runs/p93w3-n10/fig2_mvd_latency.png         70% checkpoint trigger latency
  runs/p93w3-n10/fig3_budget_evidence.png     why wall arithmetic is not evidence
  runs/p93w3-n10/fig4_confusion.png           20-scenario confusion matrix
  runs/p93w3-n10/figures.json                 manifest (captions + digests)

Usage: python3 tools/p93w3_n10/figures.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p93w3-n10"

STATE_ORDER = ("succeeded", "forced_finish", "degraded", "failed")
COLORS = {"succeeded": "#2e7d32", "forced_finish": "#c62828",
          "degraded": "#ef6c00", "failed": "#37474f"}


def load(name: str) -> dict:
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def save(fig, name: str, caption: str, manifest: list[dict]) -> None:
    path = RUN / name
    fig.tight_layout()
    fig.savefig(path, dpi=160, metadata={"Software": "matplotlib"})
    plt.close(fig)
    manifest.append({
        "name": name,
        "path": str(path.relative_to(ROOT)),
        "caption": caption,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    print("wrote", path)


def fig_layering(metrics: dict, manifest: list[dict]) -> None:
    counts = metrics["distributions"]["counts"]
    migration = metrics["criteria"]["c_backward_compat_migration"]
    total = metrics["distributions"]["total"]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    legacy = {"succeeded": migration["legacy_succeeded_rows"],
              "failed": total - migration["legacy_succeeded_rows"]}
    axes[0].bar(["stored status"], [legacy["succeeded"]], color="#90a4ae", label="succeeded")
    if legacy["failed"]:
        axes[0].bar(["stored status"], [legacy["failed"]], bottom=[legacy["succeeded"]],
                    color="#37474f", label="non-success")
    axes[0].set_ylabel("missions")
    axes[0].set_title(f"legacy storage status (n={total})")
    axes[0].legend()

    bottom = 0
    for state in STATE_ORDER:
        value = counts[state]
        axes[1].bar(["semantic state"], [value], bottom=bottom, color=COLORS[state],
                    label=f"{state} ({value})")
        bottom += value
    axes[1].set_ylabel("missions")
    axes[1].set_title(
        f"four-state layering — delivery rate {migration['delivery_rate_after']:.1%} "
        f"(before {migration['binary_success_rate_before']:.1%})"
    )
    axes[1].legend(fontsize=8, loc="lower right")
    fig.suptitle("P93W3-N10: forced closure separated from normal completion", fontsize=11)
    save(fig, "fig1_terminal_layering.png",
         "Left: the single stored status (all rows succeeded). Right: the four semantic "
         "states derived from stored facts; forced_finish/degraded were previously counted "
         "as success.", manifest)


def fig_latency(metrics: dict, manifest: list[dict]) -> None:
    latency = metrics["criteria"]["b_mvd_trigger_latency"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0))

    labels = ["p50", "p95", "p99", "p100"]
    values = [latency["p50_ms"], latency["p95_ms"], latency["p99_ms"], latency["p100_ms"]]
    bars = axes[0].bar(labels, values, color="#1565c0")
    axes[0].axhline(latency["criterion_s"] * 1000, color="#c62828", ls="--",
                    label=f"criterion {latency['criterion_s']:.0f} s")
    axes[0].set_yscale("log")
    axes[0].set_ylabel("latency [ms] (log)")
    axes[0].set_title(f"70% MVD checkpoint trigger latency over {latency['n_calls']} calls")
    for bar, value in zip(bars, values):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value * 1.05, f"{value:.2f}",
                     ha="center", fontsize=8)
    axes[0].legend(fontsize=8)

    margin = latency["criterion_s"] * 1000 / max(latency["p100_ms"], 1e-6)
    axes[1].bar(["criterion margin"], [margin], color="#2e7d32")
    axes[1].set_yscale("log")
    axes[1].set_ylabel("criterion / measured p100 (x, log)")
    axes[1].set_title(f"p100 = {latency['p100_ms']:.2f} ms -> {margin:,.0f}x margin")
    fig.suptitle("P93W3-N10: checkpoint cost is far below the 1 s trigger budget", fontsize=11)
    save(fig, "fig2_mvd_latency.png",
         "Left: percentile latency of one checkpoint evaluation on the production code path. "
         "Right: the remaining margin against the 1 s criterion.", manifest)


def fig_evidence(metrics: dict, manifest: list[dict]) -> None:
    evidence = metrics["budget_evidence"]
    composition = metrics["forced_finish_composition"]
    records = metrics["corpus"]["records"]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    keys = ["budget_marker", "wall_marker", "progress_marker", "tool_cap_only"]
    bars = axes[0].bar(range(len(keys)), [composition[k] for k in keys],
                       color=["#c62828", "#ad1457", "#6a1b9a", "#00838f"])
    axes[0].set_xticks(range(len(keys)))
    axes[0].set_xticklabels(["budget\nmarker", "wall\nmarker", "no-progress\nmarker",
                             "tool cap\n(no marker)"], fontsize=8)
    axes[0].set_ylabel("forced_finish records")
    axes[0].set_title(f"what forced closure ({composition['total']} of {records})")
    for bar, key in zip(bars, keys):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height(), composition[key],
                     ha="center", va="bottom", fontsize=8)

    axes[1].bar(["harness marker\n(ground fact)", "wall ratio >= 1.0\n(ratio-only rule)"],
                [evidence["marker_exhaustion_rows"], evidence["naive_ratio_only_counts"]["forced_finish"]],
                color=["#2e7d32", "#b0bec5"])
    axes[1].set_yscale("log")
    axes[1].set_ylabel("rows called exhausted (log)")
    axes[1].set_title(
        "wall proxy: recall %.2f, precision %.3f -> %d false forced closures"
        % (evidence["wall_proxy_recall"], evidence["wall_proxy_precision"],
           evidence["false_forced_rows_avoided"]), fontsize=9)
    fig.suptitle("P93W3-N10: exhaustion is an evidence decision, not a ratio threshold", fontsize=11)
    save(fig, "fig3_budget_evidence.png",
         "Left: composition of the forced-closure population. Right: the rejected ratio-only "
         "rule would have declared a large majority of rows exhausted; only the harness marker "
         "(and the hard-enforced tool counter) are accepted as evidence.", manifest)


def fig_confusion(metrics: dict, manifest: list[dict]) -> None:
    matrix = metrics["criteria"]["a_classification_accuracy"]["confusion"]
    accuracy = metrics["criteria"]["a_classification_accuracy"]["accuracy"]
    n = len(STATE_ORDER)
    grid = [[matrix[truth][pred] for pred in STATE_ORDER] for truth in STATE_ORDER]

    cmap = LinearSegmentedColormap.from_list("own", ["#ffffff", "#2e7d32"])
    fig, ax = plt.subplots(figsize=(5.6, 4.8))
    image = ax.imshow(grid, cmap=cmap, vmin=0, vmax=max(max(row) for row in grid))
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(STATE_ORDER, rotation=25, fontsize=8)
    ax.set_yticklabels(STATE_ORDER, fontsize=8)
    ax.set_xlabel("derived state")
    ax.set_ylabel("scenario expectation")
    for i in range(n):
        for j in range(n):
            ax.text(j, i, grid[i][j], ha="center", va="center",
                    color="#ffffff" if grid[i][j] > 2 else "#212121", fontsize=9)
    ax.set_title(f"20-scenario matrix — accuracy {accuracy:.2%}")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    save(fig, "fig4_confusion.png",
         "Confusion matrix of the 20-scenario acceptance matrix (19 classifiable scenarios plus "
         "one exception-path scenario); only the diagonal is populated.", manifest)


def main() -> None:
    metrics = load("metrics.json")
    manifest: list[dict] = []
    fig_layering(metrics, manifest)
    fig_latency(metrics, manifest)
    fig_evidence(metrics, manifest)
    fig_confusion(metrics, manifest)
    (RUN / "figures.json").write_text(
        json.dumps({"node": "P93W3-N10", "metrics_sha256": hashlib.sha256(
            (RUN / "metrics.json").read_bytes()).hexdigest(), "figures": manifest},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"n_figures": len(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()