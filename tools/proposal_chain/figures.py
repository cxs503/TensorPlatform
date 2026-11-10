"""论文图（matplotlib，数据只读工件，不手抄任何数字）。

四图
----
* ``fig1_score_distribution.png``：标注对的语义相似度分布（overlap vs distinct，直方图+核对数）
* ``fig2_threshold_curve.png``：阈值–召回/误判曲线（tune 选集、holdout 报告集、配置工作点）
* ``fig3_coverage_matrix.png``：域标签覆盖矩阵（提案侧 vs 节点侧计数）与主题命中率
* ``fig4_schema_fields.png``：节点 metadata 四必填字段的填表率/缺失数与恒等降级占比
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .config import REPO_ROOT, ChainConfig


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def read(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def fig_score_distribution(out: Path, label_eval: Mapping[str, Any], rep: str) -> Path:
    plt = _plt()
    ev = (label_eval.get("evaluations", {}) or {}).get(rep, {})
    rows = ev.get("labeled_scores", []) or []
    pos = [r["score"] for r in rows if r["label"] == "overlap"]
    neg = [r["score"] for r in rows if r["label"] == "distinct"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bins = [i / 20 for i in range(21)]
    ax.hist(neg, bins=bins, alpha=0.65, label=f"distinct (n={len(neg)})", color="#8c8c8c")
    ax.hist(pos, bins=bins, alpha=0.75, label=f"overlap (n={len(pos)})", color="#1f77b4")
    cfgd = label_eval.get("configured", {})
    ax.axvline(float(cfgd.get("dup_threshold", 0.85)), color="#d62728", ls="--",
               label=f"dup threshold={cfgd.get('dup_threshold', 0.85)}")
    ax.axvline(float(cfgd.get("review_threshold", 0.65)), color="#ff7f0e", ls=":",
               label=f"review threshold={cfgd.get('review_threshold', 0.65)}")
    ax.set_xlabel("semantic similarity (TF-IDF → LSA → cosine)")
    ax.set_ylabel("labeled pairs")
    ax.set_title("Labeled pair similarity distribution (blind annotation)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path = out / "fig1_score_distribution.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_threshold_curve(out: Path, label_eval: Mapping[str, Any], rep: str) -> Path:
    plt = _plt()
    sweeps = (label_eval.get("sweeps", {}) or {}).get(rep, []) or []
    ev = (label_eval.get("evaluations", {}) or {}).get(rep, {})
    hold = ev.get("holdout", {})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 4.0))
    thresholds = [r["threshold"] for r in sweeps]
    recalls = [r["recall"] for r in sweeps]
    fps = [r["fp_rate"] for r in sweeps]
    ax1.plot(thresholds, recalls, "o-", color="#1f77b4", label="recall (tune half)")
    ax1.plot(thresholds, fps, "s-", color="#d62728", label="false-positive rate (tune half)")
    if hold:
        ax1.axhline(float(hold.get("catch_recall", 0.0)), color="#1f77b4", ls=":",
                    label="holdout catch-recall")
        ax1.axhline(float(hold.get("review_fp_rate", 0.0)), color="#d62728", ls=":",
                    label="holdout review FP")
    cfgd = label_eval.get("configured", {})
    ax1.axvline(float(cfgd.get("review_threshold", 0.65)), color="#ff7f0e", ls="--",
                label="configured review band")
    ax1.axhline(0.9, color="#2ca02c", ls="-.", lw=1, label="recall target 0.90")
    ax1.set_xlabel("similarity threshold")
    ax1.set_ylabel("rate")
    ax1.set_title("Threshold sweep on tune half (labels blind to scores)")
    ax1.legend(fontsize=7)
    ax1.grid(alpha=0.25)

    tiers = ["auto (>=dup)", "review band", "missed"]
    base = label_eval.get("tfidf_baseline", {}).get("all", {}) or {}
    lsa = ev.get("all", {}) or {}
    def tier_counts(d: Mapping[str, Any]) -> list[float]:
        pos = max(1, int(d.get("positives", 1)))
        return [100.0 * d.get("tp", 0) / pos,
                100.0 * d.get("review_on_positives", 0) / pos,
                100.0 * d.get("fn", 0) / pos]
    x = range(len(tiers))
    ax2.bar([i - 0.2 for i in x], tier_counts(base), width=0.4, label="TF-IDF baseline", color="#8c8c8c")
    ax2.bar([i + 0.2 for i in x], tier_counts(lsa), width=0.4, label="TF-IDF→LSA", color="#1f77b4")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(tiers)
    ax2.set_ylabel("% of labeled overlapping pairs")
    ax2.set_title("Where overlapping pairs land (all labels)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    path = out / "fig2_threshold_curve.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_coverage_matrix(out: Path, matrix: Mapping[str, Any], ledger_cov: Mapping[str, Any]) -> Path:
    plt = _plt()
    rows = matrix.get("domain_matrix", []) or []
    rows = sorted(rows, key=lambda r: -(r["proposal_count"] + r["node_count"]))[:20]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.4))
    def ascii_label(text: str, idx: int) -> str:
        """图内标签只用 ASCII（本镜像无 CJK 字体；避免方块字污染论文图）。"""
        keep = "".join(ch for ch in str(text) if ch.isascii() and (ch.isalnum() or ch in " _-/."))
        keep = " ".join(keep.split())[:20]
        return keep or f"domain#{idx + 1}"

    if rows:
        labels_ = [ascii_label(r["domain"], i) for i, r in enumerate(rows)]
        p = [r["proposal_count"] for r in rows]
        n = [r["node_count"] for r in rows]
        y = range(len(rows))
        ax1.barh([i + 0.2 for i in y], p, height=0.4, color="#1f77b4", label="proposals")
        ax1.barh([i - 0.2 for i in y], n, height=0.4, color="#ff7f0e", label="campaign nodes")
        ax1.set_yticks(list(y))
        ax1.set_yticklabels(labels_, fontsize=7)
        ax1.invert_yaxis()
        ax1.set_xlabel("count")
        ax1.set_title("Seeding coverage matrix (data-provided domain labels)")
        ax1.legend(fontsize=8)
        ax1.grid(alpha=0.25, axis="x")
    stats = ledger_cov.get("stats", {}) or {}
    states = stats.get("state_counts", {}) or {}
    if states:
        keys = list(states)
        vals = [states[k] for k in keys]
        ax2.bar(keys, vals, color=["#2ca02c" if k == "covered_by_node" else "#ff7f0e" for k in keys])
        ax2.set_ylabel("ledger items")
        ax2.set_title(f"Gap ledger item states (items={stats.get('items')}, "
                      f"agreement={stats.get('agreement_rate')})")
        ax2.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    path = out / "fig3_coverage_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_schema_fields(out: Path, schema: Mapping[str, Any]) -> Path:
    plt = _plt()
    full = schema.get("full", {}) or {}
    required = list(schema.get("required_fields", []) or [])
    fill = full.get("field_fill_rate", {}) or {}
    missing = full.get("missing_field_counts", {}) or {}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 4.0))
    ax1.bar(required, [100.0 * fill.get(r, 0.0) for r in required], color="#1f77b4")
    ax1.set_ylim(0, 105)
    ax1.set_ylabel("fill rate [%]")
    ax1.set_title(f"Required metadata fields (n={full.get('total')} nodes)")
    for i, r in enumerate(required):
        ax1.text(i, 100.0 * fill.get(r, 0.0) + 1.5, f"{100.0 * fill.get(r, 0.0):.1f}",
                 ha="center", fontsize=8)
    ax1.grid(alpha=0.25, axis="y")
    ax2.bar(required, [missing.get(r, 0) for r in required], color="#d62728")
    ax2.set_ylabel("missing count")
    ax2.set_title(f"Missing fields → identity-degrade "
                  f"(degraded={full.get('degraded_count')}, ratio={full.get('degraded_ratio')})")
    for i, r in enumerate(required):
        ax2.text(i, missing.get(r, 0) + 0.5, str(missing.get(r, 0)), ha="center", fontsize=8)
    ax2.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    path = out / "fig4_schema_fields.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def render_all(cfg: ChainConfig) -> dict[str, str]:
    out = cfg.path_of("artifacts_dir")
    label_eval = read(out / "label_eval.json", {}) or {}
    matrix = read(out / "coverage_matrix.json", {}) or {}
    ledger_cov = read(out / "ledger_coverage.json", {}) or {}
    schema = read(out / "schema_validation.json", {}) or {}
    rep = str(label_eval.get("configured", {}).get("representation", "claim"))
    paths = {
        "fig1_score_distribution": fig_score_distribution(out, label_eval, rep),
        "fig2_threshold_curve": fig_threshold_curve(out, label_eval, rep),
        "fig3_coverage_matrix": fig_coverage_matrix(out, matrix, ledger_cov),
        "fig4_schema_fields": fig_schema_fields(out, schema),
    }
    return {k: str(Path(v).relative_to(REPO_ROOT)) for k, v in paths.items()}