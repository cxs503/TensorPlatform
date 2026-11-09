"""否定性知识检索引擎。

基于字符 n-gram Jaccard + 包含度的文本相似度检索（与 proposal_gate 同源设计）。
零领域语义：不依赖任何域词表，纯字符级统计。

检索流程
--------
1. 查询文本归一化（NFKC → 去排版记号 → 标点折叠 → casefold）
2. 切字符 n-gram 集合
3. 与台账中所有 active 条目计算相似度
4. 按分数降序返回 top_k 命中
5. 派单门根据阈值判定：
   - score >= gate_reject_threshold → 拒绝（必须差异化声明）
   - score >= gate_review_threshold → 需人工审查
   - score < gate_review_threshold → 通过

相似度定义
----------
``jaccard = |A∩B| / |A∪B|``（n-gram 集合）
``containment = |A∩B| / min(|A|,|B|)``（短文本被长文本覆盖的比例）
``score = max(jaccard, w_c * containment)``

注意：对于短查询匹配长文档的场景，containment 更能反映"查询内容是否已被覆盖"。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Sequence

from .config import NegativeKnowledgeConfig
from .store import NegativeKnowledgeEntry, NegativeKnowledgeStore

# 归一化前剔除的排版标记
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
            out.append(" ")
        else:
            out.append(ch.casefold())
    collapsed = " ".join("".join(out).split())
    return collapsed


def shingles(text: str, size: int) -> frozenset[str]:
    """字符 n-gram 集合。"""
    if size < 1:
        raise ValueError("shingle size 必须 ≥ 1")
    compact = normalize(text).replace(" ", "")
    if not compact:
        return frozenset()
    if len(compact) <= size:
        return frozenset({compact})
    return frozenset(compact[i : i + size] for i in range(len(compact) - size + 1))


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


@dataclass
class SearchHit:
    """检索命中结果。"""

    entry: NegativeKnowledgeEntry
    score: float
    jaccard: float
    containment: float
    matched_keywords: list[str]

    def as_dict(self) -> dict:
        return {
            "entry": self.entry.as_dict(),
            "score": round(self.score, 6),
            "jaccard": round(self.jaccard, 6),
            "containment": round(self.containment, 6),
            "matched_keywords": self.matched_keywords,
        }


@dataclass
class GateVerdict:
    """派单门判决结果。"""

    passed: bool
    action: str  # "pass" | "review" | "reject"
    hits: list[SearchHit]
    max_score: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "action": self.action,
            "max_score": round(self.max_score, 6),
            "reason": self.reason,
            "hits": [h.as_dict() for h in self.hits],
        }


class NegativeKnowledgeSearcher:
    """否定性知识检索器。"""

    def __init__(self, store: NegativeKnowledgeStore, cfg: NegativeKnowledgeConfig) -> None:
        self.store = store
        self.cfg = cfg

    def _compute_similarity(self, query_shingles: frozenset[str], entry: NegativeKnowledgeEntry) -> tuple[float, float, float]:
        """计算查询与条目的相似度，返回 (score, jaccard, containment)。

        对于短查询匹配长文档的场景，使用 containment 作为主要指标，
        因为 Jaccard 会被长文档的分母惩罚。
        """
        entry_shingles = shingles(entry.search_text(), self.cfg.search_shingle_size)
        if not query_shingles or not entry_shingles:
            return 0.0, 0.0, 0.0

        jac = _jaccard(query_shingles, entry_shingles)
        cont = _containment(query_shingles, entry_shingles)

        # 对于否定性知识检索，我们更关心"查询内容是否已被覆盖"
        # 因此使用 containment 作为主要指标，Jaccard 作为辅助
        # 当查询较短时（len_ratio < 0.5），主要依赖 containment
        len_ratio = (
            min(len(query_shingles), len(entry_shingles))
            / max(len(query_shingles), len(entry_shingles))
        ) if query_shingles and entry_shingles else 0.0

        # 综合分数：取 Jaccard 和加权 containment 的较大值
        # 对于短查询，降低 min_length_ratio 要求
        effective_min_ratio = min(self.cfg.search_min_length_ratio, 0.1)
        score = jac
        if self.cfg.search_containment_enabled and len_ratio >= effective_min_ratio:
            score = max(score, self.cfg.search_containment_weight * cont)

        return score, jac, cont

    def _find_matched_keywords(self, query: str, entry: NegativeKnowledgeEntry) -> list[str]:
        """找出查询中命中条目关键词的部分。"""
        query_lower = normalize(query)
        matched = []
        for kw in entry.keywords:
            if normalize(kw) in query_lower:
                matched.append(kw)
        return matched

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        min_score: float | None = None,
        failure_kind: str | None = None,
    ) -> list[SearchHit]:
        """检索相似否定性知识条目。

        Args:
            query: 查询文本（提案标题/描述/关键词）
            top_k: 返回最多几条（默认从配置取）
            min_score: 最低分数阈值（默认从配置取）
            failure_kind: 按失败类型过滤

        Returns:
            按分数降序排列的命中列表
        """
        k = top_k or self.cfg.search_top_k
        threshold = min_score if min_score is not None else self.cfg.search_min_score

        # 获取所有 active 条目
        entries = self.store.list_entries(status="active", failure_kind=failure_kind, limit=10000)
        if not entries:
            return []

        query_shingles = shingles(query, self.cfg.search_shingle_size)
        if not query_shingles:
            return []

        hits: list[SearchHit] = []
        for entry in entries:
            score, jac, cont = self._compute_similarity(query_shingles, entry)
            if score >= threshold:
                matched_kw = self._find_matched_keywords(query, entry)
                hits.append(SearchHit(
                    entry=entry,
                    score=score,
                    jaccard=jac,
                    containment=cont,
                    matched_keywords=matched_kw,
                ))

        # 按分数降序
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def gate_check(
        self,
        proposal_title: str,
        proposal_description: str = "",
        proposal_keywords: list[str] | None = None,
    ) -> GateVerdict:
        """派单前检索门。

        对提案标题+描述+关键词做检索，根据最高命中分数判定：
        - score >= gate_reject_threshold → reject（必须差异化声明）
        - score >= gate_review_threshold → review（需人工审查）
        - score < gate_review_threshold → pass

        Args:
            proposal_title: 提案标题
            proposal_description: 提案描述
            proposal_keywords: 提案关键词

        Returns:
            GateVerdict 判决结果
        """
        # 拼接查询文本
        parts = [proposal_title]
        if proposal_description:
            parts.append(proposal_description)
        if proposal_keywords:
            parts.extend(proposal_keywords)
        query = "\n".join(p for p in parts if p)

        hits = self.search(query, top_k=self.cfg.search_top_k)

        if not hits:
            return GateVerdict(
                passed=True,
                action="pass",
                hits=[],
                max_score=0.0,
                reason="无相似否定性知识命中",
            )

        max_score = max(h.score for h in hits)

        if max_score >= self.cfg.gate_reject_threshold:
            return GateVerdict(
                passed=False,
                action="reject",
                hits=hits,
                max_score=max_score,
                reason=f"与已证伪假设高度相似（score={max_score:.3f} >= {self.cfg.gate_reject_threshold}），必须声明差异化",
            )
        elif max_score >= self.cfg.gate_review_threshold:
            return GateVerdict(
                passed=True,
                action="review",
                hits=hits,
                max_score=max_score,
                reason=f"与已证伪假设中度相似（score={max_score:.3f} >= {self.cfg.gate_review_threshold}），建议人工审查",
            )
        else:
            return GateVerdict(
                passed=True,
                action="pass",
                hits=hits,
                max_score=max_score,
                reason=f"与已证伪假设相似度低（score={max_score:.3f} < {self.cfg.gate_review_threshold}）",
            )
