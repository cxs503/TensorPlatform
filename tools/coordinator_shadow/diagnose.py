#!/usr/bin/env python3
"""coordinator_shadow v1 —— 判读与立单决策（IN27-H1）。

两层判读，各司其职
------------------
1. **规则层（定量、可复算）**：``rule_findings()`` 只吃脉搏里的数字，按 ``registry`` 的
   阈值判越界。它决定了「哪些器官越界」——因为数字与 SQL 都来自脉搏，天然不会幻觉。
2. **LLM 层（定性、可否决）**：``judge.py`` 的判读结果**不能新增可立单的越界项**，
   只能：a) 解释与排序；b) **否决**（判定为瞬时噪声/已知无害，给理由）；c) 产出
   ``advisory``（不入单的诊断意见）。原因很直白：v0 全凭 LLM 自由发挥，误报不可控；
   v1 把「能不能立单」压回可复算证据，把 LLM 放在它真正擅长的地方——**归因与取舍**。

立单纪律（H3 才动手，本层只立单）
--------------------------------
一个越界项要变成 mission，必须同时满足：
- ``severity`` ∈ ``Policy.file_severities``（默认 high/mid）；
- **持续 ≥ ``Policy.require_persistence`` 拍**（同签名跨拍出现；单拍尖峰=噪声，不立）；
- LLM 未否决（``llm.veto`` 且``policy.llm_veto``）；
- 该签名不在冷却窗口内（``cooldown_hours``），且当前开着的诊断单未超 ``max_open_diagnosis``；
- 每拍立单数 ≤ ``max_filings_per_beat``（默认 1，宁少勿滥）。
签名（``signature``）= ``metric:direction``，**只用结构与阈值，不用 LLM 措辞**——
所以同一病灶跨拍能稳定去重，换模型也不会重复立单。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from . import registry
from .pulse import Metric, Pulse, _cluster_dict, iso, parse_ts, utc_now

VERDICT_SCHEMA = "coordinator-shadow-verdict/1"
FILING_SCHEMA = "coordinator-shadow-filing/1"
REPO = "TensorPlatform"
TASK_KEY_PREFIX = "shadowdiag"


# ─────────────────────────────── 策略 ───────────────────────────────


@dataclass
class Policy:
    """立单纪律参数（全部可注入；报告须写明当次取值）。"""

    stall_minutes: float = 120.0
    window_hours: float = 24.0
    warn_window_minutes: float = 60.0
    max_pulse_age_min: float = 90.0
    require_persistence: int = 2
    max_filings_per_beat: int = 1
    max_open_diagnosis: int = 3
    one_filing_per_organ: bool = True
    cooldown_hours: float = 6.0
    file_severities: tuple[str, ...] = ("high", "mid")
    llm_veto: bool = True
    thresholds_override: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    interval_seconds: int = 1800

    def spec(self, name: str) -> registry.MetricSpec | None:
        base = registry.spec(name)
        if base is None:
            return None
        if name in self.thresholds_override:
            warn, crit = self.thresholds_override[name]
            return registry.MetricSpec(
                base.name, base.organ, base.label, base.unit, base.direction,
                warn=warn, crit=crit, zero_flow=base.zero_flow, source=base.source,
            )
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "stall_minutes": self.stall_minutes,
            "window_hours": self.window_hours,
            "warn_window_minutes": self.warn_window_minutes,
            "max_pulse_age_min": self.max_pulse_age_min,
            "require_persistence": self.require_persistence,
            "max_filings_per_beat": self.max_filings_per_beat,
            "max_open_diagnosis": self.max_open_diagnosis,
            "one_filing_per_organ": self.one_filing_per_organ,
            "cooldown_hours": self.cooldown_hours,
            "file_severities": list(self.file_severities),
            "llm_veto": self.llm_veto,
            "interval_seconds": self.interval_seconds,
            "thresholds_overridden": sorted(self.thresholds_override),
        }


# ─────────────────────────────── 发现 ───────────────────────────────


@dataclass
class Finding:
    organ: str
    metric: str
    value: float | None
    threshold: float | None
    severity: str
    direction: str
    symptom: str
    consecutive: int = 1
    grounded: bool = True
    advisory: bool = False
    source: str = "rule"
    zero_flow: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    llm_note: str | None = None
    vetoed: bool = False
    veto_reason: str | None = None

    @property
    def signature(self) -> str:
        return f"{self.metric}:{self.direction}"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.signature.encode()).hexdigest()[:16]

    @property
    def task_key(self) -> str:
        return f"{TASK_KEY_PREFIX}-{self.fingerprint}"

    def to_dict(self) -> dict[str, Any]:
        out = {
            "organ": self.organ,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "severity": self.severity,
            "direction": self.direction,
            "symptom": self.symptom,
            "consecutive": self.consecutive,
            "grounded": self.grounded,
            "advisory": self.advisory,
            "source": self.source,
            "zero_flow": self.zero_flow,
            "signature": self.signature,
            "fingerprint": self.fingerprint,
            "task_key": self.task_key,
            "evidence": dict(self.evidence),
        }
        if self.llm_note:
            out["llm_note"] = self.llm_note
        if self.vetoed:
            out["vetoed"] = True
            out["veto_reason"] = self.veto_reason
        return out


_SEV_RANK = {"high": 0, "mid": 1, "low": 2}


def _fmt(value: Any, unit: str) -> str:
    if value is None:
        return "无定义"
    if unit == "ratio":
        return f"{float(value) * 100:.1f}%"
    if unit == "minutes":
        return f"{float(value):.0f} 分钟"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _symptom(spec: registry.MetricSpec, value: float, severity: str) -> str:
    bound = spec.crit if severity == "high" else spec.warn
    cmp = "≥" if spec.direction == "higher_worse" else "≤"
    return (f"器官 {spec.organ}（{registry.ORGANS.get(spec.organ, spec.organ)}）"
            f"指标 {spec.name} = {_fmt(value, spec.unit)}，{cmp} 阈值 {_fmt(bound, spec.unit)}")


def _metric_value(pulse: Pulse, name: str) -> tuple[float | None, dict[str, Any]]:
    """从脉搏里取值 + 证据（SQL/参数/来源）。取不到 → (None, 原因)。"""
    spec = registry.spec(name)
    if spec is None:
        return None, {"reason": "unknown-metric"}
    if name == "chronicle_age_minutes":
        age = pulse.chronicle.get("age_minutes")
        return (None if age is None else float(age),
                {"source": "chronicle_snapshots.age_minutes",
                 "chronicle_ts": pulse.chronicle.get("ts")})
    if spec.source == "warn":
        val = pulse.warn.get(name)
        return (None if val is None else float(val),
                {"source": "daemon-warn-stream", "window_minutes": pulse.warn.get("window_minutes"),
                 "paths_read": pulse.warn.get("paths_read"),
                 "clusters": [_cluster_dict(c) for c in (pulse.warn.get("clusters") or [])][:4]})
    metric: Metric | None = pulse.organs.get(name)
    if metric is None:
        return None, {"reason": "metric-absent"}
    if metric.value is None:
        return None, {"reason": "undefined", "withheld": metric.withheld,
                      "undefined_reason": metric.extra.get("undefined_reason")}
    return float(metric.value), {"sql": " ".join(metric.sql.split()), "params": dict(metric.params),
                                 "unit": metric.unit, **{k: v for k, v in metric.extra.items()
                                                         if k in ("finished_24h", "declared_24h", "counts")}}


def rule_findings(pulse: Pulse, policy: Policy) -> list[Finding]:
    """定量越界检测：只认脉搏里的数字（无定义=不判，绝不把「看不见」当「0 告警」）。"""
    findings: list[Finding] = []
    for name, base in registry.METRICS.items():
        spec = policy.spec(name) or base
        value, evidence = _metric_value(pulse, name)
        severity = spec.severity_for(value)
        if severity is None:
            continue
        assert value is not None
        findings.append(Finding(
            organ=spec.organ, metric=name, value=value,
            threshold=spec.crit if severity == "high" else spec.warn,
            severity=severity, direction=spec.direction,
            symptom=_symptom(spec, value, severity), grounded=True, source="rule",
            zero_flow=spec.zero_flow, evidence=evidence,
        ))
    findings.sort(key=lambda f: (_SEV_RANK.get(f.severity, 3), f.metric))
    return findings


# ─────────────────────────────── 诊断对象 ───────────────────────────────


@dataclass
class Diagnosis:
    as_of: str
    beat_id: str
    urgent: bool = False
    findings: list[Finding] = field(default_factory=list)
    would_do: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    withheld: list[str] = field(default_factory=list)
    pulse_digest: dict[str, Any] = field(default_factory=dict)
    skipped: str | None = None

    def filable(self, policy: Policy) -> list[Finding]:
        out = []
        for f in self.findings:
            if f.advisory or f.vetoed or not f.grounded:
                continue
            if f.severity not in policy.file_severities:
                continue
            if f.consecutive < policy.require_persistence:
                continue
            out.append(f)
        return out

    def verdict(self) -> dict[str, Any]:
        return {
            "schema": VERDICT_SCHEMA,
            "beat_id": self.beat_id,
            "as_of": self.as_of,
            "urgent": self.urgent,
            "findings": [f.to_dict() for f in self.findings],
            "would_do": list(self.would_do),
            "rationale": self.rationale,
            "provenance": dict(self.provenance),
            "withheld": list(self.withheld),
            "skipped": self.skipped,
        }


def apply_persistence(findings: list[Finding], streaks: Mapping[str, int]) -> list[Finding]:
    """把签名跨拍连击数写进 finding（streaks 由 daemon 状态文件维护）。"""
    for f in findings:
        f.consecutive = int(streaks.get(f.signature, 0)) + 1
    return findings


def apply_llm(
    findings: list[Finding],
    llm: Mapping[str, Any] | None,
    policy: Policy,
) -> tuple[list[Finding], list[Finding], str, list[str]]:
    """把 LLM 判读并入规则发现：只给「解释/否决/补充 advisory」，不新增可立单项。

    返回 (findings_with_annotations, advisory_findings, rationale, ungrounded_notes)
    """
    advisories: list[Finding] = []
    ungrounded: list[str] = []
    rationale = ""
    if not llm:
        return findings, advisories, rationale, ungrounded
    rationale = str(llm.get("rationale") or "")[:600]
    by_sig = {f.signature: f for f in findings}
    for item in llm.get("findings") or []:
        if not isinstance(item, Mapping):
            continue
        metric = str(item.get("metric") or "")
        organ = str(item.get("organ") or "")
        spec = registry.spec(metric)
        opinion = str(item.get("opinion") or item.get("stance") or "note").lower()
        note = str(item.get("symptom") or item.get("note") or "")[:400]
        if spec is None or (organ and organ != spec.organ):
            ungrounded.append(f"{organ or '?'}/{metric or '?'}: 器官或指标不在字典内")
            continue
        target = by_sig.get(f"{metric}:{spec.direction}")
        if target is not None:
            target.llm_note = note or target.llm_note
            if opinion in ("veto", "false_alarm", "noise", "transient") and policy.llm_veto:
                target.vetoed = True
                target.veto_reason = note or "LLM 判为瞬时噪声"
            continue
        # 规则层未越界 → 只能作 advisory（不入单）
        advisories.append(Finding(
            organ=spec.organ, metric=metric, value=None, threshold=None,
            severity=str(item.get("severity") or "low") if str(item.get("severity")) in _SEV_RANK else "low",
            direction=spec.direction, symptom=note or f"LLM 提示关注 {metric}",
            grounded=False, advisory=True, source="llm",
            zero_flow=spec.zero_flow, llm_note=note,
        ))
    return findings, advisories, rationale, ungrounded


def build_diagnosis(
    pulse: Pulse,
    policy: Policy,
    *,
    llm: Mapping[str, Any] | None = None,
    llm_provenance: Mapping[str, Any] | None = None,
    streaks: Mapping[str, int] | None = None,
    beat_id: str | None = None,
    skipped: str | None = None,
    would_do: Sequence[Mapping[str, Any]] | None = None,
) -> Diagnosis:
    findings = apply_persistence(rule_findings(pulse, policy), streaks or {})
    findings, advisories, rationale, ungrounded = apply_llm(findings, llm, policy)
    all_findings = findings + advisories
    urgent = any(f.severity == "high" and not f.vetoed and not f.advisory for f in findings)
    prov = dict(llm_provenance or {})
    if ungrounded:
        prov["ungrounded"] = ungrounded
    diag = Diagnosis(
        as_of=pulse.as_of,
        beat_id=beat_id or beat_id_for(parse_ts(pulse.as_of) or utc_now(), policy.interval_seconds),
        urgent=urgent,
        findings=all_findings,
        would_do=list(would_do or []),
        rationale=rationale,
        provenance=prov,
        withheld=list(pulse.withheld),
        pulse_digest=pulse.digest(),
        skipped=skipped,
    )
    return diag


def beat_id_for(as_of: datetime, interval_seconds: int = 1800) -> str:
    """拍 id = 间隔槽：同一 30 分钟槽内重复跑会命中唯一键 → 天然幂等。"""
    epoch = int(as_of.astimezone(timezone.utc).timestamp())
    return f"{epoch // interval_seconds}"


# ─────────────────────────────── 立单 ───────────────────────────────


@dataclass
class FilingProposal:
    task_key: str
    fingerprint: str
    signature: str
    organ: str
    metric: str
    severity: str
    value: float | None
    threshold: float | None
    consecutive: int
    goal: str
    repo: str = REPO
    tags: tuple[str, ...] = ("shadow-diagnosis", "in27-h1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FILING_SCHEMA,
            "task_key": self.task_key,
            "fingerprint": self.fingerprint,
            "signature": self.signature,
            "organ": self.organ,
            "metric": self.metric,
            "severity": self.severity,
            "value": self.value,
            "threshold": self.threshold,
            "consecutive": self.consecutive,
            "repo": self.repo,
            "tags": list(self.tags),
            "goal_chars": len(self.goal),
        }


GOAL_TEMPLATE = """[影子诊断单 {fingerprint}] 器官 {organ}（{organ_label}）越界。

