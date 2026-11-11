"""代理模型不确定度标定与覆盖检验（与领域无关）。

方法：深度集成（ensemble）+ split-conformal 标定。

1. ``K`` 个独立随机种子的成员给出预测集合，取对数空间标准差 ``σ`` 作为
   局部尺度估计。
2. 在**留出标定集**上计算 ``z = |log y - mean(log ŷ)| / σ``，取分位数 ``q``
   （split conformal），区间 ``ŷ·exp(±q·σ)``（对数空间对称）。
3. 在**外推集**上报告经验覆盖率（PICP）与平均区间宽度（MPIW）。

conformal 标定的保证：在标定集与测试集可交换（exchangeable）假设下，
名义 ``1-α`` 区间的覆盖率 ≥ ``1-α``（有限样本下有 ``1/(n+1)`` 的边缘修正）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class ConformalCalibration:
    """split-conformal 标定结果（对数空间）。"""
    level: float          # 名义覆盖率，例如 0.90
    q: float              # 尺度倍数
    n_calib: int          # 标定样本数
    log_bias: float = 0.0  # 集成对数均值相对真值的系统偏差（用于偏置校正）

    def interval(self, y_hat: np.ndarray, sigma: np.ndarray) -> Dict[str, np.ndarray]:
        """返回对数空间区间的上下界（真值单位）。"""
        sig = np.maximum(np.asarray(sigma, dtype=float), 1e-12)
        center = np.log(np.maximum(np.asarray(y_hat, dtype=float), 1e-300)) - self.log_bias
        lo = np.exp(center - self.q * sig)
        hi = np.exp(center + self.q * sig)
        return {"lower": lo, "upper": hi}

    def as_dict(self) -> Dict[str, float]:
        return {"level": self.level, "q": self.q, "n_calib": self.n_calib,
                "log_bias": self.log_bias}


def _conformal_quantile(z: np.ndarray, level: float) -> float:
    """有限样本修正的 conformal 分位数。"""
    n = len(z)
    if n == 0:
        return float("inf")
    k = int(np.ceil((n + 1) * level))
    k = min(max(k, 1), n)
    return float(np.sort(z)[k - 1])


def calibrate(y_hat: np.ndarray, sigma: np.ndarray, y_true: np.ndarray,
              level: float = 0.90) -> ConformalCalibration:
    """在标定集上确定 ``q``（可选偏置校正）。"""
    y_hat = np.asarray(y_hat, dtype=float)
    y_true = np.asarray(y_true, dtype=float)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-12)
    bias = float(np.mean(np.log(y_hat) - np.log(y_true)))
    z = np.abs(np.log(y_hat) - np.log(y_true) - bias) / sigma
    return ConformalCalibration(level=level, q=_conformal_quantile(z, level),
                                n_calib=int(len(z)), log_bias=bias)


def coverage(y_true: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    """经验覆盖率 PICP。"""
    y_true = np.asarray(y_true, dtype=float)
    inside = (y_true >= np.asarray(lower)) & (y_true <= np.asarray(upper))
    return float(np.mean(inside))


def mean_interval_width(lower: np.ndarray, upper: np.ndarray) -> float:
    """平均区间宽度（真值单位）。"""
    return float(np.mean(np.asarray(upper) - np.asarray(lower)))


def summarize(calib: ConformalCalibration, y_hat: np.ndarray, sigma: np.ndarray,
              y_true: np.ndarray) -> Dict[str, float]:
    """覆盖率 + 宽度 + MAPE 的一体化摘要（验收报告口径）。"""
    iv = calib.interval(y_hat, sigma)
    err = np.abs(np.asarray(y_hat) - np.asarray(y_true)) / np.abs(np.asarray(y_true))
    return {
        "coverage": coverage(y_true, iv["lower"], iv["upper"]),
        "mean_interval_width": mean_interval_width(iv["lower"], iv["upper"]),
        "mape_pct": float(np.mean(err) * 100.0),
        "p95_relative_error_pct": float(np.quantile(err, 0.95) * 100.0),
        "q": calib.q,
        "n": int(len(np.asarray(y_true))),
    }