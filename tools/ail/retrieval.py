"""意图感知检索器（SPEC §11）：确定性打分，无随机、无外部模型。

score = lex*0.45 + kind_weight*0.30 + recency*0.15 + verification*0.10
        - stale_penalty - superseded_penalty
平局用 item_id 升序稳定打破；矛盾条目一起返回（I4）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .model import INTENTS, MemoryItem, canonical, _require

# 意图 → kind 期望分布（未列出的 kind 取 0.05）
KIND_WEIGHTS: dict[str, dict[str, float]] = {
    "state": {"state": 1.0, "evidence": 0.7, "fact": 0.6, "next_action": 0.4,
              "constraint": 0.3, "decision": 0.2, "lesson": 0.2, "open_question": 0.2},
    "decision": {"decision": 1.0, "lesson": 0.8, "constraint": 0.5, "open_question": 0.4,
                 "state": 0.3, "evidence": 0.3, "fact": 0.2, "next_action": 0.3},
    "evidence": {"evidence": 1.0, "state": 0.6, "fact": 0.5, "decision": 0.3,
                 "lesson": 0.3, "constraint": 0.2, "next_action": 0.1, "open_question": 0.1},
    "next_action": {"next_action": 1.0, "constraint": 0.8, "open_question": 0.7,
                    "state": 0.6, "decision": 0.4, "lesson": 0.4, "evidence": 0.2,
                    "fact": 0.1},
    "recall": {k: 0.5 for k in ("state", "next_action", "decision", "evidence",
                                "constraint", "fact", "lesson", "open_question")},
}
VERIFICATION_WEIGHTS = {"passed": 1.0, "observed": 0.6, "unknown": 0.4, "failed": 0.1}

# 停用词：只用于提升词法覆盖的信噪比，不改变打分上限。
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "对", "为", "把", "被", "从", "到", "有", "这",
    "那", "个", "条", "项", "等", "都", "也", "就", "而", "或", "并", "之", "中", "上",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was",
    "what", "which", "how", "do", "does", "did",
}

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _tokens(text: str) -> set[str]:
    """ASCII 词 + CJK 二元组（与 Soul 的 CJK 二元组口径一致，便于两边可比）。"""
    out: set[str] = set()
    lowered = text.casefold()
    for tok in _ASCII_TOKEN_RE.findall(lowered):
        if tok not in _STOPWORDS and len(tok) > 1:
            out.add(tok)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in _STOPWORDS:
                out.add(bigram)
    return out


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def item_haystack(item: MemoryItem) -> str:
    """参与词法匹配的文本面：topic 分段 + text + value 字符串形式 + tags。"""
    parts = [item.topic.replace(".", " "), item.text, item.idempotency_key, *item.tags]
    if isinstance(item.value, str):
        parts.append(item.value)
    elif isinstance(item.value, list):
        parts.extend(str(v) for v in item.value)
    else:
        parts.append(str(item.value))
    return " ".join(parts)


@dataclass
class RetrieveQuery:
    intent: str = "recall"
    query: str = ""
    max_items: int = 12
    scope_filter: dict = field(default_factory=dict)
    include_superseded: bool = False
    include_failed: bool = False
    now: str | None = None

    def __post_init__(self) -> None:
        _require(self.intent in INTENTS, f"intent 非法：{self.intent!r}（须为 {INTENTS}）")
        _require(isinstance(self.query, str) and len(self.query) <= 200, "query 必须 <= 200 字符")
        _require(isinstance(self.max_items, int) and 1 <= self.max_items <= 200,
                 "max_items 必须是 1..200 整数")
        _require(isinstance(self.scope_filter, dict), "scope_filter 必须是对象")
        bad = set(self.scope_filter) - {"agent_id", "session_id", "mission_id"}
        _require(not bad, f"scope_filter 含不支持键：{sorted(bad)}")


def _scope_ok(item: MemoryItem, scope_filter: dict) -> bool:
    """硬过滤：条目 scope 若已限定则必须与过滤条件一致（不允许跨 scope 泄漏）。"""
    for key, wanted in scope_filter.items():
        if wanted is None:
            continue
        actual = item.scope.get(key)
        if actual is not None and canonical(actual) != canonical(wanted):
            return False
    return True


def _recency(item: MemoryItem, now: datetime) -> float:
    """半衰期 7 天的确定性衰减：0 天=1.0，7 天=0.5，14 天=0.25，未来=1.0。"""
    try:
        age_days = max(0.0, (now - _parse_utc(item.valid_from)).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 0.5
    return 0.5 ** (age_days / 7.0)


def score_item(item: MemoryItem, q: RetrieveQuery, *, now: datetime,
               query_tokens: set[str] | None = None) -> dict:
    tokens = query_tokens if query_tokens is not None else _tokens(q.query)
    if tokens:
        hay = _tokens(item_haystack(item))
        overlap = len(tokens & hay) / len(tokens)
    else:
        overlap = 0.5  # 无 query：词法项不加不减，让结构权重主导
    kind_w = KIND_WEIGHTS[q.intent].get(item.kind, 0.05)
    rec = _recency(item, now)
    ver = VERIFICATION_WEIGHTS.get(item.verification["status"], 0.2)
    if item.stale:
        ver *= 0.5
    score = overlap * 0.55 + kind_w * 0.25 + rec * 0.10 + ver * 0.10
    # 有明确 query 时，"零词法重叠"意味着与本次意图无关：结构分不能把它抬进预算
    # （否则大量同构的历史条目会把唯一命中挤掉）。这是检索的必要条件，不改变排序语义。
    if q.query.strip() and tokens and overlap == 0.0:
        score *= 0.5
    if item.stale:
        score -= 0.15
    if item.status == "superseded":
        score -= 0.30
    elif item.status == "retired":
        score -= 0.50
    return {"score": round(score, 6), "lex": round(overlap, 6), "kind_weight": kind_w,
            "recency": round(rec, 6), "verification_weight": round(ver, 6)}


def retrieve(bundle, q: RetrieveQuery) -> dict:
    """返回 {intent, items:[item dict+score], conflicts, unresolved, dropped, stats}。"""
    now = _parse_utc(q.now) if q.now else datetime.now(timezone.utc)
    query_tokens = _tokens(q.query)
    dropped: list[dict] = []
    candidates: list[tuple[MemoryItem, dict]] = []
    for item in bundle.items:
        if item.status == "superseded" and not q.include_superseded:
            dropped.append({"item_id": item.item_id, "reason": "filtered_superseded"})
            continue
        if item.status == "retired" and not q.include_superseded:
            dropped.append({"item_id": item.item_id, "reason": "filtered_retired"})
            continue
        if item.verification["status"] == "failed" and not q.include_failed:
            dropped.append({"item_id": item.item_id, "reason": "filtered_failed"})
            continue
        if not _scope_ok(item, q.scope_filter):
            dropped.append({"item_id": item.item_id, "reason": "filtered_scope"})
            continue
        candidates.append((item, score_item(item, q, now=now, query_tokens=query_tokens)))

    candidates.sort(key=lambda pair: (-pair[1]["score"], pair[0].item_id))
    selected = candidates[:q.max_items]
    selected_ids = {item.item_id for item, _ in selected}

    # I4：矛盾组要么全在、要么全不在（预算挤掉了其中一条 → 整组丢弃并记账）。
    conflicts = bundle.detect_conflicts()
    for conflict in conflicts:
        ids = set(conflict["item_ids"])
        if ids & selected_ids and not ids <= selected_ids:
            for item_id in ids & selected_ids:
                selected = [pair for pair in selected if pair[0].item_id != item_id]
                selected_ids.discard(item_id)
                dropped.append({"item_id": item_id, "reason": "conflict_partial_budget"})
    for item, _ in candidates:
        if item.item_id not in selected_ids and not any(
                d["item_id"] == item.item_id for d in dropped):
            dropped.append({"item_id": item.item_id, "reason": "budget"})

    returned_conflicts = [c for c in conflicts if set(c["item_ids"]) <= selected_ids]
    return {
        "intent": q.intent,
        "items": [{**item.to_dict(), "score_detail": detail} for item, detail in selected],
        "conflicts": returned_conflicts,
        "unresolved": any(c["unresolved"] for c in returned_conflicts),
        "dropped": sorted(dropped, key=lambda d: (d["reason"], d["item_id"])),
        "stats": {"scanned": len(bundle.items), "returned": len(selected),
                  "candidates": len(candidates)},
    }