#!/usr/bin/env python3
"""audit_findings_tracker CLI。

用法::

    python3 -m tools.audit_findings_tracker --report R.json --schema S.json \
        [--out OUT.json] [--require-categories [CAT ...]] [--quiet]

退出码：``0`` = PASS（发现已结构化）/ ``1`` = FAIL（解析失败或发现未识别）。
``--out`` 落盘时写入同一份确定性 JSON；stdout 也输出完整结果（``--quiet`` 只写文件）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .core import (
    CANONICAL_CATEGORIES,
    DEFAULT_SCHEMA_PATH,
    analyze_report,
    dump_result,
    render_summary,
)


def _parse_require(argv: Optional[Sequence[str]]) -> Optional[List[str]]:
    if argv is None:
        return None
    values = list(argv)
    if not values:
        return list(CANONICAL_CATEGORIES)
    unknown = [v for v in values if v not in CANONICAL_CATEGORIES]
    if unknown:
        raise SystemExit(f"--require-categories 含未知类别: {unknown}; 合法值={list(CANONICAL_CATEGORIES)}")
    return values


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m tools.audit_findings_tracker",
        description="交差审计发现结构化追踪：报告 JSON → 五类发现台账（PASS/FAIL 机器可裁）",
    )
    p.add_argument("--report", required=True, help="审计报告 JSON 路径")
    p.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH),
                   help="分类法 schema 路径（默认内置 audit_findings.v1.schema.json）")
    p.add_argument("--out", default=None, help="结构化 findings JSON 落盘路径（可选）")
    p.add_argument("--require-categories", nargs="*", default=None,
                   metavar="CAT", help="要求报告覆盖的类别（缺省用 schema.required_categories；"
                                       "列出该开关但不给值 = 要求全部五类）")
    p.add_argument("--quiet", action="store_true", help="不向 stdout 打印 JSON（仅落盘/摘要到 stderr）")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        required = _parse_require(args.require_categories)
    except SystemExit as exc:  # 参数错误按用法错误处理
        sys.stderr.write(str(exc) + "\n")
        return 2
    result = analyze_report(args.report, args.schema, require_categories=required)
    text = dump_result(result)
    if args.out:
        out = Path(args.out)
        if out.parent and str(out.parent) not in ("", "."):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if not args.quiet:
        sys.stdout.write(text)
    sys.stderr.write(render_summary(result) + "\n")
    return int(result.get("exit_code", 1))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())