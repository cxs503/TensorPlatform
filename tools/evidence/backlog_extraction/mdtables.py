"""Markdown 表格解析（GFM 子集）—— 只做结构切分，不做语义判断。

为什么自己写
------------
报告里的表格是提取 FAIL/WITHHELD 行的唯一结构化载体，但单元格文本里带反引号代码、
转义竖线 ``\\|``、行内 ``|`` （例如 ``| x | a\\|b |``）。宽正则切分会把代码里的竖线
当分隔符，造成列错位 → 误差值被读成量名。这里用**逐字符扫描 + 反引号状态机**切单元格，
并对每个表格记录起始行号，使下游产出 ``file:line`` 行级定位符（评测的匹配键）。

结构
----
``MTable(header, align, rows, start_line, heading)``；``rows`` 元素为
``(cells, line_no)``。 ``heading`` 记录表格所在的小节标题（``##`` 层级最近的标题），
供根因分类器取上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class MTable:
    header: list[str]
    rows: list[tuple[list[str], int]] = field(default_factory=list)
    start_line: int = 0
    heading: str = ""
    heading_level: int = 0

    def col(self, name: str) -> int | None:
        target = normalize_header(name)
        for i, h in enumerate(self.header):
            if normalize_header(h) == target:
                return i
        return None

    def cell(self, row: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    def as_dict(self, limit: int = 4) -> dict:
        return {
            "heading": self.heading,
            "start_line": self.start_line,
            "header": self.header[:8],
            "n_rows": len(self.rows),
            "sample": [c[:limit] for c, _ in self.rows[:3]],
        }


def normalize_header(text: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKC", text or "")
    for token in ("**", "__", "`", "*", "~"):
        s = s.replace(token, "")
    s = s.replace("？", "?").replace("？", "?")
    return " ".join(s.split()).casefold()


def split_cells(line: str) -> list[str]:
    """按未转义、且不在反引号内的 ``|`` 切分，返回去掉首尾空栏的单元格列表。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
            i += 1
            continue
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def is_separator_row(line: str) -> bool:
    s = line.strip()
    if "-" not in s:
        return False
    if not (s.startswith("|") or "|" in s):
        return False
    for cell in split_cells(s):
        body = cell.strip()
        if not body:
            continue
        if set(body) - set("-: "):
            return False
    return any(cell.strip().strip(":") for cell in split_cells(s))


def parse_tables(text: str, *, file: str = "") -> list[MTable]:
    lines = text.splitlines()
    tables: list[MTable] = []
    heading = ""
    heading_level = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            heading_level = level
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and is_separator_row(lines[i + 1]):
            table = MTable(
                header=split_cells(line),
                start_line=i + 1,
                heading=heading,
                heading_level=heading_level,
            )
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                cells = split_cells(lines[j])
                if len(cells) == len(table.header) or len(cells) > 1:
                    table.rows.append((cells, j + 1))
                j += 1
            tables.append(table)
            i = j
            continue
        i += 1
    return tables


def parse_tables_file(path: str | Path) -> list[MTable]:
    return parse_tables(Path(path).read_text(encoding="utf-8", errors="replace"), file=str(path))


def iter_rows(tables: Iterable[MTable]) -> Iterable[tuple[MTable, list[str], int]]:
    for table in tables:
        for cells, line in table.rows:
            yield table, cells, line


__all__ = [
    "MTable",
    "normalize_header",
    "split_cells",
    "is_separator_row",
    "parse_tables",
    "parse_tables_file",
    "iter_rows",
]