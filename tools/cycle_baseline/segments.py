"""cycle_baseline.segments —— 四段分解（纯函数：原始行 → 段时长 → 分位账本 → 归因表）。

设计取舍（诚实优先）
-------------------
平台实测相位与战役原始假设不同：>95% 的报告行早于 ``finished_at``，而 push-merge 路径的
落地 commit 也可能早于 ``finished_at``。因此本层**不假定** ``queue→exec→merge→report`` 串行
可分，而是：

* 先把每段按自己的锚点算出**原值**（允许为负，负值即相位事实）；
* 非执行开销口径另给 ``max(0, 原值)`` 的 clamped 版本，并同时记录 ``negative_share``；
* 份额只在**四段齐备的 complete-case 子集**上做可加分割（缺证据时同时收缩分子分母）。

这样"四段账本"要么是真实可加的，要么显式标 WITHHELD，不会用 clamp 或缩分母制造好看的数。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from . import spec
from .stats import grade_segment, mean, pcts


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def secs(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def build_record(raw: dict[str, Any]) -> dict[str, Any]:
    """一行原始证据 → 一条标准化记录（所有段原值 + clamped 值 + 周期）。

    入参键（DB 或 fixture 同构）：
      mission_id, repo, status, worker, created_at, claimed_at, finished_at,
      merge_at, report_at, planned_start, plan_id, node
    """
    created = parse_ts(raw.get("created_at"))
    claimed = parse_ts(raw.get("claimed_at"))
    finished = parse_ts(raw.get("finished_at"))
    merge_at = parse_ts(raw.get("merge_at"))
    report_at = parse_ts(raw.get("report_at"))

    t_queue = secs(claimed, created)
    t_exec = secs(finished, claimed)
    t_merge = secs(merge_at, finished)          # 原值：可为负
    t_report = secs(report_at, finished)        # 原值：可为负
    cycle = secs(finished, created)

    rec = {
        "mission_id": raw.get("mission_id") or raw.get("id"),
        "repo": raw.get("repo"),
        "status": raw.get("status"),
        "worker": raw.get("worker") or raw.get("claimed_by"),
        "plan_id": raw.get("plan_id"),
        "node": raw.get("node"),
        "created_at": created.isoformat() if created else None,
        "claimed_at": claimed.isoformat() if claimed else None,
        "finished_at": finished.isoformat() if finished else None,
        "merge_at": merge_at.isoformat() if merge_at else None,
        "report_at": report_at.isoformat() if report_at else None,
        "planned_start": parse_ts(raw.get("planned_start")).isoformat()
                         if parse_ts(raw.get("planned_start")) else None,
        "session_linked": raw.get("session_id") is not None,
        "report_owed": raw.get("report_owed"),
        "n_reports": raw.get("n_reports"),
        "n_pr_events": raw.get("n_pr_events"),
        "n_pr_merged": raw.get("n_pr_merged"),
        # 段原值（可负）
        "t_queue": t_queue,
        "t_exec": t_exec,
        "t_merge_raw": t_merge,
        "t_report_raw": t_report,
        # 非执行开销口径（clamped）
        "t_merge": None if t_merge is None else max(0.0, t_merge),
        "t_report": None if t_report is None else max(0.0, t_report),
        "cycle_s": cycle,
    }
    raw_vals = {k: rec[k] for k in spec.SEGMENTS}
    measured = [v for v in raw_vals.values() if v is not None]
    rec["segments_measured"] = len(measured)
    if len(measured) == len(spec.SEGMENTS):
        tot = sum(measured)
        rec["measured_total_s"] = tot
        for k in spec.SEGMENTS:
            rec[f"share_{k}"] = (rec[k] / tot) if tot > 0 else None
    else:
        rec["measured_total_s"] = sum(measured) if measured else None
        for k in spec.SEGMENTS:
            rec[f"share_{k}"] = None
    return rec


def build_records(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_record(r) for r in rows]


def _raw_pcts(records: Sequence[dict], seg: str) -> Optional[dict]:
    """t_merge / t_report 的原值（未 clamp）分位：负值即"尾段发生在终态之前"的相位事实。"""
    key = f"{seg}_raw"
    vals = [r.get(key) for r in records if r.get(key) is not None]
    if not vals:
        return None
    return pcts(vals)


def segment_ledger(records: Sequence[dict], cohort_n: Optional[int] = None) -> dict[str, Any]:
    """四段分位账本 + 覆盖率 + 份额 + 判据 (d) 的门禁等级。"""
    cohort_n = cohort_n if cohort_n is not None else len(records)
    complete = [r for r in records if r["segments_measured"] == len(spec.SEGMENTS)]
    out: dict[str, Any] = {}
    for seg in spec.SEGMENTS:
        vals = [r[seg] for r in records if r.get(seg) is not None]
        coverage = (len(vals) / cohort_n) if cohort_n else None
        shares = [r[f"share_{seg}"] for r in complete if r.get(f"share_{seg}") is not None]
        neg = None
        if seg == "t_merge":
            raw = [r["t_merge_raw"] for r in records if r["t_merge_raw"] is not None]
            neg = (sum(1 for v in raw if v < 0) / len(raw)) if raw else None
        elif seg == "t_report":
            raw = [r["t_report_raw"] for r in records if r["t_report_raw"] is not None]
            neg = (sum(1 for v in raw if v < 0) / len(raw)) if raw else None
        out[seg] = {
            "n": len(vals),
            "coverage": coverage,
            "grade": grade_segment(coverage, spec.COVERAGE_MIN),
            **pcts(vals),
            "mean_s": mean(vals),
            "share_complete_case_n": len(shares),
            "share_of_measured_total": mean(shares),
            "negative_share": neg,
            "raw_pcts": _raw_pcts(records, seg),
        }
    out["cycle_s"] = {
        "n": sum(1 for r in records if r["cycle_s"] is not None),
        "coverage": (sum(1 for r in records if r["cycle_s"] is not None) / cohort_n)
                    if cohort_n else None,
        **pcts([r["cycle_s"] for r in records]),
        "mean_s": mean([r["cycle_s"] for r in records]),
    }
    out["complete_case_n"] = len(complete)
    out["complete_case_coverage"] = (len(complete) / cohort_n) if cohort_n else None
    out["cohort_n"] = cohort_n
    return out


def attribution_table(records: Sequence[dict]) -> list[dict[str, Any]]:
    """终态 × repo 归因表：每格给段份额均值、非执行开销占比、样本数与覆盖率。

    非执行开销份额 = (clamped t_merge + t_report) / Σ 已测段（complete-case 上算，
    缺证据格 n=0 且值为 None —— 不用 0 冒充"没有开销"）。
    """
    complete = [r for r in records if r["segments_measured"] == len(spec.SEGMENTS)]
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault((r["status"], r["repo"]), []).append(r)
    rows: list[dict[str, Any]] = []
    for (status, repo), recs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        comp = [r for r in complete if r["status"] == status and r["repo"] == repo]
        row: dict[str, Any] = {
            "status": status,
            "repo": repo,
            "n": len(recs),
            "n_complete_case": len(comp),
            "cycle_p50_s": pcts([r["cycle_s"] for r in recs], qs=(0.5,))["p50"],
        }
        for seg in spec.SEGMENTS:
            sh = [r[f"share_{seg}"] for r in comp if r.get(f"share_{seg}") is not None]
            row[f"share_{seg}"] = mean(sh)
        overhead = [((r["t_merge"] or 0.0) + (r["t_report"] or 0.0)) / r["measured_total_s"]
                    for r in comp if r.get("measured_total_s")]
        row["non_exec_overhead_share"] = mean(overhead)
        exec_share = [r["share_t_exec"] for r in comp if r.get("share_t_exec") is not None]
        row["exec_share"] = mean(exec_share)
        rows.append(row)
    return rows


def coverage_gate(ledger: dict, segments: Sequence[str] = spec.SEGMENTS) -> dict[str, Any]:
    """判据 (d)：逐段门禁汇总（任一段 < 80% → 该段结论 WITHHELD）。"""
    details = {s: {"coverage": ledger[s]["coverage"], "grade": ledger[s]["grade"]}
               for s in segments}
    withheld = [s for s, d in details.items() if d["grade"] == "WITHHELD"]
    return {
        "coverage_min": spec.COVERAGE_MIN,
        "per_segment": details,
        "withheld_segments": withheld,
        "verdict": "WITHHELD" if withheld else "MEASURED",
        "rule": spec.DEFINITIONS["fairness_gate"],
    }