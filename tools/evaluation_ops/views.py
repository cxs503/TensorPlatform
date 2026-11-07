"""视图无关性（窗口只筛 cohort，不改 cohort 内的数）与旧口径负控。

判据（任务书第 6 条）
--------------------
同一 cohort 在 7/30/90 天三种视图下，逐字段数值必须**完全相等**（差异=0，不是「小到可
忽略」）。本模块把这条写成函数：``window_independence()`` 返回 ``identical`` 与逐字段
``diffs``；只要有一格不等就是 FAIL。

反面对照（负控）
----------------
``legacy_window_coverage()`` 复刻旧口径：取「最近 N 张 succeeded mission」算覆盖率。
窗口一变数就变 —— 用来证明新口径确实修掉了窗口相关性，而不是把同一件事换个名字。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence

from .cohorts import (
    DECIDED_STATUSES,
    METRIC_FIELDS,
    cohort_of,
    compute_cohorts,
    parse_ts,
    percentile,
)

#: 三种观测视图（天）
VIEW_DAYS: tuple[int, ...] = (7, 30, 90)


def in_view(row: Mapping[str, Any], *, as_of: Any, days: int) -> bool:
    """cohort 的有效周区间与 ``[as_of-days, as_of]`` 有交集即入视图。

    注意：只决定「这一行进不进视图」，不影响行内任何数（行由 ``compute_cohorts`` 产出，
    计算时没有任何窗口参数）。
    """
    as_of_dt = parse_ts(as_of)
    if as_of_dt is None:
        return False
    start = parse_ts(row.get("week_start"))
    end = parse_ts(row.get("week_end"))
    if start is None or end is None:
        return False
    lo = as_of_dt - timedelta(days=int(days))
    return start < as_of_dt and end > lo


def filter_view(cohorts: Mapping[str, Mapping[str, Any]], *, as_of: Any,
                days: int) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in cohorts.items() if in_view(v, as_of=as_of, days=int(days))}


def window_independence(cohorts: Mapping[str, Mapping[str, Any]], *, as_of: Any,
                        days: Sequence[int] = VIEW_DAYS,
                        fields: Sequence[str] = METRIC_FIELDS) -> dict[str, Any]:
    """同一 cohort 在多个视图下逐字段比对，返回机器可裁的结论。"""
    views = {int(d): filter_view(cohorts, as_of=as_of, days=int(d)) for d in days}
    keys_per_view = {d: sorted(v.keys()) for d, v in views.items()}
    common = sorted(set.intersection(*[set(k) for k in keys_per_view.values()])) if views else []

    diffs: list[dict[str, Any]] = []
    compared = 0
    maxima: dict[str, float] = {}
    for key in common:
        base_view = int(days[0])
        base = views[base_view][key]
        for d in list(days)[1:]:
            other = views[int(d)][key]
            compared += 1
            for f in fields:
                a, b = base.get(f), other.get(f)
                if a == b:
                    continue
                diffs.append({"cohort_key": key, "field": f, "views": [base_view, int(d)],
                              "values": [a, b]})
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    maxima[f] = max(maxima.get(f, 0.0), abs(float(a) - float(b)))

    return {
        "views_days": [int(d) for d in days],
        "cohorts_per_view": {str(d): keys_per_view[d] for d in keys_per_view},
        "common_cohorts": common,
        "pairs_compared": compared,
        "fields": list(fields),
        "diffs": diffs,
        "max_abs_diff": max(maxima.values()) if maxima else 0.0,
        "identical": not diffs,
        "pairwise_set_equal": len({tuple(v) for v in keys_per_view.values()}) == 1,
    }


# ─────────────────────────── 旧口径负控 ───────────────────────────

def legacy_window_coverage(missions: Iterable[Mapping[str, Any]],
                           records: Iterable[Mapping[str, Any]],
                           *, window: int) -> dict[str, Any]:
    """旧口径：最近 ``window`` 张 succeeded mission 的覆盖率（窗口一变数就变）。

    与 ``tools/outcome_evaluations._window_cte`` 同口径（``ORDER BY finished_at DESC
    LIMIT :window``），用于证明「窗口相关量」确实随窗口漂移。
    """
    parsed: list[tuple[Any, int]] = []
    for m in missions:
        try:
            mid = int(m.get("id", m.get("mission_id")))
        except (TypeError, ValueError):
            continue
        ts = parse_ts(m.get("finished_at"))
        if ts is None:
            continue
        parsed.append((ts, mid))
    parsed.sort(reverse=True)
    picked = parsed[: int(window)]
    ids = {mid for _, mid in picked}
    decided_ids: set[int] = set()
    for rec in records:
        try:
            mid = int(rec.get("mission_id"))
        except (TypeError, ValueError):
            continue
        if mid in ids and str(rec.get("status")) in DECIDED_STATUSES:
            decided_ids.add(mid)
    total = len(picked)
    return {
        "window": int(window),
        "missions": total,
        "missions_decided": len(decided_ids),
        "coverage": (len(decided_ids) / total) if total else None,
        "defined": total > 0,
    }


def legacy_window_sweep(missions: Iterable[Mapping[str, Any]],
                        records: Iterable[Mapping[str, Any]],
                        *, windows: Sequence[int] = (7, 30, 90)) -> dict[str, Any]:
    """同一批数据按三种窗口算旧口径 → 数值随窗口变化（负控）。"""
    missions = list(missions)
    records = list(records)
    rows = [legacy_window_coverage(missions, records, window=w) for w in windows]
    values = [r["coverage"] for r in rows if r["coverage"] is not None]
    spread = (max(values) - min(values)) if len(values) > 1 else 0.0
    return {
        "rows": rows,
        "spread": spread,
        "window_dependent": spread > 0,
        "note": "旧口径（最近 N 单）——同一数据、不同窗口给出不同覆盖率，正是本次要修掉的窗口相关性。",
    }


#: 旧口径（最近 N 单）敏感度扫描用的窗口：既有预登记的 7/30/90，也有跨全库的量级
LEGACY_WINDOW_SWEEP: tuple[int, ...] = (7, 30, 90, 300, 1000, 3000)


def cohort_vs_legacy(cohorts: Mapping[str, Mapping[str, Any]],
                     missions: Iterable[Mapping[str, Any]],
                     records: Iterable[Mapping[str, Any]],
                     *, as_of: Any, windows: Sequence[int] = VIEW_DAYS,
                     legacy_windows: Sequence[int] = LEGACY_WINDOW_SWEEP) -> dict[str, Any]:
    """把「新口径 0 差异」与「旧口径有差异」并排放在同一份产物里。

    ``windows``：cohort 视图的**天数**（7/30/90）。
    ``legacy_windows``：旧口径的**mission 张数**窗口（同名 "window" 但语义不同，报告须写明）。
    """
    wi = window_independence(cohorts, as_of=as_of, days=windows)
    legacy = legacy_window_sweep(missions, records, windows=legacy_windows)
    return {
        "cohort_invariant": {
            "identical": wi["identical"],
            "max_abs_diff": wi["max_abs_diff"],
            "pairs_compared": wi["pairs_compared"],
        },
        "legacy_window_dependent": {
            "window_dependent": legacy["window_dependent"],
            "spread": legacy["spread"],
            "rows": legacy["rows"],
        },
        "verdict": ("PASS：cohort 口径视图无关，旧口径存在窗口漂移"
                    if wi["identical"] and legacy["window_dependent"] else
                    "见 diffs：cohort 口径仍有窗口依赖或旧口径未漂移，需核查"),
        "details": wi,
    }