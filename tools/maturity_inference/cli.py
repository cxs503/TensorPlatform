#!/usr/bin/env python3
"""CLI：``python3 -m tools.maturity_inference.cli``（全流程）。

子命令面（保持稳定，供战役节点/CI 直接调用）::

    cli run           全流程验收（默认；--no-execute 不重跑仓库测试）
    cli infer FILE    对给定证据节点 JSON 列表跑推断，stdout 输出 JSON
    cli gate KEY...   只跑命名空间门
    cli golden        只跑黄金集
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .engine import infer
from .golden import evaluate_golden, load_golden
from .namespace import check_key
from .pipeline import run


def _cmd_infer(path: str) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else [payload]
    out = []
    for row in rows:
        res = infer(str(row.get("capability_key") or row.get("id") or ""),
                    row.get("nodes") or [],
                    claimed_level=row.get("claimed_level"),
                    contradictions=row.get("contradictions") or [])
        out.append(res.as_dict())
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_gate(keys: Sequence[str]) -> int:
    bad = 0
    for key in keys:
        ok, reason = check_key(key)
        bad += 0 if ok else 1
        print(f"{'ACCEPT' if ok else 'REJECT'}  {key}  ({reason})")
    return 0 if bad == 0 else 1


def _cmd_golden() -> int:
    rep = evaluate_golden(load_golden())
    print(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
    return 0 if rep.passes else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["run"] + argv
    cmd, rest = argv[0], argv[1:]
    if cmd == "infer":
        return _cmd_infer(rest[0])
    if cmd == "gate":
        return _cmd_gate(rest)
    if cmd == "golden":
        return _cmd_golden()
    if cmd == "run":
        ap = argparse.ArgumentParser(description="五级成熟度推断引擎验收跑批")
        ap.add_argument("--no-execute", action="store_true",
                        help="不重跑仓库测试（tested 环将不可支撑）")
        ap.add_argument("--timeout", type=int, default=900)
        ap.add_argument("--max-tests", type=int, default=24)
        ap.add_argument("--skip-pytest", action="store_true", help="跳过自测计数（离线）")
        ap.add_argument("--reuse-links", action="store_true",
                        help="复用上一轮 evidence_links.json（含其 sha256），只重算推断/报告")
        args = ap.parse_args(rest)
        acc = run(execute=not args.no_execute, timeout=args.timeout,
                  max_tests=args.max_tests, skip_pytest=args.skip_pytest,
                  reuse_links=args.reuse_links)
        print(json.dumps({k: v.get("passes") for k, v in acc["criteria"].items()},
                         ensure_ascii=False, indent=2))
        print("all_pass =", acc["all_pass"])
        return 0 if acc["all_pass"] else 1
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
