#!/usr/bin/env python3
"""引导回路生产接线入口：把库内存量评价算成下一轮派发梯度并落库。

派发侧（wave_cycle / campaign_dispatcher 之类）每轮只需调用一次::

    python3 tools/guidance/plan_cycle.py --total-pool 40 --cycle-id WAVE-<n> --json-out /tmp/plan.json

读出：``guidance_outcome`` 里的全部结论（worker / 档位 / 结论）；
算出：S1..S5 份额与整数派单数（有界漂移 + 地板 + 探索整数保底）、逐 worker
      准入集合（L1..L5，禁排名）、探索预算账户；
写入：``guidance_cycle`` / ``task_gradient`` / ``exploration_budget``（幂等）。

口令只走环境变量（PGPASSWORD / TP_PG_PASSWORD），本文件不含任何凭据。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.guidance_loop import (  # noqa: E402
    GuidanceLoop,
    OutcomeLedger,
    OutcomeRecord,
    persist_cycle,
)
from services.guidance_loop.store import GuidanceStore, PsqlExecutor, SqliteExecutor  # noqa: E402

DEFAULT_TOTAL_POOL = 40


def load_ledger(store: GuidanceStore, measurement_cycle: Optional[str] = None) -> OutcomeLedger:
    """把库内结论回灌到内存账本（``measurement_cycle`` 为空则读全部历史）。"""
    ledger = OutcomeLedger(store=None)  # 只读回灌：不重复写结论表
    for row in store.outcomes(cycle_id=measurement_cycle):
        ledger.record(
            OutcomeRecord(
                mission_key=row["mission_key"],
                worker=row["worker"],
                tier=row["tier"],
                outcome=row["outcome"],
            )
        )
    return ledger


def plan_from_store(
    store: GuidanceStore,
    total_pool: int = DEFAULT_TOTAL_POOL,
    cycle_id: Optional[str] = None,
    measurement_cycle: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """算梯度（并可选落库），返回可直接给派发侧消费的载荷。"""
    ledger = load_ledger(store, measurement_cycle)
    result = GuidanceLoop(ledger, total_pool=total_pool).run(cycle_id=cycle_id)
    payload = result.to_dict()
    payload["dispatch_plan"] = {
        tier: count for tier, count in result.plan.counts.items()
    }
    payload["admission_allowed_tiers"] = {
        worker: row["allowed_tiers"] for worker, row in result.admission.items()
    }
    if persist:
        payload["write"] = persist_cycle(store, result, reason="guidance-loop:plan_cycle")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="引导回路：评价 → 派发梯度")
    parser.add_argument("--total-pool", type=int, default=DEFAULT_TOTAL_POOL)
    parser.add_argument("--cycle-id", default=None)
    parser.add_argument("--measurement-cycle", default=None, help="只用该周期的结论做统计")
    parser.add_argument("--dry-run", action="store_true", help="用内存 SQLite 库跑通不碰生产库")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    store = GuidanceStore(SqliteExecutor() if args.dry_run else PsqlExecutor(), organization_id=0)
    store.ensure_schema()
    payload = plan_from_store(
        store,
        total_pool=args.total_pool,
        cycle_id=args.cycle_id,
        measurement_cycle=args.measurement_cycle,
        persist=not args.dry_run,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())