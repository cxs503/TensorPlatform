"""判据的可判性数据结构：四元组 (y, y_ref, τ, 口径) 与安全数学。

设计要点（为什么不能只用相对误差）
----------------------------------
一条判据的最小可判形式是四元组：

    y       被测量（实测/预测值）
    y_ref   基准（点值 / 区间 / 缺失）
    τ       容差
    口径     metric ∈ {relative, absolute, one_sided, ratio_bound, interval}
             + unit（量纲）+ condition（工况/有效域）

历史上平台用「相对误差 < τ%」一把尺子量所有判据。三类情形下这把尺子**不可判**：

T1  基准量级趋零：``(y-y_ref)/y_ref`` 在 ``y_ref→0`` 时无界，除零得 inf/NaN。
T2  被测量跨零/变号：``y`` 与 ``y_ref`` 异号时相对误差可以 >100%，且对
    符号共模平移不连续（微小的零点漂移换来判决翻转）。
T3  多口径并列无优先级：基准本身是区间（试验散布 / 上下限），或同一判据有
    「相对误差」「绝对误差」两套口径却没写谁优先——此时"误差"不是一个数。

本模块把上面三型写进数据模型（:class:`DegeneracyClass`），并提供**永不返回无界值**
的安全标量函数。判据对象是不可变的；任何缺失量用 ``None`` 表示而不是 0——
``None`` 必须显式传播成 ``UNDECIDABLE``，绝不被静默当成 0 参与除法。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Metric(str, Enum):
    """判据口径。"""

    RELATIVE = "relative"          # |y-y_ref| / |y_ref|
    ABSOLUTE = "absolute"          # |y-y_ref| <= τ（τ 与 y 同量纲）
    ONE_SIDED = "one_sided"        # 单边界：y >= target 或 y <= target
    RATIO_BOUND = "ratio_bound"    # 量比界：y/y_ref <= bound
    INTERVAL = "interval"          # 落到区间 [lo, hi]（区间即基准）
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNDECIDABLE = "UNDECIDABLE"    # 判据在该数据下不可判（不是失败，也不是通过）
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DegeneracyClass(str, Enum):
    NONE = "none"
    T1_ZERO_REFERENCE = "T1"        # 基准量级趋零
    T2_SIGN_CROSSING = "T2"         # 被测量跨零/变号
    T3_MULTI_METRIC = "T3"          # 多口径并列无优先级


@dataclass(frozen=True)
class Interval:
    """基准区间（试验散布 / 上下限）。区间是 T3 的典型来源。"""

    lo: float
    hi: float

    def __post_init__(self) -> None:
        if math.isnan(self.lo) or math.isnan(self.hi):
            raise ValueError("interval bounds must not be NaN")
        if self.hi < self.lo:
            lo, hi = self.hi, self.lo
            object.__setattr__(self, "lo", lo)
            object.__setattr__(self, "hi", hi)

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def mid(self) -> float:
        return 0.5 * (self.lo + self.hi)

    def as_dict(self) -> dict[str, float]:
        return {"lo": self.lo, "hi": self.hi, "mid": self.mid, "width": self.width}


@dataclass(frozen=True)
class Criterion:
    """一条判据的四元组 + 出处。

    ``scale`` 是归一化尺度（例如被测量自身的量级、或是工程上"有意义的最小变化"）。
    自适应判据族用它把分母从 ``|y_ref|`` 换成 ``max(|y_ref|, scale)``，
    从而在 ``y_ref→0`` 时不产生无界值。``scale`` 缺省时由 :func:`normalize_criterion`
    按 ``|y|``、``|y_ref|`` 的量级推出（并登记推导来源）。
    """

    criterion_id: str
    y: float | None = None
    y_ref: float | None = None
    tau: float | None = None
    metric: Metric = Metric.RELATIVE
    unit: str | None = None
    condition: str | None = None
    scale: float | None = None
    scale_source: str | None = None
    reference_interval: Interval | None = None
    comparator: str = "<="
    quantity: str | None = None
    declared_verdict: Verdict | None = None
    source: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    # ------------------------------------------------------------------ #
    def has_numeric_pair(self) -> bool:
        return self.y is not None and self.y_ref is not None

    def is_complete(self) -> bool:
        """四元组完整 = 有被测量、有基准、有容差。"""
        return self.tau is not None and self.y is not None and (
            self.y_ref is not None or self.reference_interval is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "y": self.y,
            "y_ref": self.y_ref,
            "tau": self.tau,
            "metric": self.metric.value,
            "unit": self.unit,
            "condition": self.condition,
            "scale": self.scale,
            "scale_source": self.scale_source,
            "reference_interval": self.reference_interval.as_dict()
            if self.reference_interval
            else None,
            "comparator": self.comparator,
            "quantity": self.quantity,
            "declared_verdict": self.declared_verdict.value if self.declared_verdict else None,
            "source": self.source,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Criterion":
        iv = d.get("reference_interval")
        return Criterion(
            criterion_id=d["criterion_id"],
            y=d.get("y"),
            y_ref=d.get("y_ref"),
            tau=d.get("tau"),
            metric=Metric(d.get("metric", "relative")),
            unit=d.get("unit"),
            condition=d.get("condition"),
            scale=d.get("scale"),
            scale_source=d.get("scale_source"),
            reference_interval=Interval(iv["lo"], iv["hi"]) if iv else None,
            comparator=d.get("comparator", "<="),
            quantity=d.get("quantity"),
            declared_verdict=Verdict(d["declared_verdict"]) if d.get("declared_verdict") else None,
            source=d.get("source") or {},
            notes=d.get("notes"),
        )


# --------------------------------------------------------------------------- #
# 安全标量函数：绝不返回 inf / NaN
# --------------------------------------------------------------------------- #


def _finite(x: float | None) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def magnitude_scale(y: float | None, y_ref: float | None) -> float:
    """推导归一化尺度：取两侧量级中较大者；两侧皆零时给 1.0（避免 0/0）。

    返回值恒 > 0 且有限，保证任何 ``something / scale`` 有界。
    """
    cands = [abs(v) for v in (y, y_ref) if _finite(v)]
    if not cands:
        return 1.0
    m = max(cands)
    if m == 0.0:
        return 1.0
    return m


def normalize_criterion(c: Criterion) -> Criterion:
    """补齐 scale / scale_source（幂等）。"""
    if _finite(c.scale) and c.scale > 0:
        return c
    scale = magnitude_scale(c.y, c.y_ref)
    return Criterion(
        criterion_id=c.criterion_id,
        y=c.y,
        y_ref=c.y_ref,
        tau=c.tau,
        metric=c.metric,
        unit=c.unit,
        condition=c.condition,
        scale=scale,
        scale_source="derived:max(|y|,|y_ref|)",
        reference_interval=c.reference_interval,
        comparator=c.comparator,
        quantity=c.quantity,
        declared_verdict=c.declared_verdict,
        source=c.source,
        notes=c.notes,
    )


def relative_error(y: float | None, y_ref: float | None, *, floor: float = 0.0) -> float | None:
    """安全相对误差 ``|y-y_ref| / max(|y_ref|, floor)``。

    - 任一侧缺失/非有限 → ``None``（显式不可算，调用方必须转成 UNDECIDABLE）
    - ``|y_ref| < floor`` 且 ``floor > 0`` → 分母被 floor 截断，返回值有界；
      调用方应同时登记这条判据已进入 T1 退化（见 :mod:`degeneracy`）。
    - ``floor == 0`` 且 ``y_ref == 0`` → 返回 ``None``（0 分母不可算），**绝不返回 inf**。
    """
    if not (_finite(y) and _finite(y_ref)):
        return None
    denom = abs(y_ref)
    if denom < floor:
        denom = floor
    if denom <= 0.0:
        return None
    val = abs(y - y_ref) / denom
    if not math.isfinite(val):
        return None
    return val


def safe_ratio(y: float | None, y_ref: float | None) -> float | None:
    """有界量比：``y / max(|y_ref|, scale)``；y_ref 为零时退化为有符号量级比。"""
    if not _finite(y):
        return None
    denom = abs(y_ref) if _finite(y_ref) else 0.0
    if denom <= 0.0:
        denom = magnitude_scale(y, y_ref)
    out = y / denom
    return out if math.isfinite(out) else None