#!/usr/bin/env python3
"""Topic-form sensitivity: run-on sentence vs space-separated keywords.

The radar probes CJK by 4-character windows, so a topic written as one long run
carries windows that straddle word boundaries and can never exist in the pool.
This script measures that effect on 5 real pool topics and writes
``artifacts/whitespace_radar/topic_form_sensitivity.json`` (numbers for the
report, no hand copying).
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core import Bm25Index, Thresholds, analyze, load_pool, pool_digest  # noqa: E402

PAIRS = [
    ("耐波响应谱矩的频率窗截断欠账量化裁决与回归护栏", "耐波 响应谱矩 频率窗 截断 回归护栏"),
    ("结构域已合并修复的服务面独立复核闭式判据", "结构域 服务面 独立复核 闭式判据"),
    ("规划师权限阶梯文档权限分级升权审批", "规划师权限阶梯 权限 升权 审批"),
    ("四中心最小视图实现与验收报告能力台账评价流", "四中心 最小视图 能力台账 评价流"),
    ("引导回路评价牵引派发的接线", "引导回路 评价牵引 派发 接线"),
]

OUT = os.path.join(REPO_ROOT, "artifacts/whitespace_radar/topic_form_sensitivity.json")


def main() -> int:
    docs = load_pool(REPO_ROOT, "data/whitespace_radar/pool/manifest.json")
    idx = Bm25Index.build(docs)
    digest = pool_digest(docs)
    th = Thresholds()
    rows = []
    for runon, spaced in PAIRS:
        a = analyze(idx, runon, thresholds=th, pool_digest=digest, as_of="pool")
        b = analyze(idx, spaced, thresholds=th, pool_digest=digest, as_of="pool")
        rows.append(
            {
                "runon": runon,
                "spaced": spaced,
                "runon_verdict": a["verdict"],
                "spaced_verdict": b["verdict"],
                "runon_specific_coverage": a["query"]["specific_coverage"],
                "spaced_specific_coverage": b["query"]["specific_coverage"],
                "runon_top_score": a["top"]["score"],
                "spaced_top_score": b["top"]["score"],
            }
        )
    summary = {
        "n_pairs": len(rows),
        "runon_covered": sum(1 for r in rows if r["runon_verdict"] == "covered"),
        "spaced_covered": sum(1 for r in rows if r["spaced_verdict"] == "covered"),
        "mean_runon_coverage": round(sum(r["runon_specific_coverage"] for r in rows) / len(rows), 4),
        "mean_spaced_coverage": round(sum(r["spaced_specific_coverage"] for r in rows) / len(rows), 4),
    }
    payload = {"pool_digest": digest, "thresholds": th.as_dict(), "summary": summary, "rows": rows}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())