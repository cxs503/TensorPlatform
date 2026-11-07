"""评分器稳健性 / 交叉验证 / 外部效度（IN38-N09 评价体系成熟化）。

三条独立判据（都可机器裁，测试与产物共用同一套函数）：

1. **稳健性（robustness）**：同一份数据换行序、重复行、拆成单 cohort 单独算，逐字段必须
   不变 —— 评分器不得依赖输入顺序或分片方式。
2. **交叉验证（cross-validation）**：cohort 指标由两条独立实现给出（Python 聚合 vs
   ``cohorts.sql_crosscheck`` 的 PG 聚合），逐格必须相等。
3. **外部效度（external validity）**：同一份事实从「线上库直读」与「导出快照再算」两条
   独立链路复算，cohort 指标必须相等 —— 检验产物（artifact）是否真的代表线上状态。
"""
from __future__ import annotations

import random
from typing import Any, Iterable, Mapping, Sequence

from .cohorts import METRIC_FIELDS, compute_cohorts, diff_metrics


def _strip_ctx(row: Mapping[str, Any]) -> dict[str, Any]:
    """比较时剔掉与数据无关的上下文（as_of / excluded / orphan 等）。"""
    return {k: row.get(k) for k in METRIC_FIELDS}


def order_invariance(missions: Sequence[Mapping[str, Any]],
                     records: Sequence[Mapping[str, Any]],
                     baseline: Mapping[str, Mapping[str, Any]],
                     *, as_of: Any, trials: int = 5, seed: int = 11) -> dict[str, Any]:
    rng = random.Random(seed)
    diffs: list[dict[str, Any]] = []
    for t in range(int(trials)):
        ms = list(missions)
        rs = list(records)
        rng.shuffle(ms)
        rng.shuffle(rs)
        got = compute_cohorts(ms, rs, as_of=as_of)
        if set(got) != set(baseline):
            diffs.append({"trial": t, "kind": "cohort_set", "got": sorted(got), "want": sorted(baseline)})
            continue
        for key, row in baseline.items():
            d = diff_metrics(_strip_ctx(row), _strip_ctx(got[key]))
            if d:
                diffs.append({"trial": t, "cohort_key": key, "fields": d})
    return {"trials": int(trials), "diffs": diffs, "identical": not diffs,
            "note": "行序/分片无关性：随机打乱 mission 与记录顺序后逐字段相等。"}


def duplicate_invariance(missions: Sequence[Mapping[str, Any]],
                         records: Sequence[Mapping[str, Any]],
                         baseline: Mapping[str, Mapping[str, Any]],
                         *, as_of: Any, copies: int = 2) -> dict[str, Any]:
    """同一 mission 行重复出现（join 膨胀）不得改变任何指标。"""
    ms = list(missions) * int(copies)
    got = compute_cohorts(ms, records, as_of=as_of)
    diffs: list[dict[str, Any]] = []
    for key, row in baseline.items():
        d = diff_metrics(_strip_ctx(row), _strip_ctx(got.get(key, {})))
        if d:
            diffs.append({"cohort_key": key, "fields": d})
    return {"copies": int(copies), "diffs": diffs, "identical": not diffs,
            "note": "重复 mission 行（模拟 join 膨胀）指标不变（按 mission id 去重）。"}


def locality_invariance(missions: Sequence[Mapping[str, Any]],
                        records: Sequence[Mapping[str, Any]],
                        baseline: Mapping[str, Mapping[str, Any]],
                        *, as_of: Any) -> dict[str, Any]:
    """局部=全局：把某一 cohort 的 mission 单独喂进评分器，必须与该 cohort 的整行相等。"""
    diffs: list[dict[str, Any]] = []
    for key, row in baseline.items():
        own = [m for m in missions if (str(m.get("finished_at")) and _cohort_of(m) == key)]
        sub = compute_cohorts(own, records, as_of=as_of)
        d = diff_metrics(_strip_ctx(row), _strip_ctx(sub.get(key, {})))
        if d:
            diffs.append({"cohort_key": key, "fields": d})
    return {"cohorts": len(baseline), "diffs": diffs, "identical": not diffs,
            "note": "同一 cohort 单独计算 = 全量计算（不存在跨 cohort 耦合，这是视图无关性的根）。"}


def _cohort_of(mission: Mapping[str, Any]) -> str | None:
    from .cohorts import cohort_key

    return cohort_key(mission.get("finished_at"))


