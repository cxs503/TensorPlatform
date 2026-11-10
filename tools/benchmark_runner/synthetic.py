"""合成（synthetic）算例工作负载——**零领域语义**的注入式测试夹具。

本模块**故意**只含初等数学：Simpson 数值积分、几何级数部分和、二分求根。
它扮演两个角色：

* **SUT**（system under test）：分辨率/迭代次数可调的近似算法，误差随参数收敛；
* **参考解**（reference）：同一问题的闭式解。

引擎（runner/gate/artifact/regression）对这些函数一无所知——只通过
``"module:function"`` 点分路径注入。真实舱位要接自己的求解器时，只需在 suite
配置里换点分路径，**不必改引擎**（这是"零领域语义"铁律的机制保证）。

所有函数：确定性（无随机数）、纯函数、返回 ``dict``（数值项并入 metrics）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

__all__ = [
    "numeric_integral", "integral_reference",
    "series_partial_sum", "series_reference",
    "bisect_root", "root_reference",
    "stall", "WORKLOADS",
]

# ── 被积函数族（初等函数，与任何应用域无关） ───────────────────────────────
_INTEGRANDS = {
    "sin": (math.sin, lambda a, b: -math.cos(b) + math.cos(a)),
    "cos": (math.cos, lambda a, b: math.sin(b) - math.sin(a)),
    "exp": (math.exp, lambda a, b: math.exp(b) - math.exp(a)),
    # ∫(x^3 - 2x + 1) dx = x^4/4 - x^2 + x
    "cubic": (lambda x: x ** 3 - 2.0 * x + 1.0,
              lambda a, b: (b ** 4 / 4 - b ** 2 + b) - (a ** 4 / 4 - a ** 2 + a)),
}


def numeric_integral(func: str, a: float, b: float, n: int) -> Dict[str, Any]:
    """复合 Simpson 积分（n 个等距子区间；n 为奇数则退化为 n+1）。"""
    if func not in _INTEGRANDS:
        raise ValueError(f"unknown integrand {func!r}")
    f, _ = _INTEGRANDS[func]
    n = int(n)
    if n < 2:
        raise ValueError("n must be >= 2")
    if n % 2:
        n += 1
    h = (b - a) / n
    total = f(a) + f(b)
    for i in range(1, n):
        total += (4.0 if i % 2 else 2.0) * f(a + i * h)
    value = total * h / 3.0
    return {"value": value, "h": h, "intervals": n, "evals": n + 1}


def integral_reference(func: str, a: float, b: float) -> Dict[str, Any]:
    """闭式积分值（参考解）。"""
    if func not in _INTEGRANDS:
        raise ValueError(f"unknown integrand {func!r}")
    _, primitive = _INTEGRANDS[func]
    return {"value": primitive(a, b)}


def series_partial_sum(ratio: float, terms: int) -> Dict[str, Any]:
    """几何级数部分和 S_n = Σ_{k=1..n} r^k，并给出轨迹与尾项估计。"""
    r = float(ratio)
    if not (0.0 < abs(r) < 1.0):
        raise ValueError("|ratio| must be in (0,1) for a convergent series")
    terms = int(terms)
    if terms < 1:
        raise ValueError("terms must be >= 1")
    traj: List[float] = []
    s = 0.0
    for k in range(1, terms + 1):
        s += r ** k
        traj.append(s)
    limit = r / (1.0 - r)
    tail = [abs(limit - v) for v in traj]
    return {"value": s, "trajectory": traj, "tail_error": tail, "terms": terms}


def series_reference(ratio: float) -> Dict[str, Any]:
    r = float(ratio)
    return {"value": r / (1.0 - r)}


def bisect_root(c: float, lo: float, hi: float, tol: float = 1e-12, max_iter: int = 200) -> Dict[str, Any]:
    """二分法求 f(x)=x^2-c 在 [lo,hi] 内的根（f(lo)<0<f(hi) 由调用者保证）。"""
    c, lo, hi, tol = float(c), float(lo), float(hi), float(tol)
    f = lambda x: x * x - c  # noqa: E731 - 局部闭包，简洁优先
    if f(lo) * f(hi) > 0:
        raise ValueError("bracket does not straddle a root")
    it = 0
    mid = 0.5 * (lo + hi)
    while it < max_iter and 0.5 * (hi - lo) > tol:
        mid = 0.5 * (lo + hi)
        if f(mid) == 0.0:
            break
        if f(lo) * f(mid) < 0:
            hi = mid
        else:
            lo = mid
        it += 1
    return {"value": mid, "iterations": it, "bracket_width": hi - lo}


def root_reference(c: float) -> Dict[str, Any]:
    return {"value": math.sqrt(float(c))}


def stall(seconds: float) -> Dict[str, Any]:
    """故意阻塞的"伪 SUT"：睡 ``seconds`` 秒再返回（供墙钟护栏的负向对照）。

    它不是数值算法，而是无人值守引擎的**故障注入器**：用来证明"挂住的算例"
    会被超时判 FAIL，而不是把门链悄悄挂住。零领域语义（只有一个 sleep）。
    """
    import time as _time
    waited = float(seconds)
    if waited < 0:
        raise ValueError("seconds must be >= 0")
    _time.sleep(waited)
    return {"value": 0.0, "slept": waited}


WORKLOADS = {
    "stall": stall,
    "numeric_integral": numeric_integral,
    "integral_reference": integral_reference,
    "series_partial_sum": series_partial_sum,
    "series_reference": series_reference,
    "bisect_root": bisect_root,
    "root_reference": root_reference,
}