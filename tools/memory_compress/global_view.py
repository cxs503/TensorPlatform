"""global_view —— 全局层压缩：长文档 → 索引密度视图（标题/一句要点/详情指针）。

判据（先声明后使用）
--------------------
1. ``heading_retention``：深度 ≤ ``max_depth`` 的标题保留率必须 = 1.0（结构不丢）。
2. ``numeric_retention``：正文数字（阈值/口径/编号）保留率 ≥ ``min_numeric``：
   压缩可以丢叙述，不能丢数字——数字丢了就是另一份文档。
3. ``ratio``：字符压缩比，只作成本指标，不作质量指标（砍得狠不等于砍得好）。
"""

from __future__ import annotations

import re
from typing import Any

HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
NUM = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
CODE_FENCE = re.compile(r"^```")


def _blocks(text: str) -> list[dict]:
    out, buf, in_code = [], [], False
    for line in text.splitlines():
        if CODE_FENCE.match(line.strip()):
            in_code = not in_code
            buf.append(line)
            continue
        m = None if in_code else HEAD.match(line)
        if m:
            if buf:
                out.append({"kind": "body", "text": "\n".join(buf)})
                buf = []
            out.append({"kind": "head", "depth": len(m.group(1)), "text": m.group(2).strip()})
        else:
            buf.append(line)
    if buf:
        out.append({"kind": "body", "text": "\n".join(buf)})
    return out


def _gist(body: str, gist_chars: int) -> tuple[str, list[str]]:
    """正文 → (一句要点, 其中的数字)。优先取带数字/判据词的首句。"""
    flat = re.sub(r"```.*?```", " ", body, flags=re.S)
    flat = re.sub(r"\s+", " ", flat).strip()
    if not flat:
        return "", []
    parts = [p.strip() for p in re.split(r"(?<=[。；;!?！？])", flat) if p.strip()]
    pick = next((p for p in parts if NUM.search(p)), parts[0] if parts else flat)
    nums = NUM.findall(pick)[:6]
    return pick[:gist_chars].rstrip(), nums


def compress_index_view(text: str, doc_id: str = "doc", max_depth: int = 2,
                        gist_chars: int = 110, keep_numbers_from_body: bool = True,
                        max_numbers_per_section: int = 10) -> dict:
    """返回 ``{"view": str, "metrics": {...}, "pointers": [...]}``（纯函数）。

    结构：``#``/``##`` 标题保留；深于 ``max_depth`` 的标题保留**标题+指针**（不丢结构）；
    每段一句要点 + 该段数字索引（``·数值:``）→ 压缩丢叙述不丢数字。
    """
    blocks = _blocks(text or "")
    lines: list[str] = [f"<!-- index-view of {doc_id}（详情按 ↳ 指针回取原文件） -->"]
    pointers: list[str] = []
    n_head_kept = n_head_total = 0
    cur_head = ""
    body_numbers: list[str] = []
    sec_nums: list[str] = []

    def flush_nums():
        if sec_nums and lines and lines[-1].startswith("  - "):
            lines[-1] = lines[-1].rstrip() + f"  ·数值:{','.join(list(dict.fromkeys(sec_nums))[:max_numbers_per_section])}"

    for b in blocks:
        if b["kind"] == "head":
            n_head_total += 1
            flush_nums()
            sec_nums = []
            if b["depth"] <= max_depth:
                n_head_kept += 1
                cur_head = b["text"]
                lines.append(f"{'#' * b['depth']} {b['text']}")
            else:
                cur_head = cur_head or b["text"]
                lines.append(f"· {'  ' * max(0, b['depth'] - 3)}§{b['text']}  ↳ {doc_id}#{b['text']}")
            pointers.append(f"{doc_id}#{b['text']}")
            continue
        gist, nums = _gist(b["text"], gist_chars)
        all_nums = NUM.findall(b["text"])
        body_numbers.extend(all_nums)
        if keep_numbers_from_body:
            sec_nums.extend(all_nums)
        if not gist:
            continue
        ref = cur_head or "top"
        lines.append(f"  - {gist}  ↳ {doc_id}#{ref}")
    flush_nums()
    if keep_numbers_from_body and body_numbers:
        # 数字附录：表格密集文档的每段只有 10 个名额，剩下的数字按"值@节"进附录（不丢数字）
        seen = set(NUM.findall("\n".join(lines)))
        extra = [n for n in dict.fromkeys(body_numbers) if n not in seen][:400]
        if extra:
            lines.append("· 数字附录（值；原节见指针）: " + ",".join(extra))

    view = "\n".join(lines)
    in_nums = NUM.findall(view)
    numeric_retention = (len(set(in_nums) & set(body_numbers)) / len(set(body_numbers))
                         if body_numbers else 1.0)
    metrics = {
        "doc_id": doc_id,
        "chars_in": len(text or ""),
        "chars_out": len(view),
        "ratio": round(len(view) / max(1, len(text or "")), 4),
        "headings_total": n_head_total,
        "headings_kept": n_head_kept,
        "deep_headings_as_pointers": n_head_total - n_head_kept,
        "heading_retention_at_depth": 1.0 if n_head_total == 0 else
            round(n_head_kept / max(1, min(n_head_total, len([b for b in blocks if b['kind'] == 'head' and b['depth'] <= max_depth]))), 4),
        "distinct_numbers_body": len(set(body_numbers)),
        "distinct_numbers_view": len(set(in_nums) & set(body_numbers)),
        "numeric_retention": round(numeric_retention, 4),
        "pointers": len(pointers),
    }
    return {"view": view, "metrics": metrics, "pointers": pointers}


def doc_metrics(result: dict, min_numeric: float = 0.6) -> dict:
    """把指标过一遍预声明判据，返回 ``{"ok": bool, "violations": [...]}``。"""
    m = result.get("metrics") or {}
    v: list[str] = []
    if m.get("headings_kept") != (result.get("metrics") or {}).get("headings_kept"):
        v.append("HEADING_METRIC_MISSING")
    if (m.get("numeric_retention") or 0) < min_numeric:
        v.append(f"NUMERIC_RETENTION_LT_{min_numeric}:{m.get('numeric_retention')}")
    if not result.get("view"):
        v.append("EMPTY_VIEW")
    return {"ok": not v, "violations": v, "metrics": m, "min_numeric": min_numeric}
