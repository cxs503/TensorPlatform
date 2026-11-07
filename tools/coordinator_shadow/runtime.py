#!/usr/bin/env python3
"""coordinator_shadow v1 —— 一拍的总装（IN27-H1）。

一拍 = 收脉搏 → 规则层判越界 → LLM 判读（可否决）→ 立单决策 → 记账 → 心跳。

失败也要留痕
------------
``run_beat_safe()`` 捕获任何异常并写一条 ``error`` 拍：这份「失败拍」不是废数据，
它是验收里「连续 72h 无人工修」的**证据面**——有错拍但下一拍自愈 = 影子自恢复；
错拍之后长期没有拍 = 真的死了（覆盖率的缺口会自己暴露）。
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import diagnose as dg
from . import filing as fl
from . import judge as jd
from . import ledger as lg
from .pulse import Pulse, collect_pulse, iso, parse_ts, utc_now
from .pulse import ReadOnlyRunner


class PulseSource(Protocol):  # pragma: no cover
    def collect(self, as_of: datetime) -> Pulse:
        ...


class LivePulseSource:
    """生产脉搏源：只读 SQL + daemon 日志尾部。"""

    def __init__(self, runner: ReadOnlyRunner, log_paths: Sequence[str] = (), policy: dg.Policy | None = None) -> None:
        self.runner = runner
        self.log_paths = list(log_paths)
        self.policy = policy or dg.Policy()

    def collect(self, as_of: datetime) -> Pulse:
        p = self.policy
        return collect_pulse(
            self.runner, as_of=as_of, log_paths=self.log_paths,
            stall_minutes=p.stall_minutes, window_hours=p.window_hours,
            warn_window_minutes=p.warn_window_minutes, max_pulse_age_min=p.max_pulse_age_min,
        )


@dataclass
class BeatResult:
    beat: lg.Beat
    diagnosis: dg.Diagnosis | None
    filings: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    judge_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"beat": self.beat.to_dict(), "filings": self.filings,
                "suppressed": self.suppressed, "judge_fallback": self.judge_fallback}


def _judge_or_fallback(judge: jd.Judge, pulse: Pulse, summary: list[Mapping[str, Any]]) -> tuple[jd.JudgeResult, bool]:
    """LLM 判读失败 → 规则判读顶上；返回 (结果, 是否回退)。"""
    result = judge.judge(pulse, summary)
    if result.ok:
        return result, False
    fallback = jd.RuleJudge().judge(pulse, summary)
    fallback.error = f"fallback-from:{result.judge}:{result.error}"
    fallback.judge = "rule-fallback"
    return fallback, True


def run_beat(
    *,
    as_of: datetime | None = None,
    source: PulseSource,
    judge: jd.Judge,
    intake: fl.Intake,
    ledger,
    state: lg.RuntimeState,
    policy: dg.Policy,
    mode: str = "live",
    allow_filing: bool = True,
    fetchall: Callable[..., Sequence[Sequence[Any]]] | None = None,
    org_id: int | None = None,
    heartbeat_path: str | None = None,
    state_path: str | None = None,
) -> BeatResult:
    as_of = as_of or utc_now()
    started = datetime.now().astimezone()
    beat_id = dg.beat_id_for(as_of, policy.interval_seconds)

    pulse = source.collect(as_of)
    findings = dg.rule_findings(pulse, policy)
    summary = [
        {"organ": f.organ, "metric": f.metric, "value": f.value, "threshold": f.threshold,
         "severity": f.severity, "symptom": f.symptom, "zero_flow": f.zero_flow}
        for f in findings
    ]
    judge_result, fell_back = _judge_or_fallback(judge, pulse, summary)
    skipped = None
    if not pulse.chronicle.get("facets_present"):
        skipped = "chronicle-absent"
    diag = dg.build_diagnosis(
        pulse, policy, llm=judge_result.verdict, llm_provenance=judge_result.provenance(),
        streaks=state.streaks, beat_id=beat_id, skipped=skipped,
        would_do=judge_result.verdict.get("would_do") or [],
    )

    proposals: list[dg.FilingProposal] = []
    suppressed: list[dict[str, Any]] = []
    if allow_filing:
        open_keys: list[str] = []
        if fetchall is not None:
            try:
                open_keys = fl.open_task_keys(fetchall)
            except Exception:
                open_keys = []
        proposals, suppressed = dg.decide_filings(
            diag, pulse, policy, open_task_keys=open_keys,
            last_filed=state.last_filed, last_filed_organ=state.last_filed_organ,
            now=parse_ts(pulse.as_of) or as_of,
        )
        if proposals and org_id is None and fetchall is not None:
            try:
                org_id = fl.latest_org_id(fetchall)
            except Exception:
                org_id = None
        if org_id is None:
            org_id = 1

    filing_records: list[dict[str, Any]] = []
    for proposal in proposals:
        outcome = intake.file(proposal, org_id)
        rec = proposal.to_dict()
        rec.update(outcome.to_dict())
        filing_records.append(rec)
        if outcome.status == "filed":
            stamp = iso(parse_ts(pulse.as_of) or as_of)
            state.last_filed[proposal.signature] = stamp
            state.last_filed_organ[proposal.organ] = stamp

    state.streaks = dg.streaks_after(state.streaks, diag.findings)
    state.last_beat_ts = pulse.as_of
    state.last_beat_id = beat_id
    state.beats_total += 1
    state.filings_total += len([r for r in filing_records if r.get("status") == "filed"])

    duration_ms = int((datetime.now().astimezone() - started).total_seconds() * 1000)
    beat = lg.beat_from_diagnosis(
        diag, filings=filing_records, suppressed=suppressed,
        model=getattr(judge, "model", None) or judge_result.model,
        duration_ms=duration_ms, error=judge_result.error if fell_back else None, mode=mode,
    )
    ledger.append(beat)
    state.save(state_path)
    lg.write_heartbeat(heartbeat_path, status="ok", beat=beat,
                       extra={"judge": beat.provenance.get("judge"), "fallback": fell_back})
    return BeatResult(beat=beat, diagnosis=diag, filings=filing_records,
                      suppressed=suppressed, judge_fallback=fell_back)


def run_beat_safe(**kwargs) -> BeatResult:
    """永不抛：失败写 error 拍（拍存在性本身就是健康信号）。"""
    as_of = kwargs.get("as_of") or utc_now()
    started = datetime.now().astimezone()
    policy: dg.Policy = kwargs.get("policy") or dg.Policy()
    ledger = kwargs["ledger"]
    state: lg.RuntimeState = kwargs.get("state") or lg.RuntimeState()
    try:
        return run_beat(**kwargs)
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        beat_id = dg.beat_id_for(as_of, policy.interval_seconds)
        beat = lg.Beat(
            beat_id=beat_id, ts=iso(as_of), model=getattr(kwargs.get("judge"), "model", None),
            urgent=False,
            verdict={"schema": dg.VERDICT_SCHEMA, "beat_id": beat_id, "as_of": iso(as_of),
                     "findings": [], "would_do": [], "rationale": "",
                     "provenance": {"error": True}, "withheld": []},
            pulse_digest={"as_of": iso(as_of)},
            provenance={"judge": "none", "parse_ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"},
            duration_ms=int((datetime.now().astimezone() - started).total_seconds() * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:400]}\n{tb.splitlines()[-1][:200]}",
            mode=kwargs.get("mode", "live"),
        )
        try:
            ledger.append(beat)
        except Exception:
            pass
        state.beats_total += 1
        state.errors_total += 1
        state.last_beat_ts = iso(as_of)
        state.last_beat_id = beat_id
        try:
            state.save(kwargs.get("state_path"))
            lg.write_heartbeat(kwargs.get("heartbeat_path"), status="error", beat=beat)
        except Exception:
            pass
        return BeatResult(beat=beat, diagnosis=None)