"""升级路径锚：为每项未达 REAL 的能力生成四阶升级路径（实现→单测→基准→证据登记）。

路径模板域无关：阶段名/产出物/判据由判据对象参数化，能力 id 只作为数据标签。
域外能力（实现位于领域仓）标记为 blocked 并写明理由——**不假装已覆盖**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .criteria import AcceptanceCriteria, PREDECLARED
from .model import AuditRow, TARGET_LEVEL, level_rank

#: 四阶路径（顺序即依赖序；每阶都有可验收产出物）
STAGE_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("algorithm", "以第一性原理重写实现：删除硬编码/随机/常量返回，写入算法出处",
     "implementation module + provenance 注释", "桩指纹扫描无命中 MOCK 签名"),
    ("unit_test", "pytest 用例：解析解/不变量/守恒断言，覆盖退化与边界",
     "tests/**::test_* 用例", "用例全绿且至少一条断言对标独立基准"),
    ("benchmark", "独立提供方基准数据集 + 逐例误差表（不自我证明）",
     "benchmark_data.json（含 provider/seed/环境）", "逐例相对误差 ≤ 判据容差"),
    ("evidence_register", "证据链登记：测试报告 + 基准数据 + 误差分析，sha256 入账",
     "evidence_chain.json", "链条 kind 覆盖判据 required_chain 且 sha256 可复算"),
)

#: 升级路径上限（REAL 是终点，不允许「超过 REAL」）
TARGET = TARGET_LEVEL


@dataclass(frozen=True)
class UpgradePath:
    capability_id: str
    from_level: str
    to_level: str
    stages: Tuple[Dict[str, Any], ...]
    blockers: Tuple[str, ...] = ()
    criteria_sha256: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    def stage_status(self) -> Dict[str, str]:
        return {s["stage"]: s["status"] for s in self.stages}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "blocked": self.blocked,
            "blockers": list(self.blockers),
            "criteria_sha256": self.criteria_sha256,
            "stages": [dict(s) for s in self.stages],
        }


def plan_path(row: AuditRow, criteria: AcceptanceCriteria = PREDECLARED,
              blockers: Sequence[str] = (), done_stages: Iterable[str] = ()) -> UpgradePath:
    """单项能力的四阶路径。``blockers`` 非空 → 阶段状态 blocked（保留路径，不隐藏）。"""
    done = set(done_stages)
    stages: List[Dict[str, Any]] = []
    for name, action, artifact, gate in STAGE_SPECS:
        if name in done:
            status = "done"
        elif blockers:
            status = "blocked"
        else:
            status = "planned"
        stages.append({"stage": name, "action": action, "artifact": artifact,
                       "gate": gate, "status": status,
                       "tolerance_pct": criteria.tolerance_pct})
    return UpgradePath(capability_id=row.cap_id,
                       from_level=row.reconciled, to_level=TARGET,
                       stages=tuple(stages), blockers=tuple(blockers),
                       criteria_sha256=criteria.sha256())


def plan_paths(rows: Iterable[AuditRow], criteria: AcceptanceCriteria = PREDECLARED,
               blockers: Optional[Mapping[str, Sequence[str]]] = None,
               done: Optional[Mapping[str, Iterable[str]]] = None) -> List[UpgradePath]:
    """**全部**未达 REAL 的能力都出路径（保守取 self-declared ∪ reconciled 缺口的并集）。"""
    blockers = blockers or {}
    done = done or {}
    out: List[UpgradePath] = []
    for row in rows:
        if not row.upgradable:
            continue
        out.append(plan_path(row, criteria, blockers.get(row.cap_id, ()),
                             done.get(row.cap_id, ())))
    return out


def coverage(paths: Sequence[UpgradePath], rows: Iterable[AuditRow]) -> Dict[str, Any]:
    """A3 判据：MOCK/STUB/WITHHELD/NOT_SUPPORTED 能力升级路径覆盖率。"""
    required = sorted(r.cap_id for r in rows if r.upgradable)
    covered = sorted(p.capability_id for p in paths)
    missing = sorted(set(required) - set(covered))
    extra = sorted(set(covered) - set(required))
    full_stage = [p.capability_id for p in paths
                  if all(s["stage"] for s in p.stages) and len(p.stages) == len(STAGE_SPECS)]
    pct = 0.0 if not required else 100.0 * (len(set(required) & set(covered)) / len(required))
    return {
        "required_count": len(required),
        "covered_count": len(set(required) & set(covered)),
        "coverage_pct": round(pct, 2),
        "missing": missing,
        "unexpected": extra,
        "with_full_stage_set": len(full_stage),
        "blocked_by_domain_boundary": sorted(p.capability_id for p in paths if p.blocked),
        "pass": not missing and not extra and pct == 100.0,
    }
