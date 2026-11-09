"""复现匹配：把「人工写的节点名」与「提案标题」对上号（四通道 + 角色通道）。

为什么不能只用一条 3-gram Jaccard
---------------------------------
实测同一能力在两处的写法差异很大：提案侧是「中英双语长标题」，人工侧是「中文简写 +
英文 slug」。全文 3-gram 被英文译文稀释（实测同一能力只有 0.15~0.26），单一阈值会
把真实匹配判成不匹配。因此本模块给出四个互补通道取最大，并**记录每个通道的值**，
让结论可复核：

* ``full_3gram``    —— 全文字符 3-gram Jaccard（与聚合口径一致）；
* ``cjk_3gram``     —— 只保留非 ASCII 字符后做 3-gram（中文改写的主要差异在英文译文）；
* ``ascii_tokens``  —— ASCII 词集合 Jaccard（``mortar`` / ``lbm`` / ``dsm`` 这类技术标识符）；
* ``prefix_3gram``  —— 标题前缀（第一个冒号之前）3-gram（人工节点常保留同一前缀）。

另有一条 ``role`` 通道：两侧都是「平台仓 + 成熟化标记」的节点，无论文本怎么写都算同一
角色（自回归增长注入的平台自优化节点在两侧措辞必然不同）。

注意：``prefix`` 必须在**归一化之前**切分——归一化会把冒号折成空格。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from tools.proposal_gate.textsim import normalize, shingles

from .aggregate import similarity_measure
from .config import SeedingConfig
from .model import ascii_tokens

CHANNELS = ("full_3gram", "cjk_3gram", "ascii_tokens", "prefix_3gram")
ROLE_CHANNEL = "role(platform)"


def zh_only(text: str) -> str:
    return "".join(ch for ch in str(text) if not ch.isascii())


def ngram_jaccard(a: str, b: str, size: int = 3) -> float:
    sa, sb = shingles(a, size), shingles(b, size)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def token_jaccard(a: str, b: str) -> float:
    ta, tb = set(ascii_tokens(a)), set(ascii_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def prefix(text: str) -> str:
    """第一个冒号之前的部分（先切分再归一化）。"""
    head = re.split(r"[：:]", str(text), maxsplit=1)[0]
    return normalize(head)


def match_channels(a: str, b: str, cfg: SeedingConfig) -> dict[str, Any]:
    ch: dict[str, Any] = {
        "full_3gram": round(similarity_measure(a, b, cfg)["jaccard"], 6),
        "cjk_3gram": round(ngram_jaccard(zh_only(a), zh_only(b)), 6),
        "ascii_tokens": round(token_jaccard(a, b), 6),
        "prefix_3gram": round(ngram_jaccard(prefix(a), prefix(b)), 6),
    }
    best = max(CHANNELS, key=lambda k: ch[k])
    ch["score"] = ch[best]
    ch["channel"] = best
    ch["channels"] = {k: ch[k] for k in CHANNELS}
    return ch


def is_platform_role(name: str, repo: str, cfg: SeedingConfig) -> bool:
    return repo == cfg.platform_repo and cfg.maturity_marker in str(name)


def best_match(name: str, candidates: list[Mapping[str, Any]], cfg: SeedingConfig,
               *, id_key: str = "item_id", text_key: str = "title") -> dict[str, Any]:
    """在候选集合里找最佳匹配（四通道取最大），返回带通道证据的字典。"""
    best: dict[str, Any] | None = None
    for cand in candidates:
        ch = match_channels(name, str(cand.get(text_key, "")), cfg)
        if best is None or ch["score"] > best["score"]:
            best = {**ch, id_key: cand.get(id_key), "text": cand.get(text_key)}
    if best is None:
        return {"score": 0.0, "channel": "none", "channels": {k: 0.0 for k in CHANNELS}}
    return best