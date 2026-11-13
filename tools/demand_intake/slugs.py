"""slugs —— 锚点 slug 的规范化、规范校验、标题相似度（全部纯函数，零 IO）。

为什么要一个独立模块
--------------------
「审计门」的第一条判据是 **slug 规范**，但来源文本里的 slug 有十来种脏形态
（小写、空格、下划线、前后空白、markdown 反引号、括号、句末标点）。把这些形态
收敛成一条确定性规则，才能让「同样的候选」在任何一次重放里得到同样的判定——
这不是风格问题：入榜幂等（同 slug 不重复入账）与去重（与 open/done 冲突检测）
都建立在这个规范化函数上。

规范（canonical form）
----------------------
1. 去掉 markdown/包裹符号与空白：``` ` `` `**`、`()`、`[]`、`<>`、引号；
2. 空格/下划线/句点/斜杠一律折成 ``-``；连续 ``-`` 折叠；首尾 ``-`` 去掉；
3. 全大写。

`is_valid_slug` 的判据（三条，全部机器可判）：
- 形如 ``SEGn-SEGn-...``，段数 ≥ 2（``FEAT-X`` 合法，``FEAT`` 不合法——单段是词不是锚）；
- 每段 2..16 字符，字符集 ``A-Z0-9``，不能以数字开头（避免 ``2026-01`` 这类日期误入）；
- 总长 4..48；
- 至少一个段含 ≥3 个连续字母（排除 ``A1-A2`` / ``B1-B4`` 这类**步骤编号**——它们在
  agent 报告里满地都是，形状像 slug 却不是锚；真锚一定带实词段，如 ``FEAT-BOARD-CLAIM``）。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Set

_WRAPPER_CHARS = "`*_~'\"“”‘’()（）[]【】<>《》「」"
_SEP_RE = re.compile(r"[\s_\./\\:：,，;；|]+")
_DASH_RE = re.compile(r"-{2,}")

_MIN_SEG, _MAX_SEG, _MAX_LEN = 2, 16, 48
_SEG_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
_ALPHA_RUN_RE = re.compile(r"[A-Z]{3,}")

#: 出现在 slug 形状 token 上但一定不是 slug 的整词（避免把 ``UTF-8`` ``HTTP-2``
#: 之类误当锚）。平台中性：只是「形状词」，不含任何领域词条。
_NOT_SLUG = {
    "UTF-8", "UTF-16", "ISO-8601", "SHA-1", "SHA-256", "SHA-512", "MD-5",
    "X-86", "X86-64", "AMD-64", "ARM-64", "CI-CD", "E-2-E", "P-VALUE",
}

#: slug 形状 token：2..6 段、大写字母开头、段内字母数字。
SLUG_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,15}(?:-[A-Z0-9]{1,16}){1,5}\b")

#: 标题相似度用的 token 规则（中英混排：英文/数字按词，中文按 2-gram）。
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


def canonical_slug(raw: object) -> str:
    """把任意来源文本收敛成规范 slug（失败时返回空串，调用方按 ``slug_invalid`` 处理）。

    >>> canonical_slug(" `feat: demand intank` ")
    'FEAT-DEMAND-INTANK'
    >>> canonical_slug("FEAT--X_2")
    'FEAT-X-2'
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    for ch in _WRAPPER_CHARS:
        text = text.replace(ch, " ")
    text = _SEP_RE.sub("-", text.strip())
    text = text.strip("-")
    text = _DASH_RE.sub("-", text)
    return text.upper()


def slug_segments(slug: str) -> List[str]:
    return [s for s in slug.split("-") if s]


def is_valid_slug(slug: str) -> bool:
    """规范 slug 判据（见模块 docstring 三条）；输入须已 ``canonical_slug``。"""
    if not slug or slug != canonical_slug(slug):
        return False
    if not (4 <= len(slug) <= _MAX_LEN):
        return False
    if slug in _NOT_SLUG:
        return False
    segs = slug_segments(slug)
    if len(segs) < _MIN_SEG:
        return False
    if not all(_SEG_RE.match(s) and _MIN_SEG <= len(s) <= _MAX_SEG for s in segs):
        return False
    return any(_ALPHA_RUN_RE.search(s) for s in segs)


def slug_problem(slug: str) -> str:
    """给出 slug 不合规的**首个**原因（空串=合规）。用于审计门的 evidence。"""
    if not slug:
        return "slug_empty"
    if slug != canonical_slug(slug):
        return "slug_not_canonical"
    if not (4 <= len(slug) <= _MAX_LEN):
        return f"slug_length_out_of_range({len(slug)})"
    if slug in _NOT_SLUG:
        return "slug_shape_token_blacklisted"
    segs = slug_segments(slug)
    if len(segs) < _MIN_SEG:
        return f"slug_segments_lt_{_MIN_SEG}"
    for s in segs:
        if not _SEG_RE.match(s):
            return f"slug_segment_bad('{s}')"
        if not (_MIN_SEG <= len(s) <= _MAX_SEG):
            return f"slug_segment_length_out_of_range('{s}')"
    if not any(_ALPHA_RUN_RE.search(s) for s in segs):
        return "slug_no_alpha_word_segment"
    return ""


def title_tokens(title: object) -> Set[str]:
    """标题 token 集合：英文/数字按词，中文按 2-gram（无需分词器，跨语言可比）。"""
    if not title:
        return set()
    text = str(title).lower()
    out: Set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.isascii():
            out.add(tok)
        else:  # 中文片段：2-gram（单字片段也保留，避免极短标题失去可比性）
            if len(tok) == 1:
                out.add(tok)
            else:
                out.update(tok[i:i + 2] for i in range(len(tok) - 1))
    return out


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def title_similarity(a: object, b: object) -> float:
    return jaccard(title_tokens(a), title_tokens(b))