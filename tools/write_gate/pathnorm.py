#!/usr/bin/env python3
"""路径规范化 + glob 匹配 —— 白名单门的判据内核。

为什么要单独一层
----------------
白名单门最容易写成「字符串前缀比较」，那会被十种写法绕过（前缀同形、``..`` 回溯、
反斜杠、全角斜杠、百分号编码、双重编码、NUL 截断、重复分隔符、大小写变体、绝对路径）。
本模块把它们统一折成**唯一规范形**再做判定，于是「绕过写法」不构成新判据，
只是同一个判据的另一条输入路径——这是机制能挡对抗输入的前提。

规范化顺序（顺序本身是安全属性，固定写死并有测试锁定）
------------------------------------------------------
1. 类型/空值/NUL 校验（NUL 会截断下游 C 层调用 → 直接判非法）
2. 去格式字符（``Cf`` 类：零宽空格/方向控制/BOM）—— 防"肉眼同形"
3. NFKC 归一 —— 全角 ``／`` ``．`` 折叠成 ASCII
4. 反斜杠 → 正斜杠
5. 百分号解码（最多两轮：单层 + 双层编码）
6. 段分解、``.`` 丢弃、``..`` 回溯；回溯越根 → ``escaped_parent`` 标志
7. 产出 POSIX 规范形（绝对路径保留前导 ``/``，越根保留前导 ``..`` 段）
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Tuple

from .model import WriteGateError

MAX_DECODE_PASSES = 2
_DRIVE_RE = re.compile(r"^[A-Za-z]:(?:/|$)")


@dataclass(frozen=True)
class Canonical:
    """规范化结果。``text`` 为 POSIX 规范形（相对/绝对/越根 ``..`` 前缀均保留语义）。"""

    text: str
    segments: Tuple[str, ...]
    absolute: bool
    escaped_parent: bool
    flags: Tuple[str, ...]

    def to_json(self) -> dict:
        return {"text": self.text, "absolute": self.absolute,
                "escaped_parent": self.escaped_parent, "flags": list(self.flags)}


def _strip_format(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def canonicalize(path: str) -> Canonical:
    """把任意写法的路径折成唯一规范形；非法输入抛 :class:`WriteGateError`。"""
    if not isinstance(path, str):
        raise WriteGateError("invalid_path_type")
    if "\x00" in path:
        raise WriteGateError("path_nul_byte")
    if not path.strip():
        raise WriteGateError("empty_path")
    flags: List[str] = []

    text = _strip_format(path)
    if text != path:
        flags.append("format_chars_stripped")
    folded = unicodedata.normalize("NFKC", text)
    if folded != text:
        flags.append("unicode_folded")
    if "\\" in folded:
        folded = folded.replace("\\", "/")
        flags.append("backslash_normalized")
    if "%" in folded:
        from urllib.parse import unquote

        for _ in range(MAX_DECODE_PASSES):
            decoded = unquote(folded)
            if decoded == folded:
                break
            folded = decoded
            flags.append("percent_decoded")

    if folded.startswith("~"):
        flags.append("home_expansion")
    absolute = folded.startswith("/")
    drive_absolute = bool(_DRIVE_RE.match(folded))
    if absolute or drive_absolute:
        flags.append("absolute_path")

    parts: List[str] = []
    escaped = False
    for segment in folded.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            flags.append("has_traversal")
            if parts:
                parts.pop()
            else:
                escaped = True
                parts.append("..")
            continue
        parts.append(segment)

    if escaped:
        flags.append("escaped_parent")
        # 越根时保留前导 ``..`` 段（顺序固定：先 ``..`` 再实段），以便跨仓规则判
        text_out = "/".join(parts)
    else:
        text_out = ("/" if absolute or drive_absolute else "") + "/".join(parts)
    if not text_out:
        raise WriteGateError("empty_path_after_normalize")
    return Canonical(text=text_out, segments=tuple(parts), absolute=absolute or drive_absolute,
                     escaped_parent=escaped, flags=tuple(sorted(set(flags))))


# ─────────────────────────── glob ───────────────────────────
def compile_glob(pattern: str) -> "re.Pattern[str]":
    """把 glob 编成正则：``**`` 跨段、``*`` 段内、``?`` 单字符、``[a-z]`` 字符类。"""
    if not isinstance(pattern, str) or not pattern:
        raise WriteGateError("empty_glob")
    out: List[str] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:[^/]+/)*")     # ``**/`` 匹配零到多段
                    i += 3
                else:
                    out.append(".*")              # ``**`` 匹配任意（含 ``/``）
                    i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(ch))
                i += 1
                continue
            body = pattern[i + 1:close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = close + 1
            continue
        out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(path: str, pattern: str, *, case_sensitive: bool = True) -> bool:
    """路径是否被 glob 命中（全串匹配，不做隐式前缀放宽）。"""
    if not case_sensitive:
        path = path.lower()
        pattern = pattern.lower()
    return bool(compile_glob(pattern).match(path))


def first_match(path: str, patterns, *, case_sensitive: bool = True) -> str:
    """返回第一个命中的模式（空串=未命中）——留痕用，便于审计"为什么放/拦"。"""
    for pattern in patterns:
        if glob_match(path, pattern, case_sensitive=case_sensitive):
            return pattern
    return ""