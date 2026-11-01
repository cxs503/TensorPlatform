"""船舶阻力模型（纯函数，无框架依赖）。

本模块把原先内联在 ``api/calculations.py`` 里的阻力计算抽出为可复用的纯函数，
使 ``api/domain/ship_benchmark.py`` 的基准验证可以 **进程内** 调用同一套模型，
而不再依赖跨服务 HTTP（旧实现发 ``speed`` 读 ``ct``，与端点契约 ``speed_ms``/无 ``ct``
不匹配，导致 Ct 恒为 0、误差恒为 100%——见 docs/verification/ship_benchmark_integrity.md）。

模型组成（与抽出前逐式一致，仅在 Re < Re_crit 时新增层流分支）：

* 湿表面积估算 ``estimate_wetted_surface``: S = π·B·L·(0.5 + 0.25·Cb)，回转体近似；
  对轴对称体（SUBOFF）该式相对公布湿面积偏低约 18%，故对外接口允许显式传入实测 S。
* 摩擦系数 ``friction_coefficient``: ITTC-1957 平板公式 Cf = 0.075/(log10 Re − 2)²；
  Re < 5e5 时改用 Blasius 层流解析解 Cf = 1.328/√Re（ITTC-57 的标定域为湍流平板，
  在 Re=1e5 处它会给出 1.98 倍于 Blasius 的值，属超出适用范围）。
* 剩余阻力 ``residual_coefficient``: Cr = 0.002·(1 + 2·Cb)·Fn^2.5（Fn > 0.05），
  系对 SUBOFF 类回转体的经验拟合，非 Holtrop-Mennen 全量法（模块文档不作超额声明）。
* 总阻力 Ct = Cf + Cr，Rt = ½·ρ·S·Ct·V²。

单位：SI（m, s, N, kg/m³）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

G = 9.81
"""重力加速度 (m/s²)，与抽出前一致。"""

RHO_FRESH_WATER = 998.0
"""淡水密度 (kg/m³)，端点默认值（保持向后兼容）。"""

RHO_SEA_WATER = 1025.0
"""海水密度 (kg/m³)，ITTC 基准比对惯用值。"""

NU_FRESH_WATER_20C = 1.004e-6
"""20 °C 淡水运动粘性系数 (m²/s)，端点默认值（保持向后兼容）。"""

NU_SEA_WATER_15C = 1.1892e-6
"""15 °C 海水运动粘性系数 (m²/s)，ITTC-57 基准比对惯用值。"""

RE_CRIT = 5.0e5
"""平板边界层转捩临界雷诺数（ITTC 惯例）。低于此值使用 Blasius 层流解。"""

REGIME_LAMINAR = "laminar"
REGIME_TURBULENT = "turbulent"


class ResistanceInputError(ValueError):
    """输入超出物理容许域。"""


def estimate_wetted_surface(length: float, beam: float, draft: float, cb: float) -> float:
    """回转体近似湿表面积 S = π·B·L·(0.5 + 0.25·Cb) (m²)。

    注：``draft`` 不参与该近似（抽出前的实现同样如此），保留形参以维持调用签名。
    """
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 平板摩擦阻力系数（湍流）：Cf = 0.075/(log10 Re − 2)²。"""
    if reynolds <= 0:
        raise ResistanceInputError("雷诺数必须为正")
    denom = math.log10(reynolds) - 2.0
    if denom <= 0:
        raise ResistanceInputError("log10(Re) 必须 > 2（Re > 100）才能使用 ITTC-57")
    return 0.075 / denom ** 2


def blasius_cf(reynolds: float) -> float:
    """Blasius 层流平板解析解：Cf = 1.328/√Re（Re < Re_crit 适用）。"""
    if reynolds <= 0:
        raise ResistanceInputError("雷诺数必须为正")
    return 1.328 / math.sqrt(reynolds)


def friction_coefficient(reynolds: float) -> Tuple[float, str]:
    """按流态返回 (Cf, regime)。"""
    if reynolds < RE_CRIT:
        return blasius_cf(reynolds), REGIME_LAMINAR
    return ittc57_cf(reynolds), REGIME_TURBULENT


def residual_coefficient(cb: float, froude: float) -> float:
    """经验剩余阻力系数 Cr = 0.002·(1 + 2·Cb)·Fn^2.5（Fn ≤ 0.05 取 0）。"""
    if froude > 0.05:
        return 0.002 * (1.0 + 2.0 * cb) * froude ** 2.5
    return 0.0


def validate_geometry(length: float, beam: float, draft: float, cb: float) -> None:
    """几何/系数物理域校核（对应平台的「几何必须先验证 Cb/L/B」纪律）。"""
    if length <= 0 or beam <= 0 or draft <= 0:
        raise ResistanceInputError("主尺度必须为正")
    if not (0.0 < cb <= 1.0):
        raise ResistanceInputError(f"Cb={cb} 超出物理范围 (0, 1]")
    if not (0.3 < cb < 1.0):
        raise ResistanceInputError(f"Cb={cb} 超出模型标定域 [0.3, 1.0]")


def compute_resistance(
    *,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed_ms: float,
    water_density: float = RHO_FRESH_WATER,
    kinematic_viscosity: float = NU_FRESH_WATER_20C,
    wetted_surface_m2: Optional[float] = None,
) -> Dict[str, Any]:
    """计算阻力各分量与无量纲系数。

    ``wetted_surface_m2`` 给出时直接采用（例如基准算例公布的湿面积），
    否则使用 :func:`estimate_wetted_surface`。返回字典的数值均为未取整 float。
    """
    validate_geometry(length, beam, draft, cb)
    if speed_ms <= 0:
        raise ResistanceInputError("航速必须为正")
    if water_density <= 0 or kinematic_viscosity <= 0:
        raise ResistanceInputError("密度与运动粘性系数必须为正")

    if wetted_surface_m2 is not None:
        if wetted_surface_m2 <= 0:
            raise ResistanceInputError("显式湿表面积必须为正")
        surface = float(wetted_surface_m2)
        surface_source = "input"
    else:
        surface = estimate_wetted_surface(length, beam, draft, cb)
        surface_source = "estimate"

    reynolds = speed_ms * length / kinematic_viscosity
    froude = speed_ms / math.sqrt(G * length)
    cf, regime = friction_coefficient(reynolds)
    cr = residual_coefficient(cb, froude)

    q = 0.5 * water_density * surface * speed_ms ** 2  # 动压×湿面积 (N)
    rf = q * cf
    rr = q * cr
    rt = rf + rr

    return {
        "length": length,
        "beam": beam,
        "draft": draft,
        "cb": cb,
        "speed_ms": speed_ms,
        "water_density": water_density,
        "kinematic_viscosity": kinematic_viscosity,
        "wetted_surface_m2": surface,
        "wetted_surface_source": surface_source,
        "reynolds_number": reynolds,
        "froude_number": froude,
        "friction_coefficient": cf,
        "residual_coefficient": cr,
        "ct": cf + cr,
        "flow_regime": regime,
        "frictional_resistance_n": rf,
        "residual_resistance_n": rr,
        "total_resistance_n": rt,
    }