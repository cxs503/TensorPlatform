"""CLI：``python3 -m tools.capability_reconcile.cli --ledger <json> [--root .]``。

退出码：0 = 报告产出且 schema 通过；1 = schema 校验失败；2 = 过度声明存在（配合
``--fail-on-overclaim``）；3 = 输入缺失。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - 允许直接执行脚本
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.capability_reconcile import engine  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="能力台账三源自动对账（code/test/doc → 等级/过度声明/建议/sha256 审计链）")
    ap.add_argument("--ledger", required=True, help="能力台账 JSON（dict id→grade 或 list[{id,claimed_level}]）")
    ap.add_argument("--root", default=".", help="工作树根（默认 cwd）")
    ap.add_argument("--bindings", default=None, help="显式绑定 JSON：{capability_id: {code:[],test:[],doc:[]}}")
    ap.add_argument("--junit", default=None, help="junit/汇总结果 JSON：{capability_id:{tests,failures,errors}}")
    ap.add_argument("--out", default=None, help="报告输出路径")
    ap.add_argument("--audit-out", default=None, help="仅审计链输出路径")
    ap.add_argument("--contrast", default=None, help="外部裁决台账（对照一致率）")
    ap.add_argument("--contrast-key", default="reconciled_level")
    ap.add_argument("--exclude", action="append", default=[], help="解析时排除的路径前缀（可多次）")
    ap.add_argument("--max-caps", type=int, default=None)
    ap.add_argument("--policy", default="evidence", choices=["evidence", "presence"])
    ap.add_argument("--fail-on-overclaim", action="store_true")
    args = ap.parse_args(argv)

    ledger = Path(args.ledger)
    if not ledger.is_file():
        print(f"ledger not found: {ledger}", file=sys.stderr)
        return 3
    junit = json.loads(Path(args.junit).read_text(encoding="utf-8")) if args.junit else None
    report = engine.build_report(
        ledger_path=ledger,
        root=Path(args.root).resolve(),
        bindings_path=Path(args.bindings) if args.bindings else None,
        junit=junit,
        excludes=args.exclude,
        max_caps=args.max_caps,
        policy=args.policy,
    )
    if args.contrast:
        report["contrast"] = engine.contrast(report, Path(args.contrast), truth_key=args.contrast_key)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.audit_out:
        audit_out = Path(args.audit_out)
        audit_out.parent.mkdir(parents=True, exist_ok=True)
        audit_out.write_text(json.dumps(report["audit_log"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    s = report["summary"]
    print(
        f"caps={s['capabilities']} scan_coverage={s['scan_coverage']} bind_coverage={s['source_binding_coverage']} "
        f"overclaim={s['overclaim_count']}(hard={s['overclaim_hard']},soft={s['overclaim_soft']}) "
        f"anchor={report['audit_log']['anchor']['value'][:16]}… schema_valid={report['validation']['valid']}",
        file=sys.stderr,
    )
    if not report["validation"]["valid"]:
        return 1
    if args.fail_on_overclaim and s["overclaim_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())