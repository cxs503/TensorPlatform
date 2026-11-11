"""CLI：``python3 -m tools.criteria_metrology <子命令>``。

子命令
------
``corpus``    采集语料快照（离线 docs + 可选平台报告采样）
``replay``    回放重判，产出退化清单与替代判据
``controls``  负控（12 退化 / 12 良性）
``invariants``不变量属性测试（≥1000 随机样本）
``cases``     四历史案例再判定
``triad``     能力三元台账校验
``retrieval`` 检索凭证重放门 + 负空间图谱
``all``       全流水线（默认），落 artifacts/criteria_metrology/*
``check``     只读校验：语料指纹是否与快照一致、全部机器判据是否通过
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import pipeline


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.criteria_metrology", description="判据度量学：四元组/退化分类/自适应判据族/回放重判")
    ap.add_argument("command", nargs="?", default="all",
                    choices=["all", "corpus", "replay", "controls", "invariants", "cases", "triad", "retrieval", "agreement", "check"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="artifacts/criteria_metrology")
    ap.add_argument("--data", default="data/criteria_metrology")
    ap.add_argument("--policy", default="config/criteria_metrology/policy.json")
    ap.add_argument("--use-reports", action="store_true", help="额外采样平台报告库（需要 token）")
    ap.add_argument("--missions", type=int, default=0)
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root)
    out = Path(args.out)
    data = Path(args.data)
    policy_path = Path(args.policy) if Path(args.policy).exists() else None
    policy = pipeline.policy_from_file(policy_path)
    cmd = args.command

    if cmd == "corpus":
        snap = pipeline.build_corpus(
            root,
            data_out=data / "corpus.json",
            use_reports=args.use_reports,
            n_missions=args.missions,
        )
        _print({k: snap[k] for k in ("n_records", "digest", "tiers", "generated_on")})
        return 0

    if cmd == "check":
        corpus_path = data / "corpus.json"
        if not corpus_path.exists():
            print("corpus.json missing", file=sys.stderr)
            return 2
        snap = pipeline.load_snapshot(corpus_path)
        summary = pipeline.run_all(
            root=root, out=out, data_dir=data, policy_path=policy_path,
            corpus_path=corpus_path, make_figures=False,
        )
        _print(summary["checks"])
        return 0 if summary["all_checks_pass"] else 1

    if cmd == "replay":
        snap = pipeline.load_snapshot(data / "corpus.json")
        rep = pipeline.run_replay(snap, policy)
        _print({k: v for k, v in rep["summary"].items() if k not in ("degenerate_rows", "flipped_rows")})
        return 0

    if cmd == "controls":
        _print(pipeline.run_controls(pipeline.load_controls(data / "controls.json"), policy))
        return 0

    if cmd == "invariants":
        res = pipeline.run_invariants()
        _print({k: v for k, v in res.items() if k != "ladder"})
        return 0 if res["pass"] else 1

    if cmd == "cases":
        _print(pipeline.run_cases(data / "cases_m7241_m7248.json", policy))
        return 0

    if cmd == "triad":
        _print(pipeline.run_triad(root, data / "capability_triad.json"))
        return 0

    if cmd == "retrieval":
        res = pipeline.run_retrieval(
            root,
            data / "retrieval_receipts.json",
            data / "retrieval_claims.json",
            data / "retrieval_regions.json",
        )
        _print({k: res[k] for k in ("n_receipts", "n_claims", "n_claims_supported", "n_replay_match", "n_replay_drift", "n_replay_unreplayable")})
        return 0

    if cmd == "agreement":
        _print({k: v for k, v in pipeline.run_agreement(data / "labels_40.json", policy).items() if k not in ("rows", "confusion")})
        return 0

    summary = pipeline.run_all(
        root=root,
        out=out,
        data_dir=data,
        policy_path=policy_path,
        corpus_path=(data / "corpus.json") if (data / "corpus.json").exists() else None,
        make_figures=not args.no_figures,
    )
    _print({"checks": summary["checks"], "all_checks_pass": summary["all_checks_pass"]})
    return 0 if summary["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())