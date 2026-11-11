"""脱敏改写器（redactor）：命中值 → ``<REDACTED:type:len>``，幂等。

为什么格式固定
--------------
出口（提交/报告/工件）需要**可机器校验**的脱敏结果：标记里带规则类型与原始长度，
既能让人复核"这是什么类别的泄漏、泄露了多少字符"，又绝不回显明文；固定格式还能
让"二次扫描不再命中"成为可断言的不变量（幂等）。

幂等性证明要点
--------------
* 标记形如 ``<REDACTED:rule_id:length>``，其中不含任何规则正则能命中的字符组合
  （无长串、无 ``=``/``:`` 后的 16+ 连续字符）；
* 因此 ``redact_text(redact_text(x)) == redact_text(x)``，由测试固化为回归护栏。
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

from .engine import Hit


def redact_token(rule_id: str, length: int) -> str:
    """单值脱敏标记。"""
    return f"<REDACTED:{rule_id}:{length}>"


def redact_text(text: str, hits: Iterable[Hit]) -> str:
    """按行内区间把命中值替换成脱敏标记（同区间多次命中只替换一次）。"""
    by_line: dict[int, List[Hit]] = {}
    for hit in hits:
        by_line.setdefault(hit.line, []).append(hit)

    lines = text.splitlines(keepends=True)
    for line_no, line_hits in by_line.items():
        index = line_no - 1
        if index < 0 or index >= len(lines):
            continue
        raw = lines[index]
        # 从右向左替换，保证前面命中的区间偏移不被扰动
        ordered = sorted(line_hits, key=lambda h: (h.start, h.end), reverse=True)
        occupied_end = None
        for hit in ordered:
            if occupied_end is not None and hit.end > occupied_end:
                # 与已替换区间重叠：跳过（重叠命中以最右者为准）
                continue
            raw = raw[:hit.start] + redact_token(hit.rule_id, hit.length) + raw[hit.end:]
            occupied_end = hit.start
        lines[index] = raw
    return "".join(lines)


def redact_payload(payload, hits: Iterable[Hit], *,
                   pointer: str = "") -> object:
    """递归脱敏结构化载荷（报告 dict/list）。

    与 :func:`redact_text` 同一套标记；``hits`` 中的 ``path`` 决定作用在哪个叶子。
    """
    grouped: dict[str, List[Hit]] = {}
    for hit in hits:
        grouped.setdefault(hit.path, []).append(hit)

    def _walk(node, current: str):
        if isinstance(node, str):
            if current in grouped:
                return redact_text(node, grouped[current])
            return node
        if isinstance(node, dict):
            return {key: _walk(value, f"{current}.{key}" if current else str(key))
                    for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(value, f"{current}[{i}]") for i, value in enumerate(node)]
        return node

    return _walk(payload, pointer)


def contains_plaintext(text: str, tokens: Iterable[str]) -> List[str]:
    """校验用：文本里是否仍残留给定明文（返回残留项，**只在内存/测试里用**）。"""
    return [tok for tok in tokens if tok and tok in text]