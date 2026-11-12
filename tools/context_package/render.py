"""render —— 把干货包渲染成注入块（索引密度：一行一条 + 决策键 + 详情指针）。

契约：渲染结果 ≤ 包预算；每行必须带 ``decision``（否则 assembler 已丢弃，这里再兜一次）；
块尾必带密度自检行（注入几条 / 丢弃几条 / 原因），使"注入了什么"在提示词里自证。
"""

from __future__ import annotations

from typing import Any, Mapping

from .loader import est_tokens

HEADER = "【干货包 {pid}｜类={cls}｜{used}/{budget} tok｜as_of={as_of}｜决策优先，详情按指针回取】"


def render_block(pkg: Mapping[str, Any], decisions: Mapping[str, Any] | None = None,
                 include_empty: bool = False) -> str:
    if not pkg or not pkg.get("slots"):
        return ""
    decisions = decisions or {}
    b = pkg.get("budget") or {}
    lines = [HEADER.format(pid=pkg.get("package_id", "?"), cls=pkg.get("task_class"),
                           used=b.get("used_tokens", 0), budget=b.get("total_tokens", 0),
                           as_of=pkg.get("as_of", "?"))]
    n_items = 0
    for s in pkg.get("slots") or []:
        items = s.get("items") or []
        if not items and not include_empty:
            continue
        dec_ids = sorted({it.get("decision") for it in items if it.get("decision")})
        dec_txt = "；".join(f"{d}={decisions.get(d, '')}" for d in dec_ids) or "—"
        lines.append(f"■ {s.get('label') or s.get('slot')}〔{s.get('status')}〕决定：{dec_txt}")
        for it in items:
            if not it.get("decision"):
                continue
            n_items += 1
            ptr = f" ↳{it['pointer']}" if it.get("pointer") else ""
            lines.append(f" - [{it['id']}]({it['decision']}) {it.get('text')}{ptr}")
    for slot, err in (pkg.get("degraded_sources") or {}).items():
        lines.append(f"! 源降级 {slot}: {err}")
    a = pkg.get("audit") or {}
    drops = a.get("dropped_by_reason") or {}
    drop_txt = ",".join(f"{k}×{v}" for k, v in drops.items()) or "0"
    lines.append(f"→ 密度自检：注入 {n_items} 条（带决策键 {a.get('decision_bearing', 0)}）"
                 f"/ 候选池 {a.get('candidate_pool', 0)} · 丢弃 {drop_txt} · "
                 f"利用率 {b.get('utilization', 0):.2f}")
    return "\n".join(lines)


def render_tokens(pkg: Mapping[str, Any], decisions: Mapping[str, Any] | None = None) -> int:
    return est_tokens(render_block(pkg, decisions))