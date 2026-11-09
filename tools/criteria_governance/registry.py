"""阈值注册表：判据入册、source_type 分类、derivation 链可追溯性、负控剔除。

设计原则
--------
1. **代码零领域语义**：判据条目（含阈值、来源类型、派生链）全部从
   ``data/criteria_governance/*.criteria.json`` 工件注入；本模块只实现通用校验规则。
2. **可裁可审计**：每条判据必须给出 ``probe``（可执行仪器）与 ``threshold``（可机器
   比对的算子+数值）；缺失即不可裁，写入 ``reasons`` 而不是静默通过。
3. **负控**：``cohort == "analytic"`` 的队列不允许混入 ``source_type != "analytic"`` 或
   派生方法落在经验族里的条目 —— 自动剔除（quarantine）并发告警，队列计数不上升。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

SOURCE_TYPES = ("analytic", "empirical", "heuristic", "standard")

#: 允许支撑 ``source_type=analytic`` 的派生方法（可解析/可复算族）
ANALYTIC_METHODS = ("analytic_identity", "refinement_scan", "standard_reference")

#: 其他来源类型允许的方法族
METHOD_FAMILY: Dict[str, Sequence[str]] = {
    "empirical": ("sampled_observation", "calibrated_fit", "analytic_identity", "refinement_scan"),
    "heuristic": ("expert_judgement", "sampled_observation"),
    "standard": ("standard_reference", "analytic_identity"),
}

THRESHOLD_OPS = ("<=", ">=", "<", ">", "==", "!=")

REQUIRED_FIELDS = ("id", "title", "statement", "cohort", "source_type",
                   "metric", "threshold", "derivation", "probe")


@dataclass
class ValidationFindings:
    """注册表校验结果（逐条判据可溯源）。"""

    registered: List[str] = field(default_factory=list)
    quarantined: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    expected_total: int = 0
    missing_field_count: int = 0
    traceable: List[str] = field(default_factory=list)
    untraceable: List[Dict[str, object]] = field(default_factory=list)
    chain_depth: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "registered": sorted(self.registered),
            "registered_total": len(self.registered),
            "expected_total": self.expected_total,
            "quarantined": self.quarantined,
            "quarantined_total": len(self.quarantined),
            "warnings": self.warnings,
            "missing_field_count": self.missing_field_count,
            "traceable": sorted(self.traceable),
            "untraceable": self.untraceable,
            "chain_depth": dict(sorted(self.chain_depth.items())),
        }


class ThresholdRegistry:
    """判据阈值的单一事实源（single source of truth）。"""

    def __init__(self) -> None:
        self._entries: Dict[str, Dict[str, object]] = {}
        self._spec_ids: List[str] = []
        self._spec_count = 0

    # ---------------------------------------------------------------- 装载 --
    @classmethod
    def from_specs(cls, paths: Iterable[str | Path]) -> "ThresholdRegistry":
        reg = cls()
        for path in paths:
            reg.add_spec(json.loads(Path(path).read_text(encoding="utf-8")))
        return reg

    def add_spec(self, spec: Dict[str, object]) -> None:
        criteria = spec.get("criteria") or []
        self._spec_count += len(criteria)
        for entry in criteria:
            cid = str(entry.get("id"))
            if cid in self._entries:
                # 重复 id：保留首条，记录冲突
                self._entries[cid].setdefault("_duplicate_ids", []).append(entry)
                continue
            record = dict(entry)
            record["_bundle_id"] = spec.get("bundle_id")
            self._entries[cid] = record
            self._spec_ids.append(cid)

    # ------------------------------------------------------------- 访问器 --
    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, cid: object) -> bool:
        return cid in self._entries

    def ids(self) -> List[str]:
        return list(self._entries)

    def get(self, cid: str) -> Dict[str, object]:
        return self._entries[cid]

    def entries(self) -> List[Dict[str, object]]:
        return [self._entries[cid] for cid in self.ids()]

    def by_source_type(self, source_type: str) -> List[str]:
        return [cid for cid in self.ids()
                if self._entries[cid].get("source_type") == source_type]

    def by_cohort(self, cohort: str) -> List[str]:
        return [cid for cid in self.ids() if self._entries[cid].get("cohort") == cohort]

    # --------------------------------------------------------------- 校验 --
    def validate(self) -> ValidationFindings:
        findings = ValidationFindings(expected_total=self._spec_count)
        for cid in self.ids():
            entry = self._entries[cid]
            reasons: List[str] = []

            missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
            if missing:
                findings.missing_field_count += len(missing)
                reasons.append(f"missing_fields:{','.join(sorted(missing))}")

            source_type = entry.get("source_type")
            if source_type not in SOURCE_TYPES:
                reasons.append(f"unknown_source_type:{source_type}")

            cohort = entry.get("cohort")
            if cohort and source_type and cohort != source_type:
                reasons.append(f"cohort_source_mismatch:{cohort}!={source_type}")
                if cohort == "analytic":
                    findings.warnings.append(
                        f"[负控] {cid} 申报 cohort=analytic 但 source_type={source_type}；"
                        f"已剔除出分析型队列（队列计数不上升）")

            threshold = entry.get("threshold") or {}
            if not isinstance(threshold, dict) or "value" not in threshold:
                reasons.append("threshold_value_missing")
            elif threshold.get("op") not in THRESHOLD_OPS:
                reasons.append(f"threshold_op_invalid:{threshold.get('op')}")

            derivation = entry.get("derivation") or {}
            if not isinstance(derivation, dict):
                reasons.append("derivation_not_object")
                derivation = {}
            method = derivation.get("method")
            if source_type == "analytic" and method not in ANALYTIC_METHODS:
                # 负控主判据：分析型队列里混入经验/判断型派生 → 剔除 + 告警
                reasons.append(f"analytic_set_poisoned:method={method}")
                findings.warnings.append(
                    f"[负控] {cid} 声明 cohort=analytic 但派生方法 method={method} "
                    f"不属可解析族 {list(ANALYTIC_METHODS)}；已剔除出分析型队列")
            elif source_type in METHOD_FAMILY and method and method not in METHOD_FAMILY[source_type]:
                findings.warnings.append(
                    f"{cid} 派生方法 {method} 不在 {source_type} 常用族内（保留，标注告警）")
            if source_type == "analytic" and not str(derivation.get("reference") or "").strip():
                reasons.append("analytic_reference_missing")

            probe = entry.get("probe") or {}
            if not isinstance(probe, dict) or not probe.get("name"):
                reasons.append("probe_missing")

            parents = list(derivation.get("parents") or [])
            unresolved = [p for p in parents if p not in self._entries]
            if unresolved:
                if source_type == "analytic":
                    reasons.append("parent_unresolved:" + ",".join(unresolved))
                else:
                    findings.warnings.append(
                        f"{cid} 的派生父判据未在本注册表解析：{unresolved}（非分析型，仅告警）")

            if entry.get("_duplicate_ids"):
                findings.warnings.append(f"{cid} 存在重复条目 {len(entry['_duplicate_ids'])} 条；保留首条")

            if reasons:
                findings.quarantined.append({"id": cid, "reasons": reasons})
            else:
                findings.registered.append(cid)

        self._trace_chains(findings)
        return findings

    def _trace_chains(self, findings: ValidationFindings) -> None:
        """派生链可追溯性：父判据全部入册、链条无环、链深可算。"""
        registered = set(findings.registered)

        def depth(cid: str, seen: Optional[set] = None) -> int:
            seen = set() if seen is None else seen
            if cid in seen:
                raise ValueError(f"derivation cycle at {cid}")
            seen = seen | {cid}
            parents = list((self._entries[cid].get("derivation") or {}).get("parents") or [])
            if not parents:
                return 0
            return 1 + max(depth(p, seen) for p in parents)

        for cid in sorted(registered):
            try:
                findings.chain_depth[cid] = depth(cid)
                findings.traceable.append(cid)
            except ValueError as exc:  # 环
                findings.untraceable.append({"id": cid, "reason": str(exc)})
                findings.warnings.append(f"{cid} 派生链成环：{exc}")

    # ----------------------------------------------------------- KPI 汇总 --
    def kpis(self, findings: Optional[ValidationFindings] = None) -> Dict[str, object]:
        findings = findings or self.validate()
        registered = findings.registered
        total = len(registered) or 1

        missing_source = sum(1 for cid in registered if not self._entries[cid].get("source_type"))
        analytic = [cid for cid in registered if self._entries[cid].get("source_type") == "analytic"]
        empirical = [cid for cid in registered if self._entries[cid].get("source_type") == "empirical"]
        heuristic = [cid for cid in registered if self._entries[cid].get("source_type") == "heuristic"]
        adjudicable = [cid for cid in registered
                       if (self._entries[cid].get("probe") or {}).get("name")
                       and (self._entries[cid].get("threshold") or {}).get("value") is not None]

        expected = findings.expected_total or len(registered)
        return {
            "expected_total": expected,
            "registered_total": len(registered),
            "coverage": len(registered) / expected if expected else 0.0,
            "source_type_missing": missing_source,
            "source_type_completeness": 1.0 - missing_source / total,
            "traceability_rate": len(findings.traceable) / total,
            "adjudicable_total": len(adjudicable),
            "adjudicable_rate": len(adjudicable) / total,
            "analytic_total": len(analytic),
            "empirical_total": len(empirical),
            "heuristic_total": len(heuristic),
            "quarantined_total": len(findings.quarantined),
        }


def compare_op(value: float, op: str, target: float, tol: float = 1e-9) -> bool:
    """阈值算子求值（``==`` 用数值容差）。"""
    if op == "<=":
        return value <= target
    if op == ">=":
        return value >= target
    if op == "<":
        return value < target
    if op == ">":
        return value > target
    if op == "==":
        return abs(value - target) <= tol
    if op == "!=":
        return abs(value - target) > tol
    raise ValueError(f"unsupported threshold op {op!r}")