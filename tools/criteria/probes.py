#!/usr/bin/env python3
"""合成算子探针（synthetic discretization probes）——派生一致性引擎的"实测"来源。

为什么需要它
------------
P171W1-N06 的核心论断是：**验收阈值不是常数，而是 dependency(h, p, N, ε) 的函数**。
要把这句话变成机器判据，就必须有一个地方能"实测"误差随分辨率衰减的指数，且这个
地方必须与任何具体领域解耦：

* 探针只用**纯数学对象**（初等函数的定积分、一阶常微分初值问题），不出现任何领域
  词条；领域语义只能经注册表的 ``source_ref`` 字符串注入。
* 每个探针都给两样东西：``error(h)``（真实离散误差，数值可复算）与
  ``bound(h)``（**先验解析界** C·h^p，常数 C 由该问题的标准误差展开给出）。
  前者供 Richardson 拟合反演实测衰减指数，后者是注册表里 ``derivation`` 表达式的
  物理（数学）出处。

三个探针的解析界（均可手算复核，常数见下）
------------------------------------------
``trapezoid_exp``  复合梯形 ∫_0^1 e^x dx，误差 ≈ (h²/12)(f'(1)-f'(0)) = (e-1)h²/12；
                   先验界 C=(e)/12（取 max|f''|=e）。p=2。
``euler_growth``   显式 Euler 解 y'=y, y(0)=1 到 T=1，误差 = e-(1+h)^{1/h} ≈ (e/2)h；
                   先验界 C=e/2。p=1。
``simpson_exp``    复合 Simpson 同上积分，误差 ≈ -(h⁴/180)f⁗(ξ) = -(h⁴/180)e^ξ；
                   先验界 C=e/180。p=4。
``midpoint_exp``   复合中点法同上积分，误差 ≈ (h²/24)(f'(1)-f'(0)) = (e-1)h²/24；
                   先验界 C=e/24。p=2。

设计纪律
--------
* 纯标准库 + 无全局状态；每个探针是 ``Probe`` 记录，注册在 ``PROBES``。
* ``error(h)`` 必须对 h = 2^-k（k≥1）稳定：探针内部用无相消差的写法（求和后再减
  精确值），并在 k 上限处仍比双精度舍入地板大 6 个数量级以上（由 tests 钉死）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Tuple

# 探针内部一律用这个被积函数/右端项：f(x)=e^x，f 的各阶导数都等于 f，
# 于是误差展开里的 max|f^(n)| 就是 e —— 常数可手算，无自由参数。
_EXACT_INTEGRAL = math.e - 1.0


def _trapezoid(f: Callable[[float], float], a: float, b: float, n: int) -> float:
    h = (b - a) / n
    s = 0.5 * (f(a) + f(b))
    for i in range(1, n):
        s += f(a + i * h)
    return s * h


def _midpoint(f: Callable[[float], float], a: float, b: float, n: int) -> float:
    h = (b - a) / n
    s = 0.0
    for i in range(n):
        s += f(a + (i + 0.5) * h)
    return s * h


def _simpson(f: Callable[[float], float], a: float, b: float, n: int) -> float:
    if n % 2:
        raise ValueError("simpson 需要偶数子区间")
    h = (b - a) / n
    s = f(a) + f(b)
    for i in range(1, n):
        s += (4.0 if i % 2 else 2.0) * f(a + i * h)
    return s * h / 3.0


@dataclass(frozen=True)
class Probe:
    """一条合成探针：实测误差函数 + 先验解析界 + 声明的收敛阶。"""

    probe_id: str
    order: int
    bound_coefficient: float
    description: str
    error_fn: Callable[[float], float]

    def error(self, h: float) -> float:
        return float(self.error_fn(h))

    def bound(self, h: float) -> float:
        """先验解析界 C·h^p。"""
        return self.bound_coefficient * (h ** self.order)

    def error_series(self, h0: float, levels: int) -> Tuple[list, list]:
        """h0, h0/2, h0/4, ... 共 levels 档的 (h, error) 序列。"""
        hs = [h0 / (2.0 ** k) for k in range(levels)]
        return hs, [self.error(h) for h in hs]


def _trapezoid_exp_error(h: float) -> float:
    n = int(round(1.0 / h))
    return abs(_trapezoid(math.exp, 0.0, 1.0, n) - _EXACT_INTEGRAL)


def _midpoint_exp_error(h: float) -> float:
    n = int(round(1.0 / h))
    return abs(_midpoint(math.exp, 0.0, 1.0, n) - _EXACT_INTEGRAL)


def _simpson_exp_error(h: float) -> float:
    n = int(round(1.0 / h))
    return abs(_simpson(math.exp, 0.0, 1.0, n) - _EXACT_INTEGRAL)


def _euler_growth_error(h: float) -> float:
    """y'=y, y(0)=1, 步长 h, 走到 T=1：数值解 (1+h)^{1/h} 与 e 之差。"""
    n = int(round(1.0 / h))
    return abs((1.0 + h) ** n - math.e)


PROBES: Dict[str, Probe] = {
    "trapezoid_exp": Probe(
        probe_id="trapezoid_exp",
        order=2,
        bound_coefficient=math.e / 12.0,
        description="复合梯形，∫_0^1 e^x dx，先验界 (max|f''|/12)h²",
        error_fn=_trapezoid_exp_error,
    ),
    "midpoint_exp": Probe(
        probe_id="midpoint_exp",
        order=2,
        bound_coefficient=math.e / 24.0,
        description="复合中点，∫_0^1 e^x dx，先验界 (max|f''|/24)h²",
        error_fn=_midpoint_exp_error,
    ),
    "simpson_exp": Probe(
        probe_id="simpson_exp",
        order=4,
        bound_coefficient=math.e / 180.0,
        description="复合 Simpson，∫_0^1 e^x dx，先验界 (max|f⁗|/180)h⁴",
        error_fn=_simpson_exp_error,
    ),
    "euler_growth": Probe(
        probe_id="euler_growth",
        order=1,
        bound_coefficient=math.e / 2.0,
        description="显式 Euler，y'=y，先验界 (e/2)h",
        error_fn=_euler_growth_error,
    ),
}


def get_probe(probe_id: str) -> Probe:
    try:
        return PROBES[probe_id]
    except KeyError:
        raise KeyError(f"未知探针 {probe_id!r}；可用: {sorted(PROBES)}") from None