"""Resistance physics core (纯函数，无 I/O、无框架依赖).

从 ``api/calculations.py`` 抽出的唯一计算源，目的有二：

1. 端点与离线审计（``scripts/audit_benchmark.py``）调用同一个函数，消除"报告里的数
   和端点里的数不是同一份代码算出来的"这类不可审计问题；
2. 让湿表面积口径成为显式入参：平台原闭式 S = π·B·L·(0.5+0.25·Cb) 是经验近似，
   对 SUBOFF 这类回转体严重偏小（见 artifacts/benchmark_integrity_audit.json），
   调用方可用试验报告的声明湿表面积覆盖它，从而把 S 的偏差从阻力误差里剥离出来。

物理口径（保持与既有实现一致，改动只做"显式化"不做"换公式"）：
    Cf : ITTC-1957 平板摩擦线   Cf = 0.075 / (log10(Re) - 2)²
    Cr : 平台经验剩余阻力系数   Cr = 0.002·(1 + 2·Cb)·Fn^2.5   (Fn > 0.05)
    Rt = 0.5·ρ·S·(Cf + Cr)·V²
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from services.hull_geometry import (  # noqa: F401  (CB_BAND_* re-exported for api layer)
    CB_BAND_HI,
    CB_BAND_LO,
    RHO_MODEL_FRESH,
    SURFACE_REL_TOL,
    axisymmetric_wetted_surface,
    check_geometry,
)

NU_WATER_20C = 1.004e-6
G_STANDARD = 9.81
CF_LINE = "ITTC-1957"
CR_MODEL = "platform empirical Cr = 0.002*(1+2*Cb)*Fn^2.5 (validity: Fn<=0.4, Cb>=0.55)"


def ittc1957_cf(reynolds: float) -> float:
    """ITTC-1957 平板摩擦阻力系数。"""
    if reynolds <= 0:
        raise ValueError(f"Reynolds number must be > 0, got {reynolds}")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def platform_cr(cb: float, froude: float) -> float:
    """平台经验剩余阻力系数（含 Fn<=0.05 的截断）。"""
    if froude <= 0.05:
        return 0.0
    return 0.002 * (1.0 + 2.0 * cb) * froude ** 2.5


def estimate_resistance(
    *,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed_ms: float,
    rho: float = RHO_MODEL_FRESH,
    nu: float = NU_WATER_20C,
    wetted_surface_m2: Optional[float] = None,
    g: float = G_STANDARD,
) -> Dict[str, Any]:
    """总阻力分解。返回 dict（可直接序列化，端点与审计共用）。

    ``wetted_surface_m2`` 为 None 时使用平台闭式；给出时按声明值计算，并标注来源。
    """
    if length <= 0 or beam <= 0 or draft <= 0:
        raise ValueError("L, B, T must all be > 0")
    if not (0.0 < cb <= 1.0):
        raise ValueError(f"Cb={cb} outside (0, 1]")
    if speed_ms <= 0:
        raise ValueError("speed must be > 0")

    s_closure = axisymmetric_wetted_surface(cb, length, beam)
    if wetted_surface_m2 is None:
        surface = s_closure
        surface_source = "platform_closure"
    else:
        surface = float(wetted_surface_m2)
        surface_source = "declared"
        # 声明湿表面积必须是同一几何下的量：允许 2 倍以内的差异，超出视为几何错配
        ratio = surface / s_closure
        if not (0.5 <= ratio <= 2.0):
            raise ValueError(
                f"declared wetted surface {surface:.4f} m² inconsistent with geometry closure "
                f"{s_closure:.4f} m² (ratio {ratio:.3f} outside [0.5, 2.0])"
            )

    reynolds = speed_ms * length / nu
    froude = speed_ms / math.sqrt(g * length)
    cf = ittc1957_cf(reynolds)
    cr = platform_cr(cb, froude)
    q = 0.5 * rho * surface * speed_ms ** 2

    rf = q * cf
    rr = q * cr
    return {
        "speed_ms": float(speed_ms),
        "wetted_surface_m2": round(surface, 6),
        "wetted_surface_closure_m2": round(s_closure, 6),
        "wetted_surface_source": surface_source,
        "wetted_surface_closure_bias": round((s_closure - surface) / surface, 6) if surface else None,
        "reynolds_number": round(reynolds, 2),
        "froude_number": round(froude, 6),
        "cf": float(f"{cf:.8e}"),
        "cr": float(f"{cr:.8e}"),
        "ct": float(f"{cf + cr:.8e}"),
        "frictional_resistance_n": round(rf, 6),
        "residual_resistance_n": round(rr, 6),
        "total_resistance_n": round(rf + rr, 6),
        "cf_line": CF_LINE,
        "cr_model": CR_MODEL,
        "validity": {
            "froude_le_0.4": froude <= 0.4,
            "cb_ge_0.55": cb >= 0.55,
            "in_validity_range": (froude <= 0.4 and cb >= 0.55),
        },
    }


def geometry_gate(
    *,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    wetted_surface_m2: Optional[float] = None,
) -> Dict[str, Any]:
    """阻力端点的几何闸门（委托 hull_geometry.check_geometry，共享同一套阈值）。"""
    return check_geometry(
        length=length,
        beam=beam,
        draft=draft,
        cb=cb,
        volume_m3=None,
        wetted_surface_m2=wetted_surface_m2,
    )