【谁报的】coordinator_shadow v1（只读影子协调官，IN27-H1）在 {as_of} 这一拍（beat {beat_id}）读到该越界，已连续 {consecutive} 拍同签不消，故立此诊断单；影子本身不动手，手术权归执行者。

【证据（可复算，零手抄）】
- 越界描述：{symptom}
- 指标：{metric}（{unit}），当前值 {value}，触发阈值 {threshold}（{severity}）
- SQL 原文：{sql}
- SQL 参数：{params}
- 同一脉搏快照 as_of={as_of}；复算命令：python3 tools/coordinator_shadow/cli.py once --json-out /tmp/beat.json（只读）

【要求】定位根因并修复，使该器官指标回到阈值内；改动最小、可验证，附 pytest 用例；若判定不是缺陷（观测口径错/已自愈），请把结论写进 finish 并在 result 里置 "diagnosis_disposition": "not_a_defect|duplicate|no_repro"，影子会据此计误报。
【边界】不得改动本影子模块的只读边界与立单纪律；不得以调阈值代替修复（阈值变更须走单独评审）。
【去重】task_key={task_key}（同签名冷却 {cooldown_hours:g} 小时内不重复立单）。"""


def build_goal(f: Finding, pulse: Pulse, policy: Policy) -> str:
    spec = policy.spec(f.metric) or registry.spec(f.metric)
    unit = spec.unit if spec else "count"
    evidence = f.evidence or {}
    return GOAL_TEMPLATE.format(
        fingerprint=f.fingerprint,
        organ=f.organ,
        organ_label=registry.ORGANS.get(f.organ, f.organ),
        symptom=f.symptom,
        as_of=pulse.as_of,
        beat_id=beat_id_for(parse_ts(pulse.as_of) or utc_now(), policy.interval_seconds),
        consecutive=f.consecutive,
        metric=f.metric,
        unit=unit,
        value=_fmt(f.value, unit),
        threshold=_fmt(f.threshold, unit),
        severity=f.severity,
        sql=evidence.get("sql", "（该指标由 chronicle/warn 面提供，见脉搏快照）"),
        params=json.dumps(evidence.get("params", {}), ensure_ascii=False),
        task_key=f.task_key,
        cooldown_hours=policy.cooldown_hours,
    )


def decide_filings(
    diag: Diagnosis,
    pulse: Pulse,
    policy: Policy,
    *,
    open_task_keys: Iterable[str] = (),
    last_filed: Mapping[str, str] | None = None,
    last_filed_organ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> tuple[list[FilingProposal], list[dict[str, Any]]]:
    """把可立单发现转成 mission 提案；返回 (提案, 抑制记录)。

    一个器官一拍最多立 1 单（``one_filing_per_organ``）：同一病灶常有多个指标同时越界
    （收养停滞会同时抖 ``oldest_queued_minutes`` 与 ``queued_stuck_2h``），按器官去重
    才能「一个病灶 → 一张单」，否则执行者会收到两张描述同一件事的诊断单。
    """
    now = now or (parse_ts(pulse.as_of) or utc_now())
    open_keys = set(open_task_keys)
    last_filed = dict(last_filed or {})
    organ_last = dict(last_filed_organ or {})
    open_count = len(open_keys)
    proposals: list[FilingProposal] = []
    suppressed: list[dict[str, Any]] = []
    organs_taken: set[str] = set()

    for f in diag.filable(policy):
        reason = None
        if f.task_key in open_keys:
            reason = "already-open"
        elif open_count >= policy.max_open_diagnosis:
            reason = "open-cap"
        elif policy.one_filing_per_organ and f.organ in organs_taken:
            reason = "organ-taken-this-beat"
        else:
            prev = parse_ts(last_filed.get(f.signature))
            if prev is not None and (now - prev) < timedelta(hours=policy.cooldown_hours):
                reason = "cooldown"
            elif policy.one_filing_per_organ:
                prev_organ = parse_ts(organ_last.get(f.organ))
                if prev_organ is not None and (now - prev_organ) < timedelta(hours=policy.cooldown_hours):
                    reason = "organ-cooldown"
        if reason:
            suppressed.append({"signature": f.signature, "task_key": f.task_key,
                               "organ": f.organ, "reason": reason})
            continue
        if len(proposals) >= policy.max_filings_per_beat:
            suppressed.append({"signature": f.signature, "task_key": f.task_key, "reason": "beat-cap"})
            continue
        proposals.append(FilingProposal(
            task_key=f.task_key, fingerprint=f.fingerprint, signature=f.signature,
            organ=f.organ, metric=f.metric, severity=f.severity, value=f.value,
            threshold=f.threshold, consecutive=f.consecutive,
            goal=build_goal(f, pulse, policy),
        ))
        open_keys.add(f.task_key)
        open_count += 1
        organs_taken.add(f.organ)
    return proposals, suppressed


def streaks_after(previous: Mapping[str, int], findings: Sequence[Finding]) -> dict[str, int]:
    """更新签名连击表：本拍出现的签名 +1，未出现的清零（只保留还在的）。"""
    current = {f.signature for f in findings if not f.advisory}
    out = {sig: int(previous.get(sig, 0)) + 1 for sig in current}
    return out