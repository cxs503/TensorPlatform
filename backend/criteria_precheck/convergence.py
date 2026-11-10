#!/usr/bin/env python3
"""收敛阶判定（E3）。

输入一系列 (网格尺寸 h_i, 误差 e_i) 对，估计收敛阶 p：
* p ≈ log(e_i / e_{i+1}) / log(h_i / h_{i+1})

判定规则：
* p ≈ 0 → SUSPECT（无收敛，判据不可达成）
* p ≈ 1 → FEASIBLE（一阶收敛）
* p ≈ 2 → PRE-FEASIBLE（二阶收敛，判据可达成）
* p < 0 → SUSPECT（发散）

纯函数、无 IO、无随机。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class ConvergenceResult:
    order: float
    verdict: str                    # "SUSPECT" | "FEASIBLE" | "PRE-FEASIBLE"
    n_points: int
    r_squared: float               # 线性拟合优度
    details: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "order": round(self.order, 6),
            "verdict": self.verdict,
            "n_points": self.n_points,
            "r_squared": round(self.r_squared, 6),
            "details": self.details,
        }


def estimate_order(
    h_values: List[float],
    e_values: List[float],
) -> Tuple[float, float]:
    """用最小二乘估计收敛阶 p。

    在 log-log 空间：log(e) ≈ p * log(h) + c
    返回 (p, r_squared)。
    """
    n = len(h_values)
    if n < 2:
        return 0.0, 0.0

    log_h = [math.log(h) for h in h_values if h > 0]
    log_e = [math.log(e) for e in e_values if e > 0]

    # 过滤掉无效值
    valid = [(lh, le) for lh, le in zip(log_h, log_e)
             if math.isfinite(lh) and math.isfinite(le)]
    if len(valid) < 2:
        return 0.0, 0.0

    log_h = [v[0] for v in valid]
    log_e = [v[1] for v in valid]
    n = len(valid)

    mean_h = sum(log_h) / n
    mean_e = sum(log_e) / n

    ss_hh = sum((x - mean_h) ** 2 for x in log_h)
    ss_he = sum((x - mean_h) * (y - mean_e) for x, y in zip(log_h, log_e))
    ss_ee = sum((y - mean_e) ** 2 for y in log_e)

    if ss_hh == 0:
        return 0.0, 0.0

    p = ss_he / ss_hh

    # R²
    if ss_ee == 0:
        r_squared = 1.0
    else:
        r_squared = (ss_he ** 2) / (ss_hh * ss_ee)
        r_squared = max(0.0, min(1.0, r_squared))

    return p, r_squared


def judge_convergence(
    h_values: List[float],
    e_values: List[float],
    *,
    suspect_threshold: float = 0.3,
    pre_feasible_threshold: float = 1.5,
) -> ConvergenceResult:
    """判定收敛阶并给出 verdict。

    * p < suspect_threshold → SUSPECT
    * p >= pre_feasible_threshold → PRE-FEASIBLE
    * 否则 → FEASIBLE
    """
    p, r2 = estimate_order(h_values, e_values)

    if p < suspect_threshold:
        verdict = "SUSPECT"
    elif p >= pre_feasible_threshold:
        verdict = "PRE-FEASIBLE"
    else:
        verdict = "FEASIBLE"

    return ConvergenceResult(
        order=p,
        verdict=verdict,
        n_points=len(h_values),
        r_squared=r2,
    )
