#!/usr/bin/env python3
"""治理机制命令行入口（ops/taskplane/governance 的可执行面）。

    python3 -m ops.taskplane.governance bench --out artifacts/in99_n10
    python3 -m ops.taskplane.governance seed  --seed seed.json [--registry used_ext_ids.json]
    python3 -m ops.taskplane.governance dedup --proposals props.json [--pool pool.json]
    python3 -m ops.taskplane.governance ledger register --record rec.json --ledger L.sqlite
    python3 -m ops.taskplane.governance ledger verify   --ledger L.sqlite
    python3 -m ops.taskplane.governance ledger query    --ledger L.sqlite [--capability C]

退出码：0 = 通过/成功；2 = 判据未过（机器门拒收 / 去重门全拒 / 台账拒登记）；3 = 输入错误。
配置可用 --config 注入 JSON（``DedupConfig`` / ``GateConfig`` 字段名），保证阈值可调。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Sequence

from . import bench as bench_mod
from . import evidence_ledger as led
from . import machine_gate as gate_mod
from . import proposal_dedup as dedup


def _load(path: str | None) -> Any:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(obj: Any, out: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)


def cmd_seed(args: argparse.Namespace) -> int:
    seed = _load(args.seed)
    cfg = gate_mod.GateConfig.load(args.config)
    registry: List[str] = list(_load(args.registry) or [])
    report = gate_mod.evaluate_seed(seed, cfg, registry)
    _dump(report.to_dict(), args.out)
    return 0 if report.ok else 2


def cmd_dedup(args: argparse.Namespace) -> int:
    proposals = _load(args.proposals) or []
    if isinstance(proposals, dict):
        proposals = proposals.get("proposals") or []
    pool = _load(args.pool) or []
    references = _load(args.references) or []
    cfg = dedup.DedupConfig.load(args.config)
    report = dedup.gate(proposals, pool, references, cfg)
    _dump(report, args.out)
    return 0 if report["n_accepted"] else 2


def cmd_ledger(args: argparse.Namespace) -> int:
    if args.ledger_action == "register":
        record = _load(args.record)
        res = led.register(args.ledger, record)
        _dump(res.to_dict(), args.out)
        return 0 if res.ok else 2
    if args.ledger_action == "verify":
        result = led.verify_chain(args.ledger)
        _dump(result, args.out)
        return 0 if result["ok"] else 2
    if args.ledger_action == "query":
        rows = led.query(args.ledger, capability_id=args.capability, grade=args.grade)
        _dump({"n": len(rows), "entries": rows,
               "index": led.capability_index(args.ledger)}, args.out)
        return 0
    if args.ledger_action == "trace":
        result = led.trace(args.ledger, args.entry)
        _dump(result, args.out)
        return 0 if result.get("ok") else 2
    raise SystemExit("未知 ledger 子命令")


def cmd_bench(args: argparse.Namespace) -> int:
    metrics = bench_mod.run(Path(args.out), with_figures=not args.no_figures)
    print(json.dumps(metrics["acceptance"], ensure_ascii=False, indent=2))
    print("all_pass =", metrics["all_pass"])
    return 0 if metrics["all_pass"] else 2


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python3 -m ops.taskplane.governance",
                                 description="战役播种治理机制（去重门/DAG/机器门/证据台账）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="跑 C1..C5 基准并落 metrics.json + 图")
    b.add_argument("--out", default="artifacts/in99_n10")
    b.add_argument("--no-figures", action="store_true")
    b.set_defaults(func=cmd_bench)

    s = sub.add_parser("seed", help="对 campaign seed 做机器门裁定")
    s.add_argument("--seed", required=True)
    s.add_argument("--config")
    s.add_argument("--registry", help="已占用 ext_id 的 JSON 数组（DB 只读导出）")
    s.add_argument("--out")
    s.set_defaults(func=cmd_seed)

    d = sub.add_parser("dedup", help="对一批提案过去重门")
    d.add_argument("--proposals", required=True)
    d.add_argument("--pool")
    d.add_argument("--references")
    d.add_argument("--config")
    d.add_argument("--out")
    d.set_defaults(func=cmd_dedup)

    lg = sub.add_parser("ledger", help="证据台账：登记/查询/校验/追溯")
    lg.add_argument("ledger_action", choices=["register", "query", "verify", "trace"])
    lg.add_argument("--ledger", required=True)
    lg.add_argument("--record")
    lg.add_argument("--capability")
    lg.add_argument("--grade")
    lg.add_argument("--entry")
    lg.add_argument("--out")
    lg.set_defaults(func=cmd_ledger)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"输入缺失: {exc}", file=sys.stderr)
        return 3
    except (ValueError, KeyError) as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())