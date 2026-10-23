"""上下文装配器（SPEC §12）：分层渲染 + token 预算硬上限，绝不超预算。

渲染顺序（L0→L4）；预算不足时从**低**层开始降级（全文 → 单行 → 仅引用），
整层仍放不下则丢弃并在 AssemblyReport 记账（审计"到底丢了什么"）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import MemoryItem, _require
from .retrieval import RetrieveQuery, retrieve
from .tokens import TokenCounter, default_counter

# kind → 层（SPEC §12 表）
TIER_OF_KIND = {
    "next_action": "L1", "constraint": "L1", "open_question": "L1",
    "decision": "L2", "lesson": "L2",
    "state": "L3", "evidence": "L3", "fact": "L3",
}
TIER_ORDER = ("L0", "L1", "L2", "L3", "L4")


@dataclass
class AssemblyReport:
    tokens_used: int = 0
    token_budget: int = 0
    tokenizer: dict = field(default_factory=dict)
    tiers_rendered: list[str] = field(default_factory=list)
    dropped_tiers: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    dropped_items: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    unresolved: bool = False
    text_fingerprint: str = ""

    def to_dict(self) -> dict:
        return {"tokens_used": self.tokens_used, "token_budget": self.token_budget,
                "tokenizer": self.tokenizer, "tiers_rendered": list(self.tiers_rendered),
                "dropped_tiers": list(self.dropped_tiers), "degraded": list(self.degraded),
                "dropped_items": list(self.dropped_items),
                "conflicts": list(self.conflicts), "unresolved": self.unresolved,
                "text_fingerprint": self.text_fingerprint}


def _value_repr(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ",".join(_value_repr(v) for v in value) + "]"
    if value is None:
        return "null"
    return str(value)


def _uncertain(item: MemoryItem) -> str:
    """unknown 绝不等于 failed：必须带不确定标记（SPEC §4.6）。"""
    status = item.verification["status"]
    if status == "unknown":
        return "?"
    if status == "observed":
        return "~"
    if status == "failed":
        return "!"
    return ""


def render_item_full(item: MemoryItem) -> str:
    return (f"[{item.kind}:{item.topic}]={_value_repr(item.value)}{_uncertain(item)} "
            f"{item.text} (src {item.source['locator']})")


def render_item_line(item: MemoryItem) -> str:
    return f"- {item.topic}={_value_repr(item.value)}{_uncertain(item)} | {item.text}"


def render_item_ref(item: MemoryItem) -> str:
    return f"- {item.item_id} ({item.kind}:{item.topic})"


def render_session_line(session, *, compact: bool) -> str:
    if compact:
        return f"## session {session.session_id} | phase={session.phase} | {session.status_summary}"
    parts = [f"## session {session.session_id}",
             f"phase={session.phase}",
             f"mission={session.mission_ref.get('mission_id')}",
             f"budget: tc={session.budget.get('tool_calls_used')} "
             f"ws={session.budget.get('wall_seconds_used')}",
             session.status_summary]
    if session.blockers:
        parts.append("blockers=" + ";".join(b.get("text", "") for b in session.blockers))
    return " | ".join(p for p in parts if p)


def _conflict_footer(conflicts: list[dict], items_by_id: dict) -> str:
    if not conflicts:
        return ""
    lines = []
    for conflict in conflicts:
        marker = "!CONFLICT-UNRESOLVED" if conflict["unresolved"] else "!CONFLICT"
        rendered = []
        for item_id in conflict["item_ids"]:
            item = items_by_id.get(item_id)
            if item is not None:
                rendered.append(f"{item.topic}={_value_repr(item.value)}")
        lines.append(f"{marker} " + " vs ".join(rendered))
    return "\n".join(lines)


def assemble(bundle, *, token_budget: int, intent: str = "recall", query: str = "",
             now: str | None = None, max_items: int = 24,
             counter: TokenCounter | None = None,
             scope_filter: dict | None = None,
             include_superseded: bool = False) -> tuple[str, AssemblyReport, dict]:
    """按 token 预算装配上下文。返回 (text, report, retrieval_result)。"""
    _require(isinstance(token_budget, int) and token_budget > 0, "token_budget 必须是正整数")
    counter = counter or default_counter()
    q = RetrieveQuery(intent=intent, query=query, max_items=max_items,
                      scope_filter=scope_filter or {}, include_superseded=include_superseded,
                      now=now)
    result = retrieve(bundle, q)
    items = [MemoryItem.from_dict({k: v for k, v in raw.items() if k != "score_detail"})
             for raw in result["items"]]
    items_by_id = {i.item_id: i for i in items}
    report = AssemblyReport(token_budget=token_budget, tokenizer=counter.info(),
                            conflicts=result["conflicts"], unresolved=result["unresolved"])
    report.dropped_items = [d for d in result["dropped"] if d["reason"] not in
                            ("filtered_superseded", "filtered_retired")]

    tiers: dict[str, list[MemoryItem]] = {t: [] for t in TIER_ORDER}
    for item in items:
        tiers[TIER_OF_KIND.get(item.kind, "L3")].append(item)
    # 层内按检索得分降序（items 已按 score 排序，这里保持其相对顺序）
    score_rank = {raw["item_id"]: idx for idx, raw in enumerate(result["items"])}
    for tier_name in tiers:
        tiers[tier_name].sort(key=lambda i: (score_rank.get(i.item_id, 10 ** 6), i.item_id))

    chunks: dict[str, str] = {}
    # L0：会话行（两档）
    l0_full = render_session_line(bundle.session, compact=False)
    l0_compact = render_session_line(bundle.session, compact=True)
    if counter.count(l0_full) <= token_budget:
        chunks["L0"] = l0_full
    elif counter.count(l0_compact) <= token_budget:
        chunks["L0"] = l0_compact
        report.degraded.append("L0:full->compact")
    else:
        report.dropped_tiers.append("L0")

    # 冲突脚注先算出来并**预留**其预算：矛盾必须完整渲染，不能因预算被挤掉（I4）。
    footer = _conflict_footer(result["conflicts"], items_by_id)
    reserved = (counter.count(footer) + 1) if footer else 0

    def _joined() -> str:
        return "\n".join(chunks[t] for t in TIER_ORDER if t in chunks)

    def _fits(candidate_lines: list[str]) -> bool:
        body = "\n".join(candidate_lines)
        prefix = _joined()
        text = (prefix + "\n" + body) if prefix else body
        return counter.count(text) + reserved <= token_budget

    # L1/L2/L3：层内**按得分贪心装入**，逐条尝试 full → line → ref，装不下则丢弃该条。
    for tier in ("L1", "L2", "L3"):
        if not tiers[tier]:
            continue
        rendered: list[str] = []
        for item in tiers[tier]:
            admitted = False
            for label, renderer in (("full", render_item_full), ("line", render_item_line),
                                    ("ref", render_item_ref)):
                line = renderer(item)
                if _fits(rendered + [line]):
                    rendered.append(line)
                    if label != "full":
                        report.degraded.append(f"{tier}:{label}:{item.item_id}")
                    admitted = True
                    break
            if not admitted:
                report.dropped_items.append({"item_id": item.item_id,
                                             "reason": f"tier {tier} budget"})
        if rendered:
            chunks[tier] = "\n".join(rendered)
        else:
            report.dropped_tiers.append(tier)

    # L4：原文摘录（可整层丢弃）
    if "L3" in chunks:
        excerpts = []
        for item in tiers["L3"]:
            for ev_id in item.evidence_refs:
                ev = bundle.evidence_map().get(ev_id)
                if ev is not None and ev.excerpt:
                    excerpts.append(f"~ {ev.locator}: {ev.excerpt}")
                if len(excerpts) >= 6:
                    break
            if len(excerpts) >= 6:
                break
        if excerpts:
            candidate = "\n".join(excerpts)
            current = _joined()
            if counter.count(current + "\n" + candidate) + reserved <= token_budget:
                chunks["L4"] = candidate
            else:
                report.dropped_tiers.append("L4")
        else:
            report.dropped_tiers.append("L4")
    else:
        report.dropped_tiers.append("L4")

    text_parts = [chunks[t] for t in TIER_ORDER if t in chunks]
    if footer:
        text_parts.append(footer)
    text = "\n".join(text_parts)

    # 硬上限：极端情况下（单条超长）继续裁剪，绝不返回超预算文本。
    while counter.count(text) > token_budget and len(text_parts) > 1:
        text_parts = text_parts[:-1]
        report.degraded.append("tail_trim")
        text = "\n".join(text_parts)

    report.tiers_rendered = [t for t in TIER_ORDER if t in chunks]
    report.tokens_used = counter.count(text)

    from .tokens import text_fingerprint
    report.text_fingerprint = text_fingerprint(text)
    return text, report, result