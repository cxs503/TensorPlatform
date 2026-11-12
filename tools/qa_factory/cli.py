#!/usr/bin/env python3
"""QA 产线 · 命令行（全量扫描 / 抽样 / 深检派单 / 一致性 / 清账 / daemon）。

  python3 -m tools.qa_factory scan --out artifacts/qa_factory/round1           # 全量 pending 三查
  python3 -m tools.qa_factory sample --scan artifacts/qa_factory/round1/latest.json \\
        --per-stratum 15 --out artifacts/qa_factory/round1/sample.json
  python3 -m tools.qa_factory dispatch --sample artifacts/qa_factory/round1/sample.json --dry-run
  python3 -m tools.qa_factory consistency --scan ... --deep docs/qa_factory/deep_review_round1.jsonl \\
        --out artifacts/qa_factory/round1/consistency.json
  python3 -m tools.qa_factory clear --batch 2000 --consistency artifacts/qa_factory/round1/consistency.json
  python3 -m tools.qa_factory daemon --once --batches 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.qa_factory import consistency as cons  # noqa: E402
from tools.qa_factory import dispatch as disp  # noqa: E402
from tools.qa_factory.daemon import (check_schema, load_consistency_gate, make_roots,  # noqa: E402
                                     cycle as daemon_cycle, log as daemon_log, DEFAULT_CONSISTENCY)
from tools.qa_factory.gate import (EvidenceRoots, corpus_sha256, gate_report,  # noqa: E402
                                   summarize)
from tools.qa_factory.source import DB  # noqa: E402

WORK_ROOT = Path(os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def db_from_args(a: argparse.Namespace) -> DB:
    return DB(host=a.host, user=a.user, db=a.dbname,
              password=a.password or os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD"))


def _gate_rows(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """子进程工作单元：一批行的三查（纯函数 + 只读缓存集，进程安全）。"""
    rows, reports, missions = args["rows"], set(args["reports"]), set(args["missions"])
    out = []
    for r in rows:
        work = (WORK_ROOT / f"m{r['mission_id']}") if r["mission_id"] else None
        roots = EvidenceRoots(work_dir=work,
                              report_exists=lambda rid, _s=reports: rid in _s,
                              mission_exists=lambda mid, _s=missions: mid in _s)
        v = gate_report(report_id=r["id"], markdown=r["markdown"] or "", title=r["title"] or "",
                        scenario_key=r["scenario_key"] or "", figures=r["figures"], roots=roots)
        out.append(v.to_dict())
    return out


def _scan_rows_concurrently(rows: List[Dict[str, Any]], cache: Dict[str, Any], jobs: int,
                            batch: int) -> List[Dict[str, Any]]:
    if jobs <= 1 or len(rows) <= 50:
        return _gate_rows({"rows": rows, "reports": cache["reports"], "missions": cache["missions"]})
    import concurrent.futures as cf
    chunks = [rows[i:i + batch] for i in range(0, len(rows), batch)]
    out: List[Dict[str, Any]] = []
    with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(_gate_rows, {"rows": c, "reports": cache["reports"],
                                       "missions": cache["missions"]}) for c in chunks]
        for f in futs:
            out.extend(f.result())
    return out


def cmd_scan(a: argparse.Namespace) -> int:
    """增量扫描：每轮取 batch 篇（默认只看未分档），边扫边写 qa_machine_* 审计列。

    --time-budget 让单次调用自我限时（控制面 shell 有 120s 上限，重活按批切片跑），
    未扫完时退出码 0 并打印 remaining 提示；反复调用直到 0 篇。
    """
    db = db_from_args(a)
    base_where = a.where or "r.qa_status = 'pending'"
    if a.only_untiered:
        base_where += " AND r.qa_machine_tier IS NULL"
    cache = {"reports": db.id_set("tensor_reports"), "missions": db.id_set("tensor_dev_missions")}
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    verdicts: List[Dict[str, Any]] = []
    scanned = 0
    while True:
        rows = db.load_reports(where=base_where, order="r.created_at ASC, r.id ASC", limit=a.batch)
        if not rows:
            break
        verdicts.extend(_scan_rows_concurrently(rows, cache, a.jobs, a.jobs_batch))
        scanned += len(rows)
        if a.write_tiers:
            db.record_tiers(verdicts[-len(rows):])
        if a.progress and scanned % a.progress < a.batch:
            print(f"  scanned {scanned} ({time.time() - t0:.1f}s)", file=sys.stderr)
        if a.time_budget and (time.time() - t0) >= a.time_budget:
            break
        if a.limit and scanned >= a.limit:
            break

    class _V:
        def __init__(self, d: Dict[str, Any]):
            self.tier, self.checks, self.detail = d["tier"], d["checks"], d["detail"]

    stats = summarize([_V(v) for v in verdicts])
    payload = {
        "generated_at_utc": _now(), "scope_where": base_where, "n": len(verdicts),
        "corpus_sha256": corpus_sha256(rows) if False else "",
        "gate_version": stats["gate_version"], "elapsed_s": round(time.time() - t0, 2),
        "stats": stats,
        "counts_by_check": {name: _count_statuses(verdicts, name)
                            for name in ("structure", "pointers", "numbers")},
        "reports": verdicts,
    }
    (outdir / "chunks").mkdir(parents=True, exist_ok=True)
    (outdir / "chunks" / f"chunk_{payload['generated_at_utc']}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    remaining = db.load_reports(where=base_where, order="r.id ASC", limit=1) if a.only_untiered else []
    print(_render_scan_md(payload))
    print(f"本轮扫描 {scanned} 篇，用时 {payload['elapsed_s']}s；"
          f"剩余未分档 {'有' if remaining else '无'}")
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    """把库里持久化的分档审计读回成一个 latest.json（抽样/一致性/报告的单一事实源）。"""
    db = db_from_args(a)
    rows = db.load_tiered(where=a.where, limit=a.limit)
    class _V:
        def __init__(self, d: Dict[str, Any]):
            self.tier, self.checks = d["tier"], d["checks"] or {}
            self.detail = d.get("detail") or {}
    stats = summarize([_V(r) for r in rows])
    payload = {"generated_at_utc": _now(), "n": len(rows), "gate_version": stats["gate_version"],
               "stats": stats, "source": "tensor_reports.qa_machine_audit",
               "counts_by_check": {name: _count_statuses(rows, name)
                                   for name in ("structure", "pointers", "numbers")},
               "reports": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    Path(a.out).with_suffix(".md").write_text(_render_scan_md(payload), encoding="utf-8")
    print(json.dumps({"n": len(rows), "tiers": stats["tiers"],
                      "reasons": dict(list(stats["reasons"].items())[:8])}, ensure_ascii=False))
    return 0


def _count_statuses(verdicts: List[Dict[str, Any]], name: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in verdicts:
        st = v["checks"][name]["status"]
        out[st] = out.get(st, 0) + 1
    return out


def _render_scan_md(payload: Dict[str, Any]) -> str:
    s = payload["stats"]
    total = s["total"] or 1
    scope = payload.get("scope_where") or payload.get("source") or "all"
    L = [f"# QA 产线·机器三查分档（{scope}）", "",
         f"- 生成：{payload['generated_at_utc']}，耗时 {payload.get('elapsed_s', 'n/a')}s，"
         f"语料 sha256 `{(payload.get('corpus_sha256') or 'n/a')[:16]}`（{payload['n']} 篇）",
         f"- 门版本：{payload['gate_version']}；判据：docs/qa_factory/PREREG_QA_FACTORY.md", "",
         "| 档 | 篇数 | 占比 |", "|---|---|---|"]
    for t, n in s["tiers"].items():
        L.append(f"| {t} | {n} | {n / total:.1%} |")
    L += ["", "| 查 | ok | flag | fail |", "|---|---|---|---|"]
    for name, d in payload["counts_by_check"].items():
        L.append(f"| {name} | {d.get('ok', 0)} | {d.get('flag', 0)} | {d.get('fail', 0)} |")
    L += ["", "## 非 ok 原因频次（优化靶子）", "", "| 原因 | 篇数 |", "|---|---|"]
    for k, v in list(s["reasons"].items())[:20]:
        L.append(f"| `{k}` | {v} |")
    L += ["", "## 报告类型分层", "", "| kind | pass | machine-flag | suspect |", "|---|---|---|---|"]
    for kind, d in s["by_kind"].items():
        L.append(f"| {kind} | {d.get('pass', 0)} | {d.get('machine-flag', 0)} | {d.get('suspect', 0)} |")
    L.append("")
    return "\n".join(L)


def cmd_sample(a: argparse.Namespace) -> int:
    tiers = cons.load_machine_tiers(Path(a.scan))
    picks = cons.stratified_sample(tiers, per_stratum=a.per_stratum, seed=a.seed)
    out = {"seed": a.seed, "per_stratum": a.per_stratum, "n": len(picks),
           "strata_population": {t: sum(1 for v in tiers.values() if v == t) for t in cons.TIERS},
           "picks": picks, "source_scan": str(a.scan)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n", "strata_population", "seed")}, ensure_ascii=False))
    return 0


def cmd_consistency(a: argparse.Namespace) -> int:
    machine = cons.load_machine_tiers(Path(a.scan))
    deep = cons.load_deep_reviews(Path(a.deep))
    res = cons.evaluate_consistency(machine, deep, gate_ref=a.gate_ref or _now(),
                                    corpus_sha=a.corpus_sha or "")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = Path(a.out).with_suffix(".md")
    md_path.write_text(cons.render_markdown(res), encoding="utf-8")
    print(cons.render_markdown(res))
    return 0 if res["gate_passed"] else 2


def cmd_clear(a: argparse.Namespace) -> int:
    db = db_from_args(a)
    info = load_consistency_gate(Path(a.consistency), a.max_age)
    if not (info["available"] and info["fresh"] and info["gate_passed"]) and not a.force:
        print(json.dumps({"cleared": 0, "refused": True, "gate": info}, ensure_ascii=False))
        return 3
    n, ids = db.clear_machine_pass(a.batch, gate_ref=info["ref"] or "forced")
    res = {"cleared": n, "gate": info,
           "refused": False, "sample_ids": ids[:10],
           "after_counts": db.counts()}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps({**res, "ids": ids}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    db = db_from_args(a)
    ok, missing = check_schema(db)
    print(json.dumps({"schema_ok": ok, "missing_columns": missing,
                      "counts": db.counts() if ok else None}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_dispatch(a: argparse.Namespace) -> int:
    sample = json.loads(Path(a.sample).read_text(encoding="utf-8"))
    out = disp.build_review_missions(sample["picks"], batch_size=a.batch_size,
                                     max_missions=a.max_missions)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"missions": len(out["missions"]), "dry_run": a.dry_run,
                      "batches": [m["reports"] for m in out["missions"]]},
                     ensure_ascii=False))
    if a.dry_run:
        return 0
    return disp.submit(out["missions"], api_base=a.api_base)


def cmd_daemon(a: argparse.Namespace) -> int:
    from tools.qa_factory import daemon as dmod
    return dmod.main(["--once"] + ([] if a.batches is None else ["--batches", str(a.batches)])
                     + ["--batch", str(a.batch), "--clear-batch", str(a.clear_batch)])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="QA 产线 CLI（FEAT-QA-FACTORY）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _db_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default=os.environ.get("TP_PG_HOST", ""))
        p.add_argument("--user", default=os.environ.get("TP_PG_USER", "postgres"))
        p.add_argument("--dbname", default=os.environ.get("TP_PG_DB", "tensorplatform"))
        p.add_argument("--password", default=None)

    p = sub.add_parser("scan", help="全量/条件扫描三查分档")
    _db_args(p)
    p.add_argument("--where", default="", help="SQL 条件（默认 pending）")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--batch", type=int, default=1500, help="单批读入篇数")
    p.add_argument("--jobs", type=int, default=8, help="并行进程数（纯函数三查，可线性扩）")
    p.add_argument("--jobs-batch", type=int, default=100, help="并行分块大小")
    p.add_argument("--time-budget", type=int, default=0, help="单次调用自我限时（秒）")
    p.add_argument("--only-untiered", action="store_true", help="只扫 qa_machine_tier IS NULL（增量）")
    p.add_argument("--out", default="artifacts/qa_factory/round1")
    p.add_argument("--progress", type=int, default=500)
    p.add_argument("--write-tiers", action="store_true", help="同时写 qa_machine_* 审计列")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export", help="从库读回分档结果（latest.json）")
    _db_args(p)
    p.add_argument("--where", default="TRUE")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default="artifacts/qa_factory/round1/latest.json")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("sample", help="分层抽样")
    p.add_argument("--scan", required=True)
    p.add_argument("--per-stratum", type=int, default=15)
    p.add_argument("--seed", default="qa-factory-r1")
    p.add_argument("--out", default="artifacts/qa_factory/round1/sample.json")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("consistency", help="机器判 vs 深检判一致性裁决")
    p.add_argument("--scan", required=True)
    p.add_argument("--deep", required=True)
    p.add_argument("--gate-ref", default="")
    p.add_argument("--corpus-sha", default="")
    p.add_argument("--out", default="artifacts/qa_factory/round1/consistency.json")
    p.set_defaults(func=cmd_consistency)

    p = sub.add_parser("clear", help="machine-pass 批清 qa_status")
    _db_args(p)
    p.add_argument("--batch", type=int, default=2000)
    p.add_argument("--consistency", default=str(DEFAULT_CONSISTENCY))
    p.add_argument("--max-age", type=int, default=86400)
    p.add_argument("--force", action="store_true", help="跳过一致性门（须在报告里声明理由）")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("status", help="schema/分档计数巡检")
    _db_args(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("dispatch", help="深检派单（复用 factory 工序04 独立评审模式）")
    p.add_argument("--sample", required=True)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--max-missions", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"))
    p.add_argument("--out", default="artifacts/qa_factory/round1/dispatch.json")
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("daemon", help="常驻 daemon（flock + 心跳）")
    p.add_argument("--batches", type=int, default=1)
    p.add_argument("--batch", type=int, default=500)
    p.add_argument("--clear-batch", type=int, default=2000)
    p.set_defaults(func=cmd_daemon)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())