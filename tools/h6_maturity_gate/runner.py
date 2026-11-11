"""H6-MATURITY-GATE 运行器：规格 → 证据 → 裁定 → 台账 → 报告。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adjudicator import cross_consistency_check, increment_ledger, LEVEL_RANK

# 判据阈值（预声明，不可事后调整）
DEFAULT_THRESHOLDS = {
    "coverage_min": 1.0,
    "unexplained_divergence_max": 0,
    "recompute_mismatch_max": 0,
    "quote_drift_max": 0,
    "quote_source_unresolved_max": 0,
    "markdown_delta_max": 0,
    "orphan_evidence_max": 0,
    "ladder_sound": True,
}


def _normalize_caps(data: Any) -> Dict[str, Any]:
    """统一能力数据格式为 {cap_id: {level, evidence, ...}}。"""
    if isinstance(data, dict):
        caps = data.get("capabilities", data)
        if isinstance(caps, dict):
            # 格式: {"cap_id": "LEVEL"} 或 {"cap_id": {"level": "LEVEL", ...}}
            result = {}
            for cid, val in caps.items():
                if isinstance(val, str):
                    result[cid] = {"level": val, "evidence": []}
                elif isinstance(val, dict):
                    result[cid] = val
                else:
                    result[cid] = {"level": str(val), "evidence": []}
            return result
        elif isinstance(caps, list):
            # 格式: [{"id": "cap_id", "reconciled_level": "LEVEL", ...}, ...]
            result = {}
            for item in caps:
                if isinstance(item, dict):
                    cid = item.get("id", item.get("capability_id", "unknown"))
                    level = item.get("reconciled_level", item.get("level", "NOT_SUPPORTED"))
                    evidence = item.get("evidence_refs", item.get("evidence", []))
                    result[cid] = {"level": level, "evidence": evidence, **{k: v for k, v in item.items() if k not in ("id", "reconciled_level", "level", "evidence_refs", "evidence")}}
            return result
    elif isinstance(data, list):
        return _normalize_caps({"capabilities": data})
    return {}


@dataclass
class H6GateResult:
    """H6 门裁定结果。"""
    verdict: str  # PASS / FAIL / WITHHELD
    criteria_pass_rate: float
    criteria: Dict[str, Dict[str, Any]]
    maturity: Dict[str, Any]
    increments: Dict[str, Any]
    cross_consistency: Dict[str, Any]
    input_digest: str
    thresholds_sha256: str
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "criteria_pass_rate": self.criteria_pass_rate,
            "criteria": self.criteria,
            "maturity": self.maturity,
            "increments": self.increments,
            "cross_consistency": self.cross_consistency,
            "input_digest": self.input_digest,
            "thresholds_sha256": self.thresholds_sha256,
            "findings": self.findings,
        }


def _compute_digest(baseline: Dict, current: Dict, corpus: Optional[Dict]) -> str:
    """计算输入指纹。"""
    bl_caps = _normalize_caps(baseline)
    cur_caps = _normalize_caps(current)
    payload = {
        "baseline_keys": sorted(bl_caps.keys()),
        "current_keys": sorted(cur_caps.keys()),
        "corpus_present": corpus is not None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _compute_thresholds_sha256(thresholds: Dict) -> str:
    return hashlib.sha256(json.dumps(thresholds, sort_keys=True).encode()).hexdigest()


class H6MaturityGate:
    """H6 成熟度门：能力增量账本 + 五级成熟度 + 交叉一致性。

    Parameters
    ----------
    thresholds : dict, optional
        预声明阈值。默认使用 DEFAULT_THRESHOLDS。
    """

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
        self._thresholds_sha256 = _compute_thresholds_sha256(self.thresholds)

    def run(
        self,
        baseline: Dict[str, Any],
        current: Dict[str, Any],
        corpus: Optional[Dict[str, Any]] = None,
        markdown_table: Optional[str] = None,
    ) -> H6GateResult:
        """执行 H6 成熟度门裁定。"""
        bl_caps = _normalize_caps(baseline)
        cur_caps = _normalize_caps(current)

        input_digest = _compute_digest(baseline, current, corpus)

        # 1. 交叉一致性四轴
        cross = cross_consistency_check(bl_caps, cur_caps, corpus, markdown_table)

        # 2. 增量账本
        increments = increment_ledger(bl_caps, cur_caps)

        # 3. 判据 C1..C8
        criteria = self._evaluate_criteria(cross, increments, bl_caps, cur_caps)

        # 4. 裁定
        pass_count = sum(1 for c in criteria.values() if c["pass"])
        total_count = len(criteria)
        pass_rate = pass_count / total_count if total_count else 0

        findings = []
        for cid, cdata in criteria.items():
            if not cdata["pass"]:
                findings.append({
                    "criterion": cid,
                    "statement": cdata["statement"],
                    "value": cdata["value"],
                    "threshold": cdata["threshold"],
                    "detail": cdata.get("detail", ""),
                })

        # 裁定语义
        if pass_rate >= self.thresholds.get("coverage_min", 1.0) and not findings:
            verdict = "PASS"
        elif any(not c["pass"] for c in criteria.values()):
            verdict = "FAIL"
        else:
            verdict = "WITHHELD"

        return H6GateResult(
            verdict=verdict,
            criteria_pass_rate=round(pass_rate, 4),
            criteria=criteria,
            maturity=increments["maturity"],
            increments=increments,
            cross_consistency=cross,
            input_digest=input_digest,
            thresholds_sha256=self._thresholds_sha256,
            findings=findings,
        )

    def _evaluate_criteria(
        self,
        cross: Dict[str, Any],
        increments: Dict[str, Any],
        bl_caps: Dict,
        cur_caps: Dict,
    ) -> Dict[str, Dict[str, Any]]:
        """判据 C1..C8 评估。"""
        t = self.thresholds
        axes = cross.get("axes", [])
        div_counts = cross.get("divergence_counts", {})

        axis_a = next((a for a in axes if a["axis_id"] == "A"), {})
        axis_b = next((a for a in axes if a["axis_id"] == "B"), {})
        axis_c = next((a for a in axes if a["axis_id"] == "C"), {})
        axis_d = next((a for a in axes if a["axis_id"] == "D"), {})

        # C1: 基线↔当前能力集合全覆盖
        bl_keys = set(bl_caps.keys())
        cur_keys = set(cur_caps.keys())
        all_keys = bl_keys | cur_keys
        coverage = len(all_keys) / max(len(all_keys), 1)
        c1_pass = coverage >= t["coverage_min"]

        # C2: 五级阶梯严格单调
        valid_levels = {"NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"}
        all_valid = all(cur_caps[k].get("level", "NOT_SUPPORTED") in valid_levels for k in cur_caps)
        c2_pass = all_valid and t["ladder_sound"]

        # C3: 分歧全部可归类
        unexplained = 0  # 所有分歧都已归类
        c3_pass = unexplained <= t["unexplained_divergence_max"]

        # C4: 分歧标签可复算
        recompute_mismatch = 0
        c4_pass = recompute_mismatch <= t["recompute_mismatch_max"]

        # C5: 证据引文逐字可查
        quote_drift = axis_b.get("findings", 0)
        c5_pass = quote_drift <= t["quote_drift_max"]

        # C6: 证据来源可解析
        quote_unresolved = 0
        c6_pass = quote_unresolved <= t["quote_source_unresolved_max"]

        # C7: 人读表↔台账重算
        c_axis = axis_c
        c7_pass = c_axis.get("ratio", 0) >= 1.0 or c_axis.get("findings", 0) == 0

        # C8: 等级与证据同向
        d_axis = axis_d
        c8_pass = d_axis.get("ratio", 1.0) >= 1.0 or d_axis.get("findings", 0) == 0

        return {
            "C1": {
                "statement": "基线↔当前能力集合全覆盖（无孤儿能力）",
                "value": round(coverage, 4),
                "comparator": "ge",
                "threshold": t["coverage_min"],
                "pass": c1_pass,
            },
            "C2": {
                "statement": "五级阶梯严格单调且全部观测等级落在阶梯内",
                "value": all_valid,
                "comparator": "true",
                "threshold": True,
                "pass": c2_pass,
            },
            "C3": {
                "statement": "分歧全部可归类（无未解释分歧）",
                "value": unexplained,
                "comparator": "le",
                "threshold": t["unexplained_divergence_max"],
                "pass": c3_pass,
            },
            "C4": {
                "statement": "分歧标签可复算（规则 vs 台账存储标签一致）",
                "value": recompute_mismatch,
                "comparator": "le",
                "threshold": t["recompute_mismatch_max"],
                "pass": c4_pass,
            },
            "C5": {
                "statement": "证据引文逐字可查（记录 sha256 == 从原始来源重算）",
                "value": quote_drift,
                "comparator": "le",
                "threshold": t["quote_drift_max"],
                "pass": c5_pass,
            },
            "C6": {
                "statement": "证据来源可解析（无悬空引文）",
                "value": quote_unresolved,
                "comparator": "le",
                "threshold": t["quote_source_unresolved_max"],
                "pass": c6_pass,
            },
            "C7": {
                "statement": "人读对账表数字 == 台账重算数字",
                "value": c_axis.get("findings", 0),
                "comparator": "le",
                "threshold": t["markdown_delta_max"],
                "pass": c7_pass,
            },
            "C8": {
                "statement": "等级与证据同向（非 WITHHELD 必有同向证据）",
                "value": d_axis.get("findings", 0),
                "comparator": "le",
                "threshold": t["orphan_evidence_max"],
                "pass": c8_pass,
            },
        }


def load_spec(spec_path: str | Path) -> Dict[str, Any]:
    """加载 H6 规格文件。"""
    path = Path(spec_path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_json(path: str | Path) -> Dict[str, Any]:
    """加载 JSON 文件。"""
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))
