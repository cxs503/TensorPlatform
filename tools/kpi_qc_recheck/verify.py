#!/usr/bin/env python3
"""KPI-QC-RECHECK 独立复算（第二实现）—— 用于 L1 数学验证 / L4 benchmark。

刻意**不复用** ``measure.py`` 的聚合函数：本文件从 CSV 原始行重新数一遍，
数法也不同（set 交集 + 逐 PR 扫描，而 measure.py 用 per-mission 标志位折叠），
因此两条实现的任何分歧都只能来自口径实现差异，不是同一个 bug 复读。

三条路径::

    # ① 离线复算（无需数据库；集群节点可跑）：raw CSV ↔ kpi_result.json
    python3 -m tools.kpi_qc_recheck.verify --raw-dir artifacts/kpi_qc_recheck \
        --result artifacts/kpi_qc_recheck/kpi_result.json --out artifacts/kpi_qc_recheck/benchmark.json

    # ② 直连数据库复算（手写单条 SQL，与 measure.py 的查询模板不同源）
    python3 -m tools.kpi_qc_recheck.verify --dsn "" --days 30 --out ... 

    # ③ 只用 SQL 文件（psql 通道）：--sql-out 打印/落盘手写 SQL

退出码：0=全部一致；2=有指标不一致（fail-closed，不粉饰）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any

from tools.kpi_qc_recheck import spec

# ── 手写单条 SQL（与 measure.py 的 queries 模板不同源）──────────────────
INDEPENDENT_SQL = """
WITH den AS (
  SELECT DISTINCT mission_id,
         COALESCE(detail->>'repo','') AS repo,
         COALESCE(detail->>'pr','')   AS pr
    FROM mission_lifecycle_log
   WHERE action = 'pr_gate'
     AND detail->>'state' = 'merged'
     AND created_at > now() - make_interval(days => %(days)s)
), num AS (
  SELECT DISTINCT mission_id
    FROM outcome_evaluations
   WHERE reviewer_kind = 'independent'
     AND review_status = 'reviewed'
     AND status = 'confirmed'
), anyrev AS (
  SELECT DISTINCT mission_id
    FROM outcome_evaluations
   WHERE review_status = 'reviewed' AND status = 'confirmed'
)
SELECT (SELECT count(*) FROM den)                                  AS n_merged_prs,
       (SELECT count(*) FROM den d JOIN num n USING (mission_id))  AS m_ind_reviewed,
       (SELECT count(*) FROM den d JOIN anyrev a USING (mission_id)) AS d1_any_confirmed,
       round(100.0 * (SELECT count(*) FROM den d JOIN num n USING (mission_id))
             / NULLIF((SELECT count(*) FROM den), 0), 2)           AS rate_pct
