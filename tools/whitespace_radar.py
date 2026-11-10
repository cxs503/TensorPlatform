#!/usr/bin/env python3
"""whitespace_radar —— 空白域雷达 CLI（schema ``radar/v1``）。

    python tools/whitespace_radar.py --topic "some free text topic" --json-out /tmp/v.json

一次调用做三件事：
1. 从 pool manifest 载入生态工件（提案 / 文档 / 模块清单 / 战役导出），建 BM25 倒排索引；
2. 对输入主题做 ``covered | partial | blank`` 三态裁决，并把**实际执行的检索式**
   与逐词 df、逐文档 BM25 贡献写进证据；
3. 用内容派生的 ``as_of`` + 规范化 JSON + sha256 封存，重跑逐字节一致。

其它入口：
    --bench data/whitespace_radar/replay_bench.json   # 18 题回放基准
    --calibrate                                      # 阈值网格 + 准确性表
    --build-manifest --repo TensorPlatform=. [...]    # 重建多仓模块清单
    --drop-term X                                     # killer 测试：从检索式删词
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

if __package__ in (None, ""):  # allow ``python tools/whitespace_radar.py``
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.whitespace_radar_core import (  # noqa: E402
    SCHEMA,
    dedup_gate,
    Bm25Index,
    Thresholds,
    analyze,
    calibration_curve,
    check_compatible,
    dumps,
    load_bench,
    load_pin,
    load_pool,
    pin_report,
    passes_acceptance,
    pool_digest,
    replay,
    seal,
    triage,
    verify,
)
from tools.whitespace_radar_core.pool_build import (  # noqa: E402
    build_manifest,
    dump as dump_json,
    empty_campaign_export,
    manifest_digest,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_POOL = "data/whitespace_radar/pool/manifest.json"
DEFAULT_BENCH = "data/whitespace_radar/replay_bench.json"
DEFAULT_PIN = "data/whitespace_radar/pool/pin.json"
CACHE_DIR = ".cache/whitespace_radar"


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


def _load_index(
    pool_manifest: str,
    *,
    use_cache: bool = True,
    stats: Optional[Dict[str, Any]] = None,
    pin_path: Optional[str] = None,
) -> tuple[Bm25Index, List[Any], str, Dict[str, Any]]:
    """Build the (pinned) pool + index.

    Returns ``(index, docs, pool_digest, pin_drift)``.  ``pin_drift`` is empty
    when no pin is configured; otherwise it records which live documents were
    added / edited / deleted since the pool was frozen, so a replay can never
    quote an accuracy without saying which evidence snapshot it used.
    """
    t0 = time.perf_counter()
    resolved = _abs(pin_path or DEFAULT_PIN)
    pin = load_pin(resolved) if resolved and os.path.isfile(resolved) else None
    drift: Dict[str, Any] = {}
    if pin is not None:
        live = load_pool(REPO_ROOT, _abs(pool_manifest))
        drift = pin_report(live, pin)
    docs = load_pool(REPO_ROOT, _abs(pool_manifest), pin=pin)
    digest = pool_digest(docs)
    t1 = time.perf_counter()
    cache = os.path.join(REPO_ROOT, CACHE_DIR, f"{digest}.pkl")
    index: Optional[Bm25Index] = None
    if use_cache and os.path.isfile(cache):
        try:
            with open(cache, "rb") as fh:
                index = pickle.load(fh)
        except Exception:  # corrupt cache must never break a query
            index = None
    if index is None:
        index = Bm25Index.build(docs)
        if use_cache:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            tmp = cache + ".tmp"
            with open(tmp, "wb") as fh:
                pickle.dump(index, fh, protocol=4)
            os.replace(tmp, cache)
    t2 = time.perf_counter()
    if stats is not None:
        stats.update(
            {
                "pool_load_s": round(t1 - t0, 6),
                "index_build_s": round(t2 - t1, 6),
                "cache": "hit" if os.path.isfile(cache) else "miss",
                "n_docs": index.n_docs,
                "n_terms": len(index.postings),
                "n_tokens": index.as_manifest()["n_tokens"],
                "pool_digest": digest,
                "pin_drift": drift.get("drift", False),
            }
        )
    return index, docs, digest, drift


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    p = _abs(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")


def _emit(envelope: Dict[str, Any], json_out: Optional[str], quiet: bool) -> None:
    if json_out:
        path = _abs(json_out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(dumps(envelope))
        if not quiet:
            print(f"[radar] wrote {path} sha256={envelope['digest']['sha256']}")
    elif not quiet:
        print(dumps(envelope))


def cmd_query(args: argparse.Namespace) -> int:
    stats: Dict[str, Any] = {}
    index, docs, digest, _drift = _load_index(
        args.pool, use_cache=not args.no_cache, stats=stats, pin_path=args.pin
    )
    th = Thresholds(s_floor=args.s_floor) if args.s_floor else Thresholds()
    results = [
        analyze(
            index,
            topic,
            thresholds=th,
            pool_digest=digest,
            as_of=f"pool:{digest[:16]}",
            drop_terms=tuple(args.drop_term or ()),
        )
        for topic in args.topic
    ]
    if args.stats_out:
        _write_json(args.stats_out, dict(stats))
    if len(results) == 1 and not args.json_out_endswith_list:
        _emit(results[0], args.json_out, args.quiet)
        if not args.quiet:
            r = results[0]
            print(f"[radar] verdict={r['verdict']} ({triage(r['verdict'])})", file=sys.stderr)
    else:
        env = seal(
            {
                "results": results,
                "as_of": f"pool:{digest[:16]}",
                "pool_digest": digest,
                "index": index.as_manifest(),
            }
        )
        _emit(env, args.json_out, args.quiet)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    stats: Dict[str, Any] = {}
    index, docs, digest, drift = _load_index(
        args.pool, use_cache=not args.no_cache, stats=stats, pin_path=args.pin
    )
    bench_path = _abs(args.bench)
    bench = load_bench(bench_path)
    import hashlib as _hl
    bench_sha = _hl.sha256(open(bench_path, "rb").read()).hexdigest()
    th = Thresholds(s_floor=args.s_floor) if args.s_floor else Thresholds()
    report = replay(
        index,
        bench,
        thresholds=th,
        pool_digest=digest,
        as_of=f"pool:{digest[:16]}",
        pool_pin=drift,
    )
    report["acceptance"] = {
        "min_correct": report["n_topics"],
        "passed": passes_acceptance(report, require_all=True),
        "blank_zero_false_positive": report["blank_false_positive"] == 0,
    }
    if args.calibrate:
        report["calibration"] = calibration_curve(index, bench, pool_digest=digest)
    report["index"] = index.as_manifest()
    # Timings are deliberately NOT part of the sealed evidence: they are wall
    # clock measurements and would break byte-identical reruns.
    if args.stats_out:
        _write_json(args.stats_out, dict(stats))
    report["bench_sha256"] = bench_sha
    report["bench_path"] = os.path.relpath(bench_path, REPO_ROOT).replace(os.sep, "/")
    env = seal(report)
    _emit(env, args.json_out, args.quiet)
    if not args.quiet:
        print(
            f"[radar] bench {report['n_correct']}/{report['n_topics']} "
            f"acc={report['accuracy']:.3f} blank_fp={report['blank_false_positive']} "
            f"passed={report['acceptance']['passed']}",
            file=sys.stderr,
        )
        for m in report["mismatches"]:
            print(f"  MISS {m['id']}: {m['label']} -> {m['verdict']} score={m['top_score']}", file=sys.stderr)
    return 0


def _validate_query_file(payload: Dict[str, Any]) -> None:
    """Validate a ``radar-query/v1`` batch against the committed JSON schema."""
    if payload.get("schema") != "radar-query/v1":
        raise SystemExit(
            f"incompatible query schema {payload.get('schema')!r}; expected 'radar-query/v1'"
        )
    schema_path = os.path.join(REPO_ROOT, "schemas/whitespace-radar-query.v1.schema.json")
    try:
        import jsonschema  # optional dependency
    except Exception:  # pragma: no cover - minimal structural fallback
        if not payload.get("topics"):
            raise SystemExit("query file has no topics")
        return
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(payload, schema)


def cmd_query_json(args: argparse.Namespace) -> int:
    with open(_abs(args.query_json), encoding="utf-8") as fh:
        payload = json.load(fh)
    _validate_query_file(payload)
    index, _docs, digest, _drift = _load_index(
        args.pool, use_cache=not args.no_cache, pin_path=args.pin
    )
    th = Thresholds(s_floor=args.s_floor) if args.s_floor else Thresholds()
    results = []
    for item in payload["topics"]:
        env = analyze(
            index,
            item["topic"],
            thresholds=th,
            pool_digest=digest,
            as_of=f"pool:{digest[:16]}",
            drop_terms=tuple(args.drop_term or ()),
        )
        gate = dedup_gate(env)
        row = {"id": item.get("id", ""), "verdict": env, "gate": gate}
        if "label" in item:
            row["expected"] = item["label"]
            row["correct"] = env["verdict"] == item["label"]
        results.append(row)
    envelope = seal(
        {
            "source": payload.get("source", ""),
            "pool_digest": digest,
            "as_of": f"pool:{digest[:16]}",
            "results": results,
        }
    )
    _emit(envelope, args.json_out, args.quiet)
    return 0


def cmd_build_manifest(args: argparse.Namespace) -> int:
    repos = []
    for spec in args.repo or []:
        name, _, path = spec.partition("=")
        if not path:
            raise SystemExit(f"--repo expects NAME=PATH, got {spec!r}")
        repos.append((name, _abs(path)))
    manifest = build_manifest(
        repos,
        max_files=args.max_files,
        include=tuple(args.include or ()),
        exclude=tuple(args.exclude or ()),
    )
    out = _abs(args.manifest_out)
    dump_json(manifest, out)
    env = seal(
        {
            "manifest_path": os.path.relpath(out, REPO_ROOT).replace(os.sep, "/"),
            "manifest_digest": manifest_digest(manifest),
            "repos": [
                {"repo": r["repo"], "n_modules": len(r["modules"])} for r in manifest["repos"]
            ],
        }
    )
    _emit(env, args.json_out, args.quiet)
    return 0


def cmd_campaign_stub(args: argparse.Namespace) -> int:
    dump_json(empty_campaign_export(args.note), _abs(args.campaign_out))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    with open(_abs(args.verify), "r", encoding="utf-8") as fh:
        env = json.load(fh)
    try:
        check_compatible(env)
        ok = verify(env)
    except ValueError as exc:
        print(f"[radar] INCOMPATIBLE: {exc}", file=sys.stderr)
        return 2
    print(f"[radar] digest_ok={ok} schema={env.get('schema')} verdict={env.get('verdict')}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whitespace_radar",
        description="Pool coverage radar: covered | partial | blank with a reproducible zero-hit proof.",
    )
    p.add_argument("--topic", action="append", help="free-text topic (repeatable)")
    p.add_argument("--json-out", help="write sealed radar/v1 JSON here")
    p.add_argument("--pool", default=DEFAULT_POOL, help=f"pool manifest (default {DEFAULT_POOL})")
    p.add_argument("--bench", default=DEFAULT_BENCH, help="replay benchmark JSON")
    p.add_argument(
        "--pin",
        default=DEFAULT_PIN,
        help=f"frozen pool snapshot for replays (default {DEFAULT_PIN}; empty string = live pool)",
    )
    p.add_argument("--calibrate", action="store_true", help="also emit the threshold grid")
    p.add_argument("--s-floor", type=float, help="override the relevance floor")
    p.add_argument("--drop-term", action="append", help="remove a term from the query (killer test)")
    p.add_argument("--no-cache", action="store_true", help="do not read/write the index cache")
    p.add_argument("--quiet", action="store_true")
    p.add_argument(
        "--stats-out",
        help="write unsealed timing/cache statistics here (excluded from the digest)",
    )
    p.add_argument("--build-manifest", action="store_true")
    p.add_argument("--repo", action="append", help="NAME=PATH for --build-manifest (repeatable)")
    p.add_argument("--max-files", type=int, default=5000)
    p.add_argument(
        "--include",
        action="append",
        help="repo-relative path prefix to index as capability evidence (repeatable)",
    )
    p.add_argument(
        "--exclude", action="append", help="repo-relative path prefix to skip (repeatable)"
    )
    p.add_argument(
        "--manifest-out",
        default="data/whitespace_radar/pool/module_manifest.json",
    )
    p.add_argument("--campaign-stub", action="store_true", help="write an empty campaign export")
    p.add_argument("--campaign-out", default="data/whitespace_radar/pool/campaign_export.json")
    p.add_argument("--note", default="no live campaign DB in this deployment")
    p.add_argument("--verify", help="verify a previously written radar/v1 envelope")
    p.add_argument(
        "--query-json",
        help="batch query file (radar-query/v1); emits sealed results + gate decisions",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.json_out_endswith_list = False
    if args.verify:
        return cmd_verify(args)
    if args.build_manifest:
        return cmd_build_manifest(args)
    if args.campaign_stub:
        return cmd_campaign_stub(args)
    if args.query_json:
        return cmd_query_json(args)
    if args.bench and os.path.isfile(_abs(args.bench)) and not args.topic:
        return cmd_bench(args)
    if not args.topic:
        raise SystemExit("nothing to do: pass --topic, --bench, --build-manifest or --verify")
    return cmd_query(args)


if __name__ == "__main__":
    sys.exit(main())