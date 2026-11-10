"""合成域数值内核：MOCK/STUB 旧实现 → REAL 新实现 + **独立提供方**参考值。

三个能力（合成命名 ``synth.*``，按仓库规约 TensorPlatform 只允许合成域名做夹具）:

===============================  ==========  ==========================================
能力                             旧等级       升级内容
===============================  ==========  ==========================================
synth.linalg.cholesky_solve      MOCK        Cholesky 分解 + 前代/回代（Golub & Van Loan）
synth.stats.normal_ppf           STUB        标准正态分位数：CDF 有界根搜索到机器精度
synth.integrate.adaptive_simpson MOCK        自适应 Simpson + Richardson 外推
synth.stats.mean_reference       REAL        **负控**：已是 REAL，升级流水线必须 no-op
===============================  ==========  ==========================================

独立性：实现是纯 Python/自写算法（provider ``capability_maturity.synth``），
参考值分别取 LAPACK（numpy.linalg）、mpmath-60dps 二分、mpmath.quad——**不同提供方**。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict

import numpy as np

IMPLEMENTATION_PROVIDER = "capability_maturity.synth(pure-python)"
NUMPY_PROVIDER = "numpy.linalg(LAPACK)"
MPMATH_PROVIDER = "mpmath(60dps-independent)"


# ── 1. 线性方程求解 ────────────────────────────────────────────────────
def baseline_cholesky_solve(A: Any, b: Any) -> float:
    """MOCK 旧实现：用对角元近似解 x_i ≈ b_i / A_ii（不做分解），返回 ‖x‖₁。"""
    A = np.asarray(A, dtype=float)
    b = np.asarray(b, dtype=float)
    x = b / np.diag(A)
    return float(np.sum(np.abs(x)))


def cholesky_solve(A: Any, b: Any) -> float:
    """REAL：Cholesky A=LLᵀ + 前代/回代（Golub & Van Loan, Matrix Computations, 4.2）。"""
    M = np.asarray(A, dtype=float)
    rhs = np.asarray(b, dtype=float)
    n = M.shape[0]
    if M.shape[0] != M.shape[1]:
        raise ValueError("A 非方阵")
    L = np.zeros_like(M)
    for i in range(n):
        for j in range(i + 1):
            s = M[i, j] - float(L[i, :j] @ L[j, :j])
            if i == j:
                if s <= 0.0:
                    raise ValueError("A 非正定")
                L[i, i] = math.sqrt(s)
            else:
                L[i, j] = s / L[j, j]
    y = np.zeros(n)
    for i in range(n):
        y[i] = (rhs[i] - float(L[i, :i] @ y[:i])) / L[i, i]
    x = np.zeros(n)
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - float(L[i + 1:, i] @ x[i + 1:])) / L[i, i]
    return float(np.sum(np.abs(x)))


def ref_linear_solve(A: Any, b: Any) -> float:
    """参考：LAPACK 的通用 LU 求解（独立实现，返回同一汇总量 ‖x‖₁）。"""
    x = np.linalg.solve(np.asarray(A, dtype=float), np.asarray(b, dtype=float))
    return float(np.sum(np.abs(x)))


# ── 2. 标准正态分位数 ──────────────────────────────────────────────────
def baseline_normal_ppf(p: float) -> float:
    """STUB 旧实现：常数 1.96（不论 p）。"""
    return 1.96 if p >= 0.5 else -1.96


def normal_ppf(p: float) -> float:
    """REAL：Φ(x)=p 的有界根搜索（对闭式 CDF 二分，收敛到双精度极限）。"""
    p = float(p)
    if not (0.0 < p < 1.0):
        raise ValueError("p 必须落在 (0,1)")
    lo, hi = -40.0, 40.0
    sq2 = math.sqrt(2.0)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        cdf = 0.5 * math.erfc(-mid / sq2)
        if cdf < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-16 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


def _mpmath_normal_ppf(p: float) -> float:
    import mpmath as mp

    with mp.workdps(60):
        target = mp.mpf(p)
        lo, hi = mp.mpf("-40"), mp.mpf("40")
        for _ in range(220):
            mid = (lo + hi) / 2
            cdf = mp.erfc(-mid / mp.sqrt(2)) / 2
            if cdf < target:
                lo = mid
            else:
                hi = mid
        return float((lo + hi) / 2)


def ref_normal_ppf(p: float) -> float:
    """参考：mpmath 60 位有效数字二分（高精度独立求逆）。"""
    return _mpmath_normal_ppf(p)


# ── 3. 自适应积分 ──────────────────────────────────────────────────────
_FUNCS: Dict[str, Callable[[float], float]] = {
    "exp_decay": lambda x: math.exp(-3.0 * x),
    "lorentz": lambda x: 1.0 / (1.0 + x * x),
    "sqrt_bump": lambda x: math.sqrt(1.0 + x),
}


def _mp_funcs() -> Dict[str, Callable[[Any], Any]]:
    import mpmath as mp

    return {
        "exp_decay": lambda x: mp.e ** (-3 * x),
        "lorentz": lambda x: 1 / (1 + x * x),
        "sqrt_bump": lambda x: mp.sqrt(1 + x),
    }


def baseline_adaptive_simpson(func_key: str, a: float, b: float) -> float:
    """MOCK 旧实现：端点梯形（不细分，不检查误差）。"""
    f = _FUNCS[func_key]
    return 0.5 * (b - a) * (f(a) + f(b))


def adaptive_simpson(func_key: str, a: float, b: float, tol: float = 1e-13) -> float:
    """REAL：自适应 Simpson + Richardson 外推（区间误差估计驱动二分）。"""
    f = _FUNCS[func_key]
    a = float(a)
    b = float(b)

    def simp(x0: float, x1: float, f0: float, fm: float, f1: float) -> float:
        return (x1 - x0) * (f0 + 4.0 * fm + f1) / 6.0

    def rec(x0: float, x1: float, f0: float, fm: float, f1: float,
            whole: float, depth: int) -> float:
        m = 0.5 * (x0 + x1)
        lm, rm = 0.5 * (x0 + m), 0.5 * (m + x1)
        flm, frm = f(lm), f(rm)
        left = simp(x0, m, f0, flm, fm)
        right = simp(m, x1, fm, frm, f1)
        if depth <= 0 or abs(left + right - whole) <= 15.0 * tol:
            return left + right + (left + right - whole) / 15.0
        return (rec(x0, m, f0, flm, fm, left, depth - 1)
                + rec(m, x1, fm, frm, f1, right, depth - 1))

    fa, fb = f(a), f(b)
    fm0 = f(0.5 * (a + b))
    return rec(a, b, fa, fm0, fb, simp(a, b, fa, fm0, fb), 60)


def ref_adaptive_simpson(func_key: str, a: float, b: float) -> float:
    """参考：mpmath.quad（tanh-sinh 求积，60 dps）——不同算法族、不同实现方。"""
    import mpmath as mp

    with mp.workdps(60):
        g = _mp_funcs()[func_key]
        return float(mp.quad(g, [mp.mpf(a), mp.mpf(b)]))


# ── 4. 负控：已是 REAL 的能力 ──────────────────────────────────────────
def baseline_mean_reference(values: Any) -> float:
    """REAL（负控对象）：Kahan 补偿求和算均值，已是 REAL。"""
    total = 0.0
    comp = 0.0
    n = len(values)
    for v in values:
        y = float(v) - comp
        t = total + y
        comp = (t - total) - y
        total = t
    return total / n


def ref_exact_mean(values: Any) -> float:
    """参考：Fraction 精确有理求和（独立提供方）。"""
    from fractions import Fraction

    return float(sum(Fraction(str(v)) for v in values) / len(values))
