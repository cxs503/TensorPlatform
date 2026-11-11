#!/usr/bin/env python3
"""能力证据采集管线 CLI。

用法::

    python3 tools/evidence_automation/cli.py collect              # 全流程（跑映射测试）
    python3 tools/evidence_automation/cli.py collect --skip-tests  # 只做静态+探针（快）
    python3 tools/evidence_automation/cli.py collect --tests-json /tmp/run.json
    python3 tools/evidence_automation/cli.py check                # 读已有产物，只跑判据 C1~C4

退出码：0 = 判据全过；1 = 有判据不达标；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evidence_automation.collect import (DEFAULT_LABELED, DEFAULT_MANUAL_LEDGER,
                                               DEFAULT_MAPPING, DEFAULT_REGISTRY, collect)
from tools.evidence_automation.mapping import MappingError


def _load_tests_payload(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "modules" in data:
        return data["modules"]
    return data


def _criteria(summary: dict) -> dict:
    cov = summary["coverage"]
    cons = summary["consistency"]
    nod = summary["no_downgrade"]
    return {
        "C1_coverage_ge_target": cov["C1_coverage_target_met"],
        "C2_labeled_agreement_ge_90": cons["labeled"]["C2_labeled_agreement_met"],
        "C2_manual_agreement_ge_90": cons["manual"]["C2_manual_agreement_met"],
        "C3_every_covered_has_acceptance": cov["C3_every_covered_has_acceptance"],
        "C4_manual_real_not_downgraded": nod["C4_manual_real_not_downgraded"],
        "C4_no_auto_overclaim": nod["C4_no_auto_overclaim"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="能力证据自动采集管线")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_collect = sub.add_parser("collect", help="执行采集并落盘")
    p_collect.add_argument("--root", default=str(ROOT))
    p_collect.add_argument("--mapping", default=DEFAULT_MAPPING)
    p_collect.add_argument("--registry", default=DEFAULT_REGISTRY)
    p_collect.add_argument("--out", default=None)
    p_collect.add_argument("--manual-ledger", default=DEFAULT_MANUAL_LEDGER)
    p_collect.add_argument("--labeled", default=DEFAULT_LABELED)
    p_collect.add_argument("--skip-tests", action="store_true")
    p_collect.add_argument("--tests-json", default=None)
    p_collect.add_argument("--target-covered", type=int, default=15)
    p_collect.add_argument("--no-write", action="store_true")

    p_check = sub.add_parser("check", help="只跑判据（读已有 coverage.json）")
    p_check.add_argument("--root", default=str(ROOT))
    p_check.add_argument("--out", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "check":
        root = Path(args.root)
        out = Path(args.out) if args.out else root / "artifacts" / "capability_evidence"
        target = out / "coverage.json"
        if not target.exists():
            print(f"ERROR: 缺产物 {target}", file=sys.stderr)
            return 2
        summary = json.loads(target.read_text(encoding="utf-8"))
        crit = _criteria(summary)
        for key, ok in crit.items():
            print(f"[{'PASS' if ok else 'FAIL'}] {key}")
        cov = summary["coverage"]
        print(f"覆盖 {cov['covered']}/{cov['total_capabilities']}"
              f"（目标 {cov['target_covered']}）；等级分布 {cov['grade_tally']}")
        return 0 if all(crit.values()) else 1

    tests_payload = _load_tests_payload(args.tests_json) if args.tests_json else None
    try:
        summary = collect(
            args.root, mapping_path=args.mapping, registry_path=args.registry,
            out_dir=args.out, run_mapped_tests=not args.skip_tests,
            tests_payload=tests_payload, manual_ledger=args.manual_ledger,
            labeled_path=args.labeled, target_covered=args.target_covered,
            write=not args.no_write,
        )
    except (MappingError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    crit = _criteria(summary)
    cov = summary["coverage"]
    print(f"能力 {summary['capabilities_total']} 项，映射 {summary['capabilities_mapped']} 项")
    print(f"覆盖 {cov['covered']}/{cov['total_capabilities']}（目标 {cov['target_covered']}）"
          f" → {'达标' if cov['C1_coverage_target_met'] else '未达标'}")
    print(f"等级分布 {cov['grade_tally']}")
    print(f"标注集一致率 {summary['consistency']['labeled']['agreement']:.2%}"
          f"（{summary['consistency']['labeled']['agree']}/{summary['consistency']['labeled']['cases']}）")
    print(f"人工裁决一致率 {summary['consistency']['manual']['agreement_with_ceiling']:.2%}"
          f"（裸判级 {summary['consistency']['manual']['agreement_raw']:.2%}）")
    for key, ok in crit.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {key}")
    if cov["uncovered_ids"]:
        print(f"未覆盖：{cov['uncovered_ids']}")
    print(f"content_digest={summary['content_digest']}")
    return 0 if all(crit.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())