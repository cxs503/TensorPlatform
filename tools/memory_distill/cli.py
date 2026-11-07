"""memory_distill CLI：distill → gate → promote，附带抽样与验收裁决。

用法（全部在 worktree 内，产物落 artifacts/ 与 docs/）：
  python -m tools.memory_distill.cli distill --reports R.jsonl --shadow S.json \
      --pool artifacts/memory_distill/candidate_pool.jsonl \
      --metrics artifacts/memory_distill/metrics.json --max-new 40
  python -m tools.memory_distill.cli sample --pool P.jsonl --k 12 --seed 7 \
      --out artifacts/memory_distill/sampling_sheet.json
  python -m tools.memory_distill.cli score --sheet S.json \
      --adjudicated A.json --out artifacts/memory_distill/sampling_result.json
  python -m tools.memory_distill.cli promote --pool P.jsonl --bootstrap B.md \
      --date-tag 2026-11-07 --out artifacts/memory_distill/bootstrap_synced.md
  python -m tools.memory_distill.cli export-reports --limit 400 --out R.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Sequence

from .distill import derive_candidates
from .ground import ground_pool
from .gate import GateConfig, gate_candidate
from .promote import promote, read_playbook_lines
from .sources import (
    export_reports_from_pg,
    load_reports,
    load_shadow_dispositions,
    write_reports_snapshot,
)
from .store import AppendOnlyStore

MIN_CANDIDATES = 10  # 战役判据：7 天内自动产出 >=10 条候选判例
MIN_PASS_RATE = 0.60  # 战役判据：人工抽样合格率 >=60%


def _dump(path: str, obj: object) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return path


def cmd_distill(args: argparse.Namespace) -> dict:
    reports = load_reports(args.reports) if args.reports else []
    shadow = load_shadow_dispositions(args.shadow) if args.shadow else []
    cands = derive_candidates(reports, shadow, max_len=args.max_len)

    store = AppendOnlyStore(args.pool)
    existing = store.lessons()
    pool_lessons = list(existing)

    passed, decisions, funnel = [], [], {}
    for cand in cands:
        body = f"- {cand.lesson}"
        dec = gate_candidate(body, existing=pool_lessons, config=GateConfig(max_len=args.max_len))
        funnel[dec.code] = funnel.get(dec.code, 0) + 1
        if dec.passed:
            passed.append(cand)
            decisions.append(dec.as_dict())  # 只与采纳项对齐（审计链不能错位）
            pool_lessons.append(body)
        if len(passed) >= args.max_new:
            break

    write_stats = store.append_candidates(passed, decisions)

    metrics = {
        "inputs": {
            "reports": len(reports),
            "shadow_dispositions": len(shadow),
            "reports_path": args.reports,
            "shadow_path": args.shadow,
        },
        "distilled_candidates": len(cands),
        "gate_funnel": funnel,
        "accepted_this_run": len(passed),
        "pool": {
            "path": args.pool,
            "appended": write_stats["appended"],
            "skipped_dup": write_stats["skipped_dup"],
            "total": len(store),
            "digest": store.digest(),
        },
        "acceptance": {
            "min_candidates": MIN_CANDIDATES,
            "candidates_ok": len(store) >= MIN_CANDIDATES,
            "pool_total": len(store),
        },
    }
    _dump(args.metrics, metrics)
    return metrics


def cmd_export_reports(args: argparse.Namespace) -> dict:
    rows = export_reports_from_pg(limit=args.limit, md_cap=args.md_cap)
    write_reports_snapshot(rows, args.out)
    return {"rows": len(rows), "out": args.out}


def cmd_sample(args: argparse.Namespace) -> dict:
    store = AppendOnlyStore(args.pool)
    records = store.load()
    rng = random.Random(args.seed)
    k = min(args.k, len(records))
    picked = rng.sample(range(len(records)), k) if k else []
    sheet = {
        "pool": args.pool,
        "pool_total": len(records),
        "seed": args.seed,
        "k": k,
        "items": [
            {
                "idx": i,
                "candidate_id": records[i].get("candidate_id"),
                "source_ref": records[i].get("source_ref"),
                "lesson": records[i].get("lesson"),
                "citations": (records[i].get("citations") or {}),
                "verdict": "",  # 人工/评审席填写：pass | fail
                "reason": "",
            }
            for i in sorted(picked)
        ],
    }
    _dump(args.out, sheet)
    return {"sampled": k, "out": args.out}


def cmd_score(args: argparse.Namespace) -> dict:
    with open(args.sheet, encoding="utf-8") as f:
        sheet = json.load(f)
    labels = {}
    if args.adjudicated and os.path.exists(args.adjudicated):
        with open(args.adjudicated, encoding="utf-8") as f:
            adj = json.load(f)
        for item in adj.get("items", []):
            labels[item.get("candidate_id")] = (item.get("verdict", ""), item.get("reason", ""))
    judged = [(i, labels.get(i.get("candidate_id"), ("", ""))) for i in sheet.get("items", [])]
    n = sum(1 for _, (v, _r) in judged if v in ("pass", "fail"))
    passed = sum(1 for _, (v, _r) in judged if v == "pass")
    rate = (passed / n) if n else 0.0
    result = {
        "pool": sheet.get("pool"),
        "sampled": len(sheet.get("items", [])),
        "adjudicated": n,
        "passed": passed,
        "pass_rate": round(rate, 4),
        "threshold": MIN_PASS_RATE,
        "pass_rate_ok": n > 0 and rate >= MIN_PASS_RATE,
        "items": [
            {
                "candidate_id": i.get("candidate_id"),
                "lesson": i.get("lesson"),
                "verdict": v,
                "reason": r,
            }
            for i, (v, r) in judged
        ],
    }
    _dump(args.out, result)
    return result


def cmd_promote(args: argparse.Namespace) -> dict:
    with open(args.bootstrap, encoding="utf-8") as f:
        text = f.read()
    store = AppendOnlyStore(args.pool)
    records = store.load()
    res = promote(records, text, date_tag=args.date_tag, limit=args.limit)
    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(res.new_text)
    if args.apply:
        with open(args.bootstrap, "w", encoding="utf-8") as f:
            f.write(res.new_text)
    summary = {
        "bootstrap": args.bootstrap,
        "out": out_path,
        "applied": bool(args.apply),
        "appended": res.appended,
        "skipped": res.skipped,
        "audit": res.audit,
        "insert_offset": res.insert_offset,
        "prefix_sha256": res.prefix_sha256,
        "suffix_sha256": res.suffix_sha256,
        "playbook_lines_before": len(read_playbook_lines(text)),
        "playbook_lines_after": len(read_playbook_lines(res.new_text)),
    }
    _dump(args.audit_out or os.path.join(os.path.dirname(out_path), "promote_audit.json"), summary)
    return summary


def cmd_ground(args: argparse.Namespace) -> dict:
    """引用项可核性：判例里的数字/路径必须能在源报告原文里找到（防幻觉）。"""
    reports = load_reports(args.reports) if args.reports else []
    by_mission: dict[str, str] = {}
    for r in reports:  # 同一 mission 可能有多份报告：合并核对，避免"拿错源"
        key = str(r.get("mission_id") or "")
        body = "\n".join(
            str(r.get(k) or "") for k in ("title", "summary", "markdown")
        )
        by_mission[key] = (by_mission.get(key, "") + "\n" + body).strip()
    store = AppendOnlyStore(args.pool)
    result = ground_pool(store.load(), by_mission)
    _dump(args.out, result)
    return result["summary"]


def cmd_acceptance(args: argparse.Namespace) -> dict:
    """机器裁：候选数 >=10 且抽样合格率 >=60% 才算验收通过。"""
    with open(args.metrics, encoding="utf-8") as f:
        metrics = json.load(f)
    sampling = {}
    if args.sampling and os.path.exists(args.sampling):
        with open(args.sampling, encoding="utf-8") as f:
            sampling = json.load(f)
    total = metrics.get("pool", {}).get("total", 0)
    rate = sampling.get("pass_rate", 0.0)
    adjudicated = sampling.get("adjudicated", 0)
    verdict = {
        "C1_candidates_ge_10": {"value": total, "threshold": MIN_CANDIDATES, "ok": total >= MIN_CANDIDATES},
        "C2_sample_pass_rate_ge_60pct": {
            "value": rate,
            "threshold": MIN_PASS_RATE,
            "adjudicated": adjudicated,
            "ok": adjudicated > 0 and rate >= MIN_PASS_RATE,
        },
        "all_ok": total >= MIN_CANDIDATES and adjudicated > 0 and rate >= MIN_PASS_RATE,
        "paths": {
            "metrics": args.metrics,
            "pool": metrics.get("pool", {}).get("path"),
            "sampling": args.sampling,
        },
    }
    _dump(args.out, verdict)
    return verdict


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory_distill", description="协调者记忆蒸馏（判例候选池）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("distill", help="报告+shadow → 候选判例池")
    d.add_argument("--reports", default="")
    d.add_argument("--shadow", default="")
    d.add_argument("--pool", required=True)
    d.add_argument("--metrics", required=True)
    d.add_argument("--max-new", type=int, default=40)
    d.add_argument("--max-len", type=int, default=120)
    d.set_defaults(func=cmd_distill)

    e = sub.add_parser("export-reports", help="只读导出 succeeded 报告快照")
    e.add_argument("--limit", type=int, default=400)
    e.add_argument("--md-cap", type=int, default=4000)
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export_reports)

    s = sub.add_parser("sample", help="抽样待审")
    s.add_argument("--pool", required=True)
    s.add_argument("--k", type=int, default=12)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_sample)

    sc = sub.add_parser("score", help="按裁决表算合格率")
    sc.add_argument("--sheet", required=True)
    sc.add_argument("--adjudicated", default="")
    sc.add_argument("--out", required=True)
    sc.set_defaults(func=cmd_score)

    pr = sub.add_parser("promote", help="append-only 同步 bootstrap PLAYBOOK 节")
    pr.add_argument("--pool", required=True)
    pr.add_argument("--bootstrap", required=True)
    pr.add_argument("--date-tag", default="")
    pr.add_argument("--limit", type=int, default=12)
    pr.add_argument("--out", required=True)
    pr.add_argument("--audit-out", default="")
    pr.add_argument("--apply", action="store_true")
    pr.set_defaults(func=cmd_promote)

    g = sub.add_parser("ground", help="引用项可核性核对（防幻觉）")
    g.add_argument("--pool", required=True)
    g.add_argument("--reports", required=True)
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_ground)

    a = sub.add_parser("acceptance", help="机器裁验收")
    a.add_argument("--metrics", required=True)
    a.add_argument("--sampling", default="")
    a.add_argument("--out", required=True)
    a.set_defaults(func=cmd_acceptance)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = args.func(args)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())