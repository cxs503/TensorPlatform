#!/usr/bin/env python3
"""report_ledger.py — R6 报告账：无报告=未收官（QUALITY_BAR R6 的机器账本）。

2026-10-25 断更事故（owner：报告 23 号后停更）的制度性修复：报告不再是
"想起来才写"的附属品，而是收官的必要条件。本脚本每 30 分钟对账一次：

  owed        = succeeded 终态 mission（cutoff 后收尾、非豁免）且无对应报告
  failed_owed = failed 终态 mission 且无失败分析条目（信息级，不强阻断）

"有对应报告" 的机器判据（并集）：
  a) tensor_reports.mission_id = m<id>；
  b) cutoff 后发布的报告 title/summary/tags 中出现 m<id>（合并报告口径，
     如一篇覆盖 m302+307-309）。
豁免：goal 以 "platform-operator 值班" 开头（值班班次自身不产研报）、
goal 含 "报告补账"（补账 mission 自身不递归欠账）。

输出：agent_work/quality/report_debt.json（原子写）+ 一行日志。
本脚本是账本不是阻断门：exit 0 恒定；债务由 operator 班次巡检第 6 步
消费（按剧本派报告补账 mission）。假阴性方向可接受（宁可多派补账）。

cron: 13,43 */1 * * * （与 operator_shift 错峰；见 README cron 拓扑）
用法: report_ledger.py [--cutoff 2026-10-23]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

PG_CONTAINER = "open-foundation-postgres"
PG_USER = "foundation"
PG_DB = "open_digital_foundation"
OUT = Path("/srv/tensor/shared/tensor_eco/agent_work/quality/report_debt.json")
EXEMPT_GOAL_PREFIX = "platform-operator 值班"
EXEMPT_GOAL_MARK = "报告补账"


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def psql(query: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
         "-t", "-A", "-F", "|", "-c", query],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def covered_ids(cutoff: str) -> set[int]:
    """mission ids 对应报告的并集判据 a)+b)。"""
    cov: set[int] = set()
    for row in psql(
            "SELECT DISTINCT mission_id FROM tensor_reports "
            "WHERE mission_id IS NOT NULL").splitlines():
        if row.strip().isdigit():
            cov.add(int(row))
    blob = psql(
        "SELECT COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' || "
        "COALESCE(tags::text,'') FROM tensor_reports "
        f"WHERE created_at >= '{cutoff}'")
    for m in re.finditer(r"\bm(\d{2,4})\b", blob):
        cov.add(int(m.group(1)))
    return cov


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff",
                    default=os.environ.get("REPORT_LEDGER_CUTOFF", "2026-10-23"))
    args = ap.parse_args()
    cutoff = args.cutoff

    cov = covered_ids(cutoff)
    owed, failed_owed, waived = [], [], []
    q = ("SELECT id, status, COALESCE(EXTRACT(EPOCH FROM finished_at),0), "
         "COALESCE(left(goal,80),'') FROM tensor_dev_missions "
         f"WHERE finished_at >= '{cutoff}' "
         "AND status IN ('succeeded','failed') ORDER BY id")
    for line in psql(q).splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        mid, status, fin, goal = (int(parts[0]), parts[1],
                                  float(parts[2]), parts[3])
        if goal.startswith(EXEMPT_GOAL_PREFIX) or EXEMPT_GOAL_MARK in goal:
            waived.append(mid)
            continue
        if mid in cov:
            continue
        entry = {"id": mid, "status": status,
                 "finished_at": time.strftime(
                     "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fin)) if fin else None,
                 "goal_head": goal}
        (owed if status == "succeeded" else failed_owed).append(entry)

    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "cutoff": cutoff, "rule": "QUALITY_BAR R6 无报告=未收官",
           "owed": owed, "failed_owed": failed_owed, "waived": waived,
           "covered_total": len(cov)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(OUT.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    log(f"report_debt: owed={len(owed)} { [e['id'] for e in owed] } "
        f"failed_owed={len(failed_owed)} { [e['id'] for e in failed_owed] } "
        f"waived={len(waived)} covered={len(cov)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
