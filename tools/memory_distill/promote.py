"""bootstrap PLAYBOOK 同步：只追加、单条 <=120 字、幂等、前缀字节不变。

不变量（``promote`` 每次自检）：
  P1 新文本 = 旧文本前缀 + 新行 + 旧文本后缀，前缀/后缀 sha256 均不变（append-only）
  P2 单条 <= max_len（默认 120，bootstrap 纪律）
  P3 幂等：已在 PLAYBOOK 节的等价条目（相似度 >= dup_threshold）不再追加
  P4 呼吸边界：只插入 PLAYBOOK 节内部，不跨节、不改标题
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Sequence

from .citations import extract_citations
from .distill import _shrink
from .gate import GateConfig, gate_candidate

PLAYBOOK_HEADER = "## PLAYBOOK"
_SECTION_END = re.compile(r"^##\s+(?!#)")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_playbook_lines(bootstrap_text: str) -> list[str]:
    """取 PLAYBOOK 节的既有条目（'- ' 开头的行），按文件顺序。"""
    lines = bootstrap_text.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        if ln.startswith(PLAYBOOK_HEADER):
            in_section = True
            continue
        if in_section and _SECTION_END.match(ln):
            break
        if in_section and ln.lstrip().startswith("- "):
            out.append(ln.strip())
    return out


@dataclass
class PromoteResult:
    new_text: str
    appended: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    insert_offset: int = -1
    prefix_sha256: str = ""
    suffix_sha256: str = ""
    audit: dict = field(default_factory=dict)


def _section_insert_offset(bootstrap_text: str) -> int:
    """返回 PLAYBOOK 节最后一个条目之后的行首偏移；节缺失抛 ValueError。"""
    lines = bootstrap_text.splitlines(keepends=True)
    start = None
    offset = 0
    last_bullet_end = None
    for ln in lines:
        if start is None:
            if ln.startswith(PLAYBOOK_HEADER):
                start = offset
        else:
            if _SECTION_END.match(ln):
                break
            if ln.lstrip().startswith("- "):
                last_bullet_end = offset + len(ln)
        offset += len(ln)
    if start is None:
        raise ValueError("bootstrap 文本缺少 '## PLAYBOOK' 节")
    return last_bullet_end if last_bullet_end is not None else start


def _fit_with_tag(lesson: str, budget: int) -> str:
    """把判例压进 budget：优先压缩「；」前的标题段，保留尾部数字与证据路径。

    直接尾截会把 evidence path 砍掉（实测 4/10 条因此掉到引用项<3 被拒），
    而证据路径是判例可核性的根——所以压缩顺序是 标题段 → 次句 → 尾截。
    """
    if len(lesson) <= budget:
        return lesson
    head, sep, tail = lesson.partition("；")
    over = len(lesson) - budget
    if sep and len(head) - over >= 16:
        return _shrink(head, len(head) - over) + sep + tail
    body, sep2, tail2 = tail.partition("；") if sep else (lesson, "", "")
    if sep2 and len(body) - max(0, over - max(0, len(head) - 16)) >= 12:
        cut = max(0, over - max(0, len(head) - 16))
        return f"{_shrink(head, len(head))}{sep}{_shrink(body, len(body) - cut)}{sep2}{tail2}"
    return _shrink(lesson, budget)


def promote(
    candidates: Sequence[dict],
    bootstrap_text: str,
    date_tag: str = "",
    limit: int = 12,
    config: GateConfig | None = None,
) -> PromoteResult:
    """把候选判例 append-only 同步进 PLAYBOOK 节；只接受已过 gate 的候选。"""
    cfg = config or GateConfig()
    existing = read_playbook_lines(bootstrap_text)
    known = list(existing)
    to_add: list[str] = []
    skipped: list[dict] = []

    for cand in candidates:
        if len(to_add) >= limit:
            break
        base = (cand.get("lesson") or "").strip()
        if not base:
            skipped.append({"id": cand.get("candidate_id"), "why": "空正文"})
            continue
        gate_rec = cand.get("gate") or {}
        if gate_rec and not gate_rec.get("passed", True):
            skipped.append({"id": cand.get("candidate_id"), "why": f"未过门控:{gate_rec.get('code')}"})
            continue
        budget = cfg.max_len - 2 - (len(date_tag) + 1 if date_tag else 0)
        if len(base) > budget:  # 给日期标签腾位置：先压标题段，保住尾部证据路径/数字
            base = _fit_with_tag(base, budget)
        line_core = f"{date_tag} {base}".strip() if date_tag else base
        body = f"- {line_core}"
        decision = gate_candidate(body, existing=known, config=cfg)
        if not decision.passed:
            skipped.append({"id": cand.get("candidate_id"), "why": f"{decision.code}:{'|'.join(decision.reasons)[:60]}"})
            continue
        to_add.append(body)
        known.append(body)

    offset = _section_insert_offset(bootstrap_text)
    prefix, suffix = bootstrap_text[:offset], bootstrap_text[offset:]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    new_text = prefix + "".join(line + "\n" for line in to_add) + suffix

    # P1 自检：前缀/后缀必须是旧文本的真实切片（append-only 硬证据）
    if not new_text.startswith(prefix.rstrip("\n")) or not new_text.endswith(suffix):
        raise AssertionError("promote 违反了 append-only：前缀或后缀被改动")
    for ln in to_add:
        if len(ln) - 2 > cfg.max_len:
            raise AssertionError(f"单条超长: {ln}")

    return PromoteResult(
        new_text=new_text,
        appended=to_add,
        skipped=skipped,
        insert_offset=offset,
        prefix_sha256=_sha256(prefix),
        suffix_sha256=_sha256(suffix),
        audit={
            "existing_lines": len(existing),
            "appended": len(to_add),
            "skipped": len(skipped),
            "prefix_unchanged": new_text.startswith(prefix.rstrip("\n")),
            "suffix_unchanged": new_text.endswith(suffix),
            "max_line_len": max((len(x) - 2 for x in to_add), default=0),
            "citations_per_line": [len(extract_citations(x)) for x in to_add],
        },
    )