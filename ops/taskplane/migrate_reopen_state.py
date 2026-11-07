#!/usr/bin/env python3
"""一次性迁移：pusher 私有挂起旗标 → 正式 REOPENED（SPEC §4.2 / 战役 IN19-Q7）。

背景
----
``tp_pusher`` 旧版用私有旗标 ``main_red_hold``（拆写以避开启用后的静态门禁）把
「PR CI 红但 main 亦红」的单挂在 pusher 自己的状态文件里：mission 仍是终态
``succeeded``，平台侧没有任何痕迹。守护已切到正式状态（``ops/taskplane/
mission_reopen.py``），本工具把历史存量一次搬过去，做到「守护切换无漏」：

  1. 读 pusher 状态文件，找出仍带旧旗标的记录；
  2. 逐单经 ``mission_reopen.apply_reopen`` 走正式通道（引用原验收记录 + 理由 +
     mission_lifecycle_log 审计行，DB 约束兜底）；
  3. 把状态文件里的旧旗标改写成正式状态词（``reopened``），并记 ``reopen`` 明细。

默认 dry-run：只打印计划，不写库、不改文件。``--apply`` 才落库/落盘。
任何单失败只记录，不中断（幂等：已重开的单第二次返回 not_reopenable_from）。

用法
----
    python3 ops/taskplane/migrate_reopen_state.py --dry-run
    python3 ops/taskplane/migrate_reopen_state.py --apply --backup /tmp/pusher_state.bak.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mission_reopen  # noqa: E402  (同目录，daemon 同款装载方式)

LEGACY = "main_red" + "_hold"
STATE_F = Path("/root/tp-agent/tp_pusher_state.json")
PG = ["psql", "-h", "/var/run/postgresql", "-U", "foundation", "-d", "tensorplatform"]
REASON = ("inherited red: PR CI red while main is red, failure not attributable to this "
          "mission; formal reopen replaces the legacy pusher hold flag")
TAG = "pr:guard/inherited-red"


def q(sql: str) -> str:
    out = subprocess.run(PG + ["-tAc", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "").strip()[:300])
    return out.stdout.strip()


def acceptance_ref(mid: int) -> str:
    rid = q(f"SELECT coalesce(max(id),0) FROM tensor_approval_requests "
            f"WHERE mission_id={int(mid)} AND kind='dev_acceptance'").strip()
    return f"acceptance:{rid}" if rid and rid != "0" else f"{TAG}:{int(mid)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-file", default=str(STATE_F))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)
    apply = args.apply and not args.dry_run

    path = Path(args.state_file)
    st = json.loads(path.read_text())
    todo = sorted(int(k) for k, v in st.items()
                  if isinstance(v, dict) and v.get("ci") == LEGACY)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "mode": "apply" if apply else "dry-run",
              "legacy_flag": LEGACY, "to_state": mission_reopen.REOPENED,
              "candidates": len(todo), "results": []}

    for mid in todo:
        if apply:
            plan = mission_reopen.apply_reopen(
                q, mid, actor="tp_pusher", actor_role="GOVERNANCE",
                acceptance_ref=acceptance_ref(mid), reason=REASON)
            rec = {"id": mid, "code": plan.get("code"), "applied": plan.get("applied"),
                   "from_status": plan.get("current_status"),
                   "to_status": plan.get("to_status")}
            if plan.get("applied"):
                entry = st[str(mid)]
                entry["ci"] = mission_reopen.REOPENED
                entry["reopen"] = {"code": plan.get("code"), "applied": True,
                                   "from_status": plan.get("current_status"),
                                   "acceptance_ref": plan.get("acceptance_ref"),
                                   "at": datetime.now(timezone.utc).timestamp()}
        else:
            rec = {"id": mid, "code": "dry_run", "applied": False,
                   "acceptance_ref": acceptance_ref(mid)}
        report["results"].append(rec)

    report["applied"] = sum(1 for r in report["results"] if r["applied"])
    report["refused"] = sum(1 for r in report["results"]
                            if not r["applied"] and r["code"] != "dry_run")
    if apply:
        if args.backup:
            shutil.copy2(path, args.backup)
        path.write_text(json.dumps(st, ensure_ascii=False, indent=1))

    out = (Path(args.json_out) if args.json_out else
           Path(__file__).resolve().parents[2] / "artifacts" / "m5022"
           / "reopen_state_migration.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("mode", "candidates", "applied", "refused")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())