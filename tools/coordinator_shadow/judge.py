#!/usr/bin/env python3
"""coordinator_shadow v1 —— LLM 判读层（IN27-H1）。

职责边界（与 ``diagnose.py`` 的分工，别搞混）
------------------------------------------
LLM **不决定「有没有越界」**（那是规则层用可复算 SQL 判的），它负责：
- ``rationale``：一句话归因；
- ``findings[].opinion`` ∈ {``confirm``, ``note``, ``veto``}：对规则层已报的越界项
  表示「确实要紧 / 补充说明 / 是瞬时噪声请勿立单」；
- 可对未越界器官提 ``advisory``（只留文本，不入单）。

输出契约（严格 JSON，解析失败也不编）
------------------------------------
    {"findings":[{"organ":"credit_backlog","metric":"credit_backlog",
                  "opinion":"confirm|note|veto","severity":"high|mid|low","symptom":"…"}],
     "rationale":"…","urgent":true}

任何越界项若引用了**字典外的器官/指标**，一律进 ``ungrounded`` 并丢弃；解析失败时
``parse_ok=False`` 且回退规则层判读（``RuleJudge``）——宁可少判，不可乱判。

无 LLM 时（测试/断网/无 key）用 ``RuleJudge``：它把规则层发现按模板写成一句话，
``provenance.judge="rule/1"``。这保证 72h 常驻**不因模型不可用而停摆**，
也让「有没有 LLM」在验收里可以分开记账。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from . import registry
from .pulse import Pulse

JUDGE_SYSTEM = (
    "你是 TensorPlatform 的影子协调官（继承者A）。你只读平台脉搏，判断「若我是当值协调者，"
    "现在最该做什么」，但不许动手。规则层已经用可复算 SQL 判出越界项；你的任务是对它们做"
    "归因与取舍：确实要紧的 confirm，需要补充的 note，认定为瞬时噪声/已知无害的 veto。"
    "你不得新增「越界」项，只能对未越界器官提 advisory 观察。"
    "严格输出 JSON（无 markdown 围栏）："
    '{"findings":[{"organ":"","metric":"","opinion":"confirm|note|veto","severity":"high|mid|low",'
    '"symptom":""}],"rationale":"一句话","urgent":bool}。'
    "器官与指标名必须取自给定字典，禁止生造；数字必须来自脉搏，禁止估算。"
)


@dataclass
class JudgeResult:
    ok: bool
    verdict: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    error: str | None = None
    latency_ms: int = 0
    model: str = ""
    judge: str = "llm"

    def provenance(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "parse_ok": self.ok,
            "error": self.error,
        }


# ─────────────────────────── JSON 抽取 ───────────────────────────


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个**配平**的 JSON 对象（容忍围栏与前后废话）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = cleaned[start:i + 1]
                try:
                    obj = json.loads(snippet)
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def validate_verdict(obj: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """规范化判读：只留字典内器官/指标；返回 (verdict, problems)。"""
    problems: list[str] = []
    if not isinstance(obj, Mapping):
        return {}, ["verdict-not-object"]
    out_findings: list[dict[str, Any]] = []
    for item in obj.get("findings") or []:
        if not isinstance(item, Mapping):
            problems.append("finding-not-object")
            continue
        organ = str(item.get("organ") or "").strip()
        metric = str(item.get("metric") or "").strip()
        spec = registry.spec(metric)
        if spec is None:
            problems.append(f"unknown-metric:{metric or '?'}")
            continue
        if organ and organ not in registry.ORGANS:
            problems.append(f"unknown-organ:{organ}")
            continue
        if organ and organ != spec.organ:
            problems.append(f"organ-metric-mismatch:{organ}/{metric}")
            continue
        opinion = str(item.get("opinion") or item.get("stance") or "note").lower()
        if opinion not in ("confirm", "note", "veto"):
            problems.append(f"bad-opinion:{opinion}")
            opinion = "note"
        severity = str(item.get("severity") or "mid").lower()
        if severity not in ("high", "mid", "low"):
            severity = "mid"
        out_findings.append({
            "organ": spec.organ,
            "metric": metric,
            "opinion": opinion,
            "severity": severity,
            "symptom": str(item.get("symptom") or item.get("note") or "")[:400],
        })
    urgent = obj.get("urgent")
    return {
        "findings": out_findings,
        "rationale": str(obj.get("rationale") or "")[:600],
        "urgent": bool(urgent) if isinstance(urgent, bool) else None,
    }, problems


# ─────────────────────────── 判读者 ───────────────────────────


class Judge(Protocol):  # pragma: no cover
    def judge(self, pulse: Pulse, findings_summary: list[Mapping[str, Any]]) -> JudgeResult:
        ...


def _prompt(pulse: Pulse, findings_summary: list[Mapping[str, Any]]) -> str:
    organs = {k: v for k, v in registry.ORGANS.items()}
    metrics = {name: {"organ": s.organ, "unit": s.unit, "direction": s.direction,
                      "warn": s.warn, "crit": s.crit, "zero_flow": s.zero_flow}
               for name, s in registry.METRICS.items()}
    payload = {
        "as_of": pulse.as_of,
        "organs_dictionary": organs,
        "metrics_dictionary": metrics,
        "pulse_digest": pulse.digest(),
        "rule_findings": list(findings_summary),
    }
    return ("平台脉搏（JSON）：\n" + json.dumps(payload, ensure_ascii=False, default=str)[:12000])


class LLMJudge:
    """OpenAI 兼容 chat/completions 判读者（urllib，无额外依赖）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 120.0, max_tokens: int = 1200, temperature: float = 0.2) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMJudge | None":
        env = dict(os.environ) if env is None else dict(env)
        base = env.get("LLM_BASE_URL", "").rstrip("/")
        key = env.get("LLM_API_KEY", "")
        model = env.get("LLM_MODEL", "")
        if not (base and key and model):
            return None
        return cls(base, key, model)

    def judge(self, pulse: Pulse, findings_summary: list[Mapping[str, Any]]) -> JudgeResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": _prompt(pulse, findings_summary)},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        started = time.time()
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
        )
        req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, ValueError, OSError) as exc:
            return JudgeResult(ok=False, error=f"{type(exc).__name__}:{str(exc)[:160]}",
                               latency_ms=int((time.time() - started) * 1000), model=self.model)
        latency = int((time.time() - started) * 1000)
        obj = extract_json(raw)
        if obj is None:
            return JudgeResult(ok=False, raw=raw[:2000], error="json-parse-failed",
                               latency_ms=latency, model=self.model)
        verdict, problems = validate_verdict(obj)
        verdict["validation_problems"] = problems
        return JudgeResult(ok=not problems or bool(verdict.get("findings")),
                           verdict=verdict, raw=raw[:2000],
                           error=";".join(problems[:5]) or None,
                           latency_ms=latency, model=self.model)


