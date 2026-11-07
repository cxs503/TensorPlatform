#!/usr/bin/env python3
"""coordinator_shadow v1 —— 验收裁决器（IN27-H1）。

判据先于实现（全部机器可裁，数字落 JSON）
----------------------------------------
| 判据 | 机器定义 | 门槛 |
|---|---|---|
| C1 连续在岗 | 窗口 [T0, T1] 内应到拍 vs 实到拍（``beat_id`` 去重、无 ``error``） | ``coverage_ratio ≥ 0.95`` |
| C2 最长连续 | 相邻有效拍间隔 ≤ 1.5×interval 的最长链 | ``≥ 0.9 × 窗口`` |
| C3 无人工修 | 影子日志/运行日志里「人工干预」行数（模式命中） | ``== 0`` |
| C4 错拍自愈 | 每个 error 拍之后 2 个 interval 内有成功拍 | 自愈率 ``≥ 0.95`` |
| C5 诊断单被执行 | 诊断单里被领取且执行完（``confirmed``）的件数 | ``≥ 3`` |
| C6 误报率 | ``FP / (FP + confirmed + inconclusive)``（``unclaimed_expired`` 单列不计入分母） | ``< 0.30`` |

裁决状态
--------
``PASS`` / ``FAIL`` / ``PENDING_WALL_CLOCK``（窗口未走完，或诊断单还未被处置完）/
``INSUFFICIENT_EVIDENCE``（连拍数不足以定谳）。**不允许把 PENDING 写成 PASS**：
72h 墙钟只能由时间自己给，本模块只负责在它走完后自动定谳，并把结论写进 JSON。

关于「事后处置定谳」
--------------------
误报率的分母只算**已被处置**的单（``confirmed`` / ``false_positive`` / ``inconclusive``）：
没人领的单（``unclaimed_expired``）说明不了诊断对错，单列成 ``unclaimed_expired`` 并
同时给出 ``fpr_including_unclaimed`` 让读者自己看两种口径的差距——不做粉饰。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .filing import infer_disposition
from .pulse import iso, parse_ts, utc_now

SCHEMA = "coordinator-shadow-acceptance/1"

#: 「人工修」模式：只在影子自身与其守护日志上扫（不去扫平台全局日志，避免跨界误判）
DEFAULT_HUMAN_PATTERNS: tuple[str, ...] = (
    r"手工修",
    r"人工修",
    r"manual(ly)?[- ]fix",
    r"hotfix",
    r"manual(ly)? restart",
    r"人工干预",
)


@dataclass
class Criteria:
    window_hours: float = 72.0
    coverage_ratio_min: float = 0.95
    longest_run_ratio_min: float = 0.90
    human_repairs_max: int = 0
    self_heal_ratio_min: float = 0.95
    claimed_executed_min: int = 3
    fpr_max: float = 0.30
    interval_seconds: int = 1800
    stale_hours: float = 48.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "coverage_ratio_min": self.coverage_ratio_min,
            "longest_run_ratio_min": self.longest_run_ratio_min,
            "human_repairs_max": self.human_repairs_max,
            "self_heal_ratio_min": self.self_heal_ratio_min,
            "claimed_executed_min": self.claimed_executed_min,
            "fpr_max": self.fpr_max,
            "interval_seconds": self.interval_seconds,
            "stale_hours": self.stale_hours,
        }


def coverage(beats: Sequence[Mapping[str, Any]], start: datetime, end: datetime,
             interval_seconds: int = 1800) -> dict[str, Any]:
    """应到拍 / 实到拍 / 缺拍明细 / 最长连续链。"""
    # 应到拍 = 窗口覆盖到的**槽**数（按 epoch 槽边界算，start 落在槽中间也不会多算/少算）
    first_slot = int(start.timestamp()) // interval_seconds
    # 窗口按 [start, end) 计：end 恰好落在槽边界时不算新槽（否则 72h 会算成 145 拍）
    last_slot = int((end - timedelta(microseconds=1)).timestamp()) // interval_seconds
    expected = max(0, last_slot - first_slot + 1)
    present: dict[str, datetime] = {}
    errors: list[str] = []
    for b in beats:
        ts = parse_ts(b.get("ts"))
        if ts is None or ts < start or ts > end:
            continue
        bid = str(b.get("beat_id") or dg_bid(ts, interval_seconds))
        if b.get("error"):
            errors.append(bid)
            continue
        present[bid] = ts
    # 应到拍槽
    expected_slots = list(range(first_slot, last_slot + 1))
    missing = [slot for slot in expected_slots if str(slot) not in present]
    ordered = sorted(present.items(), key=lambda kv: kv[1])
    longest_run = 0.0
    run_start: datetime | None = None
    prev: datetime | None = None
    gaps: list[dict[str, Any]] = []
    for bid, ts in ordered:
        if prev is None:
            run_start = ts
        else:
            delta = (ts - prev).total_seconds()
            if delta > 1.5 * interval_seconds:
                gaps.append({"from": iso(prev), "to": iso(ts), "seconds": delta})
                longest_run = max(longest_run, (prev - (run_start or ts)).total_seconds())
                run_start = ts
        prev = ts
    if ordered and run_start is not None:
        longest_run = max(longest_run, (ordered[-1][1] - run_start).total_seconds())
    ratio = min(1.0, (len(present) / expected)) if expected else 0.0
    return {
        "expected_beats": expected,
        "present_beats": len(present),
        "error_beats": len(errors),
        "missing_beats": len(missing),
        "missing_slots": [str(s) for s in missing[:24]],
        "coverage_ratio": round(ratio, 4),
        "longest_run_hours": round(longest_run / 3600.0, 3),
        "gaps": gaps[:12],
        "first_beat_ts": iso(ordered[0][1]) if ordered else None,
        "last_beat_ts": iso(ordered[-1][1]) if ordered else None,
    }


def dg_bid(ts: datetime, interval_seconds: int = 1800) -> str:
    return str(int(ts.timestamp()) // interval_seconds)


def self_healing(beats: Sequence[Mapping[str, Any]], interval_seconds: int = 1800) -> dict[str, Any]:
    """错拍之后是否在 2 个 interval 内自愈（无需人工重启）。"""
    rows = []
    for b in beats:
        ts = parse_ts(b.get("ts"))
        if ts is not None:
            rows.append((ts, bool(b.get("error"))))
    rows.sort(key=lambda r: r[0])
    errors = 0
    healed = 0
    for i, (ts, is_err) in enumerate(rows):
        if not is_err:
            continue
        errors += 1
        for ts2, err2 in rows[i + 1:]:
            if (ts2 - ts).total_seconds() > 2 * interval_seconds:
                break
            if not err2:
                healed += 1
                break
    ratio = 1.0 if errors == 0 else healed / errors
    return {"error_beats": errors, "self_healed": healed,
            "self_heal_ratio": round(ratio, 4)}


def count_human_repairs(text: str, patterns: Iterable[str] = DEFAULT_HUMAN_PATTERNS) -> list[str]:
    """扫日志文本里的人工干预痕迹（返回命中行，便于人去复核）。"""
    hits: list[str] = []
    regex = re.compile("|".join(f"(?:{p})" for p in patterns), re.I)
    for line in text.splitlines():
        if regex.search(line):
            hits.append(line.strip()[:200])
    return hits


def fpr_report(filings: Sequence[Mapping[str, Any]], *, now: datetime | None = None,
               stale_hours: float = 48.0) -> dict[str, Any]:
    """按事后处置算误报率（分母只含已处置）。"""
    now = now or utc_now()
    buckets: dict[str, int] = {}
    details: list[dict[str, Any]] = []
    for f in filings:
        disp = str(f.get("disposition") or infer_disposition(f, now=now, stale_hours=stale_hours))
        buckets[disp] = buckets.get(disp, 0) + 1
        details.append({"task_key": f.get("task_key"), "mission_id": f.get("mission_id"),
                        "status": f.get("status"), "disposition": disp})
    adjudicated = sum(buckets.get(k, 0) for k in ("confirmed", "false_positive", "inconclusive"))
    fp = buckets.get("false_positive", 0)
    fpr = (fp / adjudicated) if adjudicated else None
    total = sum(buckets.values())
    fpr_all = (fp / total) if total else None
    claimed = sum(1 for f in filings if f.get("claimed_by") or f.get("session_id"))
    return {
        "buckets": buckets,
        "filed_total": total,
        "adjudicated": adjudicated,
        "false_positives": fp,
        "confirmed": buckets.get("confirmed", 0),
        "inconclusive": buckets.get("inconclusive", 0),
        "unclaimed_expired": buckets.get("unclaimed_expired", 0),
        "pending": buckets.get("pending", 0),
        "claimed": claimed,
        "fpr": None if fpr is None else round(fpr, 4),
        "fpr_including_unclaimed": None if fpr_all is None else round(fpr_all, 4),
        "details": details,
    }


def evaluate(
    *,
    beats: Sequence[Mapping[str, Any]],
    filings: Sequence[Mapping[str, Any]],
    criteria: Criteria | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
    log_text: str = "",
    human_patterns: Iterable[str] = DEFAULT_HUMAN_PATTERNS,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    crit = criteria or Criteria()
    now = now or utc_now()
    end = end or now
    quarter = timedelta(seconds=crit.interval_seconds)
    if start is None:
        parsed = [parse_ts(b.get("ts")) for b in beats]
        parsed = [p for p in parsed if p is not None]
        start = (min(parsed) if parsed else end) - quarter
    cov = coverage(beats, start, end, crit.interval_seconds)
    heal = self_healing(beats, crit.interval_seconds)
    human_hits = count_human_repairs(log_text, human_patterns)
    fp = fpr_report(filings, now=now, stale_hours=crit.stale_hours)

    window_seconds = (end - start).total_seconds()
    longest_ratio = (cov["longest_run_hours"] * 3600.0 / window_seconds) if window_seconds else 0.0
    checks = {
        "C1_coverage": cov["coverage_ratio"] >= crit.coverage_ratio_min,
        "C2_longest_run": round(longest_ratio, 4) >= crit.longest_run_ratio_min,
        "C3_no_human_repair": len(human_hits) <= crit.human_repairs_max,
        "C4_self_heal": heal["self_heal_ratio"] >= crit.self_heal_ratio_min,
        "C5_claimed_executed": fp["confirmed"] >= crit.claimed_executed_min,
        "C6_fpr": (fp["fpr"] is not None) and (fp["fpr"] < crit.fpr_max),
    }
    world_window_done = now >= start + timedelta(hours=crit.window_hours)
    adjudication_done = not (fp["pending"] or fp["unclaimed_expired"]) and fp["filed_total"] > 0

    if not world_window_done:
        status = "PENDING_WALL_CLOCK"
        reason = f"窗口未走完：{(now - start).total_seconds() / 3600.0:.1f}h / {crit.window_hours:g}h"
    elif cov["present_beats"] < max(3, int(0.5 * crit.window_hours * 3600 / crit.interval_seconds)):
        status = "INSUFFICIENT_EVIDENCE"
        reason = f"有效拍仅 {cov['present_beats']}，不足以定谳 {crit.window_hours:g}h 窗口"
    elif not adjudication_done and all(v for k, v in checks.items() if k != "C6_fpr"):
        # 连续性/立单条数都过了，但还有单没人处置 → 误报率无法定谳，不许写成 PASS
        status = "PENDING_ADJUDICATION"
        reason = f"连续性达标，但诊断单仍有 {fp['pending'] + fp['unclaimed_expired']} 件未处置"
    elif all(checks.values()):
        status = "PASS"
        reason = "六条判据全部满足"
    else:
        failed = [k for k, v in checks.items() if not v]
        status = "FAIL"
        reason = "未满足：" + "、".join(failed)

    return {
        "schema": SCHEMA,
        "generated_at": iso(now),
        "window": {"start": iso(start), "end": iso(end),
                   "hours_elapsed": round(window_seconds / 3600.0, 2),
                   "hours_required": crit.window_hours},
        "criteria": crit.to_dict(),
        "checks": checks,
        "status": status,
        "reason": reason,
        "numbers": {
            "coverage_ratio": cov["coverage_ratio"],
            "present_beats": cov["present_beats"],
            "expected_beats": cov["expected_beats"],
            "error_beats": cov["error_beats"],
            "missing_beats": cov["missing_beats"],
            "longest_run_hours": cov["longest_run_hours"],
            "longest_run_ratio": round(longest_ratio, 4),
            "human_repair_hits": len(human_hits),
            "self_heal_ratio": heal["self_heal_ratio"],
            "claimed_executed": fp["confirmed"],
            "fpr": fp["fpr"],
            "fpr_including_unclaimed": fp["fpr_including_unclaimed"],
            "filed_total": fp["filed_total"],
            "adjudicated": fp["adjudicated"],
        },
        "coverage": cov,
        "self_healing": heal,
        "filings": fp,
        "human_repair_hits": human_hits[:20],
        "extra": dict(extra or {}),
    }


def write_json(payload: Mapping[str, Any], path: str) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))