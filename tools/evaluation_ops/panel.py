"""评价时效性监控面板（IN35-N07 第 ⑤ 条）：cohort 覆盖率趋势 + 积压排空速率 + in-flight 延迟分布。

設計取舍
--------
面板**只做投影**：所有数字都从 ``cohorts`` / ``triggers`` / ``slo`` 已有的函数取，
面板自己不定义任何新口径（否则就是一个新的漂移源）。面板输出是**机器可读 JSON**
（``schema='eval-panel/1'``），图由 ``plot_timeliness.py`` 读同一份 JSON 画，避免「图里的数
和表里的数不是同一次计算的」。

三个面（对应任务书 ⑤）
----------------------
1. ``coverage_trend``：逐 cohort 的 ``missions_total / missions_decided / coverage``，
   来自 ``cohorts.compute_cohorts``（成员只由 ``finished_at`` 周界决定 → 窗口无关）。
   附带 ``window_invariance``：同一批 cohort 在 2/7/30/90 天视图下逐字段差异数。
2. ``backlog``：当前 pending 行数、阈值、逐次排空的 ``pending_before/after/drained/
   drain_share/elapsed``，以及排空速率（行/小时，按最近一次排空的实际墙钟）。
3. ``inflight_latency``：事件表里的时延分位 + 直方图 + 按 outcome 分布。

一致性校验 ``panel_consistency()``
--------------------------------
抽样 N 个面板单元（cohort 覆盖率分子/分母、积压行数、时延分位），**用独立 SQL 从库里
重算**并比对。面板与库不一致 = 面板撒谎，必须当 FAIL。抽样单元显式列在结果里（可复核）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from . import cohorts as ch
from . import triggers
from . import views as vw

SCHEMA = "eval-panel/1"
DEFAULT_VIEW_DAYS: tuple[int, ...] = (2, 7, 30, 90)
DEFAULT_CONSISTENCY_SAMPLE = 10


def _rows(conn, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), dict(params or {})).mappings().all()]


def _scalar(conn, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    from sqlalchemy import text

    row = conn.execute(text(sql), dict(params or {})).first()
    return None if row is None else row[0]


def build_panel(conn, *, as_of: Any = None, repo: str | None = None,
                threshold: int = triggers.DEFAULT_BACKLOG_THRESHOLD,
                view_days: Sequence[int] = DEFAULT_VIEW_DAYS) -> dict[str, Any]:
    """算一次面板快照（纯读；不建表、不写库）。"""
    stamp = ch.isots(as_of) or ch.now_utc()
    missions = ch.fetch_missions(conn, repo=repo)
    records = ch.fetch_records(conn, repo=repo)
    cohorts = ch.compute_cohorts(missions, records, as_of=stamp, repo=repo)

    trend = []
    for key, row in sorted(cohorts.items()):
        trend.append({
            "cohort": key,
            "week_start": row.get("week_start") or row.get("start"),
            "week_end": row.get("week_end") or row.get("end"),
            "frozen": ch.is_frozen(row, as_of=stamp),
            "missions_total": int(row.get("missions_total") or 0),
            "missions_decided": int(row.get("missions_decided") or 0),
            "missions_pending": int(row.get("missions_pending") or 0),
            "coverage": row.get("coverage"),
            "p90_latency_hours": row.get("p90_latency_hours"),
        })

    invariance = vw.window_independence(cohorts, as_of=stamp, days=tuple(view_days))

    pending_now = triggers.pending_rows(conn)
    runs = triggers.drain_runs(conn, limit=20)
    series = [{
        "run_id": r["run_id"],
        "started_at": r["started_at"],
        "trigger_kind": r["trigger_kind"],
        "pending_before": int(r["pending_before"] or 0),
        "pending_after": int(r["pending_after"] or 0),
        "drained": int(r["drained"] or 0),
        "drain_share": r["drain_share"],
        "elapsed_seconds": r["elapsed_seconds"],
        "met": bool(r["met"]),
    } for r in reversed(runs)]  # 时间正序便于画图
    rates = [s["drained"] / (s["elapsed_seconds"] / 3600.0) if s["elapsed_seconds"] else None
             for s in series]
    rates_ok = [r for r in rates if r is not None]
    last = series[-1] if series else None

    evs = triggers.events(conn, limit=20000)
    lat = triggers.latency_percentiles([e["latency_seconds"] for e in evs
                                        if e.get("latency_seconds") is not None])

    return {
        "schema": SCHEMA,
        "as_of": stamp,
        "repo": repo,
        "coverage_trend": trend,
        "cohorts_undefined_metrics": {
            k: row.get("undefined_metrics") for k, row in sorted(cohorts.items())
        },
        "window_invariance": invariance,
        "backlog": {
            "threshold": int(threshold),
            "pending_rows": pending_now,
            "over_threshold": pending_now > int(threshold),
            "drain_runs": series,
            "last_run": last,
            "drain_rate_rows_per_hour": (rates_ok[-1] if rates_ok else None),
            "target_share": triggers.DRAIN_TARGET_SHARE,
            "window_hours": triggers.DRAIN_WINDOW_HOURS,
        },
        "inflight_latency": {
            "events": len(evs),
            "decided": sum(1 for e in evs if e.get("outcome") == "decided"),
            "percentiles_seconds": lat,
            "histogram": triggers.histogram(
                [e["latency_seconds"] for e in evs if e.get("latency_seconds") is not None]),
            "outcome_counts": triggers._count(evs, "outcome"),
            "target_p95_seconds": 60.0,
            "slo": "ok" if (lat["p95"] is not None and lat["p95"] < 60.0) else
                   ("undefined" if lat["p95"] is None else "breach"),
        },
    }


def panel_consistency(conn, panel: Mapping[str, Any], *, sample: int = DEFAULT_CONSISTENCY_SAMPLE,
                      repo: str | None = None) -> dict[str, Any]:
    """抽 ``sample`` 个面板单元 → 独立 SQL 重算（判据 J6）。

    抽样规则：先 cohort（按 key 序）、再积压（当前行数 + 最近排空前后）、再时延分位，
    依次填满 ``sample`` 个单元；每个单元记录 ``panel_value / db_value / ok``。
    """
    checks: list[dict[str, Any]] = []

    def add(name: str, kind: str, panel_value: Any, db_fn: Callable[[], Any]) -> None:
        db_value = db_fn()
        checks.append({
            "cell": name,
            "kind": kind,
            "panel_value": panel_value,
            "db_value": db_value,
            "ok": (panel_value == db_value)
                  or (isinstance(panel_value, (int, float)) and isinstance(db_value, (int, float))
                      and abs(float(panel_value) - float(db_value)) <= 1e-9),
        })

    # 1) cohort 覆盖率：分子/分母独立 SQL 复算
    for row in panel.get("coverage_trend", []):
        if len(checks) >= int(sample):
            break
        key = row["cohort"]
        lo, hi = ch.week_bounds(key)
        params = {"lo": lo.isoformat(), "hi": hi.isoformat()}
        repo_clause = "AND m.repo = :repo\n" if repo else ""
        if repo:
            params["repo"] = repo
        total = int(_scalar(
            conn,
            f"SELECT count(*) FROM {ch.TAB_MISSIONS} m WHERE m.status = 'succeeded' "
            f"AND m.finished_at IS NOT NULL AND m.finished_at >= :lo "
            f"AND m.finished_at < :hi\n{repo_clause}", params) or 0)
        decided = int(_scalar(
            conn,
            f"SELECT count(DISTINCT m.id) FROM {ch.TAB_MISSIONS} m "
            f"WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL "
            f"AND m.finished_at >= :lo AND m.finished_at < :hi\n{repo_clause}"
            f"AND EXISTS (SELECT 1 FROM {ch.TAB_EVALS} e WHERE e.mission_id = m.id "
            f"AND e.status IN ('confirmed','candidate'))", params) or 0)
        add(f"cohort:{key}:missions_total", "cohort", row["missions_total"], lambda t=total: t)
        if len(checks) < int(sample):
            add(f"cohort:{key}:missions_decided", "cohort", row["missions_decided"],
                lambda d=decided: d)
        if len(checks) < int(sample):
            expected = round(decided / total, 6) if total else None
            add(f"cohort:{key}:coverage", "cohort", row["coverage"], lambda e=expected: e)

    # 2) 积压
    if len(checks) < int(sample):
        n = triggers.pending_rows(conn)
        add("backlog:pending_rows", "backlog", panel["backlog"]["pending_rows"], lambda v=n: v)
    last = panel["backlog"].get("last_run")
    if last and len(checks) < int(sample) and triggers.has_table(conn, triggers.DRAIN_TABLE):
        add("backlog:last_run:drained", "backlog", last["drained"],
            lambda rid=last["run_id"]: int(_scalar(
                conn, f"SELECT drained FROM {triggers.DRAIN_TABLE} WHERE run_id = :r",
                {"r": rid}) or 0))

    # 3) 时延分位（独立重算：从事件表再取一遍）
    pcts = panel["inflight_latency"]["percentiles_seconds"]
    if pcts.get("n") and len(checks) < int(sample) and triggers.has_table(conn, triggers.EVENT_TABLE):
        raw = [float(r["latency_seconds"]) for r in _rows(
            conn, f"SELECT latency_seconds FROM {triggers.EVENT_TABLE} "
                  "WHERE latency_seconds IS NOT NULL")]

        def _p(q: float) -> float | None:
            return ch.percentile(raw, q)

        add("latency:p50", "latency", pcts.get("p50"), lambda: _p(0.50))
        if len(checks) < int(sample):
            add("latency:p95", "latency", pcts.get("p95"), lambda: _p(0.95))
        if len(checks) < int(sample):
            add("latency:n", "latency", pcts.get("n"), lambda v=len(raw): v)

    mismatches = [c for c in checks if not c["ok"]]
    return {
        "schema": f"{SCHEMA}#consistency",
        "sample_requested": int(sample),
        "cells_checked": len(checks),
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
        "ok": not mismatches and len(checks) >= int(sample),
        "cells": checks,
    }


def render_markdown(panel: Mapping[str, Any]) -> str:
    """面板 → 报告用表（数字全部取自同一份 JSON）。"""
    lines = [f"### cohort 覆盖率趋势（as_of={panel['as_of']}）", "",
             "| cohort | 区间 | 冻结 | 分母 | 分子 | 积压 mission | 覆盖率 | P90 时延 [h] |",
             "|---|---|---|---|---|---|---|---|"]
    for r in panel["coverage_trend"]:
        cov = "None（无定义）" if r["coverage"] is None else f"{r['coverage']:.4f}"
        p90 = "None（无定义）" if r["p90_latency_hours"] is None else f"{r['p90_latency_hours']:.3f}"
        lines.append(f"| {r['cohort']} | {r['week_start']} → {r['week_end']} | "
                     f"{r['frozen']} | {r['missions_total']} | {r['missions_decided']} | "
                     f"{r['missions_pending']} | {cov} | {p90} |")
    b = panel["backlog"]
    lines += ["", f"### 积压与排空（阈值 {b['threshold']} 行，目标 24 h 内 ≥ "
                  f"{b['target_share']:.0%}）", "",
              "| 排空 | 触发 | before | after | drained | share | 耗时 [s] | met |",
              "|---|---|---|---|---|---|---|---|"]
    for s in b["drain_runs"]:
        share = "None" if s["drain_share"] is None else f"{s['drain_share']:.4f}"
        lines.append(f"| {s['started_at']} | {s['trigger_kind']} | {s['pending_before']} | "
                     f"{s['pending_after']} | {s['drained']} | {share} | "
                     f"{s['elapsed_seconds']:.4f} | {s['met']} |")
    lat = panel["inflight_latency"]
    p = lat["percentiles_seconds"]
    lines += ["", f"### in-flight 增量评价时延（n={p['n']}，目标 P95 < "
                  f"{lat['target_p95_seconds']} s）", "",
              "| P50 [s] | P95 [s] | P99 [s] | max [s] | SLO |",
              "|---|---|---|---|---|",
              f"| {p['p50']} | {p['p95']} | {p['p99']} | {p['max']} | {lat['slo']} |"]
    return "\n".join(lines)


__all__ = ["SCHEMA", "build_panel", "panel_consistency", "render_markdown"]