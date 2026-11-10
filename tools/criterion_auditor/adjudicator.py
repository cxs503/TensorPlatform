"""提案语义裁决：融合词法/语义/强度分数，输出最终裁决。

融合公式：final_score = lexical_weight * lex + semantic_weight * sem + strength_weight * str
裁决：
- final_score < reject_threshold → reject (低新颖)
- final_score < novelty_threshold → review (需人工复核)
- final_score >= novelty_threshold → accept (新颖)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .alignment import SlotAlignment, align_with_pool
from .config_loader import AuditorConfig
from .extractor import PhysicalTriple, extract_single_triple
from .strength import StrengthAudit, audit_criterion_strength


@dataclass(frozen=True)
class AdjudicationResult:
    """裁决结果。"""

    proposal_id: str
    verdict: str  # accept/reject/review
    final_score: float
    lexical_score: float
    semantic_score: float
    strength_score: float
    nearest_neighbor: str | None = None
    nearest_neighbor_score: float = 0.0
    slot_alignment: SlotAlignment | None = None
    strength_audit: StrengthAudit | None = None
    reasons: list[str] = field(default_factory=list)
    diff_summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "verdict": self.verdict,
            "final_score": round(self.final_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "strength_score": round(self.strength_score, 4),
            "nearest_neighbor": self.nearest_neighbor,
            "nearest_neighbor_score": round(self.nearest_neighbor_score, 4),
            "slot_alignment": self.slot_alignment.as_dict() if self.slot_alignment else None,
            "strength_audit": self.strength_audit.as_dict() if self.strength_audit else None,
            "reasons": self.reasons,
            "diff_summary": self.diff_summary,
        }


class Adjudicator:
    """提案语义裁决器。"""

    def __init__(self, cfg: AuditorConfig) -> None:
        self.cfg = cfg
        self._pool: list[tuple[str, PhysicalTriple]] = []  # (id, triple)

    def add_to_pool(self, proposal_id: str, text: str, *, domain_hint: str | None = None) -> None:
        """添加提案到池。"""
        triple = extract_single_triple(text, self.cfg, domain_hint=domain_hint)
        self._pool.append((proposal_id, triple))

    def clear_pool(self) -> None:
        """清空池。"""
        self._pool.clear()

    def adjudicate(
        self,
        proposal_id: str,
        text: str,
        *,
        domain_hint: str | None = None,
        lexical_score: float | None = None,
    ) -> AdjudicationResult:
        """裁决单个提案。

        lexical_score: 外部词法相似度分数（0-1），如果为 None 则用 0.5 作为默认。
        """
        # 抽取三元组
        probe_triple = extract_single_triple(text, self.cfg, domain_hint=domain_hint)

        # 语义对齐（与池中最匹配项）
        semantic_score = 0.0
        nearest_neighbor = None
        nearest_score = 0.0
        slot_align = None

        if self._pool:
            pool_triples = [t for _, t in self._pool]
            align, idx = align_with_pool(probe_triple, pool_triples, self.cfg)
            if align is not None and idx is not None:
                slot_align = align
                # 语义相似度 = 1 - 对齐分数（对齐越高越相似，新颖度越低）
                # 改进：使用非线性映射增强区分度
                alignment_score = align.overall_score
                if alignment_score > 0.7:
                    # 高对齐度 → 低新颖度（强惩罚）
                    semantic_score = max(0.0, 1.0 - alignment_score * 1.2)
                elif alignment_score > 0.4:
                    # 中等对齐度
                    semantic_score = 1.0 - alignment_score
                else:
                    # 低对齐度 → 高新颖度
                    semantic_score = 1.0 - alignment_score * 0.8

                nearest_neighbor = self._pool[idx][0]
                nearest_score = alignment_score

        # 判据强度审计
        strength_audit = audit_criterion_strength(text, self.cfg)
        strength_score = strength_audit.strength_score

        # 词法分数
        if lexical_score is None:
            lexical_score = 0.5  # 默认中性

        # 融合
        weights = self.cfg.adjudication_weights()
        w_lex = weights.get("lexical_weight", 0.35)
        w_sem = weights.get("semantic_weight", 0.40)
        w_str = weights.get("strength_weight", 0.25)

        final_score = w_lex * lexical_score + w_sem * semantic_score + w_str * strength_score

        # 裁决
        reject_threshold = weights.get("reject_threshold", 0.15)
        novelty_threshold = weights.get("novelty_threshold", 0.30)
        min_strength = weights.get("min_strength_score", 0.20)

        reasons = []
        if strength_score < min_strength:
            verdict = "reject"
            reasons.append(f"weak_criterion_strength: {strength_score:.2f} < {min_strength}")
        elif final_score < reject_threshold:
            verdict = "reject"
            reasons.append(f"low_novelty_score: {final_score:.2f} < {reject_threshold}")
        elif final_score < novelty_threshold:
            verdict = "review"
            reasons.append(f"borderline_novelty: {final_score:.2f} in [{reject_threshold}, {novelty_threshold})")
        else:
            verdict = "accept"
            reasons.append(f"novel_proposal: {final_score:.2f} >= {novelty_threshold}")

        if nearest_neighbor:
            reasons.append(f"nearest_neighbor: {nearest_neighbor} (similarity={nearest_score:.2f})")

        # diff 摘要
        diff_summary = {}
        if slot_align:
            diff_summary = {
                "quantity": slot_align.diff_quantity,
                "criterion_form": slot_align.diff_criterion_form,
                "domain": slot_align.diff_domain,
            }

        return AdjudicationResult(
            proposal_id=proposal_id,
            verdict=verdict,
            final_score=final_score,
            lexical_score=lexical_score,
            semantic_score=semantic_score,
            strength_score=strength_score,
            nearest_neighbor=nearest_neighbor,
            nearest_neighbor_score=nearest_score,
            slot_alignment=slot_align,
            strength_audit=strength_audit,
            reasons=reasons,
            diff_summary=diff_summary,
        )

    def adjudicate_batch(
        self,
        proposals: Sequence[tuple[str, str]],
        *,
        domain_hints: dict[str, str] | None = None,
        lexical_scores: dict[str, float] | None = None,
    ) -> list[AdjudicationResult]:
        """批量裁决。"""
        domain_hints = domain_hints or {}
        lexical_scores = lexical_scores or {}
        results = []
        for pid, text in proposals:
            result = self.adjudicate(
                pid,
                text,
                domain_hint=domain_hints.get(pid),
                lexical_score=lexical_scores.get(pid),
            )
            results.append(result)
        return results
