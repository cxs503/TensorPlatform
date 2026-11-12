"""taxonomy —— 任务类判定（配置驱动，零领域语义）。

任务类 = 派单形态（工厂工序01-05 / 算例单 / 开发单 / 审计单 / 通用）。
判定只用 ``config/context_package.v1.json`` 的 ``task_classes[*].detect`` 触发词，
命中数最多者胜；同分按配置声明顺序（长类名优先由 detect 长度决定，不猜语义）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .loader import load_config


def detect_class(goal: Any, cfg: Mapping[str, Any] | None = None) -> tuple[str, dict]:
    """返回 ``(class_id, evidence)``；无命中回落 ``generic``。"""
    cfg = cfg or load_config()
    text = "" if goal is None else str(goal)
    low = text.lower()
    classes = cfg.get("task_classes") or {}
    hits: list[tuple[int, int, str, list[str]]] = []
    order = 0
    for cid, spec in classes.items():
        if cid == "generic":
            continue
        pats = [str(p) for p in (spec.get("detect") or [])]
        matched = [p for p in pats if p and p.lower() in low]
        if matched:
            # 命中数优先；同分时更长的触发词（更具体）优先；再同分按配置顺序
            hits.append((len(matched), max(len(p) for p in matched), cid, matched))
        order += 1
    if not hits:
        gid = "generic" if "generic" in classes else (next(iter(classes)) if classes else "generic")
        return gid, {"matched_triggers": [], "reason": "NO_TRIGGER", "candidates": []}
    hits.sort(key=lambda h: (-h[0], -h[1], h[2]))
    best = hits[0]
    return best[2], {
        "matched_triggers": best[3],
        "reason": "TRIGGER_HIT",
        "candidates": [{"class": h[2], "n": h[0]} for h in hits[:4]],
    }


def class_spec(class_id: str, cfg: Mapping[str, Any] | None = None) -> dict:
    cfg = cfg or load_config()
    classes = cfg.get("task_classes") or {}
    spec = dict(classes.get(class_id) or classes.get("generic") or {})
    spec.setdefault("label", class_id)
    spec.setdefault("slots", [s for s, v in (cfg.get("slots") or {}).items() if v.get("required")])
    spec.setdefault("budget_tokens", (cfg.get("defaults") or {}).get("total_budget_tokens", 1200))
    return spec