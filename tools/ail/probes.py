"""判据：探针集 + 机械打分（基准协议 §2）。

探针的 gold 一律来自**真实工件**（与 corpus 抽取同源），并带 `evidence_locator`，
保证"答案"不是我们编的。打分不依赖任何 LLM（协议 §2.1）。

四个维度（四元组，Issue #74 要求）：
  state       工作状态
  decision    历史决策
  evidence    知识依据
  next_action 下一步行动
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .model import AILSchemaError, canonical, _require

QUADS = ("state", "decision", "evidence", "next_action")

# 熵减：把常见的中文/英文数字词与符号统一，避免"16"vs"十六"这类假阴性。
_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


@dataclass
class Probe:
    probe_id: str
    quad: str
    corpus: str
    intent: str
    question: str
    gold: dict
    rationale: str = ""

    def __post_init__(self) -> None:
        _require(self.quad in QUADS, f"probe.quad 非法：{self.quad!r}")
        _require(isinstance(self.gold, dict) and "value" in self.gold,
                 "probe.gold 必须有 value")
        _require(isinstance(self.gold.get("must_contain", []), list),
                 "probe.gold.must_contain 必须是数组")

    def to_dict(self) -> dict:
        return {"probe_id": self.probe_id, "quad": self.quad, "corpus": self.corpus,
                "intent": self.intent, "question": self.question,
                "gold": dict(self.gold), "rationale": self.rationale}


def _normalize(text: str) -> str:
    lowered = text.casefold()
    for cn, digit in _CN_DIGITS.items():
        lowered = lowered.replace(cn, digit)
    # 保留数字与字母（含 CJK），压缩空白与常见分隔符
    lowered = re.sub(r"[\s,;:，；：。、（）()\[\]{}\"']+", " ", lowered)
    return lowered


def _needle_present(norm_ctx: str, needle: str) -> bool:
    """在归一化文本里找 needle；纯数字用边界匹配（避免 16 命中 160 / 16.5）。"""
    stripped = needle.strip()
    if not stripped:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        pattern = r"(?<![\d.])" + re.escape(stripped) + r"(?![\d.])"
        return re.search(pattern, norm_ctx) is not None
    return stripped in norm_ctx


def _value_present(context: str, gold_value) -> bool:
    """值在场判定：字符串包含 或 数字（含边界）出现。"""
    norm_ctx = _normalize(context)
    if isinstance(gold_value, bool):
        return _needle_present(norm_ctx, "true" if gold_value else "false")
    return _needle_present(norm_ctx, _normalize(str(gold_value)))


def score_context(probe: Probe, context: str) -> dict:
    """逐项打分（0..1 每项）。返回 dict 含四项与合成分。"""
    gold = probe.gold
    value = gold["value"]
    must_contain = [_normalize(s).strip() for s in gold.get("must_contain", []) if str(s).strip()]
    must_not = [_normalize(s).strip() for s in gold.get("must_not_contain", []) if str(s).strip()]
    norm = _normalize(context)

    # 1) answerable：正确值在场 或 must_contain 全覆盖
    direct = _value_present(context, value)
    covered = bool(must_contain) and all(_needle_present(norm, m) for m in must_contain)
    answerable = 1.0 if (direct or covered) else 0.0

    # 2) exact：在场且形式严格（数字不带引号伪装、值 canonical 形式出现）
    if isinstance(value, str):
        exact = 1.0 if _needle_present(norm, _normalize(value)) else 0.0
    elif isinstance(value, bool):
        exact = 1.0 if _value_present(context, value) else 0.0
    elif isinstance(value, (int, float)):
        exact = 1.0 if _value_present(context, value) else 0.0
    else:
        exact = 1.0 if canonical(value) and _normalize(str(value)) in norm else 0.0

    # 3) traceable：指定了 locator 就必须在上下文里能看到该来源（前缀匹配允许）
    locator = gold.get("evidence_locator")
    if locator:
        stem = _normalize(locator.split("#")[0])
        traceable = 1.0 if _needle_present(norm, stem) else 0.0
    else:
        traceable = 1.0   # n/a 记 1.0，但入账 n/a（见 scorer 汇总）
    # 4) no_confident_error：不得出现禁止值
    if must_not:
        conflicted = any(_needle_present(norm, m) for m in must_not)
        no_confident = 0.0 if conflicted else 1.0
    else:
        no_confident = 1.0

    applicable = {"answerable": True, "exact": True,
                  "traceable": bool(locator), "no_confident_error": bool(must_not)}
    parts = [answerable, exact, traceable, no_confident]
    return {"answerable": answerable, "exact": exact, "traceable": traceable,
            "no_confident_error": no_confident,
            "score": round(sum(parts) / 4.0, 6),
            "applicable": applicable}


# ── 探针集（gold 全部抄自真实工件；locator 可用 sha256 复核）─────────────
def _probe(probe_id: str, quad: str, corpus: str, intent: str, question: str,
           *, value, must_contain=None, must_not_contain=None,
           evidence_locator=None, rationale="") -> Probe:
    gold = {"value": value, "must_contain": list(must_contain or []),
            "must_not_contain": list(must_not_contain or [])}
    if evidence_locator:
        gold["evidence_locator"] = evidence_locator
    return Probe(probe_id=probe_id, quad=quad, corpus=corpus, intent=intent,
                 question=question, gold=gold, rationale=rationale)


# 真实来源：本仓库 operator_log / agent_memory；TensorPhysics outputs；TensorDEM benchmarks。
PROBES: tuple[Probe, ...] = (
    # ── TensorPlatform：state ───────────────────────────────────────────
    _probe("tp-state-guardian-total", "state", "tensorplatform", "state",
           "m192 班次实测守护进程总数是多少？",
           value=16, must_contain=["守护"], must_not_contain=["14"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json",
           rationale="三量等式闭合的核心读数，必须逐字精确；14 是 m200 时的旧值"),
    _probe("tp-state-inflight", "state", "tensorplatform", "state",
           "m192 班次在飞任务数是几个？",
           value=2, must_not_contain=["3 个"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    _probe("tp-state-relay-code", "state", "tensorplatform", "state",
           "LLM 中继那次探测返回的 HTTP 状态码是多少？",
           value=200, must_not_contain=["401"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    _probe("tp-state-cooldown-expired", "state", "tensorplatform", "state",
           "冷却态（cooldown）在本次巡检里是否已过期？",
           value=True, must_contain=["true"], must_not_contain=["false"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    _probe("tp-state-failed-24h", "state", "tensorplatform", "state",
           "24 小时内 failed 任务总数是多少？",
           value=18, evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    # ── TensorPlatform：evidence ────────────────────────────────────────
    _probe("tp-evidence-guardian-source", "evidence", "tensorplatform", "evidence",
           "守护进程数 16 这个数字出自哪个文件？",
           value=16, must_contain=["2026-10-23.m192.readings.json"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json",
           rationale="依据可追性是本阶段核心判据之一"),
    _probe("tp-evidence-relay-time", "evidence", "tensorplatform", "evidence",
           "中继探测耗时（秒）是多少，判据阈值是多少以内？",
           value=0.213707, evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    _probe("tp-evidence-checks-passed", "evidence", "tensorplatform", "evidence",
           "m192 判据报告里 inflight_count_is_2 这条检查的 passed 值是？",
           value=True, must_contain=["true"],
           evidence_locator="operator_log/2026-10-23.m192.validation_report.json"),
    # ── TensorPlatform：decision ────────────────────────────────────────
    _probe("tp-decision-gateway-unavailable", "decision", "tensorplatform", "decision",
           "24h 内 18 个 failed 里，主要归因是什么？",
           value=17, must_contain=["gateway_unavailable"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json",
           rationale="17 of 18 failed 是 gate_unavailable，属停机归因而非任务质量"),
    _probe("tp-decision-approvals", "decision", "tensorplatform", "decision",
           "审批面当前的 approved / pending / rejected 三个计数分别是？",
           value=123, must_contain=["123"], must_not_contain=["104"],
           evidence_locator="operator_log/2026-10-23.m192.readings.json"),
    # ── TensorPlatform：next_action ─────────────────────────────────────
    _probe("tp-next-handoff", "next_action", "tensorplatform", "next_action",
           "交接笔记的待办里，下一班第一件事要判什么开关的状态？",
           value="PASS_ALL", must_contain=["PASS_ALL"],
           evidence_locator="agent_memory/platform-operator/context.md",
           rationale="交接笔记 [待办] 第 1 条原文；A3 的 L1 层应优先保住它"),
    _probe("tp-next-guardian-equation", "next_action", "tensorplatform", "next_action",
           "守护计数要按什么等式核对、读哪个权威源？",
           value="workers.json", must_contain=["16"],
           evidence_locator="agent_memory/platform-operator/context.md",
           rationale="R17 教训：worktree 内 workers.json 是陈旧副本，必须读生态根权威源"),
    _probe("tp-decision-r16", "decision", "tensorplatform", "decision",
           "R16 上报的结论是什么：pending=0 / 停车表空能不能解读为验收链健康？",
           value="PASS_ALL", must_contain=["R16"],
           evidence_locator="agent_memory/platform-operator/context.md",
           rationale="PASS_ALL 旁路会让审批链静默空转，这是必须传下去的决策教训"),
    # ── TensorPhysics：state / evidence / next_action ───────────────────
    _probe("ph-state-stage", "state", "tensorphysics", "state",
           "wedge-icebreaking 场景当前处在哪个阶段？",
           value="S0", must_not_contain=["S1"],
           evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/research_goal.json"),
    _probe("ph-state-lbm-leg", "state", "tensorphysics", "state",
           "lbm 腿的 validation_status 是什么？",
           value="passed", must_not_contain=["failed"],
           evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/s0_report.json"),
    _probe("ph-evidence-scientific-status", "evidence", "tensorphysics", "evidence",
           "这份 S0 报告声明的 scientific_status 是什么？",
           value="not_a_physics_benchmark",
           evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/s0_report.json",
           rationale="不把烟测当物理验证，是本平台证据纪律的核心"),
    _probe("ph-decision-s0", "decision", "tensorphysics", "decision",
           "S0 联合调试的判决结论是什么？",
           value="s0_smoke_complete_integrity_gates_passed",
           evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/s0_report.json"),
    _probe("ph-next-stage", "next_action", "tensorphysics", "next_action",
           "S0 之后下一阶段做什么？",
           value="S1", must_contain=["DEM"],
           evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/s0_report.json"),
    _probe("ph-state-device", "state", "tensorphysics", "state",
           "该场景的约束里 device 是什么？",
           value="cpu", evidence_locator="TensorPhysics/outputs/wedge-icebreaking-s0/research_goal.json"),
    # ── TensorDEM：evidence / state / next_action ───────────────────────
    _probe("de-evidence-protocol-tpb", "evidence", "tensordem", "evidence",
           "三点弯基准的协议标识（protocol）是什么？",
           value="tensordem-dem3d-three-point-bending-v1",
           evidence_locator="TensorDEM/benchmarks/dem3d/three_point_bending_cases.json"),
    _probe("de-evidence-protocol-ucs", "evidence", "tensordem", "evidence",
           "UCS 基准的协议标识是什么？",
           value="tensordem-dem3d-ucs-v1",
           evidence_locator="TensorDEM/benchmarks/dem3d/ucs_cases.json"),
    _probe("de-evidence-breaking-strain", "evidence", "tensordem", "evidence",
           "三点弯 case 里 breaking_strain 配置值是多少？",
           value=0.025,
           evidence_locator="TensorDEM/benchmarks/dem3d/three_point_bending_cases.json"),
    _probe("de-state-three-point-case", "state", "tensordem", "state",
           "三点弯 case 的 id 是什么？",
           value="three_point_bending_load_transfer_smoke",
           evidence_locator="TensorDEM/benchmarks/dem3d/three_point_bending_cases.json"),
    _probe("de-next-sensitivity", "next_action", "tensordem", "next_action",
           "能量审计文档的 Validation limits 要求定量使用前先做哪几项研究？",
           value="time-step", must_contain=["lattice-sensitivity"],
           evidence_locator="TensorDEM/docs/dem3d_energy_audit.md",
           rationale=("原文 perform time-step and lattice-sensitivity studies ... "
                      "这一句是何时才允许定量使用的闸门")),
    _probe("de-decision-not-validation", "decision", "tensordem", "decision",
           "DEM3D 能量审计的基线是回归用例还是材料标定验证？",
           value="regression", must_contain=["reproducible regression"],
           must_not_contain=["calibrated ice-material validation or a ship-scale prediction "
                             "is asserted"],
           evidence_locator="TensorDEM/docs/dem3d_energy_audit.md",
           rationale="证据等级纪律：回归用例 ≠ 材料标定验证（原文 reproducible regression case）"),
)

PROBES_BY_CORPUS: dict[str, list[Probe]] = {}
for _p in PROBES:
    PROBES_BY_CORPUS.setdefault(_p.corpus, []).append(_p)


def probes_for(corpus: str) -> list[Probe]:
    return list(PROBES_BY_CORPUS.get(corpus, []))


def quad_coverage() -> dict[str, int]:
    counts = {q: 0 for q in QUADS}
    for probe in PROBES:
        counts[probe.quad] += 1
    return counts