"""能力台账语义三元分层的数据模型（capability / delivery / evidence）。

三层定义（本仓口径，域数据全部外部注入）：

* **capability 层**：平台声称「能做什么」（一条能力声明，带声明等级）。
* **delivery 层**：具体交付物——代码文件、测试文件、在线端点模块、已验证工件。
  每条交付物带文件 sha256、命中行号，可机器复核。
* **evidence 层**：验证证据——审计报告的裁决、带数值判据的验证工件。

三层靠 ``SemanticTriple`` 绑定：每条能力一行三元组，缺层即显式记缺，
不做「有代码就算有证据」的隐式放行。本模块零领域语义：不合任何能力 id 字面量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: 证据等级（有序，rank 越大证据越强）
LEVELS: tuple[str, ...] = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")

#: 等级 → 序
LEVEL_RANK: dict[str, int] = {lv: i for i, lv in enumerate(LEVELS)}

#: 交付物种类
DELIVERY_KINDS: tuple[str, ...] = ("code", "test", "verified", "doc", "endpoint")

#: 三元层名
TIER_CAPABILITY = "capability"
TIER_DELIVERY = "delivery"
TIER_EVIDENCE = "evidence"


def level_rank(level: str | None) -> int:
    """等级 → 序；未知/None 记 -1（比 NOT_SUPPORTED 还弱，让它显式暴露）。"""
    if level is None:
        return -1
    return LEVEL_RANK.get(str(level), -1)


def min_level(a: str, b: str) -> str:
    """取两者中较弱等级（用于「上限」语义）。"""
    return a if level_rank(a) <= level_rank(b) else b


def max_level(a: str, b: str) -> str:
    return a if level_rank(a) >= level_rank(b) else b


@dataclass(frozen=True)
class DeliveryArtifact:
    """delivery 层单件交付物。

    Attributes
    ----------
    capability_id : str
        挂在哪条能力下；孤立交付物用合成 id（``unbound.<module>``）。
    kind : str
        code / test / verified / doc / endpoint。
    path : str
        仓内相对路径（POSIX 风格）。
    sha256 : str
        文件内容 sha256（空串表示未取到内容）。
    line : int
        命中行号（0 = 无行级命中）。
    quote : str
        命中行片段（截断），供人工复核。
    endpoint_count : int
        该文件内声明的路由端点数（仅 code/endpoint 类有意义）。
    numeric_asserts : int
        测试文件内数值断言计数（仅 test 类有意义）。
    stub_markers : tuple[str, ...]
        代码内空壳标记词命中。
    mock_markers : tuple[str, ...]
        代码内硬编码/假实现标记词命中。
    numeric_pass : bool | None
        已验证工件是否给出「通过」数值判决；None = 非验证工件。
    numeric_error_pct : float | None
        已验证工件的最小误差百分数；None = 无。
    subject_hit : bool
        工件自身主题是否命中该能力（路径词干或文件首段命中）。仅凭关键词共现
        判定的工件记 False，作为「归因可靠性」披露项，不进入判据。
    """

    capability_id: str
    kind: str
    path: str
    sha256: str = ""
    line: int = 0
    quote: str = ""
    endpoint_count: int = 0
    numeric_asserts: int = 0
    stub_markers: tuple[str, ...] = ()
    mock_markers: tuple[str, ...] = ()
    numeric_pass: bool | None = None
    numeric_error_pct: float | None = None
    subject_hit: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "line": self.line,
            "quote": self.quote,
            "endpoint_count": self.endpoint_count,
            "numeric_asserts": self.numeric_asserts,
            "stub_markers": list(self.stub_markers),
            "mock_markers": list(self.mock_markers),
            "numeric_pass": self.numeric_pass,
            "numeric_error_pct": self.numeric_error_pct,
            "subject_hit": self.subject_hit,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    """evidence 层单条证据（审计裁决或数值验证工件）。"""

    source: str
    ref: str
    stance: str = "neutral"  # supports_real / contradicts / supports / neutral
    asserted_level: str | None = None
    as_of: str | None = None
    note: str = ""
    sha256: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ref": self.ref,
            "stance": self.stance,
            "asserted_level": self.asserted_level,
            "as_of": self.as_of,
            "note": self.note,
            "sha256": self.sha256,
            "path": self.path,
        }


@dataclass
class Declaration:
    """某来源对某能力等级的声明（capability 层输入）。"""

    source: str
    capability_id: str
    declared_level: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "capability_id": self.capability_id,
            "declared_level": self.declared_level,
            "note": self.note,
        }


@dataclass
class SemanticTriple:
    """一条能力的语义三元组：capability + delivery* + evidence*。"""

    capability_id: str
    label: str = ""
    domain: str = ""
    graph_role: str = "domain"
    declared_level: str = "WITHHELD"
    declared_levels: dict[str, str] = field(default_factory=dict)
    deliveries: list[DeliveryArtifact] = field(default_factory=list)
    evidences: list[EvidenceRecord] = field(default_factory=list)
    derived_level: str = "WITHHELD"
    reason_code: str = ""
    accuracy_flag: str = ""
    origin: str = "declared"  # declared / patched

    # ── 派生视图 ────────────────────────────────────────────────
    def by_kind(self, kind: str) -> list[DeliveryArtifact]:
        return [d for d in self.deliveries if d.kind == kind]

    @property
    def code(self) -> list[DeliveryArtifact]:
        return self.by_kind("code")

    @property
    def tests(self) -> list[DeliveryArtifact]:
        return self.by_kind("test")

    @property
    def verified(self) -> list[DeliveryArtifact]:
        return self.by_kind("verified")

    @property
    def endpoints(self) -> int:
        return sum(max(0, d.endpoint_count) for d in self.deliveries)

    @property
    def numeric_asserts(self) -> int:
        return sum(max(0, d.numeric_asserts) for d in self.tests)

    @property
    def numeric_passes(self) -> list[DeliveryArtifact]:
        return [d for d in self.verified if d.numeric_pass is True]

    @property
    def numeric_passes_subject_backed(self) -> list[DeliveryArtifact]:
        """主题命中（自身即关于该能力）的数值通过工件——归因更可靠的那一部分。"""
        return [d for d in self.numeric_passes if d.subject_hit]

    @property
    def min_error_pct(self) -> float | None:
        errs = [d.numeric_error_pct for d in self.verified
                if isinstance(d.numeric_error_pct, (int, float))]
        return min(errs) if errs else None

    @property
    def has_delivery(self) -> bool:
        return bool(self.deliveries)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidences)

    def tier_flags(self) -> dict[str, int]:
        return {
            TIER_CAPABILITY: 1,
            TIER_DELIVERY: len(self.deliveries),
            TIER_EVIDENCE: len(self.evidences),
        }

    def completeness(self) -> dict[str, bool]:
        """三层是否各有内容（capability 层恒真，因为本行本身就是一条声明）。"""
        return {
            TIER_CAPABILITY: True,
            TIER_DELIVERY: self.has_delivery,
            TIER_EVIDENCE: self.has_evidence,
        }

    def to_dict(self, max_items: int = 12) -> dict[str, Any]:
        comp = self.completeness()
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "domain": self.domain,
            "graph_role": self.graph_role,
            "declared_level": self.declared_level,
            "declared_levels": dict(self.declared_levels),
            "derived_level": self.derived_level,
            "reason_code": self.reason_code,
            "accuracy_flag": self.accuracy_flag,
            "origin": self.origin,
            "tiers": {
                TIER_CAPABILITY: {"declared": True},
                TIER_DELIVERY: {
                    "count": len(self.deliveries),
                    "by_kind": {k: len(self.by_kind(k)) for k in DELIVERY_KINDS},
                    "endpoints": self.endpoints,
                    "numeric_asserts": self.numeric_asserts,
                    "sample": [d.to_dict() for d in self.deliveries[:max_items]],
                },
                TIER_EVIDENCE: {
                    "count": len(self.evidences),
                    "numeric_pass_count": len(self.numeric_passes),
                    "min_error_pct": self.min_error_pct,
                    "sample": [e.to_dict() for e in self.evidences[:max_items]],
                },
            },
            "layer_complete": comp,
            "unclassified": not comp[TIER_DELIVERY] and not comp[TIER_EVIDENCE],
        }


def normalise_level(value: Any) -> str | None:
    """把任意来源的等级字段归一成合法等级；不认识返回 None（不猜）。"""
    if value is None:
        return None
    s = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    return s if s in LEVEL_RANK else None


def stable_counts(items: Iterable[str]) -> dict[str, int]:
    """计数并按等级序排序输出（确定性，便于 sha256 锚稳定）。"""
    out: dict[str, int] = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return {k: out[k] for k in sorted(out, key=lambda x: (level_rank(x), x))}


__all__ = [
    "LEVELS",
    "LEVEL_RANK",
    "DELIVERY_KINDS",
    "TIER_CAPABILITY",
    "TIER_DELIVERY",
    "TIER_EVIDENCE",
    "level_rank",
    "min_level",
    "max_level",
    "DeliveryArtifact",
    "EvidenceRecord",
    "Declaration",
    "SemanticTriple",
    "normalise_level",
    "stable_counts",
]