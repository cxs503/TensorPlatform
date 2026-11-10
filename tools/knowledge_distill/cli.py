#!/usr/bin/env python3
"""knowledge_distill CLI：一份命令跑完整条蒸馏链。

用法
----
    python3 -m tools.knowledge_distill.cli \
        --reports /path/to/work/m*/report.json \
        --references /path/to/proposals /path/to/verified \
        --out runs/x \
        [--config distill_params.json] [--truth truth.json] [--max-reports N]

``--reports`` 可以给文件、目录（按 ``--pattern`` 递归匹配，默认 ``report.json``）或通配符
（需引号避免 shell 展开次序不确定）。``--truth`` 是 ``{"node_key": "label"}`` 的可选真值
映射，仅用于聚类质量核验（判据 b）；未提供时该项判据记为"未判定"。

退出码：0 = 全部判据通过；1 = 有判据不通过；2 = 输入缺失/配置非法。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in (None, ""):  # 允许直接按文件路径执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.knowledge_distill.params import ConfigError, DistillParams
    from tools.knowledge_distill.pipeline import distill
else:
    from .params import ConfigError, DistillParams
    from .pipeline import distill


def expand_reports(patterns: Sequence[str], pattern: str = "report.json",
                   max_reports: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in patterns:
        p = Path(raw)
        candidates: Iterable[Path]
        if p.is_dir():
            candidates = sorted(p.rglob(pattern))
            if not candidates:  # 目录里没有标准命名时退化为任意 .json
                candidates = sorted(p.rglob("*.json"))
        elif p.is_file():
            candidates = [p]
        else:
            candidates = sorted(Path(x) for x in glob.glob(raw, recursive=True))
        for c in candidates:
            key = str(c.resolve())
            if key in seen or not c.is_file():
                continue
            seen.add(key)
            out.append(str(c))
    out.sort()
    return out[:max_reports] if max_reports is not None else out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨战役知识蒸馏（局限/下一步/FAIL → 提案种子）")
    ap.add_argument("--reports", nargs="+", required=True, help="报告文件/目录/通配符")
    ap.add_argument("--references", nargs="*", default=None, help="既有提案池/已验证案例")
    ap.add_argument("--out", required=True, help="产物目录")
    ap.add_argument("--config", default=None, help="参数 JSON（覆盖默认阈值）")
    ap.add_argument("--truth", default=None, help='可选节点级真值映射 {"node_key": "label"}')
    ap.add_argument("--truth-items", default=None,
                    help='可选条目级真值映射 {"原文文本": "label"}（优先于节点级）')
    ap.add_argument("--pattern", default="report.json", help="目录递归匹配的文件名")
    ap.add_argument("--max-reports", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        params = DistillParams.from_file(args.config) if args.config else DistillParams()
        params.validate()
    except (OSError, json.JSONDecodeError, ConfigError) as exc:
        print(json.dumps({"ok": False, "stage": "config", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    reports = expand_reports(args.reports, args.pattern, args.max_reports)
    if not reports:
        print(json.dumps({"ok": False, "stage": "inputs", "error": "没有匹配到报告"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    references = expand_reports(args.references or [], "*") if args.references else []
    truth = truth_items = None
    try:
        if args.truth:
            truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        if args.truth_items:
            truth_items = json.loads(Path(args.truth_items).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "stage": "truth", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    result = distill(reports, references, params, truth_by_node=truth,
                     truth_by_item=truth_items, out_dir=args.out)
    summary = {
        "ok": result["acceptance"]["all_ok"],
        "reports": result["coverage"]["reports_total"],
        "items": result["coverage"]["items"],
        "clusters": result["clustering"]["clusters"],
        "seeds": len(result["seeds"]),
        "accepted": len(result["accepted_seeds"]),
        "acceptance": result["acceptance"],
        "outputs": result["outputs"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())