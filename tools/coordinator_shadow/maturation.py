#!/usr/bin/env python3
"""SR1 影子决策成熟化引擎（IN28-N06）。

要解决的问题
------------
IN27-H7 的继承就绪仪表把 SR1（影子分歧率）算成 ``WITHHELD``：影子每拍都留下"我认为哪里
有病、我会怎么做"，但**没有一条通道把提议和事后实际处置自动对齐**，于是分母永远是 0。
本模块补的就是这条通道，并且做成通用件：

    proposal（影子提议）  --匹配阶梯-->  disposition（实际处置/事后观测）  -->  裁决

匹配阶梯（每级都留证据，级数进回填记录）
----------------------------------------
| 级 | 名称 | 证据 | 裁决 |
|---|---|---|---|
| L1 | ``mission_outcome`` | 任务表的 ``task_key`` 与提议的 ``task_key`` 精确相等，读终态/领单人 | 已执行 → 一致；失败/撤回 → 分歧 |
| L1 | ``pr_outcome`` | 影子门禁裁决 ``repo#pr`` ↔ 实际结局 ``merged/closed`` | 裁决与结局同向 → 一致 |
| L2 | ``telemetry`` | 同一指标的**后续拍**观测值（账本 ``pulse_digest``），按注册表阈值判越界 | 仍越界 → 一致；回界 → 分歧 |

为什么不是词面匹配
------------------
IN27-H7 的实现用「器官名在处置文本里出现」判处置，处置文本不写器官名就判成未处置——这会把
**数据缺口算成分歧**。本引擎改用**结构键**（task_key / repo#pr / 指标名）对齐，找不到结构键
时才退化成"无证据"（``unobservable``），并把"无证据"与"分歧"分开计数——这是本单对
`match_rate` 与 `divergence_rate` 两个口径的分工：前者数"有没有挂上处置"，后者只数"挂上且
判得出方向"的那部分。

代理路由（诚实标注）
--------------------
``pulse_digest`` 的键名与注册表指标名不完全同名，个别口径只能代理映射（例如
``campaign_blocked`` ↔ ``blocked_nodes``）。每条记录带 ``route_grade``（``exact`` / ``proxy``），
报告里把 exact-only 的匹配率单独列出，不让代理路由悄悄抬高指标。

产物边界
--------
回填库、报告、图只能写在沙箱根目录之内（``sandbox.SandboxGuard``，fail-closed）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .registry import spec as metric_spec
from .sandbox import SandboxGuard, self_check

SCHEMA = "coordinator-shadow-maturation/1"
STORE_SCHEMA = "coordinator-shadow-maturation-store/1"
CHANNEL_SHADOW = "coordinator_shadow"
CHANNEL_MERGE = "shadow_merge"

#: 判据门槛（任务书数字，写死在代码里，报告只引用不手抄）
TARGET_MATCH_RATE = 0.90
TARGET_BACKFILL = 50
TARGET_DIVERGENCE = 0.20
DEFAULT_WINDOW_HOURS = 72.0
DEFAULT_LOOKAHEAD_HOURS = 1.0

#: 账本 ``pulse_digest`` 键 → 注册表指标名（``exact``）+ 代理映射（``proxy``）
TELEMETRY_ROUTES: dict[str, tuple[str, str]] = {
    "credit_backlog": ("credit_backlog", "exact"),
    "declaration_rate": ("declaration_rate", "exact"),
    "queued_open": ("queued_open", "exact"),
    "adoption_stall_minutes": ("oldest_queued_minutes", "exact"),
    "adoption_stall_stuck": ("queued_stuck_2h", "exact"),
    "queued_stuck_2h": ("queued_stuck_2h", "exact"),
    "warn_total": ("warn_total", "exact"),
    "error_total": ("error_total", "exact"),
    "running": ("running", "exact"),
    "chronicle_age_minutes": ("chronicle_age_minutes", "exact"),
    "awaiting_pr_merge": ("awaiting_pr_merge", "exact"),
    "capability_deltas_24h": ("capability_deltas_24h", "exact"),
    # 代理：chronicle s2 的 blocked_nodes 含门停放，与注册表 campaign_blocked 口径不同
    "blocked_nodes": ("campaign_blocked", "proxy"),
    "nodes_pending_active_plans": ("campaign_pending", "proxy"),
}

#: 只有器官名（v0 的 ``would_do[].target`` / ``findings[].organ``）时用的落地指标
ORGAN_DEFAULT_METRIC: dict[str, str] = {
    "credit_backlog": "credit_backlog",
    "adoption_stall": "queued_stuck_2h",
    "queued_stuck": "queued_stuck_2h",
    "declaration_rate": "declaration_rate",
    "s5_outcomes": "declaration_rate",
    "s7_voice": "declaration_rate",
    "dispatcher_warns": "warn_total",
    "daemon_warns": "warn_total",
    "s2_campaign": "campaign_blocked",
    "s4_fleet_pulse": "running",
    "awaiting_pr_merge": "awaiting_pr_merge",
    "s1_supply": "queued_open",
}

#: 处置侧「真的执行了」的状态集合（其余按分歧计）
ACTED_MISSION_STATES = {"running", "claimed", "succeeded", "done", "completed", "passed"}
DEAD_MISSION_STATES = {"failed", "cancelled", "canceled", "closed", "abandoned", "expired"}
NON_TELEMETRY_KEYS = {
    "as_of", "clock_ts", "withheld", "warn_clusters", "facets_present",
    "chronicle_stale", "chronicle_ts",
}


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    channel: str
    key: str                       # 结构键：task_key / repo#pr / 空
    organ: str
    metric: str | None
    action: str
    claim: str
    claimed_at: datetime
    threshold: float | None = None
    direction: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["claimed_at"] = _iso(self.claimed_at)
        d["payload"] = dict(self.payload)
        return d


@dataclass(frozen=True)
class Disposition:
    disposition_id: str
    channel: str
    key: str
    state: str
    source: str                    # mission | pr_outcome | telemetry
    observed_at: datetime
    value: float | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["observed_at"] = _iso(self.observed_at)
        d["payload"] = dict(self.payload)
        return d


@dataclass
class MatchRecord:
    proposal_id: str
    channel: str
    organ: str
    metric: str | None
    action: str
    level: str                     # L1_mission_outcome | L1_pr_outcome | L2_telemetry | none
    route_grade: str               # exact | proxy | n/a
    state: str                     # matched | diverged | pending | unobservable
    writer: str = "n/a"            # v1 | v0 | n/a（写者指纹，来自账本行）
    divergence_kind: str = ""      # resolved_condition | dead_mission | gate_contradicted
    disposition_id: str | None = None
    disposition_source: str | None = None
    claimed_at: str = ""
    observed_at: str | None = None
    lag_seconds: float | None = None
    value: float | None = None
    threshold: float | None = None
    note: str = ""
    record_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _hash(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 解析：账本拍 → 提议 / 处置
# ---------------------------------------------------------------------------


def proposals_from_beats(rows: Sequence[Mapping[str, Any]]) -> list[Proposal]:
    """从 ``coordinator_shadow`` 行里抽出提议（v1 findings + v0 would_do/findings）。"""
    out: list[Proposal] = []
    for row in rows:
        rid = row.get("id")
        ts = _ts(row.get("ts"))
        if ts is None:
            continue
        verdict = row.get("verdict") or {}
        if isinstance(verdict, str):
            try:
                verdict = json.loads(verdict)
            except json.JSONDecodeError:
                verdict = {}
        writer = "v1" if (row.get("beat_id") and row.get("schema")) else "v0"
        findings = verdict.get("findings") or []
        for i, f in enumerate(findings):
            if not isinstance(f, Mapping):
                continue
            organ = str(f.get("organ") or "")
            metric = f.get("metric")
            out.append(Proposal(
                proposal_id=f"{CHANNEL_SHADOW}:beat{rid}:finding{i}",
                channel=CHANNEL_SHADOW,
                key=str(f.get("task_key") or ""),
                organ=organ,
                metric=str(metric) if metric else None,
                action="diagnose" if f.get("advisory") else "file_mission",
                claim=str(f.get("symptom") or f.get("signature") or organ),
                claimed_at=ts,
                threshold=_num(f.get("threshold")),
                direction=f.get("direction") if f.get("direction") else None,
                payload={"writer": writer, "beat_id": row.get("beat_id"),
                         "advisory": bool(f.get("advisory")),
                         "severity": f.get("severity"), "signature": f.get("signature")},
            ))
        for i, w in enumerate(verdict.get("would_do") or []):
            if not isinstance(w, Mapping):
                continue
            target = str(w.get("target") or "")
            out.append(Proposal(
                proposal_id=f"{CHANNEL_SHADOW}:beat{rid}:would_do{i}",
                channel=CHANNEL_SHADOW,
                key="",
                organ=target,
                metric=None,
                action=str(w.get("action") or "monitor"),
                claim=str(w.get("reason") or target),
                claimed_at=ts,
                payload={"writer": writer, "beat_id": row.get("beat_id")},
            ))
    return out


def dispositions_from_beats(rows: Sequence[Mapping[str, Any]],
                            ) -> list[Disposition]:
    """账本 ``pulse_digest`` 里的遥测 → 观测处置（后续拍即"事后观测"）。"""
    out: list[Disposition] = []
    for row in rows:
        rid = row.get("id")
        ts = _ts(row.get("ts"))
        digest = row.get("pulse_digest") or {}
        if isinstance(digest, str):
            try:
                digest = json.loads(digest)
            except json.JSONDecodeError:
                digest = {}
        if ts is None or not isinstance(digest, Mapping):
            continue
        for key, value in digest.items():
            if key in NON_TELEMETRY_KEYS:
                continue
            route = TELEMETRY_ROUTES.get(key)
            canonical, grade = route if route else (key, "exact")
            out.append(Disposition(
                disposition_id=f"telemetry:{canonical}:beat{rid}",
                channel=CHANNEL_SHADOW,
                key=f"telemetry:{canonical}",
                state="observed",
                source="telemetry",
                observed_at=ts,
                value=_num(value),
                payload={"telemetry_key": key, "route_grade": grade, "beat_id": row.get("beat_id")},
            ))
    return out


def dispositions_from_missions(rows: Sequence[Mapping[str, Any]]) -> list[Disposition]:
    out: list[Disposition] = []
    for row in sorted(rows, key=lambda r: (str(r.get("task_key") or ""), r.get("id") or 0)):
        task_key = str(row.get("task_key") or "")
        if not task_key:
            continue
        ts = _ts(row.get("created_at")) or _ts(row.get("finished_at"))
        if ts is None:
            continue
        out.append(Disposition(
            disposition_id=f"mission:{row.get('id')}",
            channel=CHANNEL_SHADOW,
            key=task_key,
            state=str(row.get("status") or "unknown"),
            source="mission",
            observed_at=ts,
            payload={"mission_id": row.get("id"), "claimed_by": row.get("claimed_by"),
                     "status": row.get("status")},
        ))
    return out


def proposals_and_dispositions_from_merge(doc: Mapping[str, Any],
                                          ) -> tuple[list[Proposal], list[Disposition]]:
    """``shadow_merge.json``：PR 门禁影子裁决 ↔ 实际结局（merged/closed）。"""
    props: list[Proposal] = []
    disps: list[Disposition] = []
    for key, entry in (doc.get("entries") or {}).items():
        if not isinstance(entry, Mapping):
            continue
        first = _ts(entry.get("first_seen"))
        last = _ts(entry.get("last_seen")) or first
        if first is None:
            continue
        verdict = entry.get("verdict")
        props.append(Proposal(
            proposal_id=f"{CHANNEL_MERGE}:{key}",
            channel=CHANNEL_MERGE,
            key=str(key),
            organ="s6_pr_gate",
            metric=None,
            action="gate_verdict",
            claim=str(verdict or ""),
            claimed_at=first,
            payload={"repo": entry.get("repo"), "pr": entry.get("pr"),
                     "reasons": entry.get("reasons"), "last_seen": _iso(last)},
        ))
        outcome = entry.get("outcome")
        if outcome:
            obs = _ts(entry.get("outcome_ts")) or last
            disps.append(Disposition(
                disposition_id=f"pr_outcome:{key}",
                channel=CHANNEL_MERGE,
                key=str(key),
                state=str(outcome),
                source="pr_outcome",
                observed_at=obs,
                payload={"verdict": verdict, "reasons": entry.get("reasons")},
            ))
    return props, disps


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def _violates(value: float | None, threshold: float | None,
              direction: str | None) -> bool | None:
    """按阈值/方向判"仍越界"。缺阈值或缺数值 → None（判不了）。"""
    if value is None or threshold is None:
        return None
    if direction == "lower_worse":
        return value <= threshold
    return value >= threshold


def _telemetry_key(proposal: Proposal) -> tuple[str | None, str]:
    """提议 → (规范化指标名, 路由等级)。"""
    candidates: list[str] = []
    if proposal.metric:
        candidates.append(proposal.metric)
    default = ORGAN_DEFAULT_METRIC.get(proposal.organ)
    if default:
        candidates.append(default)
    for name in candidates:
        grade = "exact"
        for key, (canonical, g) in TELEMETRY_ROUTES.items():
            if canonical == name:
                grade = g
                break
        return name, grade
    return None, "n/a"


def _index(dispositions: Iterable[Disposition]) -> dict[str, list[Disposition]]:
    idx: dict[str, list[Disposition]] = {}
    for d in dispositions:
        idx.setdefault(f"{d.channel}|{d.key}", []).append(d)
    for bucket in idx.values():
        bucket.sort(key=lambda d: d.observed_at)
    return idx


def match_proposal(proposal: Proposal, idx: Mapping[str, list[Disposition]],
                   now: datetime, lookahead_hours: float) -> MatchRecord:
    horizon = proposal.claimed_at + timedelta(hours=lookahead_hours)
    maturity_elapsed = now >= horizon
    base = MatchRecord(
        proposal_id=proposal.proposal_id,
        channel=proposal.channel,
        organ=proposal.organ,
        metric=proposal.metric,
        action=proposal.action,
        level="none",
        route_grade="n/a",
        state="unobservable",
        writer=str((proposal.payload or {}).get("writer") or "n/a"),
        claimed_at=_iso(proposal.claimed_at) or "",
    )

    # L1：结构键 → 实际处置（任务终态 / PR 结局）
    if proposal.key:
        bucket = idx.get(f"{proposal.channel}|{proposal.key}") or []
        for d in bucket:
            if d.source == "mission":
                # 立单是"提议之后"的处置：早于提议（含 5 min 时钟容差）的单一律不算
                if d.observed_at < proposal.claimed_at - timedelta(minutes=5):
                    continue
                if d.observed_at > horizon:
                    continue
                acted = d.state in ACTED_MISSION_STATES
                dead = d.state in DEAD_MISSION_STATES
                base.level = "L1_mission_outcome"
                base.route_grade = "exact"
                base.disposition_id = d.disposition_id
                base.disposition_source = d.source
                base.observed_at = _iso(d.observed_at)
                base.lag_seconds = (d.observed_at - proposal.claimed_at).total_seconds()
                if not dead:
                    base.state = "matched" if acted or d.state == "queued" else "unobservable"
                    base.note = f"mission {d.payload.get('mission_id')} status={d.state}" + (
                        f" claimed_by={d.payload.get('claimed_by')}" if d.payload.get('claimed_by') else "")
                    if d.state == "queued":
                        base.note += "（已立单未领，按 提议被采纳 计一致）"
                else:
                    base.state = "diverged"
                    base.divergence_kind = "dead_mission"
                    base.note = f"mission {d.payload.get('mission_id')} 终态 {d.state}"
                return base
            if d.source == "pr_outcome":
                verdict = str((d.payload or {}).get("verdict") or proposal.claim)
                merged = d.state == "merged"
                agree = (verdict == "可合" and merged) or (verdict == "不可合" and not merged)
                base.level = "L1_pr_outcome"
                base.route_grade = "exact"
                base.disposition_id = d.disposition_id
                base.disposition_source = d.source
                base.observed_at = _iso(d.observed_at)
                base.lag_seconds = (d.observed_at - proposal.claimed_at).total_seconds()
                base.state = "matched" if agree else "diverged"
                base.divergence_kind = "" if agree else "gate_contradicted"
                base.note = f"影子裁决 {verdict} vs 实际 {d.state}"
                return base

    # L2：指标后续观测
    metric, grade = _telemetry_key(proposal)
    base.metric = metric or base.metric
    if metric:
        base.route_grade = grade
        bucket = idx.get(f"{proposal.channel}|telemetry:{metric}") or []
        later = [d for d in bucket if proposal.claimed_at < d.observed_at <= horizon]
        if later:
            d = later[0]
            observed = _num(d.value)
            threshold = proposal.threshold
            direction = proposal.direction
            if threshold is None or direction is None:
                ms = metric_spec(metric)
                if ms is not None:
                    threshold = ms.warn if ms.warn is not None else ms.crit
                    direction = ms.direction
            verdict = _violates(observed, threshold, direction)
            base.level = "L2_telemetry"
            base.disposition_id = d.disposition_id
            base.disposition_source = d.source
            base.observed_at = _iso(d.observed_at)
            base.lag_seconds = (d.observed_at - proposal.claimed_at).total_seconds()
            base.value = observed
            base.threshold = threshold
            if verdict is None:
                base.state = "unobservable"
                base.note = "有后续观测但缺阈值/方向，判不了"
            else:
                base.state = "matched" if verdict else "diverged"
                if not verdict:
                    base.divergence_kind = "resolved_condition"
                base.note = (f"{metric}={observed:g} vs 阈值 {threshold:g}"
                             f"（{direction}）→ {'仍越界（前提成立）' if verdict else '已回界（条件消解，未归因）'}")
            return base

    # 无处置证据
    if not maturity_elapsed:
        base.state = "pending"
        base.note = f"未过 {lookahead_hours:g}h 前瞻窗口"
    else:
        base.state = "unobservable"
        base.note = f"前瞻窗口内无 {metric or proposal.organ} 的结构化处置证据"
    return base


def mature(proposals: Sequence[Proposal], dispositions: Sequence[Disposition],
           now: datetime, lookahead_hours: float = DEFAULT_LOOKAHEAD_HOURS,
           ) -> list[MatchRecord]:
    idx = _index(dispositions)
    records = [match_proposal(p, idx, now, lookahead_hours) for p in proposals]
    for r in records:
        r.record_hash = _hash({
            "proposal_id": r.proposal_id, "level": r.level, "state": r.state,
            "disposition_id": r.disposition_id, "observed_at": r.observed_at,
        })
    records.sort(key=lambda r: (r.channel, r.proposal_id))
    return records


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 95% 区间：小样本比例不能只给点估计（n=57 时 ±13pp 是常态）。"""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def summarise(records: Sequence[MatchRecord], lookahead_hours: float) -> dict[str, Any]:
    def _block(rows: Sequence[MatchRecord]) -> dict[str, Any]:
        counts = {k: sum(1 for r in rows if r.state == k)
                  for k in ("matched", "diverged", "pending", "unobservable")}
        judged = counts["matched"] + counts["diverged"]
        attached = sum(1 for r in rows if r.disposition_id)
        exact = [r for r in rows if r.route_grade == "exact"]
        exact_attached = sum(1 for r in exact if r.disposition_id)
        by_level: dict[str, int] = {}
        for r in rows:
            by_level[r.level] = by_level.get(r.level, 0) + 1
        by_grade: dict[str, int] = {}
        for r in rows:
            by_grade[r.route_grade] = by_grade.get(r.route_grade, 0) + 1
        by_writer: dict[str, dict[str, Any]] = {}
        for r in rows:
            slot = by_writer.setdefault(r.writer, {"total": 0, "matched": 0, "diverged": 0,
                                                   "pending": 0, "unobservable": 0})
            slot["total"] += 1
            slot[r.state] = slot.get(r.state, 0) + 1
        for slot in by_writer.values():
            judged_w = slot["matched"] + slot["diverged"]
            slot["divergence_rate"] = None if judged_w == 0 else slot["diverged"] / judged_w
            slot["divergence_rate_ci95"] = (None if judged_w == 0
                                            else wilson(slot["diverged"], judged_w))
        by_kind: dict[str, int] = {}
        for r in rows:
            if r.divergence_kind:
                by_kind[r.divergence_kind] = by_kind.get(r.divergence_kind, 0) + 1
        by_action: dict[str, int] = {}
        for r in rows:
            by_action[r.action] = by_action.get(r.action, 0) + 1
        lags = sorted(r.lag_seconds for r in rows if r.lag_seconds is not None)
        matured = len(rows) - counts["pending"]
        return {
            "total": len(rows),
            "counts": counts,
            "attached": attached,
            "matured": matured,
            "match_rate_matured": None if matured <= 0 else attached / matured,
            "match_rate": None if not rows else attached / len(rows),
            "match_rate_ci95": None if not rows else wilson(attached, len(rows)),
            "exact_total": len(exact),
            "exact_match_rate": None if not exact else exact_attached / len(exact),
            "judged": judged,
            "divergence_rate": None if judged == 0 else counts["diverged"] / judged,
            "divergence_rate_ci95": None if judged == 0 else wilson(counts["diverged"], judged),
            "by_level": by_level,
            "by_route_grade": by_grade,
            "by_writer": by_writer,
            "by_divergence_kind": by_kind,
            "by_action": by_action,
            "lag_seconds": {
                "n": len(lags),
                "min": lags[0] if lags else None,
                "median": lags[len(lags) // 2] if lags else None,
                "max": lags[-1] if lags else None,
            },
        }

    channels: dict[str, dict[str, Any]] = {}
    for name in sorted({r.channel for r in records}):
        channels[name] = _block([r for r in records if r.channel == name])
    aggregate = _block(records)
    aggregate["lookahead_hours"] = lookahead_hours
    return {"channels": channels, "aggregate": aggregate}


def sr1_block(records: Sequence[MatchRecord], lookahead_hours: float) -> dict[str, Any]:
    """SR1（影子分歧率）在 coordinator_shadow 通道上的读数——写 chronicle s8 用这个。"""
    rows = [r for r in records if r.channel == CHANNEL_SHADOW]
    counts = {k: sum(1 for r in rows if r.state == k)
              for k in ("matched", "diverged", "pending", "unobservable")}
    judged = counts["matched"] + counts["diverged"]
    value = None if judged == 0 else counts["diverged"] / judged
    status = "REAL" if value is not None else "WITHHELD"
    return {
        "key": "SR1",
        "label": "影子分歧率（影子提议 vs 事后实际处置）",
        "value": value,
        "status": status,
        "target": TARGET_DIVERGENCE,
        "target_rule": f"value < {TARGET_DIVERGENCE}",
        "meets_target": None if value is None else bool(value < TARGET_DIVERGENCE),
        "counts": counts,
        "items": len(rows),
        "judged": judged,
        "lookahead_hours": lookahead_hours,
        "maturity": "provisional" if counts["pending"] else "settled",
        "reason": (f"{counts['diverged']}/{judged} decided shadow items diverged"
                   f"（{counts['pending']} pending、{counts['unobservable']} unobservable）"),
    }


# ---------------------------------------------------------------------------
# 回填库（append-only + 幂等）
# ---------------------------------------------------------------------------

STORE_DDL = (
    "CREATE TABLE IF NOT EXISTS maturation_records ("
    " record_hash TEXT PRIMARY KEY, engine_schema TEXT NOT NULL, channel TEXT NOT NULL,"
    " proposal_id TEXT NOT NULL, disposition_id TEXT, level TEXT NOT NULL, state TEXT NOT NULL,"
    " route_grade TEXT, organ TEXT, metric TEXT, action TEXT, claimed_at TEXT, observed_at TEXT,"
    " lag_seconds REAL, value REAL, threshold REAL, note TEXT, payload_json TEXT, ingested_at TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_maturation_channel ON maturation_records (channel, state)",
    "CREATE INDEX IF NOT EXISTS ix_maturation_claimed ON maturation_records (claimed_at)",
)


class MaturationStore:
    """回填库：SQLite，append-only（``INSERT OR IGNORE`` 以 record_hash 主键幂等）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        for ddl in STORE_DDL:
            self.conn.execute(ddl)
        self.conn.commit()

    def append(self, records: Sequence[MatchRecord], ingested_at: datetime) -> dict[str, Any]:
        before = self.count()
        rows = [
            (r.record_hash, SCHEMA, r.channel, r.proposal_id, r.disposition_id, r.level, r.state,
             r.route_grade, r.organ, r.metric, r.action, r.claimed_at, r.observed_at,
             r.lag_seconds, r.value, r.threshold, r.note,
             json.dumps(r.to_dict(), ensure_ascii=False, sort_keys=True), _iso(ingested_at))
            for r in records
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO maturation_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        after = self.count()
        return {"path": self.path, "before": before, "after": after,
                "inserted": after - before, "attempted": len(rows)}

    def count(self, where: str = "", params: Sequence[Any] = ()) -> int:
        cur = self.conn.execute(f"SELECT count(*) FROM maturation_records {where}", tuple(params))
        return int(cur.fetchone()[0])

    def by_channel(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT channel, count(*) FROM maturation_records GROUP BY channel ORDER BY channel")
        return {row[0]: int(row[1]) for row in cur.fetchall()}

    def content_digest(self) -> str:
        cur = self.conn.execute(
            "SELECT record_hash FROM maturation_records ORDER BY record_hash")
        blob = "\n".join(row[0] for row in cur.fetchall())
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


def load_from_db(dsn: str, since: datetime, until: datetime,
                 limit_beats: int = 5000) -> dict[str, Any]:
    """读生产库：账本拍 + 影子诊断单。只读（psycopg 惰性导入）。"""
    import psycopg  # 只在需要真库时导入

    beats: list[dict[str, Any]] = []
    missions: list[dict[str, Any]] = []
    errors: list[str] = []
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT id, ts, model, beat_id, schema, verdict, pulse_digest, filings, error"
                    " FROM coordinator_shadow"
                    " WHERE ts >= %(since)s AND ts <= %(until)s"
                    " ORDER BY ts LIMIT %(lim)s",
                    {"since": since, "until": until, "lim": int(limit_beats)},
                )
                for r in cur.fetchall():
                    beats.append({"id": r[0], "ts": r[1], "model": r[2], "beat_id": r[3],
                                  "schema": r[4], "verdict": r[5], "pulse_digest": r[6],
                                  "filings": r[7], "error": r[8]})
            except Exception as exc:  # pragma: no cover - 生产库结构漂移
                errors.append(f"beats: {type(exc).__name__}: {exc}")
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT id, task_key, status, claimed_by, created_at, finished_at"
                    " FROM tensor_dev_missions"
                    " WHERE task_key IS NOT NULL AND created_at >= %(since)s",
                    {"since": since - timedelta(days=1)},
                )
                for r in cur.fetchall():
                    missions.append({"id": r[0], "task_key": r[1], "status": r[2],
                                     "claimed_by": r[3], "created_at": r[4],
                                     "finished_at": r[5]})
            except Exception as exc:  # pragma: no cover
                errors.append(f"missions: {type(exc).__name__}: {exc}")
    return {"beats": beats, "missions": missions, "errors": errors}


def load_merge(path: str) -> dict[str, Any]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:  # pragma: no cover
        return {"entries": {}, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Chronicle s8 发布（独立发布通道，不属于引擎的变更面）
# ---------------------------------------------------------------------------

CHRONICLE_INSERT = (
    "INSERT INTO chronicle_snapshots (ts, payload) VALUES (%(ts)s, %(payload)s::jsonb)"
)


def chronicle_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """与 ``tools/autonomy/sr_readiness.chronicle_payload`` 同形，让既有的 s8 趋势读取器可用。"""
    sr1 = report["sr1"]
    return {
        "collector": "tools/coordinator_shadow/maturation.py (IN28-N06 SR1 maturation)",
        "schema": "chronicle-clock/1",
        "kind": "sr_inheritance_readiness",
        "clock": {"slot": "s8", "name": "harness", "facet": "sr_inheritance"},
        "s8_harness": {
            "sr_inheritance": {
                "SR1": {
                    "value": sr1["value"],
                    "status": sr1["status"],
                    "target": sr1["target"],
                    "meets_target": sr1["meets_target"],
                    "counts": sr1["counts"],
                    "items": sr1["items"],
                    "rounds": report["inputs"]["beats"],
                    "maturity": sr1["maturity"],
                    "match_rate": report["aggregate"]["match_rate"],
                    "lookahead_hours": sr1["lookahead_hours"],
                    "source": "maturation-engine",
                }
            }
        },
        "not_measured_by_this_collector": ["SR2", "SR3", "SR4"],
        "sr1_maturation": {
            "match_rate": report["aggregate"]["match_rate"],
            "backfill_records": report["criteria"]["backfill_records"],
            "store_digest": report["store"]["content_digest"],
        },
        "generated_at": report["generated_at"],
        "domain_semantics": "none",
    }


def publish_chronicle(dsn: str, report: Mapping[str, Any],
                      ts: datetime | None = None) -> dict[str, Any]:
    import psycopg

    payload = chronicle_payload(report)
    stamp = ts or datetime.now(timezone.utc)
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(CHRONICLE_INSERT, {"ts": stamp,
                                           "payload": json.dumps(payload, ensure_ascii=False,
                                                                 sort_keys=True)})
        conn.commit()
    return {"status": "written", "table": "chronicle_snapshots", "slot": "s8", "ts": _iso(stamp)}


# ---------------------------------------------------------------------------
# 总装
# ---------------------------------------------------------------------------


def build_report(beats: Sequence[Mapping[str, Any]],
                 missions: Sequence[Mapping[str, Any]],
                 merge_doc: Mapping[str, Any],
                 now: datetime,
                 window_hours: float = DEFAULT_WINDOW_HOURS,
                 lookahead_hours: float = DEFAULT_LOOKAHEAD_HOURS,
                 source_errors: Sequence[str] = ()) -> tuple[dict[str, Any], list[MatchRecord]]:
    props = proposals_from_beats(beats)
    disps = dispositions_from_beats(beats) + dispositions_from_missions(missions)
    m_props, m_disps = proposals_and_dispositions_from_merge(merge_doc)
    props += m_props
    disps += m_disps
    records = mature(props, disps, now, lookahead_hours)
    agg = summarise(records, lookahead_hours)
    sr1 = sr1_block(records, lookahead_hours)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "window_hours": window_hours,
        "lookahead_hours": lookahead_hours,
        "inputs": {
            "beats": len(beats),
            "beats_v1": sum(1 for b in beats if b.get("beat_id") and b.get("schema")),
            "beats_v0": sum(1 for b in beats if not (b.get("beat_id") and b.get("schema"))),
            "missions": len(missions),
            "merge_entries": len((merge_doc.get("entries") or {})),
            "proposals": len(props),
            "dispositions": len(disps),
            "source_errors": list(source_errors),
        },
        "channels": agg["channels"],
        "aggregate": agg["aggregate"],
        "sr1": sr1,
    }
    return report, records


def apply_criteria(report: dict[str, Any], store_stats: Mapping[str, Any]) -> dict[str, Any]:
    agg = report["aggregate"]
    match_rate = agg["match_rate"]
    backfill = int(store_stats.get("after") or 0)
    report["criteria"] = {
        "match_rate_target": TARGET_MATCH_RATE,
        "match_rate": match_rate,
        "match_rate_matured": agg.get("match_rate_matured"),
        "match_rate_ok": bool(match_rate is not None and match_rate >= TARGET_MATCH_RATE),
        "exact_match_rate": agg["exact_match_rate"],
        "backfill_target": TARGET_BACKFILL,
        "backfill_records": backfill,
        "backfill_ok": backfill >= TARGET_BACKFILL,
        "divergence_target": TARGET_DIVERGENCE,
        "divergence_rate": report["sr1"]["value"],
        "divergence_ok": (None if report["sr1"]["value"] is None
                          else bool(report["sr1"]["value"] < TARGET_DIVERGENCE)),
        "sr1_status": report["sr1"]["status"],
    }
    report["criteria"]["all_ok"] = bool(
        report["criteria"]["match_rate_ok"] and report["criteria"]["backfill_ok"])
    return report["criteria"]


def run(sandbox_root: str, out_path: str, *, dsn: str | None = None,
        merge_path: str = "/root/tp-agent/shadow_merge.json",
        now: datetime | None = None, window_hours: float = DEFAULT_WINDOW_HOURS,
        lookahead_hours: float = DEFAULT_LOOKAHEAD_HOURS,
        store_name: str = "maturation.sqlite3", records_out: str | None = None,
        publish: bool = False, beats: Sequence[Mapping[str, Any]] | None = None,
        missions: Sequence[Mapping[str, Any]] | None = None,
        merge_doc: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """一次成熟化：取数 → 匹配 → 回填 → 判定 → 落 artifact。"""
    now = now or datetime.now(timezone.utc)
    artifact_root = os.path.dirname(os.path.abspath(out_path)) or "."
    repo_root = os.path.realpath(os.getcwd())
    if os.path.commonpath([os.path.realpath(artifact_root), repo_root]) != repo_root:
        raise ValueError(f"artifact 目录必须在本仓工作树内：{artifact_root}")
    guard = SandboxGuard(sandbox_root, extra_roots=(artifact_root,))
    source_errors: list[str] = []
    if beats is None or missions is None:
        if dsn:
            loaded = load_from_db(dsn, now - timedelta(hours=window_hours), now)
            beats = loaded["beats"]
            missions = loaded["missions"]
            source_errors += loaded["errors"]
        else:
            beats, missions = [], []
            source_errors.append("no dsn: coordinator_shadow channel empty")
    if merge_doc is None:
        merge_doc = load_merge(merge_path)
        if merge_doc.get("error"):
            source_errors.append(f"shadow_merge: {merge_doc['error']}")

    report, records = build_report(beats, missions, merge_doc, now,
                                   window_hours, lookahead_hours, source_errors)
    store_path = guard.resolve(os.path.join(sandbox_root, store_name))
    store = MaturationStore(store_path)
    try:
        stats = store.append(records, now)
        stats["content_digest"] = store.content_digest()
        stats["by_channel"] = store.by_channel()
    finally:
        store.close()
    report["store"] = stats
    records_out = records_out or os.path.join(sandbox_root, "records.json")
    if records_out:
        # 紧凑 JSON：逐条记录是审计证据，但要压体积（1.8k 条 × 缩进≈0.7 MB）
        guard.write_bytes(records_out, (json.dumps(
            {"schema": f"{SCHEMA}#records", "generated_at": _iso(now),
             "records": [r.to_dict() for r in records]},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode())
        report["records_path"] = os.path.realpath(records_out)
    apply_criteria(report, stats)
    report["sandbox"] = guard.summary()
    report["failclosed"] = self_check(os.path.join(sandbox_root, "selfcheck"),
                                     extra_roots=(artifact_root,))
    if publish:
        report["chronicle"] = publish_chronicle(dsn or "", report, now)
    # 先落盘一次，再刷新写入审计（否则 artifact 自己这次写不在审计里），最后再落一次
    guard.write_json(out_path, report)
    report["sandbox"] = guard.summary()
    report["artifact"] = os.path.realpath(out_path)
    guard.write_json(out_path, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SR1 shadow maturation engine (IN28-N06)")
    ap.add_argument("--sandbox", default="runs/in28_n06/sandbox")
    ap.add_argument("--out", default="artifacts/coordinator_shadow/sr1_maturation.json")
    ap.add_argument("--dsn", default=os.environ.get("TP_DSN")
                    or "dbname=tensorplatform user=postgres host=/var/run/postgresql")
    ap.add_argument("--no-db", action="store_true", help="不连库（只用 merge 通道）")
    ap.add_argument("--merge", default="/root/tp-agent/shadow_merge.json")
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    ap.add_argument("--lookahead-hours", type=float, default=DEFAULT_LOOKAHEAD_HOURS)
    ap.add_argument("--now", default=None, help="ISO 时间（可复算/回放用）")
    ap.add_argument("--records-out", default=None,
                    help="把逐条匹配记录落盘（默认 <sandbox>/records.json）")
    ap.add_argument("--publish-chronicle", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    report = run(args.sandbox, args.out,
                 dsn=None if args.no_db else args.dsn, merge_path=args.merge,
                 now=now, window_hours=args.window_hours,
                 lookahead_hours=args.lookahead_hours,
                 records_out=args.records_out or os.path.join(args.sandbox, "records.json"),
                 publish=args.publish_chronicle)
    print(json.dumps({
        "artifact": report["artifact"],
        "match_rate": report["aggregate"]["match_rate"],
        "exact_match_rate": report["aggregate"]["exact_match_rate"],
        "backfill_records": report["criteria"]["backfill_records"],
        "sr1_divergence": report["sr1"]["value"],
        "sr1_status": report["sr1"]["status"],
        "channels": {k: {"total": v["total"], "match_rate": v["match_rate"],
                         "divergence_rate": v["divergence_rate"]}
                     for k, v in report["channels"].items()},
        "records": report.get("records_path"),
        "chronicle": report.get("chronicle"),
    }, ensure_ascii=False, indent=2))
    return 0 if report["criteria"]["all_ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())