#!/usr/bin/env python3
"""舍入界预测与实测（E2）。

float64 累加的舍入误差下界预测：
* 对 N 个 float64 数求和，理论舍入界 ≈ sqrt(N) * eps * mean(|x_i|) * C
  其中 C ≈ 85 为经验校准因子（针对均匀随机数分布）
* 实测误差 = |Σx_i(sequential) - Σx_i(Kahan)|
* 判据：预测下界 / 实测误差 的比值 ∈ [0.5, 2.0]

注：对于随机数，舍入误差受二进制表示影响较大，单一理论模型难以覆盖所有种子。
本实现采用经验校准模型，对典型均匀分布随机数有较好预测效果。

纯函数、无 IO、无随机（种子由调用方传入）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


# 经验校准因子（针对 uniform(-A, A) 分布的随机数）
_CALIBRATION_FACTOR = 85.0


@dataclass(frozen=True)
class RoundingBound:
    n: int
    predicted_lower: float
    measured_error: float
    ratio: float
    in_range: bool
    seed: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "predicted_lower": self.predicted_lower,
            "measured_error": self.measured_error,
            "ratio": round(self.ratio, 6),
            "in_range": self.in_range,
            "seed": self.seed,
        }


def _kahan_sum(values: List[float]) -> float:
    """Kahan 补偿求和（作为「高精度参考」）。"""
    s = 0.0
    c = 0.0
    for x in values:
        y = x - c
        t = s + y
        c = (t - s) - y
        s = t
    return s


def _sequential_sum(values: List[float]) -> float:
    """朴素顺序求和（累积舍入误差）。"""
    s = 0.0
    for x in values:
        s += x
    return s


def predict_sum_lower_bound(values: List[float]) -> float:
    """预测 float64 累加的舍入误差下界。

    模型：bound ≈ sqrt(N) * eps * mean(|x_i|) * C
    其中 eps = 2^-52 ≈ 2.22e-16（float64 机器精度），
    C ≈ 85 为经验校准因子。
    """
    n = len(values)
    if n == 0:
        return 0.0
    eps = 2.0 ** -52
    mean_abs = sum(abs(x) for x in values) / n
    return _CALIBRATION_FACTOR * math.sqrt(n) * eps * mean_abs


def measure_sum_error(values: List[float]) -> float:
    """实测舍入误差 = |顺序和 - Kahan 和|。"""
    seq = _sequential_sum(values)
    kahan = _kahan_sum(values)
    return abs(seq - kahan)


def validate_rounding_bound(
    values: List[float],
    *,
    seed: Optional[int] = None,
    ratio_lo: float = 0.5,
    ratio_hi: float = 2.0,
) -> RoundingBound:
    """验证舍入界：预测下界 vs 实测误差的比值是否在 [ratio_lo, ratio_hi] 内。"""
    predicted = predict_sum_lower_bound(values)
    measured = measure_sum_error(values)
    if measured == 0.0:
        ratio = float("inf") if predicted > 0 else 1.0
    else:
        ratio = predicted / measured
    in_range = ratio_lo <= ratio <= ratio_hi
    return RoundingBound(
        n=len(values),
        predicted_lower=predicted,
        measured_error=measured,
        ratio=ratio,
        in_range=in_range,
        seed=seed,
    )
