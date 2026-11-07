"""Cohort SLO：P90 评价时延 ≤ 24h、积压 ≤ 20 行（IN38-N09）。

口径（钉死）
------------
- **时延**：``min(decided_at) - finished_at``（小时），样本 = 已判定
  （``confirmed`` | ``candidate``）的 mission。``decided_at < finished_at`` 记异常样本，
  剔出分位并单独计数（防脏时间戳把 P90 拉负）。
- **积压**：``pending`` 记录**行数**；**年龄从 ``mission.finished_at`` 起算**（不是记录
  ``created_at``）：积压年龄要回答「成果等评价等了多久」，记录什么时候建的不重要。
- 分母为 0 时 P90 是 ``None`` 并列入 ``undefined_metrics``（不折成 0h，否则「没样本」
  会被当成「瞬时评价」骗过 SLO）。

阈值是工程默认值，可注入覆盖；报告必须写明当次阈值来源。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence

from .cohorts import (
    DECIDED_STATUSES,
    PENDING_STATUS,
    now_utc,
    parse_ts,
    percentile,
)

SCHEMA = "eval-slo/1"

#: 默认 SLO（任务书第 5 条）
DEFAULT_SLO: dict[str, float] = {
    "latency_p90_hours_max": 24.0,
    "backlog_rows_max": 20.0,
}


def _round(value: float | None, nd: int = 6) -> float | None:
    return None if value is None else round(float(value), nd)


def ops_snapshot(missions: Iterable[Mapping[str, Any]],
                 records: Iterable[Mapping[str, Any]],
                 *, as_of: Any,
                 view_days: int | None = None) -> dict[str, Any]:
    """运行态快照：时延分位 + 积压行数与年龄（可选按 ``finished_at`` 限定回看天数）。"""
    as_of_dt = parse_ts(as_of)
    if as_of_dt is None:
        raise ValueError("as_of 不可解析")
    lo = as_of_dt - timedelta(days=int(view_days)) if view_days else None

    finished: dict[int, Any] = {}
    for m in missions:
        try:
            mid = int(m.get("id", m.get("mission_id")))
        except (TypeError, ValueError):
            continue
        ts = parse_ts(m.get("finished_at"))
        if ts is None:
            continue
        if lo is not None and ts < lo:
            continue
        finished[mid] = ts

    by_mission: dict[int, list[Mapping[str, Any]]] = {}
    for rec in records:
        try:
            mid = int(rec.get("mission_id"))
        except (TypeError, ValueError):
            continue
        if mid not in finished:
            continue
        by_mission.setdefault(mid, []).append(rec)

    latencies: list[float] = []
    anomalies: list[dict[str, Any]] = []
    decided_without_ts = 0
    backlog_rows = 0
    backlog_ages: list[float] = []
    unrecorded = 0

    for mid, ts in finished.items():
        recs = by_mission.get(mid, [])
        if not recs:
            unrecorded += 1
        stamps = [parse_ts(r.get("decided_at")) for r in recs
                  if str(r.get("status")) in DECIDED_STATUSES and r.get("decided_at")]
        stamps = [s for s in stamps if s is not None]
        if stamps:
            hours = (min(stamps) - ts).total_seconds() / 3600.0
            if hours < 0:
                anomalies.append({"mission_id": mid, "latency_hours": round(hours, 6)})
            else:
                latencies.append(hours)
        elif any(str(r.get("status")) in DECIDED_STATUSES for r in recs):
            decided_without_ts += 1
        pending = [r for r in recs if str(r.get("status")) == PENDING_STATUS]
        backlog_rows += len(pending)
        if pending:
            backlog_ages.append((as_of_dt - ts).total_seconds() / 3600.0)

    undefined: list[str] = []
    p90 = _round(percentile(latencies, 0.9))
    if p90 is None:
        undefined.append("p90_latency_hours")
    if not backlog_ages:
        undefined.append("backlog_age_hours")

    return {
        "schema": SCHEMA,
        "as_of": as_of_dt.replace(microsecond=0).isoformat(),
        "view_days": None if view_days is None else int(view_days),
        "missions_in_scope": len(finished),
        "missions_decided": sum(1 for mid in finished
                                if any(str(r.get("status")) in DECIDED_STATUSES
                                       for r in by_mission.get(mid, []))),
        "missions_unrecorded": unrecorded,
        "latency_n": len(latencies),
        "p90_latency_hours": p90,
        "latency_median_hours": _round(percentile(latencies, 0.5)),
        "latency_max_hours": _round(max(latencies)) if latencies else None,
        "latency_anomalies": anomalies,
        "decided_without_ts": decided_without_ts,
        "backlog_rows": backlog_rows,
        "backlog_missions": len(backlog_ages),
        "backlog_age_max_hours": _round(max(backlog_ages)) if backlog_ages else None,
        "backlog_age_median_hours": _round(percentile(backlog_ages, 0.5)),
        "backlog_age_origin": "mission.finished_at",
        "undefined_metrics": undefined,
        "computed_at": now_utc(),
    }


def evaluate_slo(snapshot: Mapping[str, Any],
                 thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
    """两条 SLO 的机器判定：``ok`` / ``breach`` / ``undefined``。"""
    th = dict(DEFAULT_SLO)
    if thresholds:
        th.update({str(k): float(v) for k, v in thresholds.items()})

    states: dict[str, str] = {}
    breaches: list[dict[str, Any]] = []
    undefined: list[str] = []

    p90 = snapshot.get("p90_latency_hours")
    if p90 is None:
        states["latency_p90"] = "undefined"
        undefined.append("p90_latency_hours")
    elif float(p90) > th["latency_p90_hours_max"]:
        states["latency_p90"] = "breach"
        breaches.append({
            "key": "latency_p90",
            "metric": "p90_latency_hours",
            "value": float(p90),
            "threshold": th["latency_p90_hours_max"],
            "comparison": ">",
            "message": f"P90 评价时延 {float(p90):.2f}h > SLO {th['latency_p90_hours_max']:.0f}h",
        })
    else:
        states["latency_p90"] = "ok"

    backlog = int(snapshot.get("backlog_rows") or 0)
    if float(backlog) > th["backlog_rows_max"]:
        states["backlog_rows"] = "breach"
        breaches.append({
            "key": "backlog_rows",
            "metric": "backlog_rows",
            "value": backlog,
            "threshold": th["backlog_rows_max"],
            "comparison": ">",
            "message": f"积压 {backlog} 行 > SLO {int(th['backlog_rows_max'])} 行",
        })
    else:
        states["backlog_rows"] = "ok"

    breached = [b for b in breaches]
    return {
        "schema": f"{SCHEMA}#slo",
        "thresholds": {k: float(v) for k, v in th.items()},
        "states": states,
        "breaches": breached,
        "undefined_metrics": undefined,
        "slo_met": not breached and not undefined,
        "slo_met_defined_only": not breached,
        "note": ("P90 为 None 表示无已判定样本（无定义），不算通过也不算越界；"
                 "积压行数按 pending 记录行计，年龄起点为 mission.finished_at。"),
    }


def slo_report(missions: Iterable[Mapping[str, Any]],
               records: Iterable[Mapping[str, Any]],
               *, as_of: Any, view_days: Sequence[int | None] = (None, 7, 30, 90),
               thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
    """多视图 SLO 快照（全局 + 7/30/90 天回看），同一阈值判定。"""
    missions = list(missions)
    records = list(records)
    views: dict[str, dict[str, Any]] = {}
    for days in view_days:
        snap = ops_snapshot(missions, records, as_of=as_of, view_days=days)
        label = "all" if days is None else f"{int(days)}d"
        views[label] = {**snap, "slo": evaluate_slo(snap, thresholds)}
    primary = views.get("all") or next(iter(views.values()))
    return {
        "schema": SCHEMA,
        "thresholds": primary["slo"]["thresholds"],
        "views": views,
        "primary_view": "all",
        "primary_slo": primary["slo"],
        "backlog_age_origin": "mission.finished_at",
    }