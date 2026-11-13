#!/usr/bin/env python3
"""预算记账覆盖率闸：新生终态单里 ``budget_used`` 非空率是否达标（机器可算）。

判据（任务书 ⑤，写死在参数默认值里，不是口头约定）
----------------------------------------------------
* 分母 = ``finished_at >= --since`` 的终态行（vocab 契约 terminal + 两个既有裁决词）；
* 分子 = 其中 ``budget_used`` 含四键 ``{tool_calls, tokens, wall_seconds, source}`` 的行；
* ``n < --min-n``（默认 5）→ 退出码 2「样本不足」，**不算通过**（未跑 ≠ 绿）；
* ``rate < --threshold``（默认 0.90）→ 退出码 1。

口径要点
--------
* 分子只认四键齐全（``{}``、``{"tool_calls_used": 12}`` 这类历史形状都不算记过账）——
  否则「有字段但不完整」会被算成已记账，覆盖率虚高。
* 每行同时输出 ``by_status`` 与 ``by_source``：终态词分布与记账出处分布，报告直接引用，
  零手抄。

用法::

    python tools/budget_accounting_coverage.py --since 2026-11-13T11:19:02+00:00
    python tools/budget_accounting_coverage.py --since-file artifacts/fp15/mechanism_live.json
    python tools/budget_accounting_coverage.py --hours 1 --json artifacts/fp15/coverage.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

TERMINAL_SQL = (
    "('succeeded','failed','stopped','revoked','orphan','budget_exhausted','no_progress')"
)
KEYS_SQL = "ARRAY['tool_calls','tokens','wall_seconds','source']"


def _dsn() -> str:
    dsn = os.environ.get("BUDGET_DB_DSN") or os.environ.get("DATABASE_URL")
    if dsn:
        return dsn.replace("postgresql+psycopg://", "postgresql://")
    # 平台默认（容器内 postgres 超级用户 socket 连接）
    return "dbname=tensorplatform"


def _connect(dsn: str):
    import psycopg

    # kwargs 形式对 URI 与 libpq 关键字串都成立（拼 ?client_encoding= 会把
    # `dbname=tensorplatform` 这种串拼成库名的一部分 —— 2026-11-13 实测踩过）
    return psycopg.connect(dsn, client_encoding="UTF8")


SQL = f"""
WITH win AS (
  SELECT id, status, finished_at, budget_used
    FROM tensor_dev_missions
   WHERE status IN {TERMINAL_SQL}
     AND finished_at >= %(since)s
), tagged AS (
  SELECT status,
         (budget_used IS NOT NULL
          AND jsonb_typeof(budget_used) = 'object'
          AND budget_used ?& {KEYS_SQL}::text[])                AS recorded,
         COALESCE(budget_used->>'recorded_by',
                  CASE WHEN budget_used IS NOT NULL
                        AND budget_used ?& {KEYS_SQL}::text[]
                       THEN 'app_supplied' ELSE 'missing' END)  AS recorded_by
    FROM win
)
SELECT (SELECT count(*) FROM win)                                  AS n,
       (SELECT count(*) FROM tagged WHERE recorded)                 AS non_empty,
       (SELECT coalesce(json_object_agg(status, n), '{{}}')::text
          FROM (SELECT status, count(*) n FROM win GROUP BY status) s)          AS by_status,
       (SELECT coalesce(json_object_agg(recorded_by, n), '{{}}')::text
          FROM (SELECT recorded_by, count(*) n FROM tagged GROUP BY recorded_by) s)
                                                                               AS by_source,
       (SELECT coalesce(json_object_agg(status, n), '{{}}')::text
          FROM (SELECT status, count(*) n FROM tagged WHERE NOT recorded GROUP BY status) s)
                                                                               AS missing_by_status;
"""


def measure(since: datetime, dsn: str | None = None) -> dict[str, Any]:
    with _connect(dsn or _dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(SQL, {"since": since})
            n, non_empty, by_status, by_source, missing_by_status = cur.fetchone()
    rate = (non_empty / n) if n else None
    return {
        "since": since.isoformat(),
        "n": int(n),
        "non_empty": int(non_empty),
        "empty": int(n) - int(non_empty),
        "rate": None if rate is None else round(100.0 * rate, 2),
        "by_status": json.loads(by_status),
        "by_source": json.loads(by_source),
        "missing_by_status": json.loads(missing_by_status),
        "schema": "fp15.budget_accounting_coverage.v1",
    }


def verdict(report: dict[str, Any], *, min_n: int, threshold: float) -> tuple[str, int]:
    if report["n"] < min_n:
        return f"insufficient_sample(n={report['n']} < {min_n})", 2
    if report["rate"] is not None and report["rate"] / 100.0 >= threshold:
        return f"pass(rate={report['rate']}% >= {threshold * 100:.0f}%)", 0
    return f"fail(rate={report['rate']}% < {threshold * 100:.0f}%)", 1


def _since_from_args(args: argparse.Namespace) -> datetime:
    if args.since_file:
        blob = json.loads(Path(args.since_file).read_text())
        for key in ("live_at", "since", "applied_at", "captured_at"):
            if key in blob:
                return datetime.fromisoformat(blob[key].replace("Z", "+00:00"))
        raise SystemExit(f"{args.since_file} 里没有 live_at/since/applied_at/captured_at")
    if args.since:
        return datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - timedelta(hours=args.hours)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="budget_used coverage gate for new terminal missions")
    parser.add_argument("--since", help="ISO 时间戳；缺省用 --hours")
    parser.add_argument("--since-file", help="机制生效时刻工件（JSON，含 live_at）")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--min-n", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--json", default="artifacts/fp15/budget_coverage.json")
    args = parser.parse_args(argv)

    since = _since_from_args(args)
    report = measure(since)
    text, code = verdict(report, min_n=args.min_n, threshold=args.threshold)
    report["verdict"] = text
    report["threshold"] = args.threshold
    report["min_n"] = args.min_n
    out = REPO / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nverdict: {text}  (artifact: {out.relative_to(REPO)})")
    return code


if __name__ == "__main__":          # pragma: no cover
    raise SystemExit(main())