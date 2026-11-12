"""seat_memory —— 席位个体层压缩：留能力证据/复发性失败模式/任务类型胜任度。

留（keep）
- **能力证据**：教训里带工件路径、数字或判据的条目（可核查 = 有证据）。
- **复发性失败模式**：同 ``failure_kind``/同 ``fail_stage`` 出现 ≥ ``min_recur`` 次 →
  升格为"模式"（一次性事故留 episode 就够）。
- **任务类型胜任度**：按工序/算例/开发/审计四类统计 n / 通过率 / 一次通过率。

去（drop，必附恢复指针）
- 一次性过程细节（"先读了几个文件""试了两版"这类无判据叙述）：episodes 在
  ``tensor_reports`` 可按 mission_id 全量恢复，记忆里只留指针。

输出契约：``{"kept": {...}, "dropped": [...], "tokens": {...}, "audit": {...}}``
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from ..context_package.loader import clip, est_tokens

EVIDENCE = re.compile(r"(tests?/[\w./-]+|docs/[\w./-]+|runs?/[\w./-]+|\.py\b|\b\d+(?:\.\d+)?%)")


def _is_evidence(text: str) -> bool:
    return bool(EVIDENCE.search(text or ""))


def compress_seat(record: Mapping[str, Any], episodes: Iterable[Mapping[str, Any]] = (),
                  min_recur: int = 2, keep_recent: int = 3) -> dict:
    eps = [dict(e) for e in (episodes or [])]
    lessons = [str(x) for x in (record.get("lessons") or [])] if isinstance(record.get("lessons"), list) \
        else [str(record.get("lessons"))] if record.get("lessons") else []

    # 1) 复发性失败模式（按 failure_kind / fail_stage 计数）
    counters: dict[str, list[dict]] = {}
    for e in eps:
        key = str(e.get("failure_kind") or e.get("fail_stage") or "")
        if key and key not in ("", "None", "succeeded"):
            counters.setdefault(key, []).append(e)
    recurrent = [{"pattern": k, "count": len(v),
                  "evidence": [f"tensor_reports#{x.get('report_id') or x.get('mission_id')}"
                               for x in v[:3]],
                  "decision": "avoid"}
                 for k, v in sorted(counters.items(), key=lambda kv: -len(kv[1])) if len(v) >= min_recur]

    # 2) 任务类型胜任度
    comp: dict[str, dict] = {}
    for e in eps:
        cls = str(e.get("task_class") or "unknown")
        c = comp.setdefault(cls, {"n": 0, "passed": 0, "first_pass": 0})
        c["n"] += 1
        ok = str(e.get("status") or "").lower() in ("succeeded", "done", "passed")
        c["passed"] += int(ok)
        c["first_pass"] += int(bool(e.get("first_pass", ok)))
    for c in comp.values():
        c["pass_rate"] = round(c["passed"] / max(1, c["n"]), 3)
        c["first_pass_rate"] = round(c["first_pass"] / max(1, c["n"]), 3)

    # 3) 能力证据 vs 一次性过程细节
    kept_lessons, dropped = [], []
    for ls in lessons:
        (kept_lessons if _is_evidence(ls) else dropped).append(ls)
    kept = {
        "seat_id": record.get("seat_id"),
        "capability_evidence": [clip(x, 160) for x in kept_lessons[:10]],
        "recurrent_failures": recurrent[:6],
        "competence": dict(sorted(comp.items(), key=lambda kv: -kv[1]["n"])),
        "recent": [{"mission_id": e.get("mission_id"), "task_class": e.get("task_class"),
                    "status": e.get("status"), "recover": f"tensor_reports#{e.get('report_id') or e.get('mission_id')}"}
                   for e in eps[:keep_recent]],
    }
    drop_rows = [{"item": clip(x, 120), "reason": "ONE_OFF_PROCESS_DETAIL",
                  "recover": "tensor_reports?mission_id=<见 recent>"} for x in dropped]
    before = est_tokens(" ".join(lessons) + " " + " ".join(
        str(e.get("summary") or "") for e in eps))
    after_text = " ".join(kept["capability_evidence"]) + " " + " ".join(
        f"{r['pattern']}×{r['count']}" for r in recurrent)
    after = est_tokens(after_text)
    return {
        "kept": kept,
        "dropped": drop_rows,
        "tokens": {"before": before, "after": after,
                   "ratio": round(after / max(1, before), 4)},
        "audit": {"episodes_in": len(eps), "lessons_in": len(lessons),
                  "kept_evidence": len(kept["capability_evidence"]),
                  "dropped": len(drop_rows), "min_recur": min_recur,
                  "rule": "keep=evidence+recurrent+competence; drop=one-off detail(recoverable)"},
    }
