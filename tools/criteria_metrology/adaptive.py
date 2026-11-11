"""自适应判据族：在退化情形下把"不可判"换成"可判、有界、口径显式"的判据。

四条成员（每条都声明自己的适用前提与不可判条件）
-------------------------------------------------
``A1 magnitude_normalized``
    ``e = |y - y_ref| / max(|y_ref|, scale)``。分母被尺度截断 ⇒ ``y_ref→0`` 时仍有界。
    用于 T1。代价：当 ``|y_ref| ≪ scale`` 时判据变"松"，所以必须同时报 ``binding=scale``
    标记——判决的含义从"相对基准"变成"相对尺度"，报告必须写明。
``A2 log_ratio_band``
    ``|ln(y/y_ref)| ≤ ln(1+τ)``。对 y↔y_ref 交换对称，乘性误差的自然口径。
    只在 y、y_ref 同为正（或同为负）时可用；否则不可判（交回 T2 处理）。
``A3 signed_absolute``
    ``|y - y_ref| ≤ τ·scale``。把相对容差换算成"尺度上的绝对容差带"。
    用于 T2（跨零/变号）——判决不再依赖分母。
``A4 interval_containment``
    ``y ∈ [lo·(1-τ), hi·(1+τ)]``（区间为基准时；负值用对称展开）。
    用于 T3 的区间基准：不再问"误差是多少"，只问"是否落在基准带内"。
``A5 one_sided_bound``
    ``y ≥ target`` 或 ``y ≤ target``（判据本身是单边界时；如"压低 >90%"）。
    此时相对误差**根本不该被计算**——这是 T3 的另一种形态（口径错配）。

合并规则（多口径并列时的优先级，写死在 :func:`merge_verdicts`，可复算）：
    - 任一成员 FAIL → FAIL（严格优先，最严者胜）
    - 全部成员 PASS → PASS
    - 其余（有成员不可判但没有 FAIL） → UNDECIDABLE
绝不允许"取最好的一条"来放行；也绝不允许在没有可用成员时默认 PASS。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .degeneracy import DegeneracyClass, DegeneracyFinding, DegeneracyPolicy, classify_degeneracy
from .model import Criterion, Metric, Verdict, _finite, magnitude_scale, relative_error


@dataclass(frozen=True)
class AdaptiveResult:
    criterion_id: str
    family_member: str
    verdict: Verdict
    value: float | None            # 该成员口径下的"误差"数值（有界或无）
    bound: float | None            # 判决阈值（同口径）
    degeneracy: DegeneracyClass
    degenerate_naive: bool
    naive_relative: float | None
    naive_verdict: Verdict
    flipped: bool                  # 自适应判决与朴素相对误差判决是否不同
    binding: str | None            # 起决定作用的量："y_ref" / "scale" / "interval"
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "family_member": self.family_member,
            "verdict": self.verdict.value,
            "value": self.value,
            "bound": self.bound,
            "degeneracy": self.degeneracy.value,
            "degenerate_naive": self.degenerate_naive,
            "naive_relative": self.naive_relative,
            "naive_verdict": self.naive_verdict.value,
            "flipped": self.flipped,
            "binding": self.binding,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# 各成员的实现
# --------------------------------------------------------------------------- #


_ZERO_REF_TINY = 0.0


def _reference_is_zero(c: Criterion) -> bool:
    return (not _finite(c.y_ref)) or abs(c.y_ref) == 0.0


def declared_scale(c: Criterion) -> float | None:
    """只有**声明**的尺度才算数；``normalize_criterion`` 自动推导的尺度不可用于零基准判据。"""
    if _finite(c.scale) and c.scale > 0 and not str(c.scale_source or "").startswith("derived:"):
        return float(c.scale)
    return None


def _a1_magnitude_normalized(c: Criterion) -> tuple[Verdict, float | None, float | None, str, tuple[str, ...]]:
    """量级归一化：分母取 max(|y_ref|, scale)。

    基准恰为零时**必须**由调用方声明 ``scale``（该判据"有意义的量级"）。否则归一化会
    退化：``|y - 0| / max(0, |y|) == 1`` 恒成立，任何 τ<1 都判 FAIL——这等于把"残差量"
    当成"相对误差"，是另一种不可判。故缺 scale 时返回 UNDECIDABLE，不放行也不误判。
    """
    if c.tau is None or not _finite(c.y):
        return Verdict.UNDECIDABLE, None, None, "none", ("missing y or tau",)
    if not _finite(c.y_ref) and c.reference_interval is None:
        return Verdict.NOT_APPLICABLE, None, None, "none", ("no_reference: 无基准即无归一化误差",)
    _ds = declared_scale(c)
    if _reference_is_zero(c) and _ds is None:
        return (
            Verdict.UNDECIDABLE,
            None,
            c.tau,
            "scale",
            ("zero_reference_requires_declared_scale: 基准为零且未声明量级，归一化无意义",),
        )
    scale = _ds if _ds is not None else magnitude_scale(c.y, c.y_ref)
    if not _finite(c.y_ref):
        # 基准缺失：只能用尺度判"是否显著偏离 0"
        val = abs(c.y) / scale
        binding = "scale"
    else:
        denom = max(abs(c.y_ref), scale)
        val = abs(c.y - c.y_ref) / denom
        binding = "y_ref" if abs(c.y_ref) >= scale else "scale"
    if not math.isfinite(val):
        return Verdict.UNDECIDABLE, None, c.tau, binding, ("non-finite value",)
    notes = ("binding=scale: 判据含义从'相对基准'退化为'相对尺度'" if binding == "scale" else "binding=y_ref",)
    return (Verdict.PASS if val <= c.tau else Verdict.FAIL), val, c.tau, binding, notes


def _a2_log_ratio_band(c: Criterion) -> tuple[Verdict, float | None, float | None, str, tuple[str, ...]]:
    """对数比对称带：|ln(y/y_ref)| <= ln(1+τ)（要求同号且非零）。"""
    if not _finite(c.y_ref):
        return Verdict.NOT_APPLICABLE, None, None, "none", ("no_reference",)
    if c.tau is None or not _finite(c.y) or c.tau <= -1.0:
        return Verdict.UNDECIDABLE, None, None, "none", ("need y, y_ref, τ>-1",)
    if c.y <= 0.0 or c.y_ref <= 0.0:
        return Verdict.UNDECIDABLE, None, None, "none", ("log-ratio 要求同号正值",)
    val = abs(math.log(c.y / c.y_ref))
    bound = abs(math.log1p(c.tau)) if c.tau > -1.0 else None
    if bound is None or not math.isfinite(val):
        return Verdict.UNDECIDABLE, None, None, "y_ref", ("non-finite",)
    return (Verdict.PASS if val <= bound else Verdict.FAIL), val, bound, "y_ref", ("乘性对称口径",)


def _a3_signed_absolute(c: Criterion) -> tuple[Verdict, float | None, float | None, str, tuple[str, ...]]:
    """带符号绝对量：|y-y_ref| <= τ·scale（基准为零时同样要求声明 scale）。"""
    if not _finite(c.y_ref):
        return Verdict.NOT_APPLICABLE, None, None, "none", ("no_reference",)
    if c.tau is None or not _finite(c.y):
        return Verdict.UNDECIDABLE, None, None, "none", ("need y, y_ref, τ",)
    _ds3 = declared_scale(c)
    if _reference_is_zero(c) and _ds3 is None:
        return (
            Verdict.UNDECIDABLE,
            None,
            None,
            "scale",
            ("zero_reference_requires_declared_scale",),
        )
    scale = _ds3 if _ds3 is not None else magnitude_scale(c.y, c.y_ref)
    val = abs(c.y - c.y_ref)
    bound = abs(c.tau) * scale
    if not (math.isfinite(val) and math.isfinite(bound)):
        return Verdict.UNDECIDABLE, None, None, "scale", ("non-finite",)
    return (Verdict.PASS if val <= bound else Verdict.FAIL), val, bound, "scale", (
        "绝对带宽 = τ × scale，判决不依赖分母符号",
    )


def _a4_interval_containment(c: Criterion) -> tuple[Verdict, float | None, float | None, str, tuple[str, ...]]:
    """区间包含：y ∈ [lo(1-τ), hi(1+τ)]（负值对称展开）。"""
    iv = c.reference_interval
    if iv is None or c.tau is None or not _finite(c.y):
        return Verdict.UNDECIDABLE, None, None, "none", ("need interval, y, τ",)
    tau = abs(c.tau)
    lo = iv.lo * (1.0 - tau) if iv.lo >= 0 else iv.lo * (1.0 + tau)
    hi = iv.hi * (1.0 + tau) if iv.hi >= 0 else iv.hi * (1.0 - tau)
    if hi < lo:
        lo, hi = hi, lo
    if lo <= c.y <= hi:
        val, verdict = 0.0, Verdict.PASS
    else:
        dist = lo - c.y if c.y < lo else c.y - hi
        width = max(iv.width, magnitude_scale(iv.mid, c.y) * 1e-12)
        val = dist / width
        verdict = Verdict.FAIL
    return verdict, val, 0.0, "interval", (
        "基准带 = [lo(1-τ), hi(1+τ)]；value 为越界距离 / 区间宽度（0 = 带内）",
    )


def _a5_one_sided(c: Criterion) -> tuple[Verdict, float | None, float | None, str, tuple[str, ...]]:
    """单边界判据：y >= target（comparator '>='）或 y <= target（'<='）。"""
    if c.tau is None or not _finite(c.y):
        return Verdict.UNDECIDABLE, None, None, "none", ("need y, target(τ)",)
    target = c.tau
    if c.comparator in (">=", ">", "≥"):
        return (Verdict.PASS if c.y >= target else Verdict.FAIL), c.y - target, target, "threshold", (
            "单边界判据：相对误差口径不适用",
        )
    return (Verdict.PASS if c.y <= target else Verdict.FAIL), c.y - target, target, "threshold", (
        "单边界判据：相对误差口径不适用",
    )


_MEMBERS = {
    "A1": _a1_magnitude_normalized,
    "A2": _a2_log_ratio_band,
    "A3": _a3_signed_absolute,
    "A4": _a4_interval_containment,
    "A5": _a5_one_sided,
}


def family_for(degeneracy: DegeneracyClass, c: Criterion | None = None) -> tuple[str, ...]:
    """退化类型 → 首选判据族顺序。第一条是主判据，其余为交叉核对。

    良态判据也要看**口径**：单边界判据（``metric=one_sided``）套相对误差毫无意义，
    它本身就是"口径错配"的一种，故走 A5。绝对容差判据（``metric=absolute``）走 A3，
    因为它的容差与 y 同量纲，直接比差值即可。
    """
    if degeneracy is DegeneracyClass.T1_ZERO_REFERENCE:
        return ("A1", "A3")
    if degeneracy is DegeneracyClass.T2_SIGN_CROSSING:
        return ("A3", "A1")
    if degeneracy is DegeneracyClass.T3_MULTI_METRIC:
        if c is not None and c.reference_interval is not None:
            return ("A4", "A2")
        if c is not None and c.metric is Metric.ONE_SIDED:
            return ("A5", "A1")
        return ("A1", "A2")
    if c is not None and c.metric is Metric.ONE_SIDED:
        return ("A5", "A1")
    if c is not None and c.metric is Metric.ABSOLUTE:
        return ("A3", "A1")
    return ("A1", "A2")


def merge_verdicts(verdicts: list[Verdict]) -> Verdict:
    """多成员合并规则（严格优先 + 不适用豁免）：

    1. 任一 ``FAIL`` → ``FAIL``（最严者胜，绝不"取最好的一条"放行）
    2. 任一 ``UNDECIDABLE``（数据缺失/需声明尺度）→ ``UNDECIDABLE``
    3. 有 ``PASS`` 且无 UNDECIDABLE → ``PASS``（``NOT_APPLICABLE`` 成员被豁免：
       例如单边界判据下"对数比带"根本不适用，它的不适用不该掩盖主判据的结论）
    4. 全部 ``NOT_APPLICABLE`` → ``UNDECIDABLE``
    """
    if not verdicts:
        return Verdict.UNDECIDABLE
    if any(v is Verdict.FAIL for v in verdicts):
        return Verdict.FAIL
    if any(v is Verdict.UNDECIDABLE for v in verdicts):
        return Verdict.UNDECIDABLE
    if any(v is Verdict.PASS for v in verdicts):
        return Verdict.PASS
    return Verdict.UNDECIDABLE


def naive_judgement(c: Criterion) -> tuple[Verdict, float | None]:
    """历史口径（朴素相对误差）判决；退化时返回 UNDECIDABLE 而不是 inf。"""
    if c.tau is None:
        return Verdict.UNDECIDABLE, None
    err = relative_error(c.y, c.y_ref)
    if err is None:
        return Verdict.UNDECIDABLE, None
    return (Verdict.PASS if err <= c.tau else Verdict.FAIL), err


def evaluate_family(
    c: Criterion,
    policy: DegeneracyPolicy | None = None,
    finding: DegeneracyFinding | None = None,
) -> AdaptiveResult:
    """对一条判据执行完整评估：退化分类 → 自适应族 → 合并判决 → 与朴素判决对照。"""
    f = finding or classify_degeneracy(c, policy)
    members = family_for(f.degeneracy, c)
    results = []
    details = []
    for m in members:
        verdict, val, bound, binding, notes = _MEMBERS[m](c)
        results.append(verdict)
        details.append(
            {"member": m, "verdict": verdict.value, "value": val, "bound": bound, "binding": binding, "notes": list(notes)}
        )
    merged = merge_verdicts(results)
    primary = details[0]
    naive_v, naive_err = naive_judgement(c)
    flipped = merged is not naive_v
    return AdaptiveResult(
        criterion_id=c.criterion_id,
        family_member="+".join(members),
        verdict=merged,
        value=primary["value"],
        bound=primary["bound"],
        degeneracy=f.degeneracy,
        degenerate_naive=f.is_degenerate,
        naive_relative=naive_err,
        naive_verdict=naive_v,
        flipped=flipped,
        binding=primary["binding"],
        notes=tuple(primary["notes"]) + tuple(f.reasons),
    )


def family_table(results: list[AdaptiveResult]) -> dict[str, Any]:
    """族级汇总：每个成员的命中数、翻转数、不可判数。"""
    by_member: dict[str, dict[str, int]] = {}
    for r in results:
        d = by_member.setdefault(r.family_member, {"n": 0, "PASS": 0, "FAIL": 0, "UNDECIDABLE": 0, "flipped": 0})
        d["n"] += 1
        d[r.verdict.value] += 1
        d["flipped"] += int(r.flipped)
    return {
        "n": len(results),
        "by_family": by_member,
        "n_flipped": sum(int(r.flipped) for r in results),
        "n_undecidable": sum(1 for r in results if r.verdict is Verdict.UNDECIDABLE),
    }