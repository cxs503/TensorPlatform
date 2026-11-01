"""
历史阻力实现快照（未修正前）——仅用于「复核有牙齿」的对照实验，不参与生产路径。

* legacy_worktree  : 本工作树修正前 backend/api/calculations.py:calc_resistance
                     （S = pi*B*L*(0.5+0.25Cb)；Cr = 0.002(1+2Cb)Fn^2.5 "curve-fit"）
* legacy_deployed  : 部署态 8001 的船体服务（work/m6 backend/api/calculations.py）
                     （S = Holtrop 式；Cr = 0.005*C4*C1*C2*C3，ΔCf = 4e-4；
                      部署态回归实测：KVLCC2@15kn rt=5512.42 kN, cr=0.004744）

保留这份快照的目的：当参考基准把「修正后」判为通过时，必须同时能证明
同一条判据会把「修正前」判为不通过（否则判据没有分辨力）。
"""
from __future__ import annotations

import math

G = 9.81


def legacy_worktree(length: float, beam: float, draft: float, cb: float,
                    speed_ms: float, rho: float = 998.0, nu: float = 1.004e-6):
    """修正前的本工作树实现（含其原始注释口径）。"""
    S = math.pi * beam * length * (0.5 + 0.25 * cb)
    Re = speed_ms * length / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * speed_ms ** 2
    Fn = speed_ms / math.sqrt(G * length)
    Cr = 0.002 * (1 + 2.0 * cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * speed_ms ** 2
    Rt = Rf + Rr
    return {"wetted_surface": S, "reynolds_number": Re, "froude_number": Fn,
            "cf": Cf, "cr": Cr, "ct": Ct_of(Rt, rho, S, speed_ms),
            "rf": Rf, "rr": Rr, "rt": Rt, "pe": Rt * speed_ms}


def legacy_deployed(length: float, beam: float, draft: float, cb: float,
                    speed_kn: float, rho: float = 1025.0, nu: float = 1.19e-6):
    """部署态（work/m6）实现：Cr = 0.005*C4*C1*C2*C3 的 fudge 版本。"""
    V = speed_kn * 0.5144
    Re = V * length / nu
    Fn = V / math.sqrt(G * length)
    Cm = 0.95 + 0.05 * cb
    Cwp = 0.75 + 0.25 * cb
    S = length * (2 * draft + beam) * math.sqrt(Cm) * (
        0.453 + 0.4425 * cb - 0.2862 * Cm - 0.003467 * beam / draft + 0.3696 * Cwp)
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    C1 = 1.0
    b_t = beam / draft
    C2 = 1.0 + 0.01 * (b_t - 2.5) if b_t > 2.5 else 1.0
    C3 = 1.0 + 10 * (cb - 0.7) ** 2 if cb > 0.7 else 1.0
    if Fn < 0.2:
        C4 = 0.5 + 2.5 * Fn
    elif Fn < 0.3:
        C4 = 1.0 + 10 * (Fn - 0.2)
    else:
        C4 = 2.0 + 5 * (Fn - 0.3)
    Cr = 0.005 * C4 * C1 * C2 * C3
    dCf = 0.0004
    Ct = Cf + Cr + dCf
    Rt = 0.5 * rho * V ** 2 * S * Ct
    return {"wetted_surface": S, "reynolds_number": Re, "froude_number": Fn,
            "cf": Cf, "cr": Cr, "ct": Ct, "rf": 0.5 * rho * V ** 2 * S * (Cf + dCf),
            "rr": 0.5 * rho * V ** 2 * S * Cr, "rt": Rt, "pe": Rt * V}


def Ct_of(rt: float, rho: float, S: float, V: float) -> float:
    return rt / (0.5 * rho * S * V ** 2)