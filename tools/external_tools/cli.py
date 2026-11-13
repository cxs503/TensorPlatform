#!/usr/bin/env python3
"""CLI: ``python3 -m tools.external_tools.cli {list,run,verify}``.

Exit codes are part of the contract so a gate can call this directly:
``0`` = coverage target met (verdict PASS), ``1`` = target not met (honest FAIL),
``2`` = the spec itself is broken (nothing was measured),
``3`` = verdict PARTIAL: a ``--only`` subset was measured and every selected class
came back REAL, so the KPI target was never put to the test (not a PASS).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT))

from tools.external_tools import harness, report, spec as spec_mod  # noqa: E402

DEFAULT_SPEC = REPO_ROOT / "data" / "external_tools" / "classes.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "external_tools"


def _print_summary(ledger: Mapping[str, Any]) -> None:
    m = ledger["measured"]
    cov = ledger["coverage"]
    scope = " (subset：KPI 未评估)" if ledger.get("partial") else ""
    print(f"suite={ledger['suite_id']} target={cov['target_classes']} "
          f"real={m['real_classes']} shortfall={cov['shortfall']} "
          f"verdict={ledger.get('verdict')}{scope}")
    for rec in ledger["classes"]:
        print(f"  {rec['status']:9s} {rec['tool_id']:26s} "
              f"{(rec.get('detail') or '')[:110]}")
    print(f"digest={ledger['digest'][:16]} value_digest={ledger['value_digest'][:16]}")


def cmd_list(args: argparse.Namespace) -> int:
    suite = spec_mod.load_suite(args.spec)
    for cls in suite.classes:
        print(f"{cls.tool_id:26s} {cls.category:22s} adapter={cls.adapter:26s} "
              f"probe={cls.probe.get('kind')}:{cls.probe.get('name') or cls.probe.get('argv')}")
    print(f"-- {len(suite.classes)} classes, target_classes={suite.target_classes}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    suite = spec_mod.load_suite(args.spec)
    only = [s.strip() for s in (args.only or "").split(",") if s.strip()] or None
    ctx = {"repo": str(args.repo or REPO_ROOT),
           "out_dir": str(args.out_dir),
           "dotenv": os.environ.get("EXTERNAL_TOOLS_DOTENV", "")}
    ledger = harness.run_suite(suite, only=only, ctx=ctx)
    ledger["verdict"] = harness.verdict(ledger)
    paths = report.write_artifacts(ledger, args.out_dir, stem=args.stem)
    _print_summary(ledger)
    for key, path in paths.items():
        print(f"{key}: {path}")
    if ledger["verdict"] == harness.VERDICT_PASS:
        return 0
    if ledger["verdict"] == harness.VERDICT_PARTIAL:
        return 3
    return 1


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-run and compare with the persisted ledger: a mismatch is a real finding."""

    latest = Path(args.out_dir) / "latest.json"
    if not latest.exists():
        print(f"no ledger to verify at {latest}; run first", file=sys.stderr)
        return 2
    before = json.loads(latest.read_text(encoding="utf-8"))
    suite = spec_mod.load_suite(args.spec)
    ctx = {"repo": str(args.repo or REPO_ROOT), "out_dir": str(args.out_dir),
           "dotenv": os.environ.get("EXTERNAL_TOOLS_DOTENV", "")}
    after = harness.run_suite(suite, ctx=ctx)
    after["verdict"] = harness.verdict(after)
    diffs: List[str] = []
    if before.get("spec_digest") != after.get("spec_digest"):
        diffs.append("spec_digest changed (spec file edited since the run)")
    if before.get("digest") != after.get("digest"):
        b = {c["tool_id"]: c["status"] for c in before.get("classes", [])}
        a = {c["tool_id"]: c["status"] for c in after.get("classes", [])}
        diffs += [f"{k}: {b.get(k)} -> {a.get(k)}" for k in sorted(set(a) | set(b))
                  if b.get(k) != a.get(k)]
    if before.get("value_digest") != after.get("value_digest"):
        diffs.append("value_digest changed (observed numbers moved)")
    if diffs:
        print("VERIFY MISMATCH")
        for d in diffs:
            print(f"  - {d}")
        return 1
    print(f"VERIFY OK digest={after['digest'][:16]} value_digest={after['value_digest'][:16]} "
          f"real={after['measured']['real_classes']}/{after['coverage']['target_classes']}")
    return 0 if after["verdict"] == harness.VERDICT_PASS else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="external_tools",
                                 description="外部工具类接入：探针→冒烟→不变式→证据")
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--stem", default="external_tools")
    ap.add_argument("--repo", default=str(REPO_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="打印规格中的工具类").set_defaults(func=cmd_list)
    p_run = sub.add_parser("run", help="跑全量探针+冒烟并落盘台账")
    p_run.add_argument("--only", default="", help="逗号分隔的 tool_id 子集")
    p_run.set_defaults(func=cmd_run)
    sub.add_parser("verify", help="重跑并与已落盘台账比对指纹").set_defaults(func=cmd_verify)
    return ap


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except spec_mod.SpecError as exc:
        print(f"SPEC ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())