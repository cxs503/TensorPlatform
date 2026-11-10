"""cli —— 派单前契约门的命令行面。

子命令
------
``check``        对一份派单声明（JSON）做就绪判定，exit 0=READY / 3=BLOCK。
``build-corpus`` 从历史任务快照推导回测语料。
``backtest``     跑回测并落 ``backtest_report.json``。
``lint``         对语料做契约缺陷体检。

用例::

    python3 -m tools.dispatch_contract.cli check --dispatch d.json
    python3 -m tools.dispatch_contract.cli build-corpus --source snapshot.json \\
        --out artifacts/dispatch_contract/corpus_v1.jsonl
    python3 -m tools.dispatch_contract.cli backtest \\
        --corpus artifacts/dispatch_contract/corpus_v1.jsonl \\
        --out artifacts/dispatch_contract/backtest_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import backtest as bt
from . import corpus as cp
from . import lint as lt
from .contract_schema import load_config
from .readiness import VERDICT_BLOCK, assess

ROOT = Path(__file__).resolve().parents[2]


def _cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    doc = json.loads(Path(args.dispatch).read_text(encoding="utf-8"))
    res = assess(doc, cfg)
    print(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True))
    return 3 if res["verdict"] == VERDICT_BLOCK else 0


def _cmd_build_corpus(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missions = cp.load_missions(args.source)
    records, stats = cp.build_corpus(missions, cfg, population_marker=args.population_marker)
    sha = cp.write_corpus(records, args.out)
    print(json.dumps({"out": str(args.out), "sha256": sha, **stats}, ensure_ascii=False, indent=2))
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    records = cp.read_corpus(args.corpus)
    report = bt.evaluate(records, cfg, shuffles=args.shuffles)
    report["corpus"] = {"path": str(args.corpus), "n_records": len(records)}
    report["config"] = {"path": cfg.get("_config_path")}
    bt.write_report(report, args.out)
    print(json.dumps({
        "out": str(args.out),
        "coverage": report["coverage"],
        "population_at_threshold": report["population"]["at_threshold"],
        "decidable_at_threshold": report["decidable"]["at_threshold"],
        "test_at_threshold": report["split"]["test_at_threshold"],
        "negative_control": report["negative_control"],
        "balanced_design": report["balanced_design"],
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    records = cp.read_corpus(args.corpus)
    res = lt.lint_corpus(records, cfg)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:4000])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dispatch_contract", description="派单前契约就绪门")
    p.add_argument("--config", default=None, help="门配置 JSON 路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="对一份派单声明做就绪判定")
    c.add_argument("--dispatch", required=True)
    c.set_defaults(func=_cmd_check)

    b = sub.add_parser("build-corpus", help="从历史任务快照推导回测语料")
    b.add_argument("--source", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--population-marker", default=None)
    b.set_defaults(func=_cmd_build_corpus)

    r = sub.add_parser("backtest", help="跑回测")
    r.add_argument("--corpus", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--shuffles", type=int, default=None)
    r.set_defaults(func=_cmd_backtest)

    l = sub.add_parser("lint", help="契约缺陷体检")
    l.add_argument("--corpus", required=True)
    l.add_argument("--out", default=None)
    l.set_defaults(func=_cmd_lint)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())