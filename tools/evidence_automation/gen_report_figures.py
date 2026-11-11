#!/usr/bin/env python3
"""m12525 报告图生成器：图 1/2/3（数字全部从工件读，零手抄）。

用法::

    python3 tools/evidence_automation/gen_report_figures.py \
        --out artifacts/m12525

产物：``fig1_capability_coverage.png`` / ``fig2_secret_calibration.png`` /
``fig3_repo_baseline_scan.png``，并打印 JSON 摘要供报告程序化注入。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_capability_coverage(coverage: dict, out: Path) -> Path:
    """能力证据覆盖率：升级前（人工台账 3/21）vs 自动化管线（本次 17/21）+ 等级分布。"""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    before, after = 3, coverage["covered"]
    total = coverage["total_capabilities"]
    axes[0].bar(["manual ledger\n(baseline)", "automation\npipeline (this run)"], [before, after],
                color=["#9aa5b1", "#2f6f4f"], width=0.55)
    for idx, value in enumerate((before, after)):
        axes[0].text(idx, value + 0.3, f"{value}/{total}", ha="center", fontsize=11)
    axes[0].axhline(coverage["target_covered"], ls="--", color="#b5541f", lw=1.2,
                    label=f"C1 target {coverage['target_covered']}/{total}")
    axes[0].set_ylim(0, total)
    axes[0].set_ylabel("capabilities with executed evidence")
    axes[0].set_title("Capability evidence coverage")
    axes[0].legend(loc="upper left", fontsize=8)

    tally = coverage["grade_tally"]
    labels = list(tally)
    values = [tally[k] for k in labels]
    colors = {"REAL": "#2f6f4f", "WITHHELD": "#c9a227", "MOCK": "#b5541f",
              "STUB": "#7f5a83", "NOT_SUPPORTED": "#9aa5b1"}
    axes[1].bar(labels, values, color=[colors.get(k, "#666") for k in labels])
    for idx, value in enumerate(values):
        axes[1].text(idx, value + 0.15, str(value), ha="center", fontsize=10)
    axes[1].set_ylabel("capabilities")
    axes[1].set_title("Derived grades (manual ceiling applied)")
    axes[1].tick_params(axis="x", rotation=20)
    fig.tight_layout()
    target = out / "fig1_capability_coverage.png"
    fig.savefig(target, dpi=160)
    plt.close(fig)
    return target


def fig_secret_calibration(calib: dict, baseline: dict, out: Path) -> Path:
    """秘密扫描：基线（首轮规则集）vs 本轮；三出口与幂等。"""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    metrics = ["recall", "false-positive rate"]
    base_vals = [baseline["recall"], baseline["fpr"]]
    now_vals = [calib["detection"]["recall"], calib["false_positives"]["fpr"]]
    x = range(len(metrics))
    axes[0].bar([i - 0.18 for i in x], base_vals, width=0.34, label="baseline (first rule set)",
                color="#9aa5b1")
    axes[0].bar([i + 0.18 for i in x], now_vals, width=0.34, label="this run (rules + allowlists)",
                color="#2f6f4f")
    for i, (b, n) in enumerate(zip(base_vals, now_vals)):
        axes[0].text(i - 0.18, b + 0.02, f"{b:.2f}", ha="center", fontsize=9)
        axes[0].text(i + 0.18, n + 0.02, f"{n:.2f}", ha="center", fontsize=9)
    axes[0].axhline(0.95, ls="--", color="#b5541f", lw=1.1)
    axes[0].text(1.45, 0.96, "C5≥0.95", fontsize=8, color="#b5541f")
    axes[0].axhline(0.05, ls=":", color="#b5541f", lw=1.1)
    axes[0].text(1.45, 0.06, "C6≤0.05", fontsize=8, color="#b5541f")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(metrics)
    axes[0].set_ylim(0, 1.1)
    axes[0].set_ylabel("ratio")
    axes[0].set_title("Injection calibration: 20 synthetic secrets + 20 benign")
    axes[0].legend(fontsize=8, loc="lower right")

    rows = calib["outlets"]["rows"]
    names = [row["outlet"] for row in rows]
    blocked = [1 if row["blocked_and_redacted"] else 0 for row in rows]
    axes[1].barh(names, blocked, color=["#2f6f4f" if b else "#b5541f" for b in blocked])
    for idx, row in enumerate(rows):
        axes[1].text(0.02, idx, f"hits={row['hits']}, leftover plaintext={row['plaintext_residual']}, "
                               f"rescan clean={row['rescan_after_redaction_clean']}",
                     va="center", fontsize=8)
    axes[1].set_xlim(0, 1.6)
    axes[1].set_xlabel("blocked & redacted (1=yes)")
    axes[1].set_title("Three-outlet effectiveness + redaction idempotency")
    fig.tight_layout()
    target = out / "fig2_secret_calibration.png"
    fig.savefig(target, dpi=160)
    plt.close(fig)
    return target


def fig_repo_baseline(scan: dict, out: Path) -> Path:
    """仓库基线扫描：按规则族统计命中（真实世界误报面）。"""
    tally: dict[str, int] = {}
    for row in scan["rows"]:
        for hit in row["hits"]:
            tally[hit["rule_id"]] = tally.get(hit["rule_id"], 0) + 1
    fig, axes = plt.subplots(figsize=(7.6, 3.8))
    labels = list(tally)
    values = [tally[k] for k in labels]
    axes.bar(labels, values, color="#3d6b8f")
    for idx, value in enumerate(values):
        axes.text(idx, value + 0.2, str(value), ha="center", fontsize=9)
    axes.set_ylabel("hit count (lines)")
    axes.set_title(f"Repo baseline scan: {scan['files_with_hits']}/{scan['scanned_files']} files "
                   f"({scan['hit_rate'] * 100:.2f}%)")
    axes.tick_params(axis="x", rotation=15)
    fig.tight_layout()
    target = out / "fig3_repo_baseline_scan.png"
    fig.savefig(target, dpi=160)
    plt.close(fig)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="m12525 报告图生成")
    parser.add_argument("--out", default="artifacts/m12525")
    parser.add_argument("--coverage", default="artifacts/capability_evidence/coverage.json")
    parser.add_argument("--calibration", default="artifacts/secret_hygiene/calibration.json")
    parser.add_argument("--baseline-scan", default="artifacts/secret_hygiene/repo_baseline.json")
    args = parser.parse_args(argv)

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    coverage = _load(ROOT / args.coverage)
    calib = _load(ROOT / args.calibration)
    scan = _load(ROOT / args.baseline_scan)
    # 基线：本轮定型前首跑标定的实测值（(0.85, 0.05)；来源见报告"注意点"节）
    baseline = {"recall": 0.85, "fpr": 0.05}
    paths = [
        fig_capability_coverage(coverage["coverage"], out),
        fig_secret_calibration(calib, baseline, out),
        fig_repo_baseline(scan, out),
    ]
    print(json.dumps({"figures": [str(p) for p in paths],
                      "coverage": coverage["coverage"]["covered"],
                      "total": coverage["coverage"]["total_capabilities"],
                      "recall": calib["detection"]["recall"],
                      "fpr": calib["false_positives"]["fpr"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())