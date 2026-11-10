"""判据强度审计器 + 提案语义裁决机制。

从提案/节点文本抽取「物理三元组」{quantity, criterion_form, domain}，
与节点/提案池做槽位对齐打分，输出 containment 与差异点；
自动检出「结构性恒等式/恒真判据」并生成可执行见证用例；
与词法分数融合，仲裁前门自动打回低新颖提案。

纪律：零领域硬编码——词典/语料经配置注入。
"""

from .extractor import PhysicalTriple, extract_triples
from .alignment import SlotAlignment, align_triples, slot_diff
from .strength import StrengthAudit, audit_criterion_strength
from .adjudicator import Adjudicator, AdjudicationResult

__all__ = [
    "PhysicalTriple",
    "extract_triples",
    "SlotAlignment",
    "align_triples",
    "slot_diff",
    "StrengthAudit",
    "audit_criterion_strength",
    "Adjudicator",
    "AdjudicationResult",
]
