#!/usr/bin/env python3
"""worker 画像的独立复算（交叉校验）——纯只读，第二次计算走不同的实现路径。

目的：把画像里的关键数字与一条**独立 SQL**（时间差在 SQL 里算、群体统计用
``FILTER``/``avg`` 而非 Python datetime）逐项比对，给出相对误差。这是本任务
「误差判定」的基准来源：本工具无物理基准可对标，唯一可用基准是独立复算，
因此逐项报相对误差并要求 <3%（实际应为 0，若不为 0 说明口径/实现分歧）。

用法：
  python3 -m tools.worker_portrait.crosscheck --report artifacts/worker_portraits/report.json \
      --out artifacts/worker_portraits/independent_recompute.json
  python3 -m tools.worker_portrait.crosscheck --report ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional

REF_SQL = """
WITH claim AS (
  SELECT session_id, min(created_at) AS claim_at
  FROM tensor_agent_session_events
  WHERE kind = 'note' AND payload->>'subtype' = 'mission_claimed'
  GROUP BY 1
), m AS (
  SELECT d.id, d.claimed_by AS worker, d.status, d.created_at, d.finished_at, c.claim_at
  FROM tensor_dev_missions d
  LEFT JOIN claim c ON c.session_id = d.session_id
  WHERE d.claimed_by IS NOT NULL AND d.claimed_by <> ''
), w AS (
  SELECT id, worker, status,
         extract(epoch FROM (claim_at - created_at)) AS pickup_s,
         extract(epoch FROM (finished_at - coalesce(claim_at, created_at))) AS wall_s
  FROM m
)
SELECT json_build_object(
  'missions_total', (SELECT count(*) FROM w),
  'workers_total',  (SELECT count(DISTINCT worker) FROM w),
  'delivered',      (SELECT count(*) FROM w WHERE status IN ('succeeded','awaiting_pr_merge')),
  'settled',        (SELECT count(*) FROM w WHERE status IN
                       ('succeeded','awaiting_pr_merge','failed','revoked','stopped')),
  'pickup_n',       (SELECT count(*) FROM w WHERE pickup_s IS NOT NULL AND pickup_s >= 0),
  'pickup_mean',    (SELECT avg(pickup_s) FROM w WHERE pickup_s IS NOT NULL AND pickup_s >= 0),
  'wall_n',         (SELECT count(*) FROM w WHERE wall_s IS NOT NULL AND wall_s >= 0),
  'within_7200',    (SELECT count(*) FROM w WHERE wall_s IS NOT NULL AND wall_s >= 0 AND wall_s <= 7200),
  'per_worker',     (SELECT json_agg(t) FROM (
                       SELECT worker, count(*) AS n
                       FROM w GROUP BY worker ORDER BY count(*) DESC, worker LIMIT 8) t)
)::text
""".strip()

REL_TOL = 0.03  # 判据：相对误差 <3%；独立复算预期 0


def _rel(tool_value: float, ref_value: float) -> float:
    if ref_value == 0:
        return 0.0 if tool_value == 0 else float("inf")
    return abs(float(tool_value) - float(ref_value)) / abs(float(ref_value))


def compare(report: Dict[str, Any], ref: Dict[str, Any], tol: float = REL_TOL) -> Dict[str, Any]:
    """把画像 report 与独立 SQL 复算 ref 逐项比对，返回 {items, ok, max_rel}。"""
    cov = report["coverage"]
    pooled = report["pooled"]
    items: List[Dict[str, Any]] = []

    def add(name: str, tool_value, ref_value, unit: str = ""):
        rel = _rel(tool_value, ref_value)
        items.append({
            "metric": name,
            "tool": round(float(tool_value), 6),
            "reference": round(float(ref_value), 6),
            "abs_err": round(abs(float(tool_value) - float(ref_value)), 6),
            "rel_err": round(rel, 6),
            "unit": unit,
            "pass": rel < tol,
        })

    add("missions_total", cov["missions_total"], ref["missions_total"], "单")
    add("workers_total", cov["workers_total"], ref["workers_total"], "个")
    add("delivered", pooled["n_delivered"], ref["delivered"], "单")
    add("settled", pooled["n_settled"], ref["settled"], "单")
    add("pickup_n", pooled["pickup_s"]["n"], ref["pickup_n"], "单")
    add("pickup_mean_s", pooled["pickup_s"]["mean"], ref["pickup_mean"], "s")
    add("wall_n", pooled["wall_s"]["n"], ref["wall_n"], "单")
    add("within_7200", pooled["completion_7200"]["n_within"], ref["within_7200"], "单")
    add("success_rate", pooled["success_rate"],
        (ref["delivered"] / ref["settled"]) if ref["settled"] else 0.0, "")
    add("completion_7200_rate", pooled["completion_7200"]["rate"],
        (ref["within_7200"] / ref["wall_n"]) if ref["wall_n"] else 0.0, "")

    by_worker = {p["worker"]: p["n_missions"] for p in report["workers"]}
    for row in ref.get("per_worker", []):
        worker = row["worker"]
        if worker in by_worker:
            add(f"n_missions[{worker}]", by_worker[worker], row["n"], "单")

    worst = max(items, key=lambda i: i["rel_err"]) if items else None
    return {
        "items": items,
        "n_items": len(items),
        "max_rel_err": worst["rel_err"] if worst else None,
        "worst_metric": worst["metric"] if worst else None,
        "tol": tol,
        "ok": all(i["pass"] for i in items),
    }


def fetch_reference(database: str = "tensorplatform", user: str = "postgres",
                    psql_bin: str = "psql") -> Dict[str, Any]:
    proc = subprocess.run(
        [psql_bin, "-U", user, "-d", database, "-tA", "-v", "ON_ERROR_STOP=1", "-c", REF_SQL],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"独立复算 SQL 失败: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout.strip())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="worker_portrait.crosscheck",
                                 description="worker 画像独立复算（只读交叉校验）")
    ap.add_argument("--report", default="artifacts/worker_portraits/report.json")
    ap.add_argument("--out", default="artifacts/worker_portraits/independent_recompute.json")
    ap.add_argument("--database", default="tensorplatform")
    ap.add_argument("--db-user", default="postgres")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    with open(args.report, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    ref = fetch_reference(args.database, args.db_user)
    result = compare(report, ref)
    result["sql"] = REF_SQL
    print(f"[crosscheck] items={result['n_items']} max_rel_err={result['max_rel_err']} "
          f"worst={result['worst_metric']} ok={result['ok']}")
    for item in result["items"]:
        print(f"  {'OK ' if item['pass'] else 'BAD'} {item['metric']}: "
              f"tool={item['tool']} ref={item['reference']} rel={item['rel_err']}")
    if args.dry_run:
        print("[dry-run] 不写文件")
        return 0 if result["ok"] else 1
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print(f"[write] {args.out}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())