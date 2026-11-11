#!/usr/bin/env python3
"""Trace runner: **independent** Wood closed-form reference (no producer import).

Wood (1955) mixture sound speed, written from the closed form only:

    c_m = [ rho_m * ( alpha/(rho_g c_g^2) + (1-alpha)/(rho_l c_l^2) ) ]^(-1/2)
    rho_m = alpha rho_g + (1-alpha) rho_l

相参数取 SI 常数量级（水 998.2 kg/m3 / 1483 m/s，空气 1.204 kg/m3 / 343 m/s），
与生产方实现**不共享任何代码或常量文件**：若一致性检查通过，说明两侧独立一致，
而不是「自己跟自己相等」。参考实现的常量在此文件里逐字可见，供审计复核。
"""

from __future__ import annotations

import json
import math

GRID = [0.0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.45]
RHO_L, C_L = 998.2, 1483.0
RHO_G, C_G = 1.204, 343.0


def wood_sound_speed(alpha: float) -> float:
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha outside [0, 1]")
    rho_m = alpha * RHO_G + (1.0 - alpha) * RHO_L
    return 1.0 / math.sqrt(rho_m * (alpha / (RHO_G * C_G ** 2)
                                   + (1.0 - alpha) / (RHO_L * C_L ** 2)))


def main() -> int:
    rows = [{"param": a, "value": wood_sound_speed(a)} for a in GRID]
    print(json.dumps({"rows": rows,
                      "meta": {"reference": "Wood(1955) closed form, independent re-implementation",
                               "rho_l": RHO_L, "c_l": C_L, "rho_g": RHO_G, "c_g": C_G}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())