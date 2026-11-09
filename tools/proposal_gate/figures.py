"""出图（matplotlib/Agg，无交互后端）：标定曲线 + 真库语料分布 + 账本四态。

图均附 sha256 入 manifest（图表溯源，防「图与数字不一致」）。
运行：``python3 -m tools.proposal_gate.figures --artifacts artifacts/proposal_gate``
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLOR_DUP = "#c0392b"
COLOR_REVIEW = "#e67e22"
COLOR_OK = "#27ae60"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_corpus_similarity(cal: dict[str, Any], out: Path, thresholds: dict[str, float]) -> Path | None:
    real = cal.get("real_corpus") or {}
    if real.get("status") != "ok":
        return None
    hist = real["hist"]
    centers = [(h["lo"] + min(h["hi"], 1.0)) / 2 for h in hist]
    counts = [h["count"] for h in hist]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.bar(centers, counts, width=0.085, color="#34495e", label=f"real campaign-node pairs (n={real['pairs']})")
    ax.set_yscale("log")
    for name, value in thresholds.items():
        ax.axvline(value, color=COLOR_DUP if name == "dup" else COLOR_REVIEW, ls="--", lw=1.4,
                   label=f"{name} threshold = {value}")
    ax.set_xlabel("char 3-gram similarity (NFKC-normalised, Jaccard/containment)")
    ax.set_ylabel("pair count (log)")
    ax.set_title("Real corpus similarity distribution: clean gap between duplicates and the rest")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_threshold_sweep(cal: dict[str, Any], out: Path) -> Path:
    sweep = cal["sweep"]
    xs = [row["threshold"] for row in sweep]
    tpr = [row["tpr"] for row in sweep]
    fpr = [row["fpr"] for row in sweep]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(xs, tpr, color=COLOR_OK, lw=1.8, label=f"TPR (positives n={cal['positives_n']})")
    ax.plot(xs, fpr, color=COLOR_DUP, lw=1.8, label=f"FPR (negatives n={cal['negatives_n']})")
    for label, key, color in (("dup", "dup", COLOR_DUP), ("review", "review", COLOR_REVIEW)):
        point = cal["operating_points"][key]
        if "tpr" in point:
            ax.plot([point["threshold"]], [point["tpr"]], "o", color=color, ms=7)
            ax.annotate(f"{label} @ {point['threshold']}\nTPR={point['tpr']:.2f} FPR={point['fpr']:.2f}",
                        (point["threshold"], point["tpr"]), textcoords="offset points",
                        xytext=(-70, -34), fontsize=8, color=color)
    ax.set_xlabel("similarity threshold")
    ax.set_ylabel("rate")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Threshold calibration on labelled synthetic cohort")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_edit_intensity(cal: dict[str, Any], out: Path, thresholds: dict[str, float]) -> Path:
    curve = cal["edit_intensity"]
    xs = [row["edits"] for row in curve]
    mean = [row["mean"] for row in curve]
    lo = [row["min"] for row in curve]
    hi = [row["max"] for row in curve]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.fill_between(xs, lo, hi, color="#95a5a6", alpha=0.35, label="min–max over 10 edit seeds")
    ax.plot(xs, mean, "-o", color="#2c3e50", lw=1.8, ms=5, label="mean similarity vs base proposal")
    for name, value in thresholds.items():
        ax.axhline(value, color=COLOR_DUP if name == "dup" else COLOR_REVIEW, ls="--", lw=1.4,
                   label=f"{name} threshold = {value}")
    ax.set_xlabel("number of synonym substitutions (edit intensity)")
    ax.set_ylabel("similarity to original")
    ax.set_title("Detection band vs edit intensity")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_ledger_states(artifacts: Path, out: Path) -> Path | None:
    arms = {}
    for name in ("strict", "lenient"):
        path = artifacts / name / "collect_latest.json"
        if path.exists():
            arms[name] = _load(path)
    if not arms:
        return None
    verdict_order = ["accepted", "adopted", "duplicate", "near_duplicate", "twin", "quarantined"]
    colors = [COLOR_OK, "#16a085", COLOR_DUP, COLOR_REVIEW, "#8e44ad", "#7f8c8d"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.0), gridspec_kw={"width_ratios": [1.35, 1]})
    width = 0.8 / max(len(arms), 1)
    for idx, (arm, payload) in enumerate(arms.items()):
        stats = payload["ledger_stats"]["verdict_counts"]
        xs = [i + idx * width for i in range(len(verdict_order))]
        ys = [stats.get(v, 0) for v in verdict_order]
        bars = ax1.bar(xs, ys, width=width, label=f"{arm} arm")
        for bar, y in zip(bars, ys):
            ax1.annotate(str(y), (bar.get_x() + bar.get_width() / 2, y), ha="center",
                         va="bottom", fontsize=7)
    ax1.set_xticks([i + width * (len(arms) - 1) / 2 for i in range(len(verdict_order))])
    ax1.set_xticklabels(verdict_order, rotation=20, fontsize=8)
    ax1.set_ylabel("ledger rows")
    ax1.set_title("proposal_gate_ledger verdict distribution (real campaign nodes)")
    ax1.legend(fontsize=8)
    for idx, (arm, payload) in enumerate(arms.items()):
        cov = next(a for a in payload["arms"] if a["arm"] == "coverage")
        rate = cov.get("mission_traceability_rate") or 0.0
        ax2.bar([idx], [rate], width=0.5, color="#2980b9")
        ax2.annotate(f"{rate:.3f}\nn={cov.get('nodes_total')}",
                     (idx, rate), ha="center", va="bottom", fontsize=8)
    ax2.set_xticks(range(len(arms)))
    ax2.set_xticklabels(list(arms), fontsize=8)
    ax2.set_ylim(0, 1.0)
    ax2.set_ylabel("mission traceability rate")
    ax2.set_title("Supply-chain traceability: node → mission link")
    ax2.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(artifacts: Path) -> dict[str, Any]:
    cal_path = artifacts / "calibration.json"
    cal = _load(cal_path)
    thresholds = {
        "dup": float(cal["operating_points"]["dup"]["threshold"]),
        "review": float(cal["operating_points"]["review"]["threshold"]),
    }
    figs_dir = artifacts / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for path in (
        fig_corpus_similarity(cal, figs_dir / "fig1_corpus_similarity.png", thresholds),
        fig_threshold_sweep(cal, figs_dir / "fig2_threshold_sweep.png"),
        fig_edit_intensity(cal, figs_dir / "fig3_edit_intensity.png", thresholds),
        fig_ledger_states(artifacts, figs_dir / "fig4_ledger_states.png"),
    ):
        if path is not None:
            made.append(path)
    manifest = {
        "schema": "proposal-gate-figures/1",
        "source": {"calibration": str(cal_path), "artifacts": str(artifacts)},
        "figures": [
            {"name": p.name, "path": str(p), "sha256": sha256(p), "bytes": p.stat().st_size}
            for p in made
        ],
    }
    (figs_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提案门图表生成")
    ap.add_argument("--artifacts", default="artifacts/proposal_gate")
    args = ap.parse_args(argv)
    manifest = run(Path(args.artifacts))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())