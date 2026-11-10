"""物理三元组抽取：{quantity, criterion_form, domain}。

从提案/节点文本中抽取三个槽位：
- quantity: 被测量或守恒量（如 resistance, RAO, efficiency）
- criterion_form: 判据形式（如 threshold_le, convergence_order, benchmark_match）
- domain: 工况或边界域（如 calm_water, head_sea, ice_condition）

所有模式由配置注入，代码零领域语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .config_loader import AuditorConfig


@dataclass(frozen=True)
class PhysicalTriple:
    """物理三元组：{quantity, criterion_form, domain}。"""

    quantity: str | None = None
    quantity_id: str | None = None
    criterion_form: str | None = None
    criterion_form_id: str | None = None
    domain: str | None = None
    domain_id: str | None = None
    raw_text: str = ""

    def as_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "quantity_id": self.quantity_id,
            "criterion_form": self.criterion_form,
            "criterion_form_id": self.criterion_form_id,
            "domain": self.domain,
            "domain_id": self.domain_id,
        }

    def is_complete(self) -> bool:
        """三个槽位都填充。"""
        return bool(self.quantity_id and self.criterion_form_id and self.domain_id)


def _match_patterns(
    text: str,
    patterns: Iterable[dict],
    *,
    case_insensitive: bool = True,
) -> list[tuple[str, str, str]]:
    """匹配模式列表，返回 [(id, display, matched_text), ...]。"""
    flags = re.IGNORECASE if case_insensitive else 0
    matches = []
    for entry in patterns:
        pid = entry.get("id", "")
        pattern = entry.get("pattern", "")
        display = entry.get("display", pid)
        if not pattern:
            continue
        try:
            m = re.search(pattern, text, flags)
            if m:
                matches.append((pid, display, m.group(0)))
        except re.error:
            continue
    return matches


def extract_quantity(text: str, cfg: AuditorConfig) -> tuple[str | None, str | None]:
    """抽取 quantity 槽位。返回 (display, id)。"""
    patterns = cfg.quantity_patterns()
    matches = _match_patterns(text, patterns)
    if not matches:
        return None, None
    # 取第一个匹配（可改为最长匹配等策略）
    pid, display, _ = matches[0]
    return display, pid


def extract_criterion_form(text: str, cfg: AuditorConfig) -> tuple[str | None, str | None]:
    """抽取 criterion_form 槽位。返回 (form_id, form_id)。"""
    patterns = cfg.criterion_form_patterns()
    matches = _match_patterns(text, patterns)
    if not matches:
        return None, None
    pid, display, _ = matches[0]
    return pid, pid


def extract_domain(text: str, cfg: AuditorConfig) -> tuple[str | None, str | None]:
    """抽取 domain 槽位。返回 (display, id)。"""
    patterns = cfg.domain_patterns()
    matches = _match_patterns(text, patterns)
    if not matches:
        return None, None
    pid, display, _ = matches[0]
    return display, pid


def extract_triples(
    text: str,
    cfg: AuditorConfig,
    *,
    domain_hint: str | None = None,
) -> list[PhysicalTriple]:
    """从文本抽取物理三元组列表。

    如果文本包含多个 quantity/criterion/domain，会生成多个三元组（笛卡尔积）。
    domain_hint 可强制指定 domain（用于回归测试或已知工况）。
    """
    if not text or not text.strip():
        return []

    quantity_matches = _match_patterns(text, cfg.quantity_patterns())
    criterion_matches = _match_patterns(text, cfg.criterion_form_patterns())

    # domain: 优先用 hint，否则从文本抽取
    if domain_hint:
        domain_display, domain_id = domain_hint, domain_hint
        domain_matches = [(domain_id, domain_display, domain_hint)]
    else:
        domain_matches = _match_patterns(text, cfg.domain_patterns())

    # 如果某个槽位没匹配到，用 None 占位
    if not quantity_matches:
        quantity_matches = [(None, None, "")]
    if not criterion_matches:
        criterion_matches = [(None, None, "")]
    if not domain_matches:
        domain_matches = [(None, None, "")]

    triples = []
    for q_pid, q_display, _ in quantity_matches:
        for c_pid, c_display, _ in criterion_matches:
            for d_pid, d_display, _ in domain_matches:
                triple = PhysicalTriple(
                    quantity=q_display,
                    quantity_id=q_pid,
                    criterion_form=c_display,
                    criterion_form_id=c_pid,
                    domain=d_display,
                    domain_id=d_pid,
                    raw_text=text[:200],  # 截断存储
                )
                triples.append(triple)

    return triples


def extract_single_triple(
    text: str,
    cfg: AuditorConfig,
    *,
    domain_hint: str | None = None,
) -> PhysicalTriple:
    """抽取单个三元组（取第一个匹配）。"""
    triples = extract_triples(text, cfg, domain_hint=domain_hint)
    if not triples:
        return PhysicalTriple(raw_text=text[:200])
    return triples[0]
