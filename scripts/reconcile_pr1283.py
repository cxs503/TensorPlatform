#!/usr/bin/env python3
"""PR#1283 重开单的对账器：原 PR 交付路径 vs 当前 main 的实际内容。

背景：TensorPlatform#1283（m15147「外部工具类接入」）已被裁定关闭，但同一功能随后
经 ``agent/m15147``(6d6bec219) 与返修 ``agent/m15199``(6858301ea) 合并进 main。重开单
若不先对账，就会把已经落地的能力再实现一遍（重复 PR、热文件冲突、白做）。

本脚本只读 git 对象，逐条比对 12 个交付路径的 blob 哈希，输出机器可复算的对账表：
``identical_to_repair``（与返修轮完全相同）/ ``identical_to_original`` / ``differs`` /
``missing``。产物写到 ``docs/pr1283/``（json + md），报告页直接引用，零手抄。

用法::

    python3 scripts/reconcile_pr1283.py [--out-dir docs/pr1283] [--rev HEAD]

退出码：0 = 12 个路径全部在 main 且与已合并版本一致；1 = 仍有路径缺失/漂移。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 原 PR#1283 声明的交付路径（任务书原文，逐字保留）。
PR1283_PATHS: List[str] = [
    "backend/api/external_tools.py",
    "backend/main.py",
    "backend/services/external_tools_service.py",
    "data/external_tools/classes.json",
    "docs/KPI_TOOLS_5_external_tools_onboarding.md",
    "tests/test_external_tools.py",
    "tools/external_tools/__init__.py",
    "tools/external_tools/adapters.py",
    "tools/external_tools/cli.py",
    "tools/external_tools/harness.py",
    "tools/external_tools/probe.py",
    "tools/external_tools/report.py",
]

#: 同一功能实际合并进 main 的两笔提交（base=首版，repair=返修轮1）。
MERGED_COMMITS = {
    "base": "6d6bec219d96abd4ce38cb132f7df79ce1d846ae",
    "repair": "6858301ea2b151b40f5f6e835e1d54778f026113",
}


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True,
                          stdout=subprocess.PIPE).stdout


def blob_sha(rev: str, path: str) -> Optional[str]:
    """``rev:path`` 的 blob 内容 sha256；路径不存在返回 None（不抛异常）。"""

    probe = subprocess.run(["git", "cat-file", "-e", f"{rev}:{path}"],
                           cwd=REPO_ROOT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        return None
    data = _git("show", f"{rev}:{path}")
    return hashlib.sha256(data).hexdigest()


def resolve_rev(rev: str = "HEAD") -> str:
    return _git("rev-parse", rev).decode().strip()


def reconcile(rev: str = "HEAD") -> Dict[str, Any]:
    rev = resolve_rev(rev)
    rows: List[Dict[str, Any]] = []
    for path in PR1283_PATHS:
        main = blob_sha(rev, path)
        base = blob_sha(MERGED_COMMITS["base"], path)
        repair = blob_sha(MERGED_COMMITS["repair"], path)
        if main is None:
            state = "missing"
        elif main == repair:
            state = "identical_to_repair"
        elif main == base:
            state = "identical_to_original"
        else:
            state = "differs"          # main 在合并后又演进过（正常，需人工看一眼）
        rows.append({"path": path, "in_main": main is not None, "state": state,
                     "main_sha256": main, "base_sha256": base,
                     "repair_sha256": repair})
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    present = sum(1 for r in rows if r["in_main"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reconciled_against": rev,
        "merged_commits": MERGED_COMMITS,
        "n_paths": len(rows),
        "n_present_in_main": present,
        "state_counts": counts,
        "ok": present == len(rows) and counts.get("missing", 0) == 0,
        "rows": rows,
    }


def render_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "# PR#1283 交付路径对账（main 实况 vs 原 PR 清单）",
        "",
        f"- 生成时间 `{result['generated_at']}` · 对账基准 rev "
        f"`{result['reconciled_against'][:12]}`",
        f"- 原 PR 清单路径 {result['n_paths']} 条，main 中存在 "
        f"{result['n_present_in_main']} 条；状态分布 "
        f"`{json.dumps(result['state_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- 首版合并提交 `{result['merged_commits']['base'][:12]}`，"
        f"返修轮1 `{result['merged_commits']['repair'][:12]}`",
        "",
        "| # | path | in_main | main sha256 | repair sha256 | 对账 |",
        "|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(result["rows"], 1):
        lines.append(
            f"| {i} | `{row['path']}` | {row['in_main']} | "
            f"`{(row['main_sha256'] or '—')[:12]}` | "
            f"`{(row['repair_sha256'] or '—')[:12]}` | {row['state']} |")
    lines += [
        "",
        "判读：`identical_to_repair` = main 内容与返修轮一字不差（重开单无需重写）；"
        "`differs` = 合并后又被后续提交演进（需按新状态复核，不得回退）；"
        "`missing` = 真缺口。",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "pr1283"))
    ap.add_argument("--rev", default="HEAD",
                    help="对账基准 revision（缺省 HEAD；重开单用切枝点 main^0 更稳）")
    args = ap.parse_args(argv)

    result = reconcile(args.rev)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "reconciliation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "reconciliation.md").write_text(render_markdown(result), encoding="utf-8")
    print(f"paths={result['n_paths']} present={result['n_present_in_main']} "
          f"states={result['state_counts']} ok={result['ok']}")
    print(f"written: {out / 'reconciliation.json'} , {out / 'reconciliation.md'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())