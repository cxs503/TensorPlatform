"""
Holtrop-Mennen (1982/1984) 阻力 —— **独立第二实现**（交叉校验用，numpy 写法）。

与 backend/services/resistance_holtrop.py 刻意不复用任何代码、不共享常量、
变量命名与计算顺序也不同：两条路径独立实现同一组公开公式，若二者对同一
输入给出不同 R_T，则说明至少一条实现有笔误——这就是「双实现交叉」
（cross-implementation）判据，区别于「单实现回归」。

输出单位：R_T [N]，P_E [W]。
"""
from __future__ import annotations

import numpy as np

RHO_SEA, NU_SEA, GRAV = 1025.0, 1.19e-6, 9.81


def _ittc57(rn):
    return 0.075 / np.square(np.log10(rn) - 2.0)


def rt_holtrop(L, B, T, cb, Vs, disp_t=None, ie=25.0, lcb=0.0, kstern=0.0,
               ks=150e-6, rho=RHO_SEA, nu=NU_SEA, cm=None, cwp=None):
    L, B, T, cb, Vs = map(float, (L, B, T, cb, Vs))
    cm = float(cm) if cm is not None else 0.97 + 0.03 * cb
    cwp = float(cwp) if cwp is not None else (1.0 + 2.0 * cb) / 3.0
    cp = cb / cm
    nabla = disp_t / (rho / 1000.0) if disp_t is not None else cb * L * B * T

    # 湿表面积（Holtrop 1984）——球鼻艏项置 0
    s_area = (L * (2.0 * T + B) * np.sqrt(cm)
              * (0.453 + 0.4425 * cb - 0.2862 * cm
                 - 0.003467 * (B / T) + 0.3696 * cwp))

    rn = Vs * L / nu
    fn = Vs / np.sqrt(GRAV * L)
    cf = _ittc57(rn)

    # (1+k1)
    lr = L * (1.0 - cp + 0.06 * cp * lcb / (4.0 * cp - 1.0))
    k1 = (0.93
          + 0.487118 * (1.0 + 0.011 * kstern)
          * np.power(B / L, 1.06806)
          * np.power(T / L, 0.46106)
          * np.power(L / lr, 0.121563)
          * np.power(L ** 3 / nabla, 0.36486)
          * np.power(1.0 - cp, -0.604247))

    # 兴波阻力
    ratio = B / L
    c7 = (0.229577 * ratio ** (1.0 / 3.0) if ratio < 0.11
          else (ratio if ratio <= 0.25 else 0.5 - 0.0625 / ratio))
    c1 = 2223105.0 * c7 ** 3.78613 * (T / B) ** 1.07961 * (90.0 - ie) ** -1.37565
    c16 = (8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
           if cp < 0.8 else 1.73014 - 0.7067 * cp)
    m1 = 0.0140407 * (L / T) - 1.75254 * (nabla ** (1.0 / 3.0) / L) - 4.79323 * ratio - c16
    lam = 1.446 * cp - (0.03 * L / B if L / B < 12.0 else 0.36)
    xi = L ** 3 / nabla
    c15 = (-1.69385 if xi < 512.0
           else (-1.69385 + (L / nabla ** (1.0 / 3.0) - 8.0) / 2.36 if xi <= 1727.0 else 0.0))
    m2 = c15 * cp ** 2 * np.exp(-0.1 / fn ** 2)
    expo = m1 * fn ** -0.9 + m2 * np.cos(lam / fn ** 2)
    rw = c1 * np.exp(expo) * rho * GRAV * nabla

    ca = (105.0 * np.cbrt(ks / L) - 0.64) * 1e-3
    q = 0.5 * rho * s_area * Vs ** 2
    rf = q * cf
    rt = rf * k1 + rw + q * ca

    return {"rt": float(rt), "rf_1k": float(rf * k1), "rw": float(rw),
            "ra": float(q * ca), "s": float(s_area), "cf": float(cf),
            "fn": float(fn), "rn": float(rn), "one_k1": float(k1),
            "pe": float(rt * Vs), "ct": float(rt / q)}