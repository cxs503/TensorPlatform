"""判据退化分类器：T1 基准趋零 / T2 跨零变号 / T3 多口径并列。

分类只依赖四元组的数值关系与结构，不看任何领域词条。判定顺序是**结构性优先**：
先看是否多口径（T3，结构问题），再看符号（T2），最后看量级（T1）——
因为区间基准上的"量级趋零"本身没有意义，区间必须先被承认是 T3。

阈值全部来自 :data:`DegeneracyPolicy`，由配置注入（``config/criteria_metrology/policy.json``），
代码里不写死任何"魔法数字"以外的语义常量；默认值写在 dataclass 缺省里并进报告。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from .model import Criterion, DegeneracyClass, Metric, Verdict, _finite


@dataclass(frozen=True)
class DegeneracyPolicy:
    """退化判据的判定策略（全部为无量纲比值，与领域无关）。"""

    #: T1：|y_ref| 低于「被测量量级 × 该比例」即视为基准趋零
    zero_ref_rel_floor: float = 1e-6
    #: T1：绝对值下限（适配物理量普遍使用 SI 量级、但被测量本身可能 <1 的情形）
    zero_ref_abs_floor: float = 0.0
    #: T2：|y| 相对 scale 低于该比例视为"贴零"，此时相对误差主要由零点漂移决定
    sign_cross_rel_floor: float = 1e-3
    #: T2：相对误差的分母被 y_ref 量级放大到该倍数即视为失真（>100% 常见于变号）
    sign_cross_err_ratio: float = 1.0
    #: T3：同一判据登记的并列口径数（metric 或 reference）达到该值即退化
    multi_metric_min: int = 2

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "DegeneracyPolicy":
        if not d:
            return DegeneracyPolicy()
        known = {f: d[f] for f in d if f in DegeneracyPolicy.__dataclass_fields__}
        return DegeneracyPolicy(**known)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class DegeneracyFinding:
    """一条判据的退化判定 + 触发依据（可复算）。"""

    criterion_id: str
    degeneracy: DegeneracyClass
    reasons: tuple[str, ...]
    evidence: dict[str, Any]

    @property
    def is_degenerate(self) -> bool:
        return self.degeneracy is not DegeneracyClass.NONE

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "degeneracy": self.degeneracy.value,
            "reasons": list(self.reasons),
            "evidence": self.evidence,
        }


def _multiplicity(c: Criterion) -> tuple[int, list[str]]:
    """统计同一判据上并列的口径数（结构性问题）。

    来源三类：
    1. ``reference_interval`` 存在 → 基准不是单值（区间＝多个候选值，无优先级）
    2. ``source['alt_metrics']`` 显式登记了并列口径列表
    3. ``source['alt_references']`` 显式登记了并列基准列表
    """
    tags: list[str] = []
    count = 0
    if c.reference_interval is not None:
        # 区间基准本身就是"两个并列候选端点、没有优先级"，故计 2 个口径
        tags.append("reference:interval(lo,hi)")
        count += 2
    alt_m = c.source.get("alt_metrics") or []
    if isinstance(alt_m, (list, tuple)) and len(alt_m) >= 1:
        tags.append(f"metrics:{','.join(str(m) for m in alt_m)}")
        count += len(alt_m)
    alt_r = c.source.get("alt_references") or []
    if isinstance(alt_r, (list, tuple)) and len(alt_r) >= 1:
        tags.append(f"references:{len(alt_r)}")
        count += len(alt_r)
    return count, tags


def classify_degeneracy(
    c: Criterion,
    policy: DegeneracyPolicy | None = None,
) -> DegeneracyFinding:
    """对单条判据判定退化类型。

    判定顺序 **T3 → T1 → T2**：结构性问题（基准不是单值）先定；其次是分母趋零
    （此时符号问题无从谈起）；最后才是跨零/变号（含"分母失真"——相对误差已超过
    100%，量的是"偏离几个基准量级"而不是误差）。
    """
    pol = policy or DegeneracyPolicy()
    reasons: list[str] = []
    ev: dict[str, Any] = {}
    y, y_ref = c.y, c.y_ref

    # ---- T3：多口径并列（结构性优先） ---------------------------------- #
    n_alt, tags = _multiplicity(c)
    ev["multiplicity_tags"] = tags
    ev["multiplicity_count"] = n_alt
    if n_alt >= pol.multi_metric_min:
        iv = c.reference_interval
        if iv is not None:
            ev["interval"] = iv.as_dict()
            reasons.append(
                f"reference_is_interval: 基准是区间[{iv.lo:.6g}, {iv.hi:.6g}]，"
                "无单值基准即无单值相对误差"
            )
        if tags:
            reasons.append("parallel_metrics_without_priority: " + "; ".join(tags))
        return DegeneracyFinding(c.criterion_id, DegeneracyClass.T3_MULTI_METRIC, tuple(reasons), ev)

    # ---- T1：基准量级趋零 ------------------------------------------------ #
    if _finite(y_ref):
        scale = c.scale if _finite(c.scale) and c.scale > 0 else max(
            abs(y) if _finite(y) else 0.0, 1e-300
        )
        floor = max(pol.zero_ref_rel_floor * scale, pol.zero_ref_abs_floor)
        ev["abs_y_ref"] = abs(y_ref)
        ev["zero_ref_floor"] = floor
        if abs(y_ref) <= floor:
            reasons.append(
                f"reference_near_zero: |y_ref|={abs(y_ref):.6g} <= floor={floor:.6g}"
                "（相对误差分母趋零，除零→无界）"
            )
            return DegeneracyFinding(c.criterion_id, DegeneracyClass.T1_ZERO_REFERENCE, tuple(reasons), ev)
    elif c.reference_interval is None:
        ev["reason"] = "reference_missing"
        # 基准缺失不是"数值退化"，而是可判性缺失：交给调用方判 UNDECIDABLE
        return DegeneracyFinding(c.criterion_id, DegeneracyClass.NONE, (), ev)

    # ---- T2：跨零 / 变号 / 分母失真 -------------------------------------- #
    # 记录若为"由报告误差反推的 y"（方向不可知），符号类证据不成立，只保留分母失真证据。
    derived = bool((c.source or {}).get("derived_y"))
    if _finite(y) and _finite(y_ref):
        scale2 = c.scale if _finite(c.scale) and c.scale > 0 else max(abs(y), abs(y_ref), 1.0)
        ev["sign_y"] = math.copysign(1.0, y) if y != 0 else 0.0
        ev["sign_y_ref"] = math.copysign(1.0, y_ref) if y_ref != 0 else 0.0
        ev["abs_y_over_scale"] = abs(y) / scale2
        ev["abs_y_ref_over_scale"] = abs(y_ref) / scale2
        if (not derived) and y * y_ref < 0.0:
            reasons.append(
                f"sign_opposite: y={y:.6g} 与 y_ref={y_ref:.6g} 异号，"
                "相对误差大于 100% 且对符号漂移不连续"
            )
        elif (not derived) and (
            abs(y) / scale2 < pol.sign_cross_rel_floor
            and abs(y_ref) / scale2 >= pol.sign_cross_rel_floor
        ):
            reasons.append(
                f"measurement_near_zero: |y|/scale={abs(y) / scale2:.3g} < "
                f"{pol.sign_cross_rel_floor:.3g}，判决由零点漂移支配"
            )
        elif c.metric is Metric.RELATIVE and abs(y_ref) > 0.0:
            err = abs(y - y_ref) / abs(y_ref)
            ev["naive_relative_error"] = err
            if math.isfinite(err) and err > pol.sign_cross_err_ratio:
                reasons.append(
                    f"relative_error_exceeds_100pct: |y-y_ref|/|y_ref|={err:.3g} > "
                    f"{pol.sign_cross_err_ratio:.3g}，相对误差已退化为'偏离了几个基准量级'"
                )
        if reasons:
            return DegeneracyFinding(c.criterion_id, DegeneracyClass.T2_SIGN_CROSSING, tuple(reasons), ev)

    return DegeneracyFinding(c.criterion_id, DegeneracyClass.NONE, (), ev)


def degeneracy_report(
    criteria: Iterable[Criterion],
    policy: DegeneracyPolicy | None = None,
) -> dict[str, Any]:
    """批量退化测绘：分布 + 比例 + 逐条证据。"""
    findings = [classify_degeneracy(c, policy) for c in criteria]
    total = len(findings)
    counts: dict[str, int] = {k.value: 0 for k in DegeneracyClass}
    for f in findings:
        counts[f.degeneracy.value] += 1
    degenerate = total - counts[DegeneracyClass.NONE.value]
    return {
        "n_total": total,
        "n_degenerate": degenerate,
        "degenerate_rate": (degenerate / total) if total else 0.0,
        "counts": counts,
        "policy": (policy or DegeneracyPolicy()).as_dict(),
        "findings": [f.as_dict() for f in findings],
    }


def announced_verdict(c: Criterion) -> Verdict:
    """历史报告里声明的判决（用于对照"回放重判"是否翻转结论）。"""
    if c.declared_verdict is not None:
        return c.declared_verdict
    return Verdict.UNDECIDABLE