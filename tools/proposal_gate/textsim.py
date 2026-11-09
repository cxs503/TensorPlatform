"""文本相似度：归一化 → 字符 n-gram 集合 → Jaccard（+ 长度比受控的包含度）。

为什么是字符 n-gram 而不是分词
------------------------------
* 提案池跨中英混排、跨作者、跨工具生成，没有统一分词器；字符 n-gram 不需要词典，
  因此天然满足「零领域词表」纪律（字符 n-gram 里不存在任何域词）。
* 归一化链（NFKC → 标记符号剔除 → 标点/符号/空白折叠 → casefold）把「排版差异」
  与「语义差异」解耦；实测见 docs/N08_proposal_gate_maturity.md 表。

相似度定义
----------
``jaccard = |A∩B| / |A∪B|``（n-gram 集合）
``containment = |A∩B| / min(|A|,|B|)``（短文本被长文本覆盖的比例：同义改写常表现为
「加/删从句」，Jaccard 被分母惩罚，包含度不受惩罚）
``score = max(jaccard, w_c * containment)``，仅当长度比 ≥ ``min_length_ratio`` 时
才允许包含度参与（否则短标题会被长提案「完全包含」造成误判）。

所有权重/阈值来自配置（``GateConfig``），本模块无字面量阈值。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from .config import GateConfig

# 归一化前剔除的排版标记（纯 ASCII 排版记号，非域词；顺序即执行序）
_MARKUP_TOKENS: tuple[str, ...] = ("**", "__", "`", "~~", "##", "#")


def normalize(text: str) -> str:
    """NFKC → 去排版记号 → 标点/符号/控制符折算为空格 → 折叠空白 → casefold。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for token in _MARKUP_TOKENS:
        s = s.replace(token, " ")
    out: list[str] = []
    for ch in s:
        if ch.isspace():
            out.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat[0] in ("P", "S", "C", "Z"):
            # 标点/符号/控制/分隔 → 单个空格（保留「词与词被隔开」的信息）
            out.append(" ")
        else:
            out.append(ch.casefold())
    collapsed = " ".join("".join(out).split())
    return collapsed


def shingles(text: str, size: int) -> frozenset[str]:
    """字符 n-gram 集合（先压实去掉空白，让「空格/排版变化」不影响集合）。"""
    if size < 1:
        raise ValueError("shingle size 必须 ≥ 1")
    compact = normalize(text).replace(" ", "")
    if not compact:
        return frozenset()
    if len(compact) <= size:
        return frozenset({compact})
    return frozenset(compact[i : i + size] for i in range(len(compact) - size + 1))


@dataclass(frozen=True)
class SimResult:
    """一次相似度测量的完整证据（判据数字可复核）。"""

    score: float
    jaccard: float
    containment: float
    length_ratio: float
    size_a: int
    size_b: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "score": round(self.score, 6),
            "jaccard": round(self.jaccard, 6),
            "containment": round(self.containment, 6),
            "length_ratio": round(self.length_ratio, 6),
            "size_a": self.size_a,
            "size_b": self.size_b,
        }


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _containment(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def similarity_texts(a: str, b: str, cfg: GateConfig) -> SimResult:
    """按配置口径计算两条文本的相似度（score 即去重判据用的那一个数）。"""
    size = int(cfg.need("dedup.shingle_size"))
    return sim_from_sets(shingles(a, size), shingles(b, size), cfg)


def sim_from_sets(sa: frozenset[str], sb: frozenset[str], cfg: GateConfig) -> SimResult:
    """已预算好 n-gram 集合时的相似度（索引侧复用，避免 O(n²) 重复切分）。"""
    jac = _jaccard(sa, sb)
    cont = _containment(sa, sb)
    len_ratio = (min(len(sa), len(sb)) / max(len(sa), len(sb))) if sa and sb else 0.0
    score = jac
    if cfg.get("dedup.containment_enabled", True) and len_ratio >= float(
        cfg.get("dedup.min_length_ratio", 0.0)
    ):
        score = max(score, float(cfg.get("dedup.containment_weight", 1.0)) * cont)
    return SimResult(
        score=float(score),
        jaccard=jac,
        containment=cont,
        length_ratio=len_ratio,
        size_a=len(sa),
        size_b=len(sb),
    )


def similarity(a: str, b: str, cfg: GateConfig) -> float:
    """只取判据分值（便捷函数）。"""
    return similarity_texts(a, b, cfg).score


def canonical_blob(parts: Iterable[str]) -> str:
    """把提案的多字段拼成一段用于相似度比较的规范文本（字段间以换行分隔）。"""
    return "\n".join(p for p in (str(x).strip() for x in parts) if p)


def best_match(
    probe: str,
    candidates: Sequence[tuple[str, str]],
    cfg: GateConfig,
    *,
    threshold: float,
) -> tuple[str | None, SimResult | None]:
    """在 ``[(key, text)]`` 中找最相似项；无候选或最高分 < threshold → (None, None)。"""
    best_key: str | None = None
    best: SimResult | None = None
    for key, text in candidates:
        res = similarity_texts(probe, text, cfg)
        if best is None or res.score > best.score:
            best_key, best = key, res
    if best is None or best.score < threshold:
        return None, best
    return best_key, best