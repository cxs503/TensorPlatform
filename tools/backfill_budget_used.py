#!/usr/bin/env python3
"""历史终态单的预算记账补录（幂等、可回滚、零手抄）。

为什么要有补录
--------------
机制修复（migrations/125 的终态触发器）只对**此后**进入终态的行生效；修复之前
落在库里的终态单账依然是空的（2026-11-13 基线：16583 条终态行里 16571 条空，
99.93%）。「43 单预算耗尽裁决外部不可复核」要能复核，历史行也得有账。

推导口径 = 单一真源
-------------------
每一行的记账都由 ``backend/services/budget_accounting.py:budget_used_for_row`` 推
出来（与 API 落库、与触发器同一套优先级：result 事实 > 心跳 progress > runner
detail > 服务端时钟上界）。**墙钟用 ``finished_at - created_at``**，不是
``now() - created_at``——补录跑在几周后，用 now() 会把耗时虚增成月。
出处标签照实写 ``source=...``，无数据的字段记 0 且标 ``unavailable``，不编数。

幂等 / 可回滚
-------------
* 只补 ``budget_used`` 为空（NULL 或 ``{}``）的行；已记账的行一律不动
  （``WHERE budget_used IS NULL OR budget_used = '{}'``）。
* 默认 ``--dry-run``；``--apply`` 才写库。
* 写库前把 ``{id: {old, new}}`` 落到 ``--ledger``（默认
  ``artifacts/fp15/budget_backfill_ledger.json``），回滚脚本据此还原旧值
  （旧值是 ``{}`` 本身无信息量，但留痕是纪律）。

用法::

    python tools/backfill_budget_used.py                       # 干跑 + 统计
    python tools/backfill_budget_used.py --apply --limit 500   # 小批试写
    python tools/backfill_budget_used.py --apply               # 全量
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.budget_accounting import (          # noqa: E402
    BUDGET_USED_KEYS,
    budget_used_for_row,
    dumps,
    usage_is_recorded,
)

TERMINAL = ("succeeded", "failed", "stopped", "revoked", "orphan",
            "budget_exhausted", "no_progress")
BATCH = 500


def _dsn(explicit: str | None) -> str:
    dsn = explicit or os.environ.get("BUDGET_DB_DSN") or os.environ.get("DATABASE_URL") \
        or "dbname=tensorplatform"
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def _connect(dsn: str):
    import psycopg
    return psycopg.connect(dsn, client_encoding="UTF8")


def _as_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def plan_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """把「空账的终态行」映射成待写记录（纯函数，便于单测）。"""
    planned = []
    for row in rows:
        mid, result, created_at, finished_at, budget_used = row
        if usage_is_recorded(budget_used):
            continue
        anchor = _as_time(finished_at) or _as_time(created_at) or datetime.now(timezone.utc)
        record = budget_used_for_row({"result": result, "created_at": created_at},
                                     now=anchor)
        record["recorded_by"] = "backfill_20261113"
        missing = [k for k in BUDGET_USED_KEYS if k not in record]
        assert not missing, f"mission {mid}: 推导结果缺键 {missing}"
        planned.append({"id": int(mid), "old": budget_used or {}, "new": record})
    return planned


def measure(cur, *, where_time: str = "") -> dict[str, Any]:
    cur.execute(f"""
        SELECT count(*)                                        AS n,
               count(*) FILTER (WHERE budget_used IS NULL
                                  OR budget_used = '{{}}'::jsonb) AS empty
          FROM tensor_dev_missions
         WHERE status = ANY(%s) {where_time}
    """, (list(TERMINAL),))
    n, empty = cur.fetchone()
    return {"n_terminal": int(n), "n_empty": int(empty),
            "pct_empty": round(100.0 * empty / max(n, 1), 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="backfill budget_used for legacy terminal missions")
    parser.add_argument("--dsn", help="libpq DSN / SQLAlchemy URL（缺省 env BUDGET_DB_DSN|DATABASE_URL）")
    parser.add_argument("--apply", action="store_true", help="真写库（缺省干跑）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少行（0=全量）")
    parser.add_argument("--status", nargs="*", choices=TERMINAL, help="只处理这些终态词")
    parser.add_argument("--ledger", default="artifacts/fp15/budget_backfill_ledger.json")
    parser.add_argument("--out", default="artifacts/fp15/budget_backfill_report.json")
    args = parser.parse_args(argv)

    statuses = tuple(args.status) if args.status else TERMINAL
    dsn = _dsn(args.dsn)
    before: dict[str, Any]
    planned: list[dict[str, Any]] = []
    written = 0

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            before = measure(cur)
            limit_sql = f"LIMIT {int(args.limit)}" if args.limit else ""
            cur.execute(
                """
                SELECT id, result, created_at, finished_at, budget_used
                  FROM tensor_dev_missions
                 WHERE status = ANY(%s)
                   AND (budget_used IS NULL OR budget_used = '{}'::jsonb)
                 ORDER BY id
                 """ + limit_sql,
                (list(statuses),),
            )
            while True:
                batch = cur.fetchmany(BATCH)
                if not batch:
                    break
                planned.extend(plan_rows(batch))
                if args.limit and len(planned) >= args.limit:
                    planned = planned[: args.limit]
                    break
            conn.commit()

            if args.apply and planned:
                for item in planned:
                    cur.execute("""
                        UPDATE tensor_dev_missions
                           SET budget_used = CAST(%s AS jsonb)
                         WHERE id = %s
                           AND (budget_used IS NULL OR budget_used = '{}'::jsonb)
                    """, (dumps(item["new"]), item["id"]))
                    written += int(cur.rowcount or 0)
                conn.commit()
                after = measure(cur)
            else:
                after = before

    ledger = REPO / args.ledger
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"schema": "fp15.budget_backfill_ledger.v1",
                                  "at": datetime.now(timezone.utc).isoformat(),
                                  "applied": bool(args.apply),
                                  "rows": planned}, ensure_ascii=False, indent=1))

    by_status: dict[str, int] = {}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, count(*) FROM tensor_dev_missions
                 WHERE status = ANY(%s) AND budget_used ?& %s::text[]
                 GROUP BY status
            """, (list(statuses), list(BUDGET_USED_KEYS)))
            by_status = {row[0]: int(row[1]) for row in cur.fetchall()}

    report = {
        "schema": "fp15.budget_backfill_report.v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "applied": bool(args.apply),
        "dry_run": not args.apply,
        "planned": len(planned),
        "written": written,
        "statuses": list(statuses),
        "before": before,
        "after": after,
        "recorded_by_status": by_status,
        "ledger": str(ledger.relative_to(REPO)),
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":          # pragma: no cover
    raise SystemExit(main())