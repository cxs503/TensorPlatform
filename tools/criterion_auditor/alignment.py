"""槽位对齐打分：物理三元组与节点/提案池的对齐。

对每个槽位（quantity, criterion_form, domain）计算相似度，
加权求和得到总体对齐分数。支持贪心/匈牙利指派。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config_loader import AuditorConfig
from .extractor import PhysicalTriple


@dataclass(frozen=True)
class SlotAlignment:
    """槽位对齐结果。"""

    quantity_score: float = 0.0
    criterion_form_score: float = 0.0
    domain_score: float = 0.0
    overall_score: float = 0.0
    quantity_match: bool = False
    criterion_form_match: bool = False
    domain_match: bool = False
    diff_quantity: str = ""
    diff_criterion_form: str = ""
    diff_domain: str = ""

    def as_dict(self) -> dict:
        return {
            "quantity_score": round(self.quantity_score, 4),
            "criterion_form_score": round(self.criterion_form_score, 4),
            "domain_score": round(self.domain_score, 4),
            "overall_score": round(self.overall_score, 4),
            "quantity_match": self.quantity_match,
            "criterion_form_match": self.criterion_form_match,
            "domain_match": self.domain_match,
            "diff_quantity": self.diff_quantity,
            "diff_criterion_form": self.diff_criterion_form,
            "diff_domain": self.diff_domain,
        }


def _slot_match_score(a: str | None, b: str | None) -> float:
    """单槽位匹配分数：完全匹配=1.0，部分匹配=0.5-0.7，不匹配=0.0。

    改进版：更细粒度的部分匹配。
    """
    if a is None or b is None:
        return 0.0
    if a == b:
        return 1.0

    a_lower = a.lower()
    b_lower = b.lower()

    # 子串关系
    if a_lower in b_lower or b_lower in a_lower:
        shorter = min(len(a_lower), len(b_lower))
        longer = max(len(a_lower), len(b_lower))
        return 0.5 + 0.2 * (shorter / longer) if longer > 0 else 0.5

    # 共同词根
    a_words = set(a_lower.split("_"))
    b_words = set(b_lower.split("_"))
    common = a_words & b_words
    if common:
        # 根据共同词根的比例给分
        union = a_words | b_words
        jaccard = len(common) / len(union) if union else 0.0
        return 0.3 + 0.3 * jaccard

    # 编辑距离启发式（简单版）
    if len(a_lower) > 3 and len(b_lower) > 3:
        # 检查前缀相似度
        common_prefix_len = 0
        for ca, cb in zip(a_lower, b_lower):
            if ca == cb:
                common_prefix_len += 1
            else:
                break
        if common_prefix_len >= 3:
            return 0.2

    return 0.0


def slot_diff(a: str | None, b: str | None, slot_name: str) -> str:
    """生成槽位级结构化 diff。"""
    if a == b:
        return f"{slot_name}: identical ({a})"
    if a is None:
        return f"{slot_name}: missing in probe, reference has '{b}'"
    if b is None:
        return f"{slot_name}: probe has '{a}', missing in reference"
    return f"{slot_name}: probe='{a}' vs reference='{b}'"


def align_triples(
    probe: PhysicalTriple,
    reference: PhysicalTriple,
    cfg: AuditorConfig,
) -> SlotAlignment:
    """对齐两个三元组，返回槽位级分数与 diff。"""
    weights = cfg.slot_weights()
    w_q = weights.get("quantity", 0.45)
    w_c = weights.get("criterion_form", 0.30)
    w_d = weights.get("domain", 0.25)

    q_score = _slot_match_score(probe.quantity_id, reference.quantity_id)
    c_score = _slot_match_score(probe.criterion_form_id, reference.criterion_form_id)
    d_score = _slot_match_score(probe.domain_id, reference.domain_id)

    overall = w_q * q_score + w_c * c_score + w_d * d_score

    return SlotAlignment(
        quantity_score=q_score,
        criterion_form_score=c_score,
        domain_score=d_score,
        overall_score=overall,
        quantity_match=q_score >= 0.5,
        criterion_form_match=c_score >= 0.5,
        domain_match=d_score >= 0.5,
        diff_quantity=slot_diff(probe.quantity_id, reference.quantity_id, "quantity"),
        diff_criterion_form=slot_diff(probe.criterion_form_id, reference.criterion_form_id, "criterion_form"),
        diff_domain=slot_diff(probe.domain_id, reference.domain_id, "domain"),
    )


def align_with_pool(
    probe: PhysicalTriple,
    pool: Sequence[PhysicalTriple],
    cfg: AuditorConfig,
) -> tuple[SlotAlignment | None, int | None]:
    """与池中最匹配项对齐。返回 (最佳对齐, 最佳索引)。"""
    if not pool:
        return None, None

    best_align = None
    best_idx = None
    best_score = -1.0

    for i, ref in enumerate(pool):
        align = align_triples(probe, ref, cfg)
        if align.overall_score > best_score:
            best_score = align.overall_score
            best_align = align
            best_idx = i

    return best_align, best_idx


def greedy_assignment(
    probes: Sequence[PhysicalTriple],
    references: Sequence[PhysicalTriple],
    cfg: AuditorConfig,
) -> list[tuple[int, int, SlotAlignment]]:
    """贪心指派：按分数从高到低贪心匹配。

    返回 [(probe_idx, ref_idx, alignment), ...]。
    """
    if not probes or not references:
        return []

    # 计算所有对的分数
    scores = []
    for i, p in enumerate(probes):
        for j, r in enumerate(references):
            align = align_triples(p, r, cfg)
            scores.append((align.overall_score, i, j, align))

    # 按分数降序排序
    scores.sort(key=lambda x: x[0], reverse=True)

    # 贪心匹配
    used_probes = set()
    used_refs = set()
    assignments = []

    for score, i, j, align in scores:
        if i in used_probes or j in used_refs:
            continue
        assignments.append((i, j, align))
        used_probes.add(i)
        used_refs.add(j)
        if len(assignments) >= min(len(probes), len(references)):
            break

    return assignments


def hungarian_assignment(
    probes: Sequence[PhysicalTriple],
    references: Sequence[PhysicalTriple],
    cfg: AuditorConfig,
) -> list[tuple[int, int, SlotAlignment]]:
    """匈牙利指派（最优指派）。需要 SciPy。

    如果 SciPy 不可用，回退到贪心。
    """
    try:
        from scipy.optimize import linear_sum_assignment
        import numpy as np
    except ImportError:
        # 回退到贪心
        return greedy_assignment(probes, references, cfg)

    if not probes or not references:
        return []

    n = len(probes)
    m = len(references)

    # 构建代价矩阵（负分数，因为 linear_sum_assignment 最小化）
    cost_matrix = np.zeros((n, m))
    align_map = {}
    for i, p in enumerate(probes):
        for j, r in enumerate(references):
            align = align_triples(p, r, cfg)
            cost_matrix[i, j] = -align.overall_score
            align_map[(i, j)] = align

    # 匈牙利算法
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    assignments = []
    for i, j in zip(row_ind, col_ind):
        if j < m:  # 有效匹配
            assignments.append((i, j, align_map[(i, j)]))

    return assignments
