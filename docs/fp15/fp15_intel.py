#!/usr/bin/env python3
"""FP15-BUDGET-ACCOUNTING ①/③ 取证脚本 —— 24h 全量，零手抄，只读。

产出 ``docs/fp15/fp15_intel.json``，每个数字都带生成它的 SQL（``queries``
节里就是原文），外部任何人可拿同一份 DSN 重跑对拍。

三件事
------
1. **现状**：窗口内终态单数 / ``budget_used`` 非空率 / 终局裁决分布
   （``budget_exhausted`` / ``no_progress`` …）/ 帽分布与贴帽率。
2. **可回填性分级**：历史单里"能从 result 事实回算出 tool_calls"的有多少、
   "连事实都没有"的有多少——后者就是必须如实报的不可回填缺口，
   不许用 0 或插值糊过去。
3. **窗口边界**：since/until 时间戳写进 JSON（脚本自己算，不接受手填）。

用法::

    FP15_DSN='postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql' \
        python docs/fp15/fp15_intel.py --hours 24

    # 默认 DSN 走 DATABASE_URL 或 postgresql:///tensorplatform
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

TERMINAL = ("succeeded", "failed", "stopped", "revoked", "orphan")

#: 推导用的 tool_calls 出处（与 backend/services/budget_accounting.py 同序）
USED_TOOL_CALLS_SQL = """
    COALESCE(
        CASE WHEN budget_used ? 'tool_calls'
              AND (budget_used->>'tool_calls') ~ '^[0-9]+$'
             THEN (budget_used->>'tool_calls')::bigint END,
        CASE WHEN (result->>'tool_calls') ~ '^[0-9]+$'
             THEN (result->>'tool_calls')::bigint END,
        CASE WHEN (result->'progress'->>'tool_calls') ~ '^[0-9]+$'
             THEN (result->'progress'->>'tool_calls')::bigint END,
        CASE WHEN (result->>'detail') ~ 'tool_calls=[0-9]+'
             THEN substring(result->>'detail' from 'tool_calls=([0-9]+)')::bigint END
    )
"""

#: 终局裁决（fail_stage）三处落地位置合一：列、result.fail_stage、evidence.checks
FAIL_STAGE_SQL = """
    COALESCE(
        NULLIF(fail_stage, ''),
        NULLIF(result->>'fail_stage', ''),
        NULLIF(result->'evidence'->'checks'->>'fail_stage', ''),
        NULLIF(result->>'stop_condition', '')
    )
