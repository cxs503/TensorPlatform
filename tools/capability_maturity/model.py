"""能力等级与审计行/表的数据模型（域无关）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

#: 证据等级（全序：REAL 最高；升级 = 沿本序向上）
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK: Dict[str, int] = {name: i for i, name in enumerate(LEVELS)}
TARGET_LEVEL = "REAL"
#: 尚未达到 REAL 的等级（升级路径必须覆盖的集合）
UPGRADABLE_LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD")


def level_rank(level: str) -> int:
    if level not in LEVEL_RANK:
        raise KeyError(f"未登记的等级 {level!r}（应在 {LEVELS} 内）")
    return LEVEL_RANK[level]


@dataclass(frozen=True)
class AuditRow:
    """一项能力在审计表里的一行（自述等级 vs 对账等级 vs 分歧类型）。"""

    cap_id: str
    declared: str
    reconciled: str
    divergence: str = "?"
    domain: str = ""

    @property
    def upgradable(self) -> bool:
        """是否需要升级路径：任一来源等级未达 REAL 就要有路径（保守取并集）。"""
        return self.reconciled in UPGRADABLE_LEVELS or self.declared in UPGRADABLE_LEVELS

    @property
    def is_real(self) -> bool:
        """负控对象：对账与自述都为 REAL 的能力。"""
        return self.reconciled == TARGET_LEVEL and self.declared == TARGET_LEVEL

    @property
    def gap_rank(self) -> int:
        return level_rank(TARGET_LEVEL) - level_rank(self.reconciled)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.cap_id,
            "declared_level": self.declared,
            "reconciled_level": self.reconciled,
            "divergence": self.divergence,
            "domain": self.domain,
            "upgradable": self.upgradable,
            "gap_rank": self.gap_rank,
        }


@dataclass
class AuditTable:
    """审计表：行 + 渲染器自报 + 真值来源/契约（可回溯到 audit_capabilities 输出）。"""

    rows: List[AuditRow] = field(default_factory=list)
    counted: Optional[int] = None
    source_path: str = ""
    contract: Dict[str, str] = field(default_factory=dict)
    text_sha256: str = ""

    def __len__(self) -> int:
        return len(self.rows)

    def by_id(self) -> Dict[str, AuditRow]:
        return {r.cap_id: r for r in self.rows}

    def upgradable_rows(self) -> List[AuditRow]:
        return [r for r in self.rows if r.upgradable]

    def real_rows(self) -> List[AuditRow]:
        return [r for r in self.rows if r.is_real]

    def level_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {lvl: 0 for lvl in LEVELS}
        for r in self.rows:
            out[r.reconciled] = out.get(r.reconciled, 0) + 1
        return out

    def self_consistent(self) -> Dict[str, Any]:
        """渲染器自报条数必须等于解析出的行数（仪器自检，防止静默漏行）。"""
        return {
            "counted": self.counted,
            "parsed": len(self.rows),
            "unique_ids": len({r.cap_id for r in self.rows}),
            "ok": self.counted == len(self.rows) == len({r.cap_id for r in self.rows}),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rows": [r.as_dict() for r in self.rows],
            "counted": self.counted,
            "source_path": self.source_path,
            "contract": dict(self.contract),
            "text_sha256": self.text_sha256,
            "level_counts": self.level_counts(),
            "self_consistent": self.self_consistent(),
        }


def level_of(levels: Mapping[str, str], cap_id: str) -> Optional[str]:
    return levels.get(cap_id)
