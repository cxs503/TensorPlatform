"""合成线性算子探针（零领域语义）：已知阶的离散算子误差细化扫描。

这些探针只含数学对象（周期正弦、指数函数、中心差分/复合梯形），用于给
"阈值是 h 的函数"这类判据提供**可复算的观测序列**；不含任何物理/几何语义。
参数从判据工件的 ``probe.params`` 注入，代码侧不出现常数。
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from . import kernels


def _periodic_derivative_scan(stencil: int, k: float, resolutions, length: float) -> Dict:
    """周期正弦 u=sin(kx) 的一阶导数离散近似 vs 解析 k·cos(kx)。"""
    if stencil not in (2, 4):
        raise ValueError(f"unsupported stencil size {stencil}")

    def err(n: int) -> float:
        h = length / n
        x = np.arange(n) * h
        u = np.sin(k * x)
        exact = k * np.cos(k * x)
        if stencil == 2:
            approx = (np.roll(u, -1) - np.roll(u, 1)) / (2.0 * h)
        else:
            approx = (-np.roll(u, -2) + 8.0 * np.roll(u, -1)
                      - 8.0 * np.roll(u, 1) + np.roll(u, 2)) / (12.0 * h)
        return float(np.max(np.abs(approx - exact)))

    scan = kernels.refinement_scan(err, [int(n) for n in resolutions], length)
    exponent = 2.0 if stencil == 2 else 4.0
    # 解析误差主项：h^p/p! · |u^(p+1)| = h^p · k^(p+1) / (p+1)! 的系数量级
    analytic_constant = (k ** (exponent + 1.0)) / (6.0 if exponent == 2.0 else 30.0)
    scan.update({
        "kind": "refinement",
        "value": scan["errors"][-1],
        "detail": {"stencil": stencil, "wavenumber": k,
                   "declared_exponent": exponent,
                   "analytic_error_constant": float(analytic_constant),
                   "final_error": scan["errors"][-1]},
    })
    return scan


def linear_operator_refinement(params: Dict) -> Dict:
    """中心差分导数算子的细化扫描（stencil=2 → O(h²)，stencil=4 → O(h⁴)）。"""
    return _periodic_derivative_scan(
        int(params.get("stencil", 2)),
        float(params.get("wavenumber", 1.0)),
        params.get("resolutions", [32, 64, 128, 256]),
        float(params.get("length", 2.0 * np.pi)),
    )


def integration_refinement(params: Dict) -> Dict:
    """复合梯形求积的细化扫描：指数函数在 [0,1] 上，误差主项解析已知 O(h²)。"""
    resolutions = [int(n) for n in params.get("resolutions", [16, 32, 64, 128])]
    a = float(params.get("lower", 0.0))
    b = float(params.get("upper", 1.0))
    exact = np.exp(b) - np.exp(a)

    def err(n: int) -> float:
        h = (b - a) / n
        x = a + np.arange(n + 1) * h
        y = np.exp(x)
        approx = h * (0.5 * y[0] + np.sum(y[1:-1]) + 0.5 * y[-1])
        return float(abs(approx - exact))

    scan = kernels.refinement_scan(err, resolutions, b - a)
    # 复合梯形误差 = (b-a)h²/12 · (f'(b)-f'(a)) —— 对指数函数为精确表达式
    analytic_constant = (np.exp(b) - np.exp(a)) / 12.0
    scan.update({
        "kind": "refinement",
        "value": scan["errors"][-1],
        "detail": {"lower": a, "upper": b,
                   "declared_exponent": 2.0,
                   "analytic_error_constant": float(analytic_constant),
                   "final_error": scan["errors"][-1]},
    })
    return scan


SYNTHETIC_PROBES = {
    "linear_operator_refinement": linear_operator_refinement,
    "integration_refinement": integration_refinement,
}