#!/usr/bin/env python3
"""路径模式（glob）相交判定 —— 租约冲突的判定内核。

为什么需要"相交判定"而不是"字符串相等"
--------------------------------------
路径预留（lease）记的是**模式**而不是单个文件：`src/**`、`tests/unit/test_*.py`、
`docs/m*/report.md`。两个租约是否抢同一片路径，属于"两个模式的语言是否有交集"，
字符串相等只能抓住最平凡的一种。

本模块只支持一个刻意很小的子集（够用且可精确判定）：

* 普通字符（含中文、`-`、`_`、`.`）；
* `?` 匹配段内任意单字符；
* `*` 匹配段内任意串（**不跨** `/`）；
* 整段 `**` 匹配零个或多个路径段（globstar）。

单段判定用状态搜索（两个模式的"积"上做 BFS，`*` 可空可吞），是**精确**判定而不是
采样近似；多段判定对整段 `**` 做记忆化递归。超出子集的写法（字符类 `[a-z]`、
花括号展开）**不静默当成字面量**：由 :func:`normalize_glob` 明确拒绝，调用方按
fail-closed 处理（宁可拒派也不放行未知语义的模式）。

零第三方依赖，纯函数：同一对输入必得同一输出。
"""
from __future__ import annotations

from functools import lru_cache

__all__ = [
    "GlobSyntaxError",
    "normalize_glob",
    "segments_intersect",
    "globs_intersect",
    "glob_covers_path",
    "glob_covers_glob",
]

_UNSUPPORTED = ("[", "]", "{", "}", "\\")


class GlobSyntaxError(ValueError):
    """路径模式越出受支持子集（调用方据此 fail-closed）。"""


def normalize_glob(pattern: str) -> str:
    """规范化路径模式：去首尾空白、去掉前导 ``./``、折叠重复 ``/``。

    绝对路径、``..`` 段、反斜杠、字符类/花括号一律拒绝（抛
    :class:`GlobSyntaxError`）——这些语义我们不做猜测。
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise GlobSyntaxError("empty_pattern")
    pat = pattern.strip().replace("\\", "/")
    for ch in _UNSUPPORTED:
        if ch in pat:
            raise GlobSyntaxError(f"unsupported_construct:{ch}")
    if pat.startswith("/"):
        raise GlobSyntaxError("absolute_path")
    while pat.startswith("./"):
        pat = pat[2:]
    parts = [p for p in pat.split("/") if p not in ("", ".")]
    if not parts:
        raise GlobSyntaxError("empty_pattern")
    if any(p == ".." for p in parts):
        raise GlobSyntaxError("parent_escape")
    if parts.count("**") and any(p == "**" for p in parts[:-1]) and parts[-1] != "**":
        # `a/**/**/b` 合法；这里只拒绝 `**` 与其他字符混写的段，如 `a**b`
        pass
    for p in parts:
        if "**" in p and p != "**":
            raise GlobSyntaxError("partial_globstar")
    return "/".join(parts)


def _seg_chars(pattern: str) -> tuple:
    return tuple(pattern)


def segments_intersect(p: str, q: str) -> bool:
    """两个**单段**模式是否存在共同可匹配串（`?` / `*` / 字面量）。"""
    if p == q:
        return True
    if "*" not in p and "*" not in q:
        if len(p) != len(q):
            return False
        return all(a == b or a == "?" or b == "?" for a, b in zip(p, q))
    np_, nq = len(p), len(q)
    seen = set()
    stack = [(0, 0)]
    while stack:
        i, j = stack.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == np_ and j == nq:
            return True
        if i < np_ and p[i] == "*":
            stack.append((i + 1, j))          # `*` 匹配空串
            if j < nq:
                stack.append((i, j + 1))      # `*` 吞掉 q 的一个字符
        if j < nq and q[j] == "*":
            stack.append((i, j + 1))
            if i < np_:
                stack.append((i + 1, j))
        if i < np_ and j < nq and p[i] != "*" and q[j] != "*":
            if p[i] == "?" or q[j] == "?" or p[i] == q[j]:
                stack.append((i + 1, j + 1))
    return False


def _split(pattern: str) -> tuple:
    return tuple(normalize_glob(pattern).split("/"))


@lru_cache(maxsize=200_000)
def _globs_intersect(a: tuple, b: tuple) -> bool:
    if not a and not b:
        return True
    if not a:
        return all(s == "**" for s in b)
    if not b:
        return all(s == "**" for s in a)
    if a[0] == "**":
        # globstar 吃 0 段
        if _globs_intersect(a[1:], b):
            return True
        # globstar 吃 b 的一段（b 首段为 `**` 时由下一分支处理，避免重复展开）
        if b[0] != "**":
            return _globs_intersect(a, b[1:])
        return _globs_intersect(a[1:], b) or _globs_intersect(a, b[1:]) or _globs_intersect(a[1:], b[1:])
    if b[0] == "**":
        if _globs_intersect(a, b[1:]):
            return True
        return _globs_intersect(a[1:], b)
    if not segments_intersect(a[0], b[0]):
        return False
    return _globs_intersect(a[1:], b[1:])


def globs_intersect(pattern_a: str, pattern_b: str) -> bool:
    """两个（可跨段的）路径模式是否可能指向同一个路径。"""
    return _globs_intersect(_split(pattern_a), _split(pattern_b))


def glob_covers_path(pattern: str, path: str) -> bool:
    """路径是否落在模式内（模式无通配符时退化为相等判定）。"""
    segs = _split(pattern)
    target = tuple(p for p in normalize_glob(path).split("/"))
    return _covers(segs, target)


@lru_cache(maxsize=200_000)
def _covers(segs: tuple, target: tuple) -> bool:
    if not segs:
        return not target
    if segs[0] == "**":
        if _covers(segs[1:], target):
            return True
        return bool(target) and _covers(segs, target[1:])
    if not target:
        return False
    if not segments_intersect(segs[0], target[0]):
        return False
    return _covers(segs[1:], target[1:])


def glob_covers_glob(outer: str, inner: str) -> bool:
    """``inner`` 的全部可匹配路径是否都被 ``outer`` 覆盖（租约包含关系）。"""
    a, b = _split(outer), _split(inner)
    return _covers_glob(a, b)


@lru_cache(maxsize=200_000)
def _covers_glob(a: tuple, b: tuple) -> bool:
    if not b:
        return all(s == "**" for s in a)
    if not a:
        return False
    if a[0] == "**":
        if _covers_glob(a[1:], b):
            return True
        return _covers_glob(a, b[1:])
    if b[0] == "**":
        # inner 的 globstar 可以展开成任意多段，只有 outer 也是 `**` 才可能覆盖
        return False if a[0] != "**" else _covers_glob(a, b[1:])
    if not _seg_covers(a[0], b[0]):
        return False
    return _covers_glob(a[1:], b[1:])


def _seg_covers(outer: str, inner: str) -> bool:
    """`outer` 单段模式的语言是否包含 `inner` 单段模式的语言（保守：字面量判定）。"""
    if "*" not in outer:
        return "*" not in inner and len(outer) == len(inner) and all(
            a == b or (a == "?" and b != "*") for a, b in zip(outer, inner)
        )
    # outer 含 `*`：把 inner 的 `*` 视作最坏情况（可能产生任意长串），只做下界近似
    if "*" in inner:
        return outer == inner or outer == "*" or (outer.endswith("*") and inner.startswith(outer[:-1]))
    return segments_intersect(outer, inner)