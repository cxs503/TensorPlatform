#!/usr/bin/env python3
"""评测/覆盖率作图（matplotlib，无 seaborn；数字全部来自 artifacts JSON，零手抄）。

产物：``artifacts/backlog_extraction/figs/``
1. ``fig1_debt_distribution.png`` —— 欠账分布（仓 × 根因类，判词 FAIL/WITHHELD 分色）
2. ``fig2_error_magnitude.png``  —— |误差| 分布（对数轴，3% 门槛线，标注超差条数）
3. ``fig3_metrics_vs_gates.png`` —— 行级召回/误报率/根因 macro-F1（两种口径）/去重准确率 vs 门槛
4. ``fig4_coverage.png``         —— 战役节点覆盖（欠账条数；缺报节点红叉）
5. ``fig5_rootcause_matrix.png`` —— 严格口径根因混淆矩阵
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ART = Path("artifacts/backlog_extraction")
FIGS = ART / "figs"
CAUSES = (
    "reference_limited",
    "discretization_resolution",
    "implementation_defect",
    "reference_missing",
)
CAUSE_EN = {
    "reference_limited": "reference-limited",
    "discretization_resolution": "discretization",
    "implementation_defect": "implementation",
    "reference_missing": "ref-missing",
}
COLORS = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]


def _load():
    backlog = [
        json.loads(line)
        for line in (ART / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "backlog": backlog,
        "eval": json.loads((ART / "eval.json").read_text(encoding="utf-8")),
        "coverage": json.loads((ART / "coverage.json").read_text(encoding="utf-8")),
    }


def fig1(backlog: list[dict]) -> Path:
    repos = sorted({r["repo"] for r in backlog})
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    bottom = np.zeros(len(repos))
    for cause, color in zip(CAUSES, COLORS):
        vals = np.array(
            [
                sum(1 for r in backlog if r["repo"] == repo and r["rootcause"] == cause)
                for repo in repos
            ]
        )
        ax.bar(repos, vals, bottom=bottom, label=CAUSE_EN[cause], color=color, edgecolor="white")
        bottom += vals
    for i, total in enumerate(bottom):
        ax.text(i, total + 0.3, f"{int(total)}", ha="center", fontsize=9)
    ax.set_ylabel("debt rows")
    ax.set_title("Fig.1 FAIL/WITHHELD debt rows by repo x root cause (n=%d)" % len(backlog))
    ax.legend(fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=8)
    fig.tight_layout()
    out = FIGS / "fig1_debt_distribution.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def fig2(backlog: list[dict]) -> Path:
    errs = [abs(r["error_pct"]) for r in backlog if r["error_pct"] is not None]
    no_err = sum(1 for r in backlog if r["error_pct"] is None)
    over = sum(1 for e in errs if e > 3.0)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bins = np.logspace(-2, 2.4, 28)
    ax.hist(errs, bins=bins, color="#4C78A8", edgecolor="white", alpha=0.9)
    ax.set_xscale("log")
    ax.axvline(3.0, color="#E45756", ls="--", lw=1.4, label="3% gate")
    ax.set_xlabel("|error| [%] (log scale)")
    ax.set_ylabel("count")
    ax.set_title(
        f"Fig.2 error magnitude (with value: {len(errs)}, of which >3%: {over}; no value: {no_err})"
    )
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = FIGS / "fig2_error_magnitude.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def fig3(ev: dict) -> Path:
    rl, rc, dd, gates = ev["row_level"], ev["rootcause"], ev["dedup"], ev["gates"]
    items = [
        ("row recall", rl["recall"], gates["recall_min"], "≥"),
        ("false-positive rate", rl["false_positive_rate"], gates["false_positive_rate_max"], "≤"),
        ("rootcause macro-F1 (line)", rc["macro_f1"], gates["macro_f1_min"], "≥"),
        ("rootcause macro-F1 (strict)", rc.get("macro_f1_strict", 0.0), gates["macro_f1_min"], "≥"),
        ("near-dup 40-pair acc.", dd["accuracy"] or 0.0, gates["dedup_pair_accuracy_min"], "≥"),
    ]
    labels = [f"{n}\n(gate {s}{t:.2f})" for n, _, t, s in items]
    vals = [v for _, v, _, _ in items]
    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    bars = ax.bar(labels, vals, color=["#54A24B" if _ok(v, t, s) else "#E45756" for (_, v, t, s) in items])
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.015, f"{v:.3f}", ha="center", fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("value")
    ax.set_title("Fig.3 acceptance metrics vs gates (green=pass, red=fail)")
    plt.setp(ax.get_xticklabels(), fontsize=8)
    fig.tight_layout()
    out = FIGS / "fig3_metrics_vs_gates.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def _ok(v: float, thr: float, sense: str) -> bool:
    return v >= thr if sense == "≥" else v <= thr


def fig4(cov: dict) -> Path:
    nodes, counts, missing = [], [], []
    for wave in cov["waves"]:
        for n in wave["nodes"]:
            nodes.append(n["node"])
            counts.append(n["debt_rows"])
            missing.append(not n["report_present"])
    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    x = np.arange(len(nodes))
    ax.bar(x, counts, color="#4C78A8", edgecolor="white")
    for xi, mi in zip(x, missing):
        if mi:
            ax.text(xi, 0.15, "no report", ha="center", va="bottom", fontsize=7, color="#E45756")
    ax.set_xticks(x)
    ax.set_xticklabels(nodes, rotation=55, ha="right", fontsize=7)
    ax.set_ylabel("debt rows")
    ax.set_title(
        "Fig.4 node coverage: plan-21 (IN20, 12 nodes) + P21 (plan-44, 9 nodes)"
    )
    fig.tight_layout()
    out = FIGS / "fig4_coverage.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def fig5(ev: dict) -> Path:
    strict = ev["rootcause"]["strict_per_class"]
    mat = np.zeros((len(CAUSES), len(CAUSES)))
    for i, gold in enumerate(CAUSES):
        mat[i, i] = strict[gold]["tp"]
    # 用 line 级 mismatches 构造非对角元（同一批行，仅统计有 pred 的）
    for mis in ev["rootcause"]["mismatches"]:
        g, p = mis["gold"], mis["pred"]
        if g in CAUSES and p in CAUSES:
            mat[CAUSES.index(g), CAUSES.index(p)] += 1
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(CAUSES)))
    ax.set_xticklabels([CAUSE_EN[c] for c in CAUSES], fontsize=8)
    ax.set_yticks(range(len(CAUSES)))
    ax.set_yticklabels([CAUSE_EN[c] for c in CAUSES], fontsize=8)
    ax.set_xlabel("predicted")
    ax.set_ylabel("gold")
    for i in range(len(CAUSES)):
        for j in range(len(CAUSES)):
            if mat[i, j]:
                ax.text(j, i, f"{int(mat[i, j])}", ha="center", va="center",
                        color="white" if mat[i, j] > mat.max() / 2 else "black", fontsize=9)
    ax.set_title("Fig.5 root-cause confusion matrix (line-level gold x predicted)", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    out = FIGS / "fig5_rootcause_matrix.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def main() -> int:
    FIGS.mkdir(parents=True, exist_ok=True)
    data = _load()
    made = [
        fig1(data["backlog"]),
        fig2(data["backlog"]),
        fig3(data["eval"]),
        fig4(data["coverage"]),
        fig5(data["eval"]),
    ]
    (FIGS / "figures_metadata.json").write_text(
        json.dumps(
            {"figures": [{"name": p.name, "bytes": p.stat().st_size} for p in made]},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    for p in made:
        print(f"[figs] {p} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())