class RuleJudge:
    """无 LLM 时的确定性判读：把规则层发现写成一句话，意见一律 ``confirm``（不否决）。

    为什么不让它 veto：否决权是 LLM 的定性判断；规则层若自己否决自己，等于把
    「阈值越界」偷偷抹掉——那会让 FPR 看起来变好、但漏报变多。宁可不否决。
    """

    model = "rule/1"
    judge = "rule"

    def judge(self, pulse: Pulse, findings_summary: list[Mapping[str, Any]]) -> JudgeResult:
        started = time.time()
        findings = [{
            "organ": f.get("organ"),
            "metric": f.get("metric"),
            "opinion": "confirm",
            "severity": f.get("severity"),
            "symptom": f.get("symptom", ""),
        } for f in findings_summary]
        high = [f for f in findings if f["severity"] == "high"]
        zero_flow = [f for f in findings_summary if f.get("zero_flow")]
        rationale_bits = []
        if high:
            rationale_bits.append(f"{len(high)} 项高severity越界")
        if zero_flow:
            rationale_bits.append("零流器官：" + "、".join(sorted({str(f.get('organ')) for f in zero_flow})))
        if not findings:
            rationale_bits.append("各器官在阈值内")
        return JudgeResult(
            ok=True,
            verdict={"findings": findings, "rationale": "；".join(rationale_bits) or "无越界",
                     "urgent": bool(high), "validation_problems": []},
            raw="", latency_ms=int((time.time() - started) * 1000), model=self.model,
            judge="rule",
        )


class ScriptedJudge:
    """测试/演练用：给定脚本化判读（含坏 JSON 场景），验证解析与回退路径。"""

    def __init__(self, script: list[str | dict[str, Any] | None], model: str = "scripted") -> None:
        self.script = list(script)
        self.model = model

    def judge(self, pulse: Pulse, findings_summary: list[Mapping[str, Any]]) -> JudgeResult:
        item = self.script.pop(0) if self.script else None
        if item is None:
            return JudgeResult(ok=False, error="scripted-unavailable", model=self.model)
        if isinstance(item, dict):
            obj = item
            raw = json.dumps(item, ensure_ascii=False)
        else:
            raw = str(item)
            obj = extract_json(raw)
        if obj is None:
            return JudgeResult(ok=False, raw=raw[:2000], error="json-parse-failed", model=self.model)
        verdict, problems = validate_verdict(obj)
        verdict["validation_problems"] = problems
        return JudgeResult(ok=True, verdict=verdict, raw=raw[:2000],
                           error=";".join(problems[:5]) or None, model=self.model)