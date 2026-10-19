#!/usr/bin/env python3
"""refill_check 判定纯函数回归（剧本案例 2=m49 误续的防复发）。

  V1 m49 复刻：行内 agent_lost + 账本 [refilled, worktree_bug_crash,
     operator_rejected] → 拒绝（旧 prose 纪律下被误续的正是这条）
  V2 infra 干净：agent_lost 无驳回标记 → 可续
  V3 owner 锁：行内 owner_stopped → 拒绝
  V4 honest FAIL：finished_without_acceptance → 拒绝
  V5 budget_exceeded 无账本 → 可续
  V6 在飞（stop=None, running）→ 拒绝
  V7 未知 stop 值 → 拒绝
运行：python3 test_refill_check.py；退出码 0=全过。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refill_check import EXIT_OK, EXIT_REFUSE, verdict  # noqa: E402

CASES = [
    ("V1 m49 复刻（行滞后于账本驳回）", "agent_lost", "failed",
     [{"ts": "2026-10-19 11:25:00", "mission": 49, "status": "refilled"},
      {"ts": "2026-10-19 11:50:00", "mission": 49, "status": "worktree_bug_crash"},
      {"ts": "2026-10-19 11:58:00", "mission": 49, "status": "operator_rejected"}],
     EXIT_REFUSE, "驳回过即锁死"),
    ("V2 infra 干净", "agent_lost", "failed",
     [{"ts": "2026-10-19 12:00:00", "mission": 50, "status": "refilled"}],
     EXIT_OK, "infra"),
    ("V3 owner 锁", "owner_stopped", "failed", [], EXIT_REFUSE, "owner"),
    ("V4 honest FAIL", "finished_without_acceptance", "failed", [],
     EXIT_REFUSE, "honest"),
    ("V5 budget_exceeded 无账本", "budget_exceeded", "failed", [],
     EXIT_OK, "infra"),
    ("V6 在飞", None, "running", [], EXIT_REFUSE, "在飞"),
    ("V7 未知值", "mystery_condition", "failed", [], EXIT_REFUSE, "非 infra"),
]


def main() -> int:
    npass = 0
    for name, stop, status, ledger, want_rc, want_frag in CASES:
        rc, msg = verdict(stop, status, ledger)
        ok = rc == want_rc and want_frag in msg
        print(f"{'PASS' if ok else 'FAIL'}  {name}  rc={rc}")
        if not ok:
            print("  msg:", msg[:160])
        npass += bool(ok)
    print(f"{npass}/{len(CASES)}")
    return 0 if npass == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
