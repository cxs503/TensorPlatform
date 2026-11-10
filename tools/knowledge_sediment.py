#!/usr/bin/env python3
"""knowledge_sediment.py — 四契约知识沉淀管线的命令行入口。

子命令::

    scan     扫 ``work/m<id>/`` 报告工件 → 抽四契约 → 入库（默认 dry-run）
    recall   对独立标注集算召回率/虚构槽位（判据 a）
    bench    合成 1000 条知识压测检索时延（判据 f）
    search   就地检索（判据 d）
    stats    库概况（判据 b：schema 完整率）
    rejected 拒绝入库账（判据 e：为什么没收）

例::

    python3 tools/knowledge_sediment.py scan --root /root/tp-agent/work --store artifacts/knowledge_sediment/store
    python3 tools/knowledge_sediment.py recall --labels artifacts/knowledge_sediment/reference_labels.json
    python3 tools/knowledge_sediment.py bench --n 1000 --rounds 50

纪律：本工具只读 mission 工件、只写 ``--store`` 目录与 ``--out`` 报告；
默认 dry-run，误触不会污染知识库。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.knowledge_sediment import KnowledgeStore, scan_root  # noqa: E402
from services.knowledge_sediment.contracts import content_sha256  # noqa: E402
from services.knowledge_sediment.extractor import load_domain_map  # noqa: E402
from services.knowledge_sediment.pipeline import (  # noqa: E402
    load_labels,
    recall_against_reference,
    write_report,
)
from services.knowledge_sediment.store import store_dir  # noqa: E402


def _emit(doc: dict, out: str | None) -> None:
    text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"[knowledge-sediment] 报告已落盘: {out}")
    else:
        print(text)


def cmd_scan(args: argparse.Namespace) -> int:
    store = KnowledgeStore(args.store)
    summary = scan_root(
        root=args.root,
        store=store,
        limit=args.limit,
        domain_map=load_domain_map(args.domain_map),
        dry_run=not args.write,
        pattern=args.pattern,
    )
    if args.out:
        write_report(args.out, summary)
        print(f"[knowledge-sediment] 扫描报告: {args.out}")
    printed = {k: v for k, v in summary.items() if k != "rows"}
    _emit(printed, None if not args.out else None)
    if args.show_rows:
        for row in summary["rows"]:
            print(f"  {row['mission_id']:>8s} {row['status']:<14s} {(row['reason'] or ''):<22s} {','.join(row['filled'])}")
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    store = KnowledgeStore(args.store)
    labels = load_labels(args.labels)
    summary = scan_root(root=args.root, store=store, domain_map=load_domain_map(args.domain_map), dry_run=True)
    result = recall_against_reference(summary["rows"], labels)
    result["root"] = summary["root"]
    result["missions_scanned"] = summary["missions_scanned"]
    result["contamination"] = summary["contamination"]
    if args.out:
        write_report(args.out, result)
    _emit(result, None if args.out else None)
    return 0 if result["recall"] >= args.threshold else 1


def cmd_bench(args: argparse.Namespace) -> int:
    """判据 f：1000 条知识规模下的检索响应（合成语料，语料即随机不落库）。"""
    rng = random.Random(args.seed)
    store = KnowledgeStore(args.store)
    batch: list[dict] = []
    pool = ["boundary", "convergence", "verification", "benchmark", "energy", "coupling", "spectrum", "index"]
    for i in range(args.n):
        words = " ".join(rng.sample(pool, k=3))
        contracts = {
            "problem": f"synthetic case {i}: {words}",
            "method": f"synthetic method {i}: {words} pipeline",
            "evidence": f"synthetic evidence {i}: residual 1e-{rng.randint(3, 12)}, 42 samples",
            "limitations": f"synthetic limitation {i}: {words} outside scope",
        }
        sha = content_sha256(f"bench{i}", contracts)
        batch.append(
            {
                "id": f"ke-{sha[:16]}",
                "mission_id": f"bench{i}",
                "domain": rng.choice(["alpha", "beta", "gamma"]),
                "sha256": sha,
                "tags": words.split(),
                "related": [],
                "source": {"mission_dir": "synthetic"},
                "created_at": "2026-11-11T00:00:00+00:00",
                **contracts,
            }
        )
    store.add_many(batch)
    assert store.count() == args.n, f"bench 库应含 {args.n} 条，实际 {store.count()}"
    timings: list[float] = []
    queries = ["convergence", "residual", "synthetic method 7", "spectrum coupling", "zzz-no-hit"]
    for r in range(args.rounds):
        q = queries[r % len(queries)]
        timings.append(store.search(q=q, limit=args.limit)["took_ms"])
    timings.sort()
    stats = {
        "n_entries": store.count(),
        "rounds": args.rounds,
        "limit": args.limit,
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 3),
        "max_ms": round(max(timings), 3),
        "threshold_ms": args.threshold,
        "pass": round(max(timings), 3) <= args.threshold,
    }
    if args.out:
        write_report(args.out, stats)
    _emit(stats, None if args.out else None)
    return 0 if stats["pass"] else 1


def cmd_search(args: argparse.Namespace) -> int:
    store = KnowledgeStore(args.store)
    _emit(store.search(q=args.q, domain=args.domain, limit=args.limit), None)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _emit(KnowledgeStore(args.store).stats(), None)
    return 0


def cmd_rejected(args: argparse.Namespace) -> int:
    rows = KnowledgeStore(args.store).rejected(limit=args.limit)
    _emit({"count": len(rows), "items": rows}, None)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="四契约知识沉淀管线 CLI")
    p.add_argument("--store", default=None, help=f"知识库目录（默认 {store_dir()}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫报告工件并入库")
    s.add_argument("--root", default="/root/tp-agent/work")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--pattern", default=None, help="mission 目录名正则（默认 ^m\\d+$）")
    s.add_argument("--domain-map", default=None, help="标签→领域 映射 JSON 路径")
    s.add_argument("--write", action="store_true", help="真正写库（默认 dry-run）")
    s.add_argument("--out", default=None, help="扫描报告 JSON 落盘路径")
    s.add_argument("--show-rows", action="store_true")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("recall", help="对独立标注集算召回率")
    r.add_argument("--labels", default=str(REPO_ROOT / "artifacts/knowledge_sediment/reference_labels.json"))
    r.add_argument("--root", default="/root/tp-agent/work")
    r.add_argument("--domain-map", default=None)
    r.add_argument("--threshold", type=float, default=0.8)
    r.add_argument("--out", default=None)
    r.set_defaults(func=cmd_recall)

    b = sub.add_parser("bench", help="合成语料压测检索时延")
    b.add_argument("--n", type=int, default=1000)
    b.add_argument("--rounds", type=int, default=50)
    b.add_argument("--limit", type=int, default=20)
    b.add_argument("--seed", type=int, default=20261111)
    b.add_argument("--threshold", type=float, default=100.0)
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_bench)

    q = sub.add_parser("search", help="检索")
    q.add_argument("--q", default="")
    q.add_argument("--domain", default=None)
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(func=cmd_search)

    t = sub.add_parser("stats", help="库概况")
    t.set_defaults(func=cmd_stats)

    j = sub.add_parser("rejected", help="拒绝入库账")
    j.add_argument("--limit", type=int, default=50)
    j.set_defaults(func=cmd_rejected)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "store", None) is None:
        args.store = str(REPO_ROOT / "artifacts/knowledge_sediment/store")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())