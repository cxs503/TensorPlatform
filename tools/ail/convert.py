"""转换器：Record（真实记录）→ AIL MemoryItem / EvidenceRef / SessionState。

设计纪律（ADR-001 §3.1 / SPEC §4）：
- 抽取是**机械的**：不做生成式改写，不调用任何模型；同输入 → 同输出（含 ID 与排序）。
- 只抽能落成 (topic, value, text) 的断言；抽不出来的留在 A0 臂的原始文本里，
  不假装抽取完整（报告里由"A3 未覆盖条目数"披露）。
- 条目必须以 `source` 指向原始 record（含 sha256），保证每条都能回到原文件核对。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .corpus import Record
from .model import (AILSchemaError, EvidenceRef, MemoryItem, _require,
                    canonical, checksum)
from .retrieval import item_haystack
from .state import SessionState

_GENERIC_TOPIC = "ail.record.summary"

# 语料 → bundle 会话元信息（真实 mission/goal 摘要）
CORPUS_SESSIONS = {
    "tensorplatform": {
        "session_id": "s-tensorplatform-operator",
        "mission_ref": {"mission_id": 192, "task_key": "platform-operator:shift:2026-10-23"},
        "status_summary": "平台运维班次 m192：巡检五步全绿，零处置，冷却态已清空",
        "phase": "awaiting_acceptance",
        "plan": [
            ("p1", "采集在飞/心跳与守护计数读数"),
            ("p2", "核对中继与冷却态"),
            ("p3", "统计 24h 失败面与审批面"),
            ("p4", "落 operator_log 报告与判据工件"),
        ],
        "next": [
            ("n1", "下一班次继续按五步巡检；冷却态为空则零处置"),
            ("n2", "把关掉的任务 refill 或标上报（R15/R16 已上报）"),
        ],
        "questions": [("q1", "m173 批复遗留的 head 是否已由收割线代推？")],
        "budget": {"tool_calls_used": 200, "wall_seconds_used": 7200, "token_budget": 0},
    },
    "tensorphysics": {
        "session_id": "s-tensorphysics-s0",
        "mission_ref": {"mission_id": None, "task_key": "wedge-icebreaking-s0-001"},
        "status_summary": "四腿求解器 S0 联合调试泼烟：完整性门全部通过，scientific_status=not_a_physics_benchmark",
        "phase": "done",
        "plan": [
            ("p1", "lbm 腿跑圆柱绕流泼烟"),
            ("p2", "fvm 腿收敛并输出场"),
            ("p3", "fem 腿注册表校验"),
            ("p4", "dem 腿时间演化"),
        ],
        "next": [("n1", "S1：DEM 冰层 + 规定楔形船体（单向耦合）")],
        "questions": [],
        "budget": {"tool_calls_used": 0, "wall_seconds_used": 0, "token_budget": 0},
    },
    "tensordem": {
        "session_id": "s-tensordem-benchmarks",
        "mission_ref": {"mission_id": None, "task_key": "dem3d:benchmark-protocol:review"},
        "status_summary": "DEM3D 基准协议与验收阈值复核：数值烟测通过，但未标定材料、未证明收敛",
        "phase": "executing",
        "plan": [
            ("p1", "核对 cases 验收阈值口径"),
            ("p2", "核对能量审计的适用边界"),
        ],
        "next": [("n1", "做时间步与晶格敏感性研究后才能定量使用")],
        "questions": [("q1", "三点弯参考解来源是否需要独立实验对标？")],
        "budget": {"tool_calls_used": 0, "wall_seconds_used": 0, "token_budget": 0},
    },
}


@dataclass
class ConversionResult:
    items: list[MemoryItem]
    evidence: list[EvidenceRef]
    skipped: list[dict]      # 未能转换的记录（显式披露，不静默丢弃）
    session: SessionState


def _slug(text: str, *, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.casefold()).strip("_")
    return slug[:limit] or "x"


def _text_for(record: Record) -> str:
    """把记录压成一条自足单行（≤400 码点）：标题 + 值的要点。"""
    base = record.title
    if record.value is not None:
        value = record.value if not isinstance(record.value, list) else ",".join(map(str, record.value))
        text = f"{base}：{value}"
    else:
        text = base
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) > 400:
        compact = compact[:397] + "..."
    return compact


def _value_for(record: Record):
    value = record.value
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # 多行值（工具 stdout）折叠成单行再按上限截断：语义不变，格式合规。
        flat = re.sub(r"\s+", " ", value).strip()
        return flat if len(flat) <= 120 else flat[:120]
    if isinstance(value, list):
        flat = []
        for v in value[:16]:
            if isinstance(v, str):
                one = re.sub(r"\s+", " ", v).strip()
                flat.append(one if len(one) <= 120 else one[:120])
            else:
                flat.append(v)
        return flat
    return str(value)[:120]


def _salience(kind: str, record: Record) -> int:
    """确定性显著度：kind 基线 + 语料权重（供预算不足时降级排序）。"""
    base = {"next_action": 90, "constraint": 85, "decision": 80, "state": 75,
            "lesson": 70, "open_question": 70, "evidence": 60, "fact": 50}[kind]
    if record.record_id.startswith("m192-"):
        base += 5
    if record.kind_hint == "evidence":
        base += 3
    return max(0, min(100, base))


def _verification_for(record: Record) -> dict:
    """验证状态**只能由记录本身决定**，转换器不得凭空上调（I3）。

    - 判据报告里 passed=true 的 check → passed，validator = 该报告文件；
    - 其余带明确来源的读数 → observed（工具输出，尚未独立复核）；
    - 无法解析的原始行 → unknown。
    """
    if record.kind_hint == "evidence" and "validation_report" in record.source_path:
        if record.value == "true":
            return {"status": "passed", "validator": record.source_path,
                    "checked_at": record.retrieved_at}
        if record.value == "false":
            return {"status": "failed", "validator": record.source_path,
                    "checked_at": record.retrieved_at}
    if record.value is None:
        return {"status": "unknown", "validator": None, "checked_at": None}
    return {"status": "observed", "validator": None, "checked_at": None}


def _sensitivity_for(record: Record) -> str:
    tags = set(record.tags)
    if "agent_memory" in tags:
        return "private"
    return "organization"


def convert_records(records: list[Record], *, corpus: str) -> ConversionResult:
    """把某语料的记录集转成 AIL 条目集（确定性）。"""
    _require(records, f"语料 {corpus} 为空：拒绝生成空 bundle（否则基准分母为 0）")
    items: list[MemoryItem] = []
    evidence: list[EvidenceRef] = []
    skipped: list[dict] = []

    for record in records:
        kind = record.kind_hint if record.kind_hint in (
            "state", "next_action", "decision", "evidence", "constraint", "fact",
            "lesson", "open_question") else "fact"
        topic = record.topic_hint or f"{_GENERIC_TOPIC}.{_slug(record.record_id)}"
        try:
            ev = EvidenceRef.make(
                kind="json_pointer" if "#" in record.pointer else "file",
                locator=f"{record.source_path}#{record.pointer}"
                        if "#" not in record.pointer else record.source_path,
                sha256=record.sha256,
                excerpt=_text_for(record)[:300],
                retrieved_at=record.retrieved_at)
            verification = _verification_for(record)
            item = MemoryItem.make(
                kind=kind, topic=topic, value=_value_for(record), text=_text_for(record),
                source={"kind": "json_pointer",
                        "locator": f"{record.source_path}#{record.pointer}",
                        "sha256": record.sha256, "retrieved_at": record.retrieved_at},
                retrieved_at=record.retrieved_at, scope={"agent_id": None,
                                                         "session_id": None,
                                                         "mission_id": None},
                sensitivity=_sensitivity_for(record), verification=verification,
                evidence_refs=[ev.evidence_id],
                valid_from=record.valid_from or record.retrieved_at,
                salience=_salience(kind, record),
                idempotency_key=f"{corpus}:{record.record_id}",
                tags=[corpus, *record.tags[:6]])
        except AILSchemaError as exc:
            skipped.append({"record_id": record.record_id, "reason": str(exc)})
            continue
        evidence.append(ev)
        items.append(item)

    items = _close_temporal_intervals(items)
    session = _session_for(corpus, items)
    return ConversionResult(items=items, evidence=evidence, skipped=skipped, session=session)


def _close_temporal_intervals(items: list[MemoryItem]) -> list[MemoryItem]:
    """同 topic 同 scope 的时间序列 → 显式区间（SPEC C2 演进，非 C1 矛盾）。

    真实记录里同一主题会有先后多个值（mission 状态 104 → 105 变化等）。它们不是矛盾，
    而是**演进**。这里把较早条目的 `valid_to` 设为后继的 `valid_from`，让区间不重叠，
    从而在检索的"当前视图"里只出现最新值，历史值仍可追（include_superseded 可见）。
    这是**赋值区间**而非改写历史：不删条目、不改值、不隐式 supersede（C5 仍受约束）。
    """
    grouped: dict[tuple, list[MemoryItem]] = {}
    for item in items:
        grouped.setdefault((item.topic, canonical(item.scope)), []).append(item)
    for members in grouped.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda m: (m.valid_from, m.item_id))
        for older, newer in zip(ordered, ordered[1:]):
            if older.valid_to is None and older.valid_from <= newer.valid_from:
                older.valid_to = newer.valid_from
    return items


def _session_for(corpus: str, items: list[MemoryItem]) -> SessionState:
    spec = CORPUS_SESSIONS[corpus]
    next_actions = [{"action_id": aid, "text": text, "preconditions": [], "owner": "agent"}
                    for aid, text in spec["next"]]
    # 记录里声明的 next_action 类条目也并入会话下一步（不覆盖，保持可追）
    for item in items:
        if item.kind == "next_action":
            next_actions.append({"action_id": "rec:" + item.item_id,
                                 "text": item.text[:200], "preconditions": [], "owner": "agent"})
    plan = [{"step_id": sid, "text": text, "status": "active"}
            for sid, text in spec["plan"]]
    questions = [{"question_id": qid, "text": text} for qid, text in spec["questions"]]
    return SessionState.make(
        session_id=spec["session_id"], phase=spec["phase"],
        status_summary=spec["status_summary"],
        updated_at=max((i.recorded_at for i in items), default="2026-10-23T07:00:00Z"),
        mission_ref=spec["mission_ref"], active_plan=plan[:12], next_actions=next_actions[:8],
        blockers=[], open_questions=questions[:8], budget=spec["budget"],
        revision=1)


def coverage_report(records: list[Record], items: list[MemoryItem]) -> dict:
    """披露抽取覆盖率（防止"抽了一半就宣称信息等价"）。"""
    total = len(records)
    converted = len(items)
    return {
        "records": total,
        "converted": converted,
        "skipped_or_merged": total - converted,
        "coverage": round(converted / total, 4) if total else 0.0,
        "note": "未转换的记录仍保留在 A0/原始文本臂中；A3 若在这些记录上有探针，会体现为答不出。",
    }


def item_digest(items: list[MemoryItem]) -> str:
    return checksum([i.to_dict() for i in items])[:16]


def record_tokens_estimate(record: Record) -> int:  # pragma: no cover - 便利函数
    return len(record.raw_text) // 3


__all__ = ["convert_records", "ConversionResult", "CORPUS_SESSIONS", "coverage_report",
           "item_digest", "item_haystack"]