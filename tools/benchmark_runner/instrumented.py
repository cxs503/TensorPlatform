"""带计数的包装 SUT —— 供**回归检测演示/测试**使用的合成工作负载。

语义：数值结果与原 SUT 逐位相同，只是额外报一个"工作量"指标（``evals``）
*多算了一部分*。于是：

* 判据门（误差类）仍然通过 —— 数值没变；
* 回归检测却应当报警 —— 关键指标 ``evals`` 漂移超阈值。

这正是回归检测器"独立于判据门"的存在价值：门只看阈值内/外，回归看**相对基线的变化**。
"""
from __future__ import annotations

from typing import Any, Dict

from .synthetic import numeric_integral


def integral_with_overhead(func: str, a: float, b: float, n: int,
                           overhead: float = 0.05) -> Dict[str, Any]:
    """与 :func:`tools.benchmark_runner.synthetic.numeric_integral` 数值一致，
    ``evals`` 按 ``1+overhead`` 放大（模拟求解器悄悄多算/缓存失效）。"""
    out = numeric_integral(func=func, a=a, b=b, n=n)
    base_evals = out["evals"]
    return {"value": out["value"], "h": out["h"], "intervals": out["intervals"],
            "evals": base_evals * (1.0 + float(overhead))}