"""基准执行器：跑实现/基线/独立参考，逐例算相对误差并按预声明判据裁决。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .criteria import AcceptanceCriteria, PREDECLARED
from .validators import ProbeRegistry


def rel_error_pct(computed: Any, reference: Any, floor: float = 0.0) -> float:
    """相对误差（%）。

    ``floor`` = 零点尺度：|reference| ≤ floor 时相对误差无定义（分母趋 0），
    改用 floor 作分母的声明尺度误差——尺度在判据里预声明，不事后调。
    floor=0 时退化为纯相对误差，参考值恰为 0 则 0/0 判 0、非零判 inf（保守）。
    """
    c = float(computed)
    r = float(reference)
    if not (math.isfinite(c) and math.isfinite(r)):
        return float("inf")
    denom = max(abs(r), float(floor))
    if denom == 0.0:
        return 0.0 if abs(c) == 0.0 else float("inf")
    return abs(c - r) / denom * 100.0


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    computed: float
    reference: float
    error_pct: float
    baseline: Optional[float]
    baseline_error_pct: Optional[float]
    passed: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"case_id": self.case_id, "computed": self.computed,
                "reference": self.reference, "error_pct": self.error_pct,
                "baseline": self.baseline,
                "baseline_error_pct": (None if self.baseline_error_pct is None
                                       else self.baseline_error_pct),
                "passed": self.passed}


@dataclass
class TrialResult:
    capability_id: str
    implementation: str
    reference: str
    cases: List[CaseResult] = field(default_factory=list)
    verdict: str = "pending"
    reasons: List[str] = field(default_factory=list)
    independence: Dict[str, Any] = field(default_factory=dict)
    criteria_sha256: str = ""

    @property
    def max_error_pct(self) -> float:
        return max((c.error_pct for c in self.cases), default=float("inf"))

    @property
    def max_baseline_error_pct(self) -> Optional[float]:
        vals = [c.baseline_error_pct for c in self.cases if c.baseline_error_pct is not None]
        return max(vals) if vals else None

    @property
    def passed(self) -> bool:
        return self.verdict == "passed"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "implementation": self.implementation,
            "reference": self.reference,
            "n_cases": len(self.cases),
            "max_error_pct": self.max_error_pct,
            "max_baseline_error_pct": (None if self.max_baseline_error_pct is None
                                       else self.max_baseline_error_pct),
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "independence": self.independence,
            "criteria_sha256": self.criteria_sha256,
            "cases": [c.as_dict() for c in self.cases],
        }


def run_trial(spec: Mapping[str, Any], registry: ProbeRegistry,
              criteria: AcceptanceCriteria = PREDECLARED) -> TrialResult:
    """执行一项能力的升级基准。

    ``spec`` 键：capability_id / implementation / reference / cases / baseline?(可选)
    ``cases`` 为 ``[{"case_id": str, "args": [JSON 标量/数组...]}]``。
    判据全部来自 ``criteria``（预声明，不在运行期改）。
    """
    cap = str(spec["capability_id"])
    impl_name = str(spec["implementation"])
    ref_name = str(spec["reference"])
    cases = list(spec.get("cases") or [])
    result = TrialResult(capability_id=cap, implementation=impl_name, reference=ref_name,
                         criteria_sha256=criteria.sha256())

    indep = registry.independence(impl_name, ref_name)
    result.independence = indep
    impl = registry.get(impl_name)
    ref = registry.get(ref_name)
    baseline = registry.get(str(spec["baseline"])) if spec.get("baseline") else None

    for case in cases:
        args = list(case.get("args") or [])
        try:
            computed = float(impl.fn(*args))
        except Exception as exc:  # 实现炸了=该例失败，不是崩整个试验
            result.reasons.append(f"{case.get('case_id')}: implementation error {type(exc).__name__}: {exc}")
            result.cases.append(CaseResult(case_id=str(case.get("case_id")), computed=float("nan"),
                                           reference=float("nan"), error_pct=float("inf"),
                                           baseline=None, baseline_error_pct=None, passed=False))
            continue
        reference = float(ref.fn(*args))
        err = rel_error_pct(computed, reference, criteria.zero_reference_floor)
        base_val = base_err = None
        if baseline is not None:
            try:
                base_val = float(baseline.fn(*args))
                base_err = rel_error_pct(base_val, reference,
                                          criteria.zero_reference_floor)
            except Exception:
                base_val, base_err = None, None
        result.cases.append(CaseResult(case_id=str(case.get("case_id")), computed=computed,
                                       reference=reference, error_pct=err, baseline=base_val,
                                       baseline_error_pct=base_err,
                                       passed=err <= criteria.tolerance_pct))

    if len(result.cases) < criteria.min_cases:
        result.verdict = "rejected_insufficient_cases"
        result.reasons.append(f"用例数 {len(result.cases)} < 判据下界 {criteria.min_cases}")
        return result
    if criteria.require_independent_reference and not indep["independent"]:
        result.verdict = "rejected_not_independent"
        result.reasons.append(
            f"实现与参考提供方相同（{impl.provider}）→ 自我证明，不算独立验证")
        return result
    failing = [c.case_id for c in result.cases if not c.passed]
    if failing:
        result.verdict = "rejected_criteria"
        result.reasons.append(
            f"{len(failing)} 例相对误差 > {criteria.tolerance_pct}%：{failing[:5]}"
            f"（最大 {result.max_error_pct:.6g}%）")
        return result
    if criteria.require_baseline_improvement and baseline is not None:
        base_max = result.max_baseline_error_pct
        if base_max is not None and base_max <= criteria.tolerance_pct:
            result.verdict = "rejected_no_improvement"
            result.reasons.append(
                f"原实现最大误差 {base_max:.6g}% 已在容差内 → 不构成升级")
            return result
    result.verdict = "passed"
    result.reasons.append(
        f"{len(result.cases)} 例全部 ≤ {criteria.tolerance_pct}%（最大 "
        f"{result.max_error_pct:.6g}%），参考提供方 {ref.provider} 独立于 {impl.provider}")
    return result
