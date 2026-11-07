#!/usr/bin/env python3
"""coordinator_shadow v1 —— 72h 加速演练（IN27-H1）。

为什么要有这一层（先把话说清楚）
--------------------------------
任务验收要的是**连续 72 小时墙钟**。墙钟只能由时间给，任何一次任务都跑不完 72h；
所以本模块做的是**时钟加速的压力演练**：把 144 拍（30 min/拍）在秒级内跑完，验证
- 连续 144 拍不断链、零未捕获异常（``run_beat_safe`` 的自愈路径确实自愈）；
- 三个真故障（信用积压 / 告警流爆簇 / 收养停滞）各立 1 单，共 3 单；
- 一个**单拍尖峰**被持久性门槛挡掉（不立单）；
- 一个**越界但良性**的告警流被 LLM 否决（不立单）；
- 演练里的诊断单被「模拟舰队」领取并执行 → 事后处置计 ``confirmed``，误报率 0%。

诚实标注：这是**合成平台状态**下的演练（``source="synthetic-72h"``），它证明的是
「逻辑与纪律在 72h 尺度的时序上自洽」，**不等于**生产 72h 墙钟验收。墙钟那一条由
``acceptance.py`` 在真实跑满后自动定谳，本次报告里如实标 ``PENDING_WALL_CLOCK``。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from . import diagnose as dg
from . import judge as jd
from . import ledger as lg
from .pulse import Metric, Pulse, WarnCluster, iso, utc_now
from .runtime import run_beat_safe


@dataclass(frozen=True)
class Phase:
    start_h: float
    end_h: float
    metrics: Mapping[str, float]
    label: str = ""
    truth: str | None = None      # real_fault | benign | noise | None


#: 72h 剧本：三个真故障 + 一个单拍尖峰 + 一个良性越界
PHASES: tuple[Phase, ...] = (
    Phase(0.0, 6.0, dict(credit_backlog=5, oldest_queued_minutes=40, queued_stuck_2h=0,
                         declaration_rate=0.86, running=4, error_total=0, warn_total=2,
                         campaign_blocked=1, capability_deltas_24h=14, awaiting_pr_merge=3,
                         queued_open=6, campaign_pending=12), "baseline", None),
    Phase(6.0, 20.0, dict(credit_backlog=28, oldest_queued_minutes=45, queued_stuck_2h=1,
                          declaration_rate=0.84, running=4, error_total=0, warn_total=3,
                          campaign_blocked=1, capability_deltas_24h=13, awaiting_pr_merge=4,
                          queued_open=7, campaign_pending=11), "credit-backlog-ramp", "real_fault"),
    Phase(20.0, 24.0, dict(credit_backlog=6, oldest_queued_minutes=50, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=13, awaiting_pr_merge=4,
                           queued_open=7, campaign_pending=11), "credit-backlog-cleared", None),
    Phase(24.0, 31.5, dict(credit_backlog=6, oldest_queued_minutes=55, queued_stuck_2h=2,
                           declaration_rate=0.83, running=3, error_total=7, warn_total=6,
                           campaign_blocked=2, capability_deltas_24h=11, awaiting_pr_merge=5,
                           queued_open=8, campaign_pending=12), "daemon-error-burst", "real_fault"),
    Phase(31.5, 40.0, dict(credit_backlog=6, oldest_queued_minutes=50, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "recovered", None),
    Phase(40.0, 52.0, dict(credit_backlog=7, oldest_queued_minutes=210, queued_stuck_2h=7,
                           declaration_rate=0.82, running=3, error_total=0, warn_total=4,
                           campaign_blocked=2, capability_deltas_24h=10, awaiting_pr_merge=4,
                           queued_open=14, campaign_pending=13), "adoption-stall", "real_fault"),
    Phase(52.0, 60.0, dict(credit_backlog=6, oldest_queued_minutes=45, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "recovered-2", None),
    Phase(60.0, 60.5, dict(credit_backlog=26, oldest_queued_minutes=44, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "single-beat-noise", "noise"),
    Phase(60.5, 64.0, dict(credit_backlog=6, oldest_queued_minutes=44, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "recovered-3", None),
    Phase(64.0, 65.5, dict(credit_backlog=6, oldest_queued_minutes=44, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=16,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "warn-burst-benign", "benign"),
    Phase(65.5, 72.0, dict(credit_backlog=6, oldest_queued_minutes=44, queued_stuck_2h=1,
                           declaration_rate=0.85, running=4, error_total=0, warn_total=3,
                           campaign_blocked=1, capability_deltas_24h=12, awaiting_pr_merge=3,
                           queued_open=6, campaign_pending=12), "final", None),
)

#: 合成探针 SQL（与生产探针同形，仅作留痕；演练不连库）
_SQL = {
    "credit_backlog": "SELECT count(*) FROM outcome_evaluations e WHERE e.status IN ('confirmed','rejected') AND NOT EXISTS (...)",
    "oldest_queued_minutes": "SELECT min(created_at) FROM tensor_dev_missions WHERE status = 'queued'",
    "queued_stuck_2h": "SELECT count(*) FROM tensor_dev_missions WHERE status = 'queued' AND created_at < :cut",
    "declaration_rate": "SELECT count(*) FROM tensor_dev_missions WHERE finished_at >= :cut AND report_id IS NOT NULL",
    "running": "SELECT count(*) FROM tensor_dev_missions WHERE status IN ('claimed','running')",
    "warn_total": "daemon-warn-stream",
    "error_total": "daemon-warn-stream",
    "campaign_blocked": "SELECT status, count(*) FROM campaign_nodes GROUP BY status",
    "campaign_pending": "SELECT status, count(*) FROM campaign_nodes GROUP BY status",
    "capability_deltas_24h": "SELECT count(*) FROM tensor_capability_deltas WHERE created_at >= :cut",
    "awaiting_pr_merge": "SELECT count(*) FROM tensor_dev_missions WHERE status = 'awaiting_pr_merge'",
    "queued_open": "SELECT count(*) FROM tensor_dev_missions WHERE status = 'queued'",
}

_UNITS = {"declaration_rate": "ratio", "oldest_queued_minutes": "minutes"}


def phase_at(hours: float) -> Phase:
    for ph in PHASES:
        if ph.start_h <= hours < ph.end_h:
            return ph
    return PHASES[-1]


def synthetic_pulse(as_of: datetime, t0: datetime) -> Pulse:
    """按剧本生成一拍的脉搏（确定性，无随机——同一时刻永远同一读数）。"""
    hours = (as_of - t0).total_seconds() / 3600.0
    ph = phase_at(hours)
    organs: dict[str, Metric] = {}
    for name, value in ph.metrics.items():
        organs[name] = Metric(name=name, value=value, sql=_SQL.get(name, "-- synthetic"),
                              params={}, unit=_UNITS.get(name, "count"))
    # 告警流：数字直接给 total；簇按剧本命名
    warn = {
        "window_minutes": 60.0,
        "warn_total": ph.metrics.get("warn_total"),
        "error_total": ph.metrics.get("error_total"),
        "clusters": [],
        "paths_read": ["synthetic://campaign_dispatcher.log"],
        "path_problems": [],
    }
    if (ph.metrics.get("error_total") or 0) > 0:
        warn["clusters"] = [WarnCluster(signature="孤儿收养单 draft 异常 IndexError",
                                       count=int(ph.metrics["error_total"]), level="high",
                                       sample="WARN 孤儿收养单 draft 异常 IndexError")]
    chronicle = {
        "ts": iso(as_of - timedelta(minutes=12)),
        "age_minutes": 12.0,
        "stale": False,
        "schema": "chronicle-eval-pulse/1",
        "facets": {f: {"synthetic": True} for f in ("s1_missions", "s2_campaign", "s3_capability",
                                                    "s4_fleet_pulse", "s5_outcomes", "s6_daemons",
                                                    "s7_voice", "s8_harness")},
        "facets_present": ["s1_missions", "s2_campaign", "s3_capability", "s4_fleet_pulse",
                           "s5_outcomes", "s6_daemons", "s7_voice", "s8_harness"],
    }
    return Pulse(as_of=iso(as_of), organs=organs, chronicle=chronicle, warn=warn,
                 blocked={"counts": {"blocked": int(ph.metrics.get("campaign_blocked", 0))}},
                 audit=[], withheld=[], source="synthetic-72h")


class SyntheticSource:
    def __init__(self, t0: datetime) -> None:
        self.t0 = t0

    def collect(self, as_of: datetime) -> Pulse:
        return synthetic_pulse(as_of, self.t0)


def scripted_judge_script(n_beats: int) -> list[Any]:
    """脚本化 LLM：对良性告警流簇（warn_total）连续三拍投否决，其余照常 confirm。"""
    veto = {
        "findings": [{"organ": "daemon_warns", "metric": "warn_total", "opinion": "veto",
                      "severity": "low",
                      "symptom": "批量合并窗口的已知噪声（读日志重试行），不构成器官病"}],
        "rationale": "告警流簇为批量并行的已知重试噪声，不立单；其余器官按规则层结论。",
        "urgent": False,
    }
    script: list[Any] = []
    for _ in range(n_beats):
        script.append(veto)
    return script


@dataclass
class SoakResult:
    beats: list[dict[str, Any]] = field(default_factory=list)
    filings: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    judge_mode: str = "scripted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "coordinator-shadow-soak/1",
            "judge_mode": self.judge_mode,
            "beats": len(self.beats),
            "beat_rows": list(self.beats),
            "filings": [f for f in self.filings],
            "timeline": self.timeline,
        }


def run_soak(
    *,
    hours: float = 72.0,
    interval_seconds: int = 1800,
    t0: datetime | None = None,
    judge_mode: str = "scripted",
    policy: dg.Policy | None = None,
) -> SoakResult:
    # 演练把开单上限放宽到 6：让「良性越界」在无 LLM 否决时真的能立出来，
    # 这样两种判读模式的误报率差异才是被判出来的，而不是被「开单上限」顺手挡掉。
    policy = policy or dg.Policy(interval_seconds=interval_seconds, max_open_diagnosis=6)
    t0 = t0 or datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
    n_beats = int(hours * 3600 / interval_seconds)
    ledger = lg.SqliteLedger(":memory:")
    ledger.migrate()
    intake = _FleetSimIntake()
    state = lg.RuntimeState()
    source = SyntheticSource(t0)
    if judge_mode == "scripted":
        judge: jd.Judge = jd.ScriptedJudge(scripted_judge_script(n_beats))
    elif judge_mode == "rule":
        judge = jd.RuleJudge()
    else:
        raise ValueError(f"未知 judge_mode：{judge_mode}")

    result = SoakResult(judge_mode=judge_mode)
    for i in range(n_beats):
        as_of = t0 + timedelta(seconds=i * interval_seconds)
        beat_res = run_beat_safe(
            as_of=as_of, source=source, judge=judge, intake=intake, ledger=ledger, state=state,
            policy=policy, mode="soak", allow_filing=True,
        )
        beat = beat_res.beat.to_dict()
        result.beats.append(beat)
        ph = phase_at((as_of - t0).total_seconds() / 3600.0)
        result.timeline.append({
            "t_hours": round((as_of - t0).total_seconds() / 3600.0, 2),
            "credit_backlog": ph.metrics.get("credit_backlog"),
            "oldest_queued_minutes": ph.metrics.get("oldest_queued_minutes"),
            "error_total": ph.metrics.get("error_total"),
            "warn_total": ph.metrics.get("warn_total"),
            "declaration_rate": ph.metrics.get("declaration_rate"),
            "phase": ph.label,
            "truth": ph.truth,
            "findings": int(beat.get("findings") or 0),
            "advisories": int(beat.get("advisories") or 0),
            "filings": len(beat.get("filings") or []),
            "suppressed": [s.get("reason") for s in (beat.get("suppressed") or [])],
            "error": beat.get("error"),
        })
    result.filings = _simulate_fleet(intake.filed, ledger.read(), t0, hours)
    return result


@dataclass
class _FleetSimIntake:
    """演练用「舰队」：立单即记，领取/执行在收尾时按时间线模拟。"""

    existing: set[str] = field(default_factory=set)
    filed: list[dict[str, Any]] = field(default_factory=list)
    _next: int = 950000

    def file(self, proposal, org_id: int = 1):
        from .filing import FilingOutcome

        if proposal.task_key in self.existing:
            return FilingOutcome(proposal.task_key, None, "duplicate", "task-key-exists")
        self.existing.add(proposal.task_key)
        self._next += 1
        self.filed.append({
            "mission_id": self._next, "task_key": proposal.task_key, "organ": proposal.organ,
            "metric": proposal.metric, "severity": proposal.severity, "signature": proposal.signature,
            "goal": proposal.goal,
        })
        return FilingOutcome(proposal.task_key, self._next, "filed")


def _simulate_fleet(filed: Sequence[Mapping[str, Any]], beats: Sequence[Mapping[str, Any]],
                    t0: datetime, hours: float) -> list[dict[str, Any]]:
    """模拟舰队处置：立单后 0.5h 领取、2h 执行完；每单的处置结论按剧本真值给。"""
    filed_at: dict[str, datetime] = {}
    for b in beats:
        ts = b.get("ts")
        for f in b.get("filings") or []:
            if f.get("status") == "filed" and ts:
                filed_at[str(f["task_key"])] = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    out: list[dict[str, Any]] = []
    for item in filed:
        key = str(item["task_key"])
        created = filed_at.get(key, t0)
        claimed = created + timedelta(minutes=30)
        finished = created + timedelta(hours=2)
        within = finished <= t0 + timedelta(hours=hours)
        truth = phase_at((created - t0).total_seconds() / 3600.0).truth
        # 事后处置按剧本真值给：真故障 → fixed（真阳性）；良性/噪声 → not_a_defect（误报）
        disp_raw = "fixed" if truth == "real_fault" else "not_a_defect"
        out.append({
            **dict(item),
            "created_at": created.isoformat(),
            "claimed_by": "fleet-sim" if within else None,
            "session_id": 1 if within else None,
            "status": "succeeded" if within else "queued",
            "finished_at": finished.isoformat() if within else None,
            "result": {"diagnosis_disposition": disp_raw if within else None,
                       "ground_truth": truth, "simulated": True},
            "disposition": ("confirmed" if disp_raw == "fixed" else "false_positive") if within else "pending",
        })
    return out


def iron_law_check(result: SoakResult) -> dict[str, Any]:
    """演练的机器判据：3 真故障各 1 单、噪声/良性 0 单、无 error 拍。"""
    by_metric = {}
    for f in result.filings:
        by_metric[f["metric"]] = by_metric.get(f["metric"], 0) + 1
    errors = sum(1 for b in result.beats if b.get("error"))
    suppressed = {}
    for b in result.beats:
        for s in b.get("suppressed") or []:
            suppressed[s.get("reason")] = suppressed.get(s.get("reason"), 0) + 1
    return {
        "beats": len(result.beats),
        "error_beats": errors,
        "filings": len(result.filings),
        "filings_by_metric": by_metric,
        "suppressed_by_reason": suppressed,
        "real_faults_covered": [m for m in ("credit_backlog", "error_total", "oldest_queued_minutes")
                                if by_metric.get(m)],
        "false_positive_filings": sum(1 for f in result.filings if f.get("disposition") == "false_positive"),
        "confirmed_filings": sum(1 for f in result.filings if f.get("disposition") == "confirmed"),
    }