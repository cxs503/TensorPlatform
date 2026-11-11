#!/usr/bin/env python3
"""CLI：跨战役知识蒸馏 → EVOI 派单排序的端到端评估。

    # 1) 拉一份任务日志快照（只读；把派单时刻字段与结局字段裁成一份可复算的工件）
    python3 -m tools.seed_dispatch.cli fetch --out runs/p318w2/n10/mission_snapshot.json

    # 2) 跑蒸馏（复用 tools.knowledge_distill）
    python3 -m tools.knowledge_distill.cli --reports '/root/tp-agent/work/*/report.json' \\
        --references /root/tp-agent/proposals --out runs/p318w2/n10/distill --max-reports 400

    # 3) 评估 + 判据 (a)~(g) + 图 + 报告
    python3 -m tools.seed_dispatch.cli evaluate \\
        --snapshot runs/p318w2/n10/mission_snapshot.json \\
        --distill runs/p318w2/n10/distill --out runs/p318w2/n10

退出码：0 = 全部判据通过；1 = 有判据不通过；2 = 输入/参数非法（不落产物）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许按文件路径执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.seed_dispatch.params import ConfigError, load_params
else:
    from .params import ConfigError, load_params

__all__ = ["main", "fetch"]

#: 快照保留的字段（派单时刻 + 结局），其余丢弃以控制体积
_KEEP = (
    "id", "repo", "goal", "budget", "co_repos", "parent_mission_id",
    "created_at", "finished_at", "status", "result", "campaign_phase",
)
_GOAL_CLIP = 600


def fetch(out: str, *, api_base: str | None = None, token: str | None = None) -> dict:
    from tools.seed_dispatch.log import fetch_missions, snapshot_sha256

    raw = fetch_missions(api_base, token)
    kept = []
    for row in raw:
        item = {k: row.get(k) for k in _KEEP if k != "goal"}
        item["goal"] = str(row.get("goal") or "")[:_GOAL_CLIP]
        result = row.get("result") or {}
        if isinstance(result, dict):
            item["result"] = {
                k: result.get(k) for k in ("accepted", "tool_calls", "headline", "fail_stage")
            }
        kept.append(item)
    kept.sort(key=lambda r: (str(r.get("created_at")), int(r.get("id") or 0)))
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return {"snapshot": str(path), "records": len(kept), "sha256": snapshot_sha256(kept)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="拉任务日志快照")
    f.add_argument("--out", default="runs/p318w2/n10/mission_snapshot.json")
    f.add_argument("--api-base", default=None)

    e = sub.add_parser("evaluate", help="评估 + 判据 + 图 + 报告")
    e.add_argument("--snapshot", default="runs/p318w2/n10/mission_snapshot.json")
    e.add_argument("--distill", default="runs/p318w2/n10/distill")
    e.add_argument("--out", default="runs/p318w2/n10")
    e.add_argument("--config", default=None)
    e.add_argument("--no-checks", action="store_true", help="跳过 pytest/coverage/域词/真值检查")
    e.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        info = fetch(args.out, api_base=args.api_base)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0

    try:
        params = load_params(path=args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    from tools.seed_dispatch.acceptance import run

    try:
        payload = run(snapshot=args.snapshot, distill_dir=args.distill, out=args.out,
                      params=params, with_checks=not args.no_checks,
                      with_figures=not args.no_figures)
    except FileNotFoundError as exc:
        print(f"missing input: {exc}", file=sys.stderr)
        return 2
    gates = payload["gates"]
    print(json.dumps({k: v.get("ok") for k, v in gates.items() if isinstance(v, dict)},
                     ensure_ascii=False, indent=1))
    print("all_pass:", gates["all_pass"])
    print("json:", Path(args.out) / "eval_report.json")
    print("md  :", Path(args.out) / "eval_report.md")
    return 0 if gates["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())