"""


def _window(hours: float) -> tuple[str, str]:
    moment = datetime.now(timezone.utc)
    return ((moment - timedelta(hours=hours)).replace(microsecond=0).isoformat(),
            moment.replace(microsecond=0).isoformat())


def _rows(conn, sql: str, **params) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def collect(engine, *, hours: float = 24.0) -> dict[str, Any]:
    since, until = _window(hours)
    where = ("status = ANY(:terminal) AND finished_at >= :since AND finished_at <= :until")
    params = {"terminal": list(TERMINAL), "since": since, "until": until}
    queries: dict[str, str] = {}
    out: dict[str, Any] = {
        "schema": "fp15.intel.v1",
        "generated_at": until,
        "window": {"since": since, "until": until, "hours": hours,
                   "definition": "tensor_dev_missions.finished_at in [since, until]"},
        "terminal_statuses": list(TERMINAL),
    }

    with engine.connect() as conn:
        sql = f"""
            SELECT count(*) AS terminal_total,
                   count(*) FILTER (WHERE budget_used IS NOT NULL AND budget_used <> '{{}}'::jsonb)
                       AS budget_used_nonempty,
                   count(*) FILTER (WHERE budget_used ? 'tool_calls') AS budget_used_has_tool_calls
            FROM tensor_dev_missions WHERE {where}
        """
        queries["window_totals"] = " ".join(sql.split())
        out.update(_rows(conn, sql, **params)[0])

        sql = f"""
            SELECT {FAIL_STAGE_SQL} AS verdict, count(*) AS n
            FROM tensor_dev_missions WHERE {where}
            GROUP BY 1 ORDER BY 2 DESC
        """
        queries["verdict_distribution"] = " ".join(sql.split())
        out["verdict_distribution"] = _rows(conn, sql, **params)

        sql = f"""
            SELECT COALESCE(budget->>'max_tool_calls', 'unset') AS cap,
                   count(*) AS n,
                   count(*) FILTER (WHERE status = 'succeeded') AS succ,
                   count(*) FILTER (WHERE {USED_TOOL_CALLS_SQL} IS NOT NULL) AS usage_known,
                   count(*) FILTER (
                       WHERE COALESCE(budget->>'max_tool_calls', '') ~ '^[0-9]+$'
                         AND {USED_TOOL_CALLS_SQL} >= COALESCE(budget->>'max_tool_calls','')::bigint - 5
                   ) AS near_cap,
                   count(*) FILTER (
                       WHERE COALESCE(budget->>'max_tool_calls', '') ~ '^[0-9]+$'
                         AND {USED_TOOL_CALLS_SQL} >= COALESCE(budget->>'max_tool_calls','')::bigint
                   ) AS at_cap,
                   round(percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY {USED_TOOL_CALLS_SQL})
                       FILTER (WHERE status = 'succeeded')::numeric, 1) AS p50_tool_calls_succ
            FROM tensor_dev_missions WHERE {where}
            GROUP BY 1 ORDER BY 2 DESC
        """
        queries["cap_distribution"] = " ".join(sql.split())
        cap_rows = _rows(conn, sql, **params)
        for row in cap_rows:
            known = int(row["usage_known"] or 0)
            row["rate"] = round(100.0 * int(row["succ"]) / int(row["n"]), 2) if row["n"] else None
            row["near_cap_rate"] = round(100.0 * int(row["near_cap"]) / known, 2) if known else None
            row["at_cap_rate"] = round(100.0 * int(row["at_cap"]) / known, 2) if known else None
        out["cap_distribution"] = cap_rows

        sql = f"""
            SELECT
              count(*) FILTER (WHERE budget_used ? 'tool_calls') AS from_budget_used,
              count(*) FILTER (WHERE (result->>'tool_calls') ~ '^[0-9]+$') AS from_result_tool_calls,
              count(*) FILTER (WHERE (result->'progress'->>'tool_calls') ~ '^[0-9]+$')
                  AS from_heartbeat_progress,
              count(*) FILTER (WHERE (result->>'detail') ~ 'tool_calls=[0-9]+') AS from_detail
            FROM tensor_dev_missions WHERE {where}
        """
        queries["usage_fact_availability"] = " ".join(sql.split())
        fact_rows = _rows(conn, sql, **params)[0]
        out["usage_fact_availability"] = fact_rows

        sql = f"""
            SELECT CASE WHEN {USED_TOOL_CALLS_SQL} IS NOT NULL THEN 'backfillable'
                        ELSE 'not_backfillable' END AS klass,
                   count(*) AS n
            FROM tensor_dev_missions WHERE {where}
            GROUP BY 1 ORDER BY 2 DESC
        """
        queries["backfill_classification"] = " ".join(sql.split())
        klass = _rows(conn, sql, **params)
        out["backfill_classification"] = klass
        out["backfill"] = {
            "backfillable": next((r["n"] for r in klass if r["klass"] == "backfillable"), 0),
            "not_backfillable": next((r["n"] for r in klass if r["klass"] == "not_backfillable"), 0),
            "applied": False,
            "note": ("只统计不写库：历史行 budget_used 的变更属不可逆证据面改动，"
                     "由 owner 决定是否 apply；本脚本只给出可回填/不可回填的实数。"),
        }
        sql = f"""
            SELECT id, status, COALESCE(budget->>'max_tool_calls','unset') AS cap
            FROM tensor_dev_missions
            WHERE {where} AND {USED_TOOL_CALLS_SQL} IS NULL
            ORDER BY id DESC LIMIT 20
        """
        queries["not_backfillable_sample"] = " ".join(sql.split())
        out["not_backfillable_sample"] = _rows(conn, sql, **params)

    out["queries"] = queries
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FP15 budget-accounting evidence collector")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--dsn", default=os.environ.get("FP15_DSN")
                        or os.environ.get("DATABASE_URL")
                        or "postgresql:///tensorplatform")
    parser.add_argument("--out", default="docs/fp15/fp15_intel.json")
    args = parser.parse_args(argv)

    engine = create_engine(args.dsn, future=True)
    report = collect(engine, hours=args.hours)
    report["dsn_kind"] = args.dsn.split("://", 1)[0]
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parents[2] / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({k: report[k] for k in
                      ("window", "terminal_total", "budget_used_nonempty",
                       "verdict_distribution", "cap_distribution",
                       "backfill_classification", "backfill")},
                     ensure_ascii=False, indent=2, default=str))
    print(f"\nartifact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())