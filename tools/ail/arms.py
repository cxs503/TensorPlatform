"""四臂对照实现（基准协议 §3）：A0 原始历史 / A1 自然语言摘要 / A2 结构化 JSON / A3 AIL。

每个臂的接口一致：
    run(...) -> ArmOutput(text, tokens, chars, truncated, meta)

纪律：
- 全部**确定性**：无随机、无模型调用、无时间依赖（除给定的 now）。
- A1/A2 是**弱基线**（确定性抽取，不是真实 LLMLingua/SimpleMem）；不得据此外推
  "优于 LLMLingua"（协议 §8 强制限定）。
- A0 是无预算臂：其自然成本即被测量对象，但为可比较也会在预算下截断，并记 truncated。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .corpus import Record
from .tokens import TokenCounter


@dataclass
class ArmOutput:
    arm: str
    text: str
    tokens: int
    chars: int
    truncated: bool
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"arm": self.arm, "tokens": self.tokens, "chars": self.chars,
                "truncated": self.truncated, "meta": self.meta}


def _count(text: str, counter: TokenCounter) -> tuple[int, int]:
    return counter.count(text), len(text)


# ── A0 原始历史 ───────────────────────────────────────────────────────────
def arm_raw(records: list[Record], *, token_budget: int | None,
            counter: TokenCounter) -> ArmOutput:
    """把记录原样按时间顺序拼接（现状做法的直接形态）。超预算则从尾部截断。"""
    ordered = sorted(records, key=lambda r: (r.retrieved_at, r.record_id))
    lines: list[str] = []
    for r in ordered:
        lines.append(f"### {r.record_id} [{r.corpus}/{r.kind_hint}] {r.source_path}#{r.pointer}")
        lines.append(r.raw_text)
    text = "\n".join(lines)
    truncated = False
    if token_budget is not None and counter.count(text) > token_budget:
        # 成对（标题+正文）裁到预算内：二分定位保留的对数，避免逐行重编码（O(n)→O(log n)）。
        pairs = len(lines) // 2
        low, high = 0, pairs
        while low < high:
            mid = (low + high + 1) // 2
            if counter.count("\n".join(lines[: mid * 2])) <= token_budget:
                low = mid
            else:
                high = mid - 1
        lines = lines[: low * 2]
        text = "\n".join(lines)
        truncated = True
        guard = 0
        while counter.count(text) > token_budget and len(text) > 1 and guard < 200:
            text = text[: max(1, int(len(text) * 0.9))]
            guard += 1
    tokens, chars = _count(text, counter)
    return ArmOutput("raw", text, tokens, chars, truncated,
                     {"records": len(records), "mode": "verbatim_concat"})


# ── A1 自然语言摘要（抽取式）───────────────────────────────────────────────
_SENT_SPLIT = re.compile(r"(?<=[。；;.!?])\s*|\n+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _first_sentence(text: str, limit: int = 160) -> str:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    sentence = parts[0] if parts else text.strip()
    sentence = re.sub(r"\s+", " ", sentence)
    return sentence if len(sentence) <= limit else sentence[: limit - 3] + "..."


def arm_nl_summary(records: list[Record], *, token_budget: int | None,
                   counter: TokenCounter) -> ArmOutput:
    """抽取式自然语言摘要：每条记录一句 + 保留其中的数字。

    这是"人写的摘要"的确定性替身：它确实更省 token，但没有 topic/value 语义，
    也没有意图检索——正是 A3 要超越的下界。
    """
    ordered = sorted(records, key=lambda r: (r.retrieved_at, r.record_id))
    lines = []
    for record in ordered:
        sentence = _first_sentence(record.raw_text)
        numbers = _NUM_RE.findall(record.raw_text)[:4]
        num_part = (" 数字：" + "/".join(numbers)) if numbers else ""
        lines.append(f"- {record.title}：{sentence}{num_part}")
    text = "\n".join(lines)
    truncated = False
    if token_budget is not None and counter.count(text) > token_budget:
        low, high = 0, len(lines)
        while low < high:
            mid = (low + high + 1) // 2
            if counter.count("\n".join(lines[:mid])) <= token_budget:
                low = mid
            else:
                high = mid - 1
        lines = lines[:low]
        text = "\n".join(lines)
        truncated = True
    tokens, chars = _count(text, counter)
    return ArmOutput("nl_summary", text, tokens, chars, truncated,
                     {"records": len(records), "mode": "extractive_first_sentence"})


# ── A2 结构化 JSON（无语义检索）──────────────────────────────────────────
def arm_json_struct(records: list[Record], *, token_budget: int | None,
                    counter: TokenCounter) -> ArmOutput:
    """同样信息量组织成 JSON 数组：结构清晰但**无 topic/value/intent**，
    预算不足按时间顺序从尾部丢弃。"""
    ordered = sorted(records, key=lambda r: (r.retrieved_at, r.record_id))
    payload = [{"id": r.record_id, "kind": r.kind_hint, "title": r.title,
                "text": _first_sentence(r.raw_text, 120),
                "ref": f"{r.source_path}#{r.pointer}"} for r in ordered]
    truncated = False
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if token_budget is not None and counter.count(text) > token_budget:
        low, high = 0, len(payload)
        while low < high:
            mid = (low + high + 1) // 2
            if counter.count(json.dumps(payload[:mid], ensure_ascii=False,
                                        separators=(",", ":"))) <= token_budget:
                low = mid
            else:
                high = mid - 1
        payload = payload[:low]
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        truncated = True
    tokens, chars = _count(text, counter)
    return ArmOutput("json_struct", text, tokens, chars, truncated,
                     {"records": len(payload), "mode": "flat_json_no_semantics"})


# ── A3 AIL（检索 + 分层装配）──────────────────────────────────────────────
def arm_ail(bundle, *, token_budget: int | None, counter: TokenCounter,
            intent: str, query: str, now: str | None = None) -> ArmOutput:
    from .assembly import assemble

    budget = token_budget if token_budget is not None else 100000
    text, report, result = assemble(bundle, token_budget=budget, intent=intent, query=query,
                                    now=now, counter=counter)
    tokens, chars = _count(text, counter)
    full = report.to_dict()
    # 记账工件需可审计但不必内联整份"被丢弃清单"（几百条 × 上百单元格会把工件撑到 MB 级）。
    # 保留：计数 + 前 10 条样本 + 全部降级/丢弃层名 + 文本指纹（可用指纹复算文本）。
    dropped_items = full.pop("dropped_items", [])
    meta = {"intent": intent, "query": query,
            "tiers_rendered": report.tiers_rendered,
            "dropped_tiers": report.dropped_tiers,
            "degraded": report.degraded[:20],
            "conflicts": report.conflicts,
            "unresolved": report.unresolved,
            "returned_items": [i["item_id"] for i in result["items"]],
            "dropped_items_count": len(dropped_items),
            "dropped_items_sample": dropped_items[:10],
            "report": full}
    return ArmOutput("ail", text, tokens, chars, bool(report.dropped_tiers), meta)


ARM_REGISTRY = {
    "raw": arm_raw,
    "nl_summary": arm_nl_summary,
    "json_struct": arm_json_struct,
    # 'ail' 需要 bundle，签名不同，由 run_arm 特判
    # 'compact'（真实 LLMLingua）为留空插槽：本阶段 NOT RUN（协议 §3.1）
}

ARM_NAMES = ("raw", "nl_summary", "json_struct", "ail")


def run_arm(arm: str, *, records: list[Record], bundle, token_budget: int | None,
            counter: TokenCounter, intent: str = "recall", query: str = "",
            now: str | None = None) -> ArmOutput:
    if arm == "ail":
        return arm_ail(bundle, token_budget=token_budget, counter=counter,
                       intent=intent, query=query, now=now)
    try:
        fn = ARM_REGISTRY[arm]
    except KeyError:  # pragma: no cover - 参数校验
        raise KeyError(f"未知臂：{arm!r}（可选 {sorted(ARM_REGISTRY)} + 'ail'）")
    return fn(records, token_budget=token_budget, counter=counter)