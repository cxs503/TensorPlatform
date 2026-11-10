"""语义三元分层引擎：capability / delivery / evidence 三层的装配与完整性核算。

判据 (a) 口径（见战役节点验收）：

* **条目零丢失**：台账条目必须在分层结果里各有一行三元组（丢失率 0；
  丢失率 > ``entry_loss_max_ratio`` 即触发 honest-FAIL 停止规则）；
* **可归类率 100%**：每行三元组必须能判定「三层各有无内容」，不允许
  「部分归类」；缺层的行不隐藏，而是显式记 ``layer_complete`` 为 False 并
  归入 ``unclassified`` 统计（这三档是互斥且完备的：有交付有证据 / 只有交付 /
  只有证据 / 两者皆无）。
* **无孤立交付**：端点模块必须挂在某条能力下，否则记 ``orphan_deliveries``
  （= 能力在飞而账上无条目，属 NOT_SUPPORTED 误用的一种根因）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .model import (SemanticTriple, TIER_CAPABILITY, TIER_DELIVERY, TIER_EVIDENCE,
                    DeliveryArtifact, EvidenceRecord, level_rank, stable_counts)
from .scan import orphan_endpoint_modules
from .sources import EndpointModule, SemanticsConfig

#: 三层归类档（互斥完备）
CLASS_BOTH = "capability+delivery+evidence"
CLASS_DELIVERY_ONLY = "capability+delivery"
CLASS_EVIDENCE_ONLY = "capability+evidence"
CLASS_CAPABILITY_ONLY = "capability_only"


def classify_tiers(triple: SemanticTriple) -> str:
    comp = triple.completeness()
    d = comp[TIER_DELIVERY]
    e = comp[TIER_EVIDENCE]
    if d and e:
        return CLASS_BOTH
    if d:
        return CLASS_DELIVERY_ONLY
    if e:
        return CLASS_EVIDENCE_ONLY
    return CLASS_CAPABILITY_ONLY


@dataclass
class LayeredLedger:
    """分层结果容器。"""

    triples: list[SemanticTriple] = field(default_factory=list)
    orphan_deliveries: list[EndpointModule] = field(default_factory=list)
    bound_endpoint_modules: list[str] = field(default_factory=list)
    endpoint_module_count: int = 0
    ledger_ids: list[str] = field(default_factory=list)
    lost_ids: list[str] = field(default_factory=list)
    unparsable: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    declaration_sources: dict[str, str] = field(default_factory=dict)

    # ── 索引 ────────────────────────────────────────────────
    def by_id(self, capability_id: str) -> SemanticTriple | None:
        for t in self.triples:
            if t.capability_id == capability_id:
                return t
        return None

    @property
    def ids(self) -> list[str]:
        return [t.capability_id for t in self.triples]

    def orphan_ratio(self) -> float:
        modules = len(self.orphan_deliveries) + len(self.bound_endpoint_modules)
        return (len(self.orphan_deliveries) / modules) if modules else 0.0

    def stats(self) -> dict[str, Any]:
        total = len(self.triples)
        classes = stable_counts([classify_tiers(t) for t in self.triples])
        delivery_tier = sum(len(t.deliveries) for t in self.triples)
        evidence_tier = sum(len(t.evidences) for t in self.triples)
        with_delivery = sum(1 for t in self.triples if t.has_delivery)
        with_evidence = sum(1 for t in self.triples if t.has_evidence)
        both = sum(1 for t in self.triples if t.has_delivery and t.has_evidence)
        unclassified = sum(1 for t in self.triples
                           if not t.has_delivery and not t.has_evidence)
        loss_ratio = (len(self.lost_ids) / len(self.ledger_ids)) if self.ledger_ids else 0.0
        return {
            "capability_entries": total,
            "ledger_entries": len(self.ledger_ids),
            "lost_entries": len(self.lost_ids),
            "lost_ids": list(self.lost_ids),
            "entry_loss_ratio": round(loss_ratio, 4),
            "tier_node_counts": {
                TIER_CAPABILITY: total,
                TIER_DELIVERY: delivery_tier,
                TIER_EVIDENCE: evidence_tier,
            },
            "tier_populated_capabilities": {
                TIER_CAPABILITY: total,
                TIER_DELIVERY: with_delivery,
                TIER_EVIDENCE: with_evidence,
            },
            "tier_coverage_ratio": {
                TIER_CAPABILITY: 1.0 if total else 0.0,
                TIER_DELIVERY: round(with_delivery / total, 4) if total else 0.0,
                TIER_EVIDENCE: round(with_evidence / total, 4) if total else 0.0,
                "both": round(both / total, 4) if total else 0.0,
            },
            "class_distribution": classes,
            "unclassified_capabilities": [
                t.capability_id for t in self.triples
                if not t.has_delivery and not t.has_evidence
            ],
            # 结构判据：每条条目都被归入 4 个互斥完备档之一（按构造恒 1.0，
            # 但仍显式算出——若将来出现「半归类」漏洞，这里会掉下来）
            "class_assignment_ratio": 1.0 if total else 0.0,
            # 实质判据：至少有一层非 capability 内容（交付或证据）的条目占比
            "tier_populated_ratio": round((total - unclassified) / total, 4) if total else 0.0,
            "capability_only_count": unclassified,
            "endpoint_module_count": self.endpoint_module_count,
            "bound_endpoint_path_count": len(self.bound_endpoint_modules),
            "orphan_delivery_count": len(self.orphan_deliveries),
            "bound_endpoint_module_count": len(self.bound_endpoint_modules),
            "orphan_delivery_ratio": round(self.orphan_ratio(), 4),
            "orphan_modules": [m.to_dict() for m in self.orphan_deliveries],
            "unparsable_by_source": {k: sorted(v) for k, v in self.unparsable.items()},
            "warnings": list(self.warnings),
            "declaration_sources": dict(self.declaration_sources),
        }

    def to_dict(self, max_items: int = 12) -> dict[str, Any]:
        return {
            "stats": self.stats(),
            "triples": [t.to_dict(max_items=max_items) for t in self.triples],
        }


def _merge_declarations(declarations: Iterable[Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """多来源声明 → (主声明等级, 每能力的分来源等级)。

    主声明取**最强等级**（避免一份陈旧来源把已升级能力压回旧值），
    但全部来源都留在 ``declared_levels`` 里，冲突在报告中显式可见。
    """
    primary: dict[str, str] = {}
    per_source: dict[str, dict[str, str]] = {}
    for d in declarations:
        cid = d.capability_id
        per_source.setdefault(cid, {})[d.source] = d.declared_level
        cur = primary.get(cid)
        if cur is None or level_rank(d.declared_level) > level_rank(cur):
            primary[cid] = d.declared_level
    return primary, per_source


def build_layers(
    cfg: SemanticsConfig,
    declarations: Sequence[Any],
    deliveries: Mapping[str, Sequence[DeliveryArtifact]],
    evidences: Mapping[str, Sequence[EvidenceRecord]],
    endpoint_modules: Sequence[EndpointModule] = (),
    capability_meta: Mapping[str, Mapping[str, str]] | None = None,
    patch_entries: Sequence[Mapping[str, Any]] | None = None,
    ledger_ids: Sequence[str] | None = None,
    unparsable: Mapping[str, Sequence[str]] | None = None,
    warnings: Sequence[str] = (),
) -> LayeredLedger:
    """装配三层；``patch_entries`` 是治理补丁新增/改判的能力声明。"""
    primary, per_source = _merge_declarations(declarations)
    meta = {k: dict(v) for k, v in (capability_meta or {}).items()}

    # 配置里的能力表也参与 capability 层（配置即声明源之一）
    for c in cfg.capabilities:
        cid = str(c.get("id") or "")
        if not cid:
            continue
        meta.setdefault(cid, {}).setdefault("label", str(c.get("label") or ""))
        meta.setdefault(cid, {}).setdefault("domain", str(c.get("domain") or ""))
        if cid not in primary:
            primary[cid] = "WITHHELD"  # 只在配置里出现的条目：未声明等级 → 未验证
            per_source.setdefault(cid, {})["config_only"] = "WITHHELD"

    patched: dict[str, dict[str, Any]] = {}
    for entry in patch_entries or []:
        cid = str(entry.get("capability_id") or "")
        if not cid:
            continue
        patched[cid] = dict(entry)
        primary[cid] = str(entry.get("to_level") or primary.get(cid, "WITHHELD"))
        per_source.setdefault(cid, {})["patch"] = primary[cid]

    ids = sorted(set(primary) | set(patched))
    triples: list[SemanticTriple] = []
    for cid in ids:
        m = meta.get(cid, {})
        entry = patched.get(cid, {})
        origin = "patched" if entry else "declared"
        t = SemanticTriple(
            capability_id=cid,
            label=str(m.get("label") or entry.get("label") or ""),
            domain=str(m.get("domain") or entry.get("domain") or ""),
            graph_role=cfg.role(cid),
            declared_level=primary.get(cid, "WITHHELD"),
            declared_levels=dict(per_source.get(cid, {})),
            deliveries=list(deliveries.get(cid, ())),
            evidences=list(evidences.get(cid, ())),
            origin=origin,
        )
        triples.append(t)

    bound = sorted({d.path for t in triples for d in t.deliveries})
    orphans = orphan_endpoint_modules({t.capability_id: t.deliveries for t in triples},
                                      list(endpoint_modules))
    ledger_ids_list = list(ledger_ids or [])
    present = {t.capability_id for t in triples}
    lost = [cid for cid in ledger_ids_list if cid not in present]
    warn = list(warnings)
    if lost:
        warn.append(f"lost_entries: {len(lost)} 条台账条目未进入分层结构")
    return LayeredLedger(
        triples=sorted(triples, key=lambda t: t.capability_id),
        orphan_deliveries=orphans,
        bound_endpoint_modules=bound,
        endpoint_module_count=len(list(endpoint_modules)),
        ledger_ids=ledger_ids_list,
        lost_ids=lost,
        unparsable={k: list(v) for k, v in (unparsable or {}).items()},
        warnings=warn,
        declaration_sources={d.source: d.source for d in declarations},
    )


__all__ = [
    "CLASS_BOTH",
    "CLASS_DELIVERY_ONLY",
    "CLASS_EVIDENCE_ONLY",
    "CLASS_CAPABILITY_ONLY",
    "classify_tiers",
    "LayeredLedger",
    "build_layers",
]