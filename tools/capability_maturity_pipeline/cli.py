"""CLI：能力成熟度自治管线的机器入口（p156w2-n07）。

用法::

    # 跑一轮闭环（扫描→判定→台账→审计），写台账与运行工件
    python3 -m tools.capability_maturity_pipeline.cli run \
        --worktree . --ledger artifacts/capability_maturity/ledger.json \
        --out artifacts/capability_maturity/run.json \
        --roots artifacts runs tensor_reports

    # 只校验台账与审计链（不扫描、不改台账）
    python3 -m tools.capability_maturity_pipeline.cli verify \
        --ledger artifacts/capability_maturity/ledger.json

    # 回放到某世代（追加 MANUAL_ROLLBACK 事件）
    python3 -m tools.capability_maturity_pipeline.cli rollback \
        --ledger artifacts/capability_maturity/ledger.json --to-generation 0

退出码：0=成功 / 1=校验失败（链断或台账不一致）/ 2=参数错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from ..capability_ledger_maturity.adjudication_engine import AdjudicationThresholds
from .contradiction import ContradictionPolicy
from .ledger import MaturityLedger
from .pipeline import DEFAULT_ROOTS, MaturityPipeline, PipelineConfig


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    worktree = Path(args.worktree).resolve()
    ledger_path = Path(args.ledger)
    if ledger_path.exists():
        ledger = MaturityLedger.load(ledger_path)
    else:
        ledger = MaturityLedger()
    thresholds = AdjudicationThresholds(
        real_error_pct=args.real_error_pct,
        withheld_error_pct=args.withheld_error_pct,
        mock_error_pct=args.mock_error_pct,
        min_evidence_count=args.min_evidence,
    )
    policy = ContradictionPolicy(deviation_threshold_pct=args.deviation_pct)
    attribution = ()
    if args.attribution:
        attribution = PipelineConfig.load_attribution(Path(args.attribution))
    config = PipelineConfig(
        roots=tuple(Path(r) for r in (args.roots or DEFAULT_ROOTS)),
        thresholds=thresholds,
        policy=policy,
        attribution=attribution,
    )
    pipeline = MaturityPipeline(worktree, config=config, ledger=ledger)
    run = pipeline.run(persist=True, ledger_path=ledger_path)
    summary = MaturityPipeline.summarize(run)
    if args.out:
        _write_json(Path(args.out), run.artifact)
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if run.artifact["verification"]["ok"] else 1


def cmd_verify(args: argparse.Namespace) -> int:
    ledger = MaturityLedger.load(Path(args.ledger))
    result = ledger.verify()
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    ledger = MaturityLedger.load(Path(args.ledger))
    decisions = ledger.rollback_to_generation(args.to_generation)
    ledger.save(Path(args.ledger))
    payload = {"rolled_back": [d.to_dict() for d in decisions],
               "generation": ledger.generation,
               "chain_head_sha256": ledger.chain.head,
               "chain_length": len(ledger.chain),
               "verification": ledger.verify()}
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if payload["verification"]["ok"] else 1


def cmd_levels(args: argparse.Namespace) -> int:
    ledger = MaturityLedger.load(Path(args.ledger))
    rows = []
    for cap, st in sorted(ledger.states.items()):
        rows.append({
            "capability_id": cap,
            "level": st.level,
            "decided_at": st.decided_at,
            "event_seq": st.event_seq,
            "rule_fired": st.rule_fired,
            "evidence_refs": len(st.evidence_refs),
            "superseded_refs": len(st.superseded_refs),
            "rollback_of": st.rollback_of,
            "changes": len(st.history),
        })
    print(json.dumps({"generation": ledger.generation, "capabilities": rows},
                     indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capability_maturity_pipeline",
        description="能力成熟度自治管线：扫描→判定→台账→回滚→审计")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="跑一轮完整闭环")
    r.add_argument("--worktree", default=".")
    r.add_argument("--ledger", default="artifacts/capability_maturity/ledger.json")
    r.add_argument("--out", default="artifacts/capability_maturity/run.json")
    r.add_argument("--roots", nargs="*", default=None)
    r.add_argument("--attribution", default=None,
                   help="归因配置 JSON（相对路径 glob → capability_id）")
    r.add_argument("--real-error-pct", type=float, default=3.0)
    r.add_argument("--withheld-error-pct", type=float, default=15.0)
    r.add_argument("--mock-error-pct", type=float, default=15.0)
    r.add_argument("--deviation-pct", type=float, default=1.0)
    r.add_argument("--min-evidence", type=int, default=1)
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="校验审计链与台账一致性")
    v.add_argument("--ledger", required=True)
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("rollback", help="回放到某世代（追加事件，不删历史）")
    b.add_argument("--ledger", required=True)
    b.add_argument("--to-generation", type=int, required=True)
    b.set_defaults(func=cmd_rollback)

    lv = sub.add_parser("levels", help="列出当前台账等级与判定依据")
    lv.add_argument("--ledger", required=True)
    lv.set_defaults(func=cmd_levels)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())