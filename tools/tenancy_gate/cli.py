#!/usr/bin/env python3
"""命令行入口：把三段门接到派单/收单/推送流水线上。

    python3 -m tools.tenancy_gate.cli lease-check  --active leases.json --candidate cand.json
    python3 -m tools.tenancy_gate.cli intake       --finish finish.json --peers peers.json
    python3 -m tools.tenancy_gate.cli rehearse      --repo /path/to/repo --ours agent/m1 --theirs main
    python3 -m tools.tenancy_gate.cli rehearse-batch --repo /path/to/repo --pairs pairs.json
    python3 -m tools.tenancy_gate.cli query         --kind intake_matrix --repo TenantA
    python3 -m tools.tenancy_gate.cli verify
    python3 -m tools.tenancy_gate.cli scan

退出码即门裁决：0 = 放行，2 = 拒绝（冲突或 unknown，fail-closed），1 = 用法/输入错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .intake import conflict_matrix
from .lease import check_lease
from .ledger import (
    append_events,
    make_event,
    query_events,
    read_events,
    record_intake,
    record_rehearsal,
    verify_chain,
)
from .model import FinishRecord, PathLease
from .rehearsal import rehearse_pair, rehearse_pairs
from .semantics import load_denylist, scan_tree

EXIT_ADMIT, EXIT_ERROR, EXIT_BLOCK = 0, 1, 2


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tenancy_gate", description="多租户并发写冲突检测与合并预演门")
    parser.add_argument("--config", help="配置文件路径（缺省用内置默认值）")
    parser.add_argument("--ledger", help="台账 JSONL 路径（缺省取配置）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lease-check", help="派单前路径预留判定")
    p.add_argument("--active", required=True, help="在租租约列表 JSON")
    p.add_argument("--candidate", required=True, help="待派发租约 JSON")
    p.add_argument("--now", help="判定时刻（ISO-8601，缺省取 candidate.ts）")

    p = sub.add_parser("intake", help="收单冲突矩阵")
    p.add_argument("--finish", required=True)
    p.add_argument("--peers", required=True)
    p.add_argument("--now")

    p = sub.add_parser("rehearse", help="两分支三方合并 dry-run")
    p.add_argument("--repo", required=True)
    p.add_argument("--ours", required=True)
    p.add_argument("--theirs", required=True)
    p.add_argument("--base")
    p.add_argument("--now")

    p = sub.add_parser("rehearse-batch", help="批量两两预演（预筛 + 预算护栏）")
    p.add_argument("--repo", required=True)
    p.add_argument("--pairs", required=True, help='[[ours, theirs], ...] JSON')
    p.add_argument("--base")
    p.add_argument("--now")

    p = sub.add_parser("query", help="台账查询")
    p.add_argument("--kind")
    p.add_argument("--repo")
    p.add_argument("--mission")
    p.add_argument("--since")
    p.add_argument("--until")

    sub.add_parser("verify", help="台账审计链自检")

    p = sub.add_parser("scan", help="零领域语义扫描")
    p.add_argument("--root", default=str(Path(__file__).parent))
    p.add_argument("--denylist")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    ledger_path = args.ledger or cfg["ledger"]["path"]

    try:
        if args.cmd == "lease-check":
            leases = [PathLease.from_dict(x) for x in _load_json(args.active)]
            candidate = PathLease.from_dict(_load_json(args.candidate))
            decision = check_lease(candidate, leases, cfg, now_ts=args.now)
            append_events(ledger_path, [make_event("lease_decision", decision, args.now or candidate.ts)])
            _emit(decision)
            return EXIT_ADMIT if decision["admitted"] else EXIT_BLOCK

        if args.cmd == "intake":
            finish = FinishRecord.from_dict(_load_json(args.finish))
            peers = _load_json(args.peers)
            matrix = conflict_matrix(finish, peers, cfg, now_ts=args.now)
            record_intake(ledger_path, matrix, finish, peers, args.now or finish.ts, now_ts=args.now)
            _emit(matrix)
            return EXIT_ADMIT if matrix["decision"] == "admit" else EXIT_BLOCK

        if args.cmd == "rehearse":
            report = rehearse_pair(args.repo, args.ours, args.theirs, cfg, base=args.base)
            record_rehearsal(ledger_path, report, args.now or _ts_of(report))
            _emit(report)
            return EXIT_ADMIT if report["allowed"] else EXIT_BLOCK

        if args.cmd == "rehearse-batch":
            pairs = _load_json(args.pairs)
            batch = rehearse_pairs(args.repo, pairs, cfg, base=args.base)
            record_rehearsal(ledger_path, batch, args.now or _ts_of(batch), kind="rehearsal_batch")
            _emit(batch)
            return EXIT_ADMIT if batch["allowed"] else EXIT_BLOCK

        if args.cmd == "query":
            events = query_events(ledger_path, kind=args.kind, repo=args.repo,
                                  mission_id=args.mission, since=args.since, until=args.until)
            _emit({"events": len(events), "items": events})
            return EXIT_ADMIT

        if args.cmd == "verify":
            result = verify_chain(ledger_path)
            _emit(result)
            return EXIT_ADMIT if result["ok"] else EXIT_BLOCK

        if args.cmd == "scan":
            denylist = load_denylist(args.denylist) if args.denylist else None
            result = scan_tree(args.root, denylist)
            _emit(result)
            return EXIT_ADMIT if result["ok"] else EXIT_BLOCK
    except (ValueError, KeyError, OSError) as exc:
        print(f"tenancy_gate: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


def _ts_of(obj: dict) -> str:
    """事件时间戳：从对象自身可复算字段取，避免引入墙钟。"""
    for key in ("evaluated_at", "ts"):
        if obj.get(key):
            return str(obj[key])
    inner = (obj.get("finish") or {}).get("ts")
    return str(inner or "1970-01-01T00:00:00Z")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())