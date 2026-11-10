"""缺口账本解析（把注入的 markdown 账本变成机验条目，零域词硬编码）。

为什么解析注入文档而不是抄一份
------------------------------
覆盖率的“真值”只能来自账本本身。这里**不改**账本内容，只按结构抽取条目：

* 条目来源三类（全部结构性规则，与域内容无关）：
  1. ``gap_id``：文本中出现配置正则 ``coverage.gap_id_pattern`` 的标识符（如 ``GAP-XXX-N0``）；
  2. ``table_row``：markdown 表格行（``|`` 分隔，跳过表头/分隔行）；
  3. ``list_item``：以 ``1.`` / ``-`` / ``*`` 开头的列表行。
* 条目归属的**声明状态**来自所在章节标题的映射（配置
  ``coverage.ledger_parse.section_status``：标题含某个标记 → 该章节条目的 declared_status），
  未命中映射即 ``unmapped``（不参与一致性判定，只入账）。
* 每个条目记 ``line_no`` 与所在章节标题，审计链可回原文逐行核对。

条目文本 = 结构单元原文（表格行取 ``table_text_column`` 列，列表行取整行），
因此“逐项比对”可以逐条打印/回放。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.、)])\s+(.*)$")


@dataclass(frozen=True)
class LedgerItem:
    item_id: str
    kind: str
    section: str
    line_no: int
    text: str
    declared_status: str
    gap_ids: tuple[str, ...] = ()
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "section": self.section,
            "line_no": self.line_no,
            "text": self.text,
            "declared_status": self.declared_status,
            "gap_ids": list(self.gap_ids),
        }


def _section_status(section: str, parse_cfg: Mapping[str, Any]) -> str:
    for rule in parse_cfg.get("section_status", []) or []:
        marker = str(rule.get("heading_contains", ""))
        if marker and marker in section:
            return str(rule.get("declared_status", "unmapped"))
    return "unmapped"


def _split_row(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [c.strip() for c in body.split("|")]


def parse_gap_ledger(
    markdown: str, cfg: Mapping[str, Any], *, source: str = ""
) -> tuple[list[LedgerItem], dict[str, Any]]:
    """解析账本 → (条目列表, 元信息)。同一条目去重键 = (kind, section, text, line_no)。"""
    cov = dict(cfg.get("coverage", {}) or {})
    parse_cfg = dict(cov.get("ledger_parse", {}) or {})
    gap_re = re.compile(str(cov.get("gap_id_pattern", r"GAP-[A-Z0-9-]+")))
    text_col = int(parse_cfg.get("table_text_column", -1))
    max_len = int(parse_cfg.get("text_max_chars", 400))

    items: list[LedgerItem] = []
    section = ""
    seen: set[tuple[str, str, str]] = set()
    counter = 0
    lines = markdown.splitlines()
    for ln, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(2).strip()
            continue
        if not line.strip() or _TABLE_SEP.match(line):
            continue
        kind = ""
        text = ""
        if _TABLE_ROW.match(line):
            nxt = lines[ln] if ln < len(lines) else ""
            if _TABLE_SEP.match(nxt) and _TABLE_SEP.search(nxt):
                continue  # 表头行（下一行是分隔行）不计为条目
            cells = _split_row(line)
            if not cells:
                continue
            kind = "table_row"
            text = cells[text_col] if -len(cells) <= text_col < len(cells) else " ".join(cells)
        else:
            m = _LIST_ITEM.match(line)
            if not m:
                continue
            kind = "list_item"
            text = m.group(1).strip()
        text = " ".join(text.split())[:max_len]
        if not text:
            continue
        gap_ids = tuple(sorted(set(gap_re.findall(text)) or set(gap_re.findall(line))))
        key = (kind, section, text)
        if key in seen:
            continue
        seen.add(key)
        counter += 1
        items.append(
            LedgerItem(
                item_id=f"L{counter:04d}",
                kind=kind,
                section=section,
                line_no=ln,
                text=text,
                declared_status=_section_status(section, parse_cfg),
                gap_ids=gap_ids,
                raw=line.strip()[:max_len],
            )
        )

    meta = {
        "source": source,
        "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "line_count": len(markdown.splitlines()),
        "item_count": len(items),
        "gap_id_count": len({g for it in items for g in it.gap_ids}),
        "declared_status_counts": _counts(i.declared_status for i in items),
        "kind_counts": _counts(i.kind for i in items),
    }
    return items, meta


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))