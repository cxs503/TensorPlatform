#!/usr/bin/env python3
"""生成 audit-findings tracker 的证据图（数据全部来自结构化输出，零手抄）。

用法::

    python3 -m tools.audit_findings_tracker.make_figures \
        --out artifacts/audit_findings_tracker/fig_audit_findings_tracker.png

左图：四份样本报告 × 五类发现的计数（含派生条目）；右图：PASS/FAIL 判定与
recognized / withheld 分布（每个数字都由 :func:`core.analyze_report` 现算）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .core import CANONICAL_CATEGORIES, analyze_report  # noqa: E402

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "audit_findings_tracker"
SAMPLES = (
    "report_15311.json",
    "report_15312.json",
    "report_15311_counts_drift.json",
    "report_15312_withheld.json",
)


def _load() -> List[Dict[str, Any]]:
    return [analyze_report(SAMPLE_DIR / name) for name in SAMPLES]


def render(out: Path) -> Path:
    results = _load()
    labels = [name.replace("report_", "").replace(".json", "") for name in SAMPLES]
    colors = ["#2a6ebb", "#4f9d69", "#c98b2a", "#b0433b"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.8))

    width = 0.2
    for i, (res, label) in enumerate(zip(results, labels)):
        counts = [res["counts"].get(c, 0) for c in CANONICAL_CATEGORIES]
        xs = [j + (i - (len(results) - 1) / 2) * width for j in range(len(CANONICAL_CATEGORIES))]
        ax1.bar(xs, counts, width=width, label=label, color=colors[i])
        for x, v in zip(xs, counts):
            if v:
                ax1.text(x, v + 0.05, str(v), ha="center", va="bottom", fontsize=8)
    ax1.set_xticks(range(len(CANONICAL_CATEGORIES)))
    ax1.set_xticklabels(CANONICAL_CATEGORIES, rotation=15, fontsize=9)
    ax1.set_ylabel("structured findings (count)")
    ax1.set_title("A. findings per category (synthetic fixtures)")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.25)

    recognized = [r["summary"]["recognized"] for r in results]
    withheld = [r["summary"]["withheld"] for r in results]
    xs = list(range(len(results)))
    ax2.bar(xs, recognized, color="#4f9d69", label="recognized")
    ax2.bar(xs, withheld, bottom=recognized, color="#b0433b", label="WITHHELD")
    for x, res in zip(xs, results):
        total = res["summary"]["total"]
        ax2.text(x, max(total, 0.2) + 0.1, f'{res["status"]} (rc={res["exit_code"]})',
                 ha="center", va="bottom", fontsize=9,
                 color="#2e7d32" if res["status"] == "PASS" else "#b0433b")
    ax2.set_xticks(xs)
    ax2.set_xticklabels(labels, rotation=12, fontsize=8)
    ax2.set_ylabel("entries")
    ax2.set_title("B. gate decision: PASS=0 / FAIL=1")
    ax2.set_ylim(0, max([r["summary"]["total"] for r in results] + [1]) * 1.6)
    ax2.legend(fontsize=8)
    ax2.grid(axis="y", alpha=0.25)

    fig.suptitle("audit_findings_tracker: cross-audit findings are structured, decoded and gated")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="render audit-findings tracker evidence figure")
    p.add_argument("--out", default=str(SAMPLE_DIR / "fig_audit_findings_tracker.png"))
    args = p.parse_args(argv)
    path = render(Path(args.out))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())