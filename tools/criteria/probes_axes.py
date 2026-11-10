#!/usr/bin/env python3
"""依赖轴合成探针（axis probes）——把派生一致性从"只认 h"推广到任意依赖量。

为什么需要它
------------
``tools/criteria/probes.py`` 里的四个探针只覆盖一条轴：离散分辨率 ``h``
（误差 ∝ h^p，p>0）。但平台上一大批判据的阈值并不随 h 变，而是随

* **统计样本数 N**：抽样估计的误差 ∝ N^(-1/2)（中心极限定理），
* **相对容差 ε**：迭代停止准则的残差 ∝ ε^1（压缩映射的线性传播），

变。旧引擎对这类判据的处理是**静默跳过**：``derivation_check`` 里
``if "h" not in deps: continue``——于是"声明了依赖量、挂不了实测"的判据既不进
PASS 也不进 FAIL，门照旧判绿。这正是本项目要消灭的那类假绿。

本模块补上两条轴，让"阈值 = dependency(h, p, N, ε) 的函数"这句话对 N、ε 也成立，
并且**实测来自与领域无关的纯数学对象**：

``monte_carlo_mean``（轴 N，指数 -1/2）
    X ~ U(0,1)，E[X]=1/2，σ=1/√12。用 N 个样本的样本均值估计 E[X]，
    R 次重复取均方根误差。中心极限定理给出 RMSE → σ·N^(-1/2)，
    先验界常数 C=σ=1/√12（可由 √12 手算复核），指数 -1/2。
    误差随 N **增大**而下降 ⇒ 细化方向 = N×2。

``tol_forward_difference``（轴 eps，指数 +1）
    容差 ε 直接当差分步长：f(x)=e^x 在 x0=0 的导数用前向差分估，
    截断误差 =(e^eps-1)/eps-1 ≈ eps/2。先验界常数 C=1/2，指数 +1。
    误差随 eps **减小**而下降 ⇒ 细化方向 = eps/2。

``tol_central_difference``（轴 eps，指数 +2）
    同上但用中心差分：误差 =(e^eps-e^-eps)/(2eps)-1 ≈ eps²/6。C=1/6，指数 +2。
    同一轴两个不同指数，用来证明引擎反演的是**声明的那条幂律**而不是碰巧拟合。

两条轴的细化方向由**声明指数的符号**决定（见 ``refine_series``），不写死方向表：
指数为负 ⇒ 变量放大是细化；指数为正 ⇒ 变量缩小是细化。这条规则与 h 轴自洽
（h 轴指数为正、方向为缩小），所以旧判据行为不变。

零领域语义
----------
只用标准库与纯数学对象（均匀随机数、余弦不动点迭代）。随机性必须是**确定性**的：
每个重复用固定种子 ``SEED_BASE + r`` 派生独立随机流，重跑同树同结果——否则门会
自己抖成假红/假绿。本模块不出现任何领域词条。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Tuple

#: 蒙特卡洛重复次数（越大 RMSE 估计越稳，代价线性）。64 → 估计相对波动 ≈ 8.8%。
MC_REPLICATES = 64
#: 每个重复的随机流种子基（确定性，重跑可复现）
SEED_BASE = 20261110
#: 均匀分布 U(0,1) 的标准差 = 1/√12（=先验界常数 C）
SIGMA_UNIFORM = 1.0 / math.sqrt(12.0)


def refine_series(reference: float, exponent: float, levels: int) -> List[float]:
    """按"越往后越细"的顺序给出 axis 取值序列。

    指数为负 ⇒ 变量放大是细化（N, 2N, 4N…）；指数为正 ⇒ 变量缩小是细化
    （x, x/2, x/4…，h 轴与 ε 轴都是这一类）。
    """
    if exponent < 0:
        return [reference * (2.0 ** k) for k in range(levels)]
    return [reference / (2.0 ** k) for k in range(levels)]


@dataclass(frozen=True)
class AxisProbe:
    """一条"非 h 轴"合成探针：实测误差函数 + 先验解析界 + 声明指数（带符号）。"""

    probe_id: str
    axis: str
    exponent: float
    bound_coefficient: float
    reference_value: float
    description: str
    error_fn: Callable[[float], float]

    def error(self, value: float) -> float:
        return float(self.error_fn(value))

    def bound(self, value: float) -> float:
        """先验解析界 C·value^exponent（exponent 可负）。"""
        return self.bound_coefficient * (float(value) ** self.exponent)

    def series(self, levels: int = 6) -> Tuple[List[float], List[float]]:
        vals = refine_series(self.reference_value, self.exponent, levels)
        return vals, [self.error(v) for v in vals]

    @property
    def expression(self) -> str:
        return f"C*{self.axis}**({_fmt(self.exponent)})"


def _fmt(x: float) -> str:
    """把指数写成可复算的形式：-0.5 → -0.5，1.0 → 1。"""
    return repr(float(x)) if float(x) != int(x) else str(int(x))


# --------------------------------------------------------------------------- #
# 轴 1：统计样本数 N（中心极限定理，指数 -1/2）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def _mc_mean_rmse(n: int) -> float:
    """N 个 U(0,1) 样本的样本均值偏差的均方根（R=MC_REPLICATES 次重复）。

    每次重复用独立固定种子——确定性、可复算、无全局状态。
    """
    n = int(n)
    if n < 1:
        raise ValueError("N 必须 ≥ 1")
    acc = 0.0
    for r in range(MC_REPLICATES):
        rng = random.Random(SEED_BASE + 7919 * r)
        s = 0.0
        for _ in range(n):
            s += rng.random()
        dev = s / n - 0.5
        acc += dev * dev
    return math.sqrt(acc / MC_REPLICATES)


def _mc_mean_error(value: float) -> float:
    return _mc_mean_rmse(int(round(float(value))))


# --------------------------------------------------------------------------- #
# 轴 2：相对容差 ε（容差决定步长 ⇒ 截断误差 ∝ ε^k，指数连续可测）
# --------------------------------------------------------------------------- #
#: 容差驱动的测点（纯数学，无自由参数）：f(x)=e^x 在 x0=0 处的导数真值为 1。
_X0 = 0.0
_FP_EXACT = math.exp(_X0)  # f'(x0) = e^{x0}
#: 前向差分 (f(x+eps)-f(x))/eps：截断误差 ≈ (eps/2)*f2(x0) ⇒ C=1/2，指数 1
_FORWARD_C = 0.5
#: 中心差分 (f(x+eps)-f(x-eps))/(2*eps)：截断误差 ≈ (eps^2/6)*f3(x0) ⇒ C=1/6，指数 2
_CENTRAL_C = 1.0 / 6.0


def _forward_diff_error(value: float) -> float:
    """eps 作为前向差分步长时的**真实**截断误差（不是界，是可复算的差）。"""
    eps = float(value)
    if eps <= 0.0:
        raise ValueError("eps 必须 > 0")
    approx = (math.exp(_X0 + eps) - math.exp(_X0)) / eps
    return abs(approx - _FP_EXACT)


def _central_diff_error(value: float) -> float:
    """eps 作为中心差分步长时的真实截断误差。"""
    eps = float(value)
    if eps <= 0.0:
        raise ValueError("eps 必须 > 0")
    approx = (math.exp(_X0 + eps) - math.exp(_X0 - eps)) / (2.0 * eps)
    return abs(approx - _FP_EXACT)


AXIS_PROBES: Dict[str, AxisProbe] = {
    "monte_carlo_mean": AxisProbe(
        probe_id="monte_carlo_mean",
        axis="N",
        exponent=-0.5,
        bound_coefficient=SIGMA_UNIFORM,
        reference_value=1024.0,
        description="U(0,1) 样本均值，RMSE = sigma*N^(-1/2)，先验界常数 C=1/sqrt(12)（CLT）",
        error_fn=_mc_mean_error,
    ),
    "tol_forward_difference": AxisProbe(
        probe_id="tol_forward_difference",
        axis="eps",
        exponent=1.0,
        bound_coefficient=_FORWARD_C,
        reference_value=1e-2,
        description="容差 eps 作前向差分步长，截断误差 = (1/2)eps（f=e^x, x0=0，真值 1）",
        error_fn=_forward_diff_error,
    ),
    "tol_central_difference": AxisProbe(
        probe_id="tol_central_difference",
        axis="eps",
        exponent=2.0,
        bound_coefficient=_CENTRAL_C,
        reference_value=1e-2,
        description="容差 eps 作中心差分步长，截断误差 = (1/6)eps^2（f=e^x, x0=0，真值 1）",
        error_fn=_central_diff_error,
    ),
}


def get_axis_probe(probe_id: str) -> AxisProbe:
    try:
        return AXIS_PROBES[probe_id]
    except KeyError:
        raise KeyError(f"未知轴探针 {probe_id!r}；可用: {sorted(AXIS_PROBES)}") from None