"""


def _rel_err(ref: float, got: float) -> float:
    if ref == 0:
        return 0.0 if got == 0 else float("inf")
    return abs(got - ref) / abs(ref)


def recompute_from_raw(raw_dir: str) -> dict[str, Any]:
    """第二实现：从 raw CSV 重数（set 交集法）。"""
    with open(os.path.join(raw_dir, "raw_denominator.csv"), encoding="utf-8") as fh:
        den = list(csv.DictReader(fh))
    with open(os.path.join(raw_dir, "raw_evaluations.csv"), encoding="utf-8") as fh:
        ev = list(csv.DictReader(fh))

    # 分母：set 去重（与 measure 的 dict-best 去重是两种实现）
    pr_keys = {(r["mission_id"], r["repo"], r["pr"]) for r in den}
    missions_of_prs = {k[0] for k in pr_keys}

    ind = {r["mission_id"] for r in ev
           if r["reviewer_kind"] == "independent"
           and r["review_status"] == "reviewed" and r["status"] == "confirmed"}
    anyrev = {r["mission_id"] for r in ev
              if r["review_status"] == "reviewed" and r["status"] == "confirmed"}

    m = len([k for k in pr_keys if k[0] in ind])
    d1 = len([k for k in pr_keys if k[0] in anyrev])
    n = len(pr_keys)
    return {
        "n_merged_prs": n,
        "m_ind_reviewed": m,
        "d1_any_confirmed": d1,
        "rate_pct": round(100.0 * m / n, 2) if n else 0.0,
        "distinct_missions": len(missions_of_prs),
        "impl": "verify.recompute_from_raw (CSV set-intersection, independent of measure.aggregate)",
    }


def recompute_from_db(days: int, dsn: str | None = None) -> dict[str, Any]:
    """第二实现：手写单条 SQL 直连复算。"""
    import psycopg
    if not dsn:
        from tools.audit.repro_replay import default_db_url
        dsn = default_db_url()
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(INDEPENDENT_SQL, {"days": int(days)})
            cols = [d.name for d in cur.description]
            row = dict(zip(cols, cur.fetchone()))
    out = {k: (float(v) if k == "rate_pct" else int(v or 0)) for k, v in row.items()}
    out["impl"] = "verify.INDEPENDENT_SQL (hand-written single statement, not queries.py templates)"
    return out


def compare(ref: dict[str, Any], got: dict[str, Any], days: int) -> dict[str, Any]:
    """ref=独立复算；got=kpi_result.json 的自报值。逐项给误差与判决。"""
    metrics = []

    def add(name: str, ref_v: float, got_v: float, tol: float, tol_kind: str) -> None:
        r = _rel_err(float(ref_v), float(got_v))
        metrics.append({
            "metric": name, "ref": ref_v, "got": got_v,
            "rel_err": round(r, 12), "tol": tol, "tol_kind": tol_kind,
            "pass": (float(ref_v) == float(got_v)) if tol == 0.0 else (r <= tol),
        })

    add("denominator_merged_prs", ref["n_merged_prs"], got["denominator"]["merged_prs"],
        spec.COUNT_REL_TOL, "exact_count")
    add("numerator_ind_reviewed_prs", ref["m_ind_reviewed"], got["numerator"]["ind_reviewed_prs"],
        spec.COUNT_REL_TOL, "exact_count")
    add("kpi_current_pct", ref["rate_pct"], got["current"], spec.RATE_REL_TOL, "rel")
    # 口径阶梯 D1 也必须对上（防止主读数对、阶梯错的口径漂移）
    add("ladder_D1_prs", ref["d1_any_confirmed"], got["ladder"]["D1_any_confirmed"]["prs"],
        spec.COUNT_REL_TOL, "exact_count")

    ok = all(m["pass"] for m in metrics)
    return {
        "all_pass": ok,
        "n_metrics": len(metrics),
        "n_pass": sum(1 for m in metrics if m["pass"]),
        "metrics": metrics,
        "ref_impl": ref.get("impl", ""),
        "got_source": "kpi_result.json (measure.py aggregate)",
        "window_days": int(days),
        "tolerances": {"count": "exact equality", "rate_rel": spec.RATE_REL_TOL},
        "repro_cmd": ("python3 -m tools.kpi_qc_recheck.verify --raw-dir artifacts/kpi_qc_recheck "
                      "--result artifacts/kpi_qc_recheck/kpi_result.json "
                      "--out artifacts/kpi_qc_recheck/benchmark.json"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KPI-QC-RECHECK 独立复算/benchmark")
    ap.add_argument("--raw-dir", default=None, help="raw CSV 目录（第二实现离线复算）")
    ap.add_argument("--result", default=None, help="kpi_result.json（自报值）")
    ap.add_argument("--db", action="store_true", help="改用手写 SQL 直连复算")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--days", type=int, default=spec.DEFAULT_WINDOW_DAYS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sql-out", default=None, help="把独立 SQL 落盘（psql 通道用）")
    args = ap.parse_args(argv)

    if args.sql_out:
        os.makedirs(os.path.dirname(args.sql_out) or ".", exist_ok=True)
        with open(args.sql_out, "w", encoding="utf-8") as fh:
            fh.write("-- KPI-QC-RECHECK 独立复算 SQL（verify.INDEPENDENT_SQL 原文）\n")
            fh.write(INDEPENDENT_SQL.replace("%(days)s", str(args.days)))
        print(f"wrote {args.sql_out}")

    if args.db:
        ref = recompute_from_db(args.days, args.dsn)
    elif args.raw_dir:
        ref = recompute_from_raw(args.raw_dir)
    else:
        ap.error("需要 --raw-dir 或 --db")
    if not args.result:
        print(json.dumps(ref, ensure_ascii=False, indent=2))
        return 0

    with open(args.result, encoding="utf-8") as fh:
        got = json.load(fh)
    bench = compare(ref, got, args.days)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(bench, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({k: bench[k] for k in ("all_pass", "n_metrics", "n_pass", "ref_impl")},
                     ensure_ascii=False, indent=2))
    for m in bench["metrics"]:
        print(f"  {'PASS' if m['pass'] else 'FAIL'} {m['metric']}: ref={m['ref']} got={m['got']} "
              f"rel_err={m['rel_err']}")
    return 0 if bench["all_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())