def denominator_none(missions: Sequence[Mapping[str, Any]],
                     records: Sequence[Mapping[str, Any]], *, as_of: Any) -> dict[str, Any]:
    """分母语义：分母为 0 的量必须 ``None`` 并单列；分母非 0 的 0 是真值。

    逐条判据：
    - ``coverage_denominator == 0`` → ``coverage is None``；``> 0`` → 必须是数（不许 None）
    - ``latency_n == 0`` → ``p90_latency_hours is None`` 且在 ``undefined_metrics``；
      不许出现 ``0.0``（「没样本」被写成「瞬时评价」）
    - ``backlog_rows == 0`` → ``backlog_age_*` is None``；``> 0`` → 年龄必须是数
    """
    got = compute_cohorts(missions, records, as_of=as_of)
    violations: list[dict[str, Any]] = []
    undefined_ok: list[str] = []

    for key, row in got.items():
        total = int(row.get("coverage_denominator") or 0)
        cov = row.get("coverage")
        if total == 0 and cov is not None:
            violations.append({"cohort_key": key, "reason": "coverage 分母为 0 却给出数值", "value": cov})
        elif total > 0 and cov is None:
            violations.append({"cohort_key": key, "reason": "coverage 分母非 0 却为 None"})

        lat_n = int(row.get("latency_n") or 0)
        p90 = row.get("p90_latency_hours")
        if lat_n == 0:
            if p90 is not None:
                violations.append({"cohort_key": key, "reason": "无时延样本却给出 P90",
                                   "value": p90})
            elif "p90_latency_hours" not in (row.get("undefined_metrics") or []):
                violations.append({"cohort_key": key, "reason": "P90 为 None 但未列入 undefined_metrics"})
            else:
                undefined_ok.append(f"{key}:p90_latency_hours")
        elif p90 is None:
            violations.append({"cohort_key": key, "reason": "有时延样本却 P90 为 None"})

        backlog = int(row.get("backlog_rows") or 0)
        age = row.get("backlog_age_max_hours")
        if backlog == 0 and age is not None:
            violations.append({"cohort_key": key, "reason": "无积压却给出积压年龄", "value": age})
        elif backlog > 0 and age is None:
            violations.append({"cohort_key": key, "reason": "有积压却无积压年龄"})

    # 真空 cohort（0 张 mission）的分母为 0 分支：必须 None 且单列
    from .cohorts import parse_ts
    from .cohorts import _cohort_metrics

    empty = _cohort_metrics(
        {"cohort_key": "1970-W01", "week_start": "1969-12-29", "week_end": "1970-01-05",
         "missions": [], "records": []},
        as_of_dt=parse_ts(as_of) or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
        repo=None,
    )
    if empty.get("coverage") is not None or "coverage" not in empty.get("undefined_metrics", []):
        violations.append({"cohort_key": "<empty>", "reason": "空 cohort 的 coverage 语义错误",
                           "value": empty.get("coverage")})
    else:
        undefined_ok.append("<empty>:coverage")

    return {"cohorts": len(got), "undefined_cases": undefined_ok,
            "violations": violations, "ok": not violations}


def external_validity(live: Mapping[str, Mapping[str, Any]],
                      snapshot: Mapping[str, Mapping[str, Any]],
                      *, name: str = "snapshot") -> dict[str, Any]:
    """两条独立链路（线上直读 vs 导出快照）逐 cohort 比对。"""
    diffs: list[dict[str, Any]] = []
    only_live = sorted(set(live) - set(snapshot))
    only_snap = sorted(set(snapshot) - set(live))
    for key in sorted(set(live) & set(snapshot)):
        d = diff_metrics(_strip_ctx(live[key]), _strip_ctx(snapshot[key]))
        if d:
            diffs.append({"cohort_key": key, "fields": d})
    return {
        "compared_with": name,
        "cohorts_compared": len(set(live) & set(snapshot)),
        "cohorts_only_live": only_live,
        "cohorts_only_snapshot": only_snap,
        "diffs": diffs,
        "identical": not diffs and not only_live and not only_snap,
        "note": "外部效度：同一事实的两条独立读取链路必须给出同一组 cohort 数值。",
    }


def score_invariants(missions: Sequence[Mapping[str, Any]],
                     records: Sequence[Mapping[str, Any]], *, as_of: Any,
                     trials: int = 5) -> dict[str, Any]:
    """三件套打包（产物里直接落这段 JSON）。"""
    baseline = compute_cohorts(missions, records, as_of=as_of)
    order = order_invariance(missions, records, baseline, as_of=as_of, trials=trials)
    dup = duplicate_invariance(missions, records, baseline, as_of=as_of)
    loc = locality_invariance(missions, records, baseline, as_of=as_of)
    den = denominator_none(missions, records, as_of=as_of)
    ok = bool(order["identical"] and dup["identical"] and loc["identical"] and den["ok"])
    return {
        "schema": "eval-score-invariants/1",
        "cohorts": len(baseline),
        "order": order,
        "duplicates": dup,
        "locality": loc,
        "denominator": den,
        "ok": ok,
    }