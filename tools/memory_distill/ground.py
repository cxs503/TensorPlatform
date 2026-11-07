"""判例可核性核对（grounding）：判例里的每个引用项必须能在源报告里找到。

这是「防幻觉」的机器面：蒸馏只允许**搬运**源报告出现的数字与路径，不允许生成。
  - 数字：去空白后做子串匹配（`3.13 %` 对 `3.13%` 视为命中；`=1e5` 允许剥前导 '='）；
  - 路径：必须原样出现在源报告文本里（路径=证据锚，不能凭印象写）。

输出 grounded_ratio = 命中引用项 / 总引用项；低于阈值（默认 1.0）即判该判例不可核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .citations import extract_citations


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


@dataclass
class GroundResult:
    candidate_id: str
    total: int
    grounded: int
    missing: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.grounded / self.total) if self.total else 0.0

    @property
    def ok(self) -> bool:
        return self.total >= 3 and self.grounded == self.total

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "total": self.total,
            "grounded": self.grounded,
            "ratio": round(self.ratio, 4),
            "missing": list(self.missing),
            "ok": self.ok,
        }


def ground_candidate(candidate_id: str, lesson: str, source_text: str) -> GroundResult:
    cit = extract_citations(lesson)
    src = source_text or ""
    nsrc = _norm(src)

    def num_hit(n: str) -> bool:
        for variant in {n, n.lstrip("="), n.rstrip("‰%").strip()}:
            v = _norm(variant)
            if v and v in nsrc:
                return True
        return False

    grounded = 0
    missing: list[str] = []
    for n in cit.numbers:
        if num_hit(n):
            grounded += 1
        else:
            missing.append(n)
    for p in cit.paths:
        if p in src:
            grounded += 1
        else:
            missing.append(p)
    total = len(cit.numbers) + len(cit.paths)
    return GroundResult(candidate_id, total, grounded, missing)


def ground_pool(
    candidates: Iterable[dict],
    reports_by_mission: dict[str, str],
) -> dict:
    """批量核对：返回 {items, summary}。源报告缺失的判例直接记 not_grounded。"""
    items: list[dict] = []
    for cand in candidates:
        ref = str(cand.get("source_ref") or "")
        mission = ref.split(":", 1)[1] if ref.startswith("mission:") else ""
        src = reports_by_mission.get(mission, "")
        if not src:
            items.append(
                {
                    "candidate_id": cand.get("candidate_id"),
                    "total": 0,
                    "grounded": 0,
                    "ratio": 0.0,
                    "missing": ["<源报告缺失>"],
                    "ok": False,
                }
            )
            continue
        items.append(
            ground_candidate(str(cand.get("candidate_id")), cand.get("lesson", ""), src).as_dict()
        )
    n = len(items)
    ok = sum(1 for i in items if i["ok"])
    return {
        "items": items,
        "summary": {
            "candidates": n,
            "grounded_full": ok,
            "grounded_rate": round(ok / n, 4) if n else 0.0,
            "mean_ratio": round(sum(i["ratio"] for i in items) / n, 4) if n else 0.0,
        },
    }