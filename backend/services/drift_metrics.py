"""漂移指标（PSI / KS）单一实现源 —— 平台内唯一权威定义。

背景（见 docs/reports/DRIFT_METRICS_AUDIT.md）：
平台原有三处同名 PSI 实现，分箱方式与 eps 兜底互不相同：
  · backend/api/model_monitoring.py::_compute_psi         联合等宽箱 eps=1e-10
  · backend/services/drift_detection_service.py::_psi_test 联合等宽箱 eps=1e-4
  · backend/services/data_analysis_service.py::_psi        基准分位箱 eps=1e-6（离散数据退化为 0）
复核结论：同名指标给出不同数值（最大 +2792%），且分位数分箱在离散/退化数据上
塌缩成单箱恒返回 0（把"完全分离"判成"无漂移"）。本模块给出唯一、可复算、
与文献一致的定义，三处调用方统一委托到这里。

定义（Siddiqi 2006；Yurdakul 2018 综述）：
    PSI = Σ_i (p_cur,i − p_ref,i) · ln(p_cur,i / p_ref,i)
  分箱边界由 **基准样本(reference)** 单次确定并被所有后续窗口复用：
    默认取基准样本的 10 个等频分位数边界，首末边界置 ±inf 以吸收越界样本。
  这样 PSI 在不同窗口之间可比，且平台沿用多年的经验阈值（0.1/0.2/0.25）
  才有意义 —— 阈值是针对分位数分箱 PSI 标定的。

退化处理：
  · 基准样本分位数去重后不足 3 个边界（离散/并列值多）→ 改用基准样本的
    去重取值作为分箱边界，等价于对离散变量按取值分箱（此时两箱闭式解可逐位复算）。
  · 基准样本只有 1 个取值 → 返回 0.0 并在 psi_detailed() 里标记
    status="constant_reference"，调用方可据此判定"指标不可用"而非"无漂移"。

只依赖 numpy；不引入数据库/框架依赖，便于单测与复算。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np

__all__ = ["psi", "psi_detailed", "ks_statistic", "ks_detailed", "DEFAULT_BINS", "DEFAULT_EPS"]

DEFAULT_BINS = 10
DEFAULT_EPS = 1e-6


def _clean(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float).reshape(-1)
    if arr.size:
        arr = arr[np.isfinite(arr)]
    return arr


def _bin_edges(reference: np.ndarray, bins: int) -> tuple[np.ndarray, str]:
    """由基准样本确定分箱边界（含 ±inf 首末边界）。"""
    qs = np.linspace(0.0, 1.0, bins + 1)
    try:
        # method="lower"：取分位点左侧的实测值，避免线性插值在离散/并列数据上
        # 造出数据里不存在的假边界（例如 0/1 两值样本插值出 0.6）。
        raw_edges = np.quantile(reference, qs, method="lower")
    except TypeError:  # numpy < 1.22
        raw_edges = np.quantile(reference, qs, interpolation="lower")
    quantile_edges = np.unique(raw_edges)
    if quantile_edges.size >= 3:
        edges, strategy = quantile_edges, "quantile"
    else:
        # 离散/并列值多 → 分位数塌缩，按基准样本去重取值的相邻中点分箱
        # （首末再补 ±inf，保证越界样本被吸收而不丢弃）
        distinct = np.unique(reference)
        mids = (distinct[:-1] + distinct[1:]) / 2.0
        edges = np.concatenate([[-np.inf], mids, [np.inf]])
        return np.asarray(edges, dtype=float), "distinct_values"
    edges = np.asarray(edges, dtype=float)
    edges[0], edges[-1] = -np.inf, np.inf
    return edges, strategy


def psi_detailed(
    reference: Sequence[float],
    current: Sequence[float],
    bins: int = DEFAULT_BINS,
    eps: float = DEFAULT_EPS,
) -> Dict[str, Any]:
    """计算 PSI 并返回分箱诊断（零箱/prop 明细，全部可复算）。"""
    ref = _clean(reference)
    cur = _clean(current)
    if ref.size == 0 or cur.size == 0:
        return {"psi": 0.0, "status": "empty_input", "strategy": None,
                "bins": 0, "edges": [], "p_ref": [], "p_cur": []}
    if np.unique(ref).size < 2:
        return {"psi": 0.0, "status": "constant_reference", "strategy": None,
                "bins": 0, "edges": [], "p_ref": [], "p_cur": []}

    edges, strategy = _bin_edges(ref, max(2, int(bins)))
    ref_hist = np.histogram(ref, bins=edges)[0].astype(float)
    cur_hist = np.histogram(cur, bins=edges)[0].astype(float)
    p_ref = np.clip(ref_hist / ref.size, eps, None)
    p_cur = np.clip(cur_hist / cur.size, eps, None)
    value = float(np.sum((p_cur - p_ref) * np.log(p_cur / p_ref)))
    return {
        "psi": max(0.0, value),
        "status": "ok",
        "strategy": strategy,
        "bins": int(len(edges) - 1),
        "edges": [float(e) for e in edges],
        "p_ref": [float(p) for p in p_ref],
        "p_cur": [float(p) for p in p_cur],
    }


def psi(
    reference: Sequence[float],
    current: Sequence[float],
    bins: int = DEFAULT_BINS,
    eps: float = DEFAULT_EPS,
) -> float:
    """分布稳定性指数 PSI（基准分位数分箱，定义见模块 docstring）。"""
    return psi_detailed(reference, current, bins=bins, eps=eps)["psi"]


def ks_detailed(reference: Sequence[float], current: Sequence[float]) -> Dict[str, Any]:
    """两样本 Kolmogorov-Smirnov 统计量 D = sup|F_ref − F_cur|。

    优先用 scipy.stats.ks_2samp；不可用时用严格等价的纯 numpy 经验 CDF 实现
    （并列值用 side='right'，与 scipy 逐位一致，见 tests 中的等价性断言）。
    """
    ref = _clean(reference)
    cur = _clean(current)
    if ref.size == 0 or cur.size == 0:
        return {"statistic": 0.0, "p_value": None, "status": "empty_input", "engine": None}
    try:  # pragma: no cover - 依赖环境是否装 scipy
        from scipy.stats import ks_2samp

        res = ks_2samp(ref, cur)
        return {"statistic": float(res.statistic), "p_value": float(res.pvalue),
                "status": "ok", "engine": "scipy"}
    except ImportError:  # pragma: no cover
        ref_sorted = np.sort(ref)
        cur_sorted = np.sort(cur)
        all_vals = np.sort(np.concatenate([ref_sorted, cur_sorted]))
        cdf_ref = np.searchsorted(ref_sorted, all_vals, side="right") / ref_sorted.size
        cdf_cur = np.searchsorted(cur_sorted, all_vals, side="right") / cur_sorted.size
        return {"statistic": float(np.max(np.abs(cdf_ref - cdf_cur))), "p_value": None,
                "status": "ok", "engine": "numpy_fallback"}


def ks_statistic(reference: Sequence[float], current: Sequence[float]) -> float:
    """两样本 KS 统计量（float）。"""
    return ks_detailed(reference, current)["statistic"]