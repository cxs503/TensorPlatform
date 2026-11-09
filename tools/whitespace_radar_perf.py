#!/usr/bin/env python3
"""Index performance + scale report for the whitespace radar.

Produces ``artifacts/whitespace_radar/perf_report.json`` with three
measurements that matter for the maturation item ("索引性能优化与多仓扩展"):

1. **cold build** vs **warm load** on the live pool (cache effect);
2. **query throughput** (topics/second) on the live pool;
3. **scaling** on synthetic multi-repo pools (N docs), so the cost of onboarding
   the other Tensor* repositories can be extrapolated instead of guessed.

Timings are wall clock and therefore live in an unsealed artifact; the radar's
sealed evidence never contains them (that is what keeps reruns byte-identical).
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core import Doc, Thresholds, analyze, load_pool, pool_digest  # noqa: E402
from tools.whitespace_radar_core.index import Bm25Index  # noqa: E402

POOL = os.path.join(REPO_ROOT, "data/whitespace_radar/pool/manifest.json")
BENCH = os.path.join(REPO_ROOT, "data/whitespace_radar/replay_bench.json")
OUT = os.path.join(REPO_ROOT, "artifacts/whitespace_radar/perf_report.json")


def synthetic_pool(n_docs: int) -> list:
    """Deterministic filler pool of ``n_docs`` module-like documents."""
    out = []
    for i in range(n_docs):
        out.append(
            Doc(
                doc_id=f"module:syn/repo{i % 7}/mod_{i:06d}.py",
                kind="module",
                title=f"mod_{i:06d}.py",
                text=(
                    f"module syn repo{i % 7} slot{i} handler{i * 3} 通道{i % 97} "
                    f"记录 编号{i} 覆盖 抽样 索引 预算 上限 复核 检验 归档"
                ),
                ref=f"syn/repo{i % 7}/mod_{i:06d}.py",
            )
        )
    return out


def main() -> int:
    report = {"pool_manifest": os.path.relpath(POOL, REPO_ROOT)}

    t0 = time.perf_counter()
    docs = load_pool(REPO_ROOT, POOL)
    digest = pool_digest(docs)
    t1 = time.perf_counter()
    idx = Bm25Index.build(docs)
    t2 = time.perf_counter()
    report["live_pool"] = {
        "n_docs": len(docs),
        "n_tokens": idx.as_manifest()["n_tokens"],
        "n_terms": len(idx.postings),
        "pool_load_s": round(t1 - t0, 4),
        "index_build_s": round(t2 - t1, 4),
        "docs_per_s": round(len(docs) / max(t2 - t1, 1e-9), 1),
    }

    blob = pickle.dumps(idx, protocol=4)
    t3 = time.perf_counter()
    _ = pickle.loads(blob)
    t4 = time.perf_counter()
    report["cache"] = {
        "pickle_bytes": len(blob),
        "dump_s": round(t3 - t3, 6),
        "load_s": round(t4 - t3, 4),
        "speedup_vs_rebuild": round((t2 - t1) / max(t4 - t3, 1e-9), 2),
    }

    topics = [
        "引导回路 评价牵引 派发 接线",
        "耐波 响应谱矩 频率窗 截断 回归护栏",
        "地幔对流谱元法并行求解",
        "提案去重门 跨组织 联邦学习 语义相似度",
    ]
    th = Thresholds()
    t5 = time.perf_counter()
    reps = 25
    for _ in range(reps):
        for t in topics:
            analyze(idx, t, thresholds=th, pool_digest=digest, as_of="perf")
    t6 = time.perf_counter()
    n_q = reps * len(topics)
    report["query"] = {
        "n_queries": n_q,
        "total_s": round(t6 - t5, 4),
        "ms_per_query": round((t6 - t5) / n_q * 1000, 3),
        "queries_per_s": round(n_q / max(t6 - t5, 1e-9), 1),
    }

    scaling = []
    for n in (500, 2000, 8000):
        syn = synthetic_pool(n)
        a = time.perf_counter()
        sidx = Bm25Index.build(syn)
        b = time.perf_counter()
        for t in topics:
            analyze(sidx, t, thresholds=th, pool_digest="syn", as_of="perf")
        c = time.perf_counter()
        scaling.append(
            {
                "n_docs": n,
                "build_s": round(b - a, 3),
                "docs_per_s": round(n / max(b - a, 1e-9), 1),
                "ms_per_query": round((c - b) / len(topics) * 1000, 3),
            }
        )
    report["scaling"] = scaling
    report["note"] = (
        "timings are wall clock, unsealed; the sealed radar/v1 evidence excludes them "
        "so that reruns stay byte identical"
    )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())