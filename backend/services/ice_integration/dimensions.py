"""量纲代数与单位换算 (dimensional algebra & unit conversion).

W8-N08 跨域集成门的第一道机器门: 各模块输入/输出必须能以 SI 量纲向量核对.
本模块只做"量纲/单位"这一层的唯一事实来源 (single source of truth):
  * Dim  = (M, L, T, Theta, N, I, J) 的整数 7 元组
  * Unit = (scale_to_SI, Dim)
  * convert(value, from_unit, to_unit) -> value * scale_from/scale_to
  * 量纲不兼容时抛 DimensionError, 绝不静默通过 (门禁必须能判负).

单位表刻意保持"显式白名单": 拼写错误/未登记单位一律报错, 避免字符
近似导致假通过 (例如把 "kN" 当成 "N")。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

Dim = Tuple[int, int, int, int, int, int, int]  # M, L, T, Theta, N(current), I(luminous), J(amount)

ZERO: Dim = (0, 0, 0, 0, 0, 0, 0)


class DimensionError(ValueError):
    """量纲/单位不一致 (接口契约违约)."""


class UnknownUnitError(KeyError):
    """单位未在登记表中."""


@dataclass(frozen=True)
class Unit:
    name: str
    scale: float  # 乘到 SI 基准的系数
    dim: Dim
    note: str = ""


def _d(m: int = 0, l: int = 0, t: int = 0, th: int = 0, n: int = 0, i: int = 0, j: int = 0) -> Dim:
    return (m, l, t, th, n, i, j)


#: 显式单位白名单 (名称 -> Unit). 组合单位也直接登记, 不做前缀猜测.
UNITS: Dict[str, Unit] = {
    # --- 无量纲 / 角度 -------------------------------------------------
    "1": Unit("1", 1.0, ZERO, "dimensionless ratio"),
    "-": Unit("-", 1.0, ZERO, "dimensionless (log ratio)"),
    "rad": Unit("rad", 1.0, ZERO, "plane angle, treated as dimensionless"),
    "deg": Unit("deg", 3.141592653589793 / 180.0, ZERO, "degree"),
    # --- 基本量 -------------------------------------------------------
    "kg": Unit("kg", 1.0, _d(m=1)),
    "g": Unit("g", 1e-3, _d(m=1)),
    "m": Unit("m", 1.0, _d(l=1)),
    "mm": Unit("mm", 1e-3, _d(l=1)),
    "cm": Unit("cm", 1e-2, _d(l=1)),
    "km": Unit("km", 1e3, _d(l=1)),
    "s": Unit("s", 1.0, _d(t=1)),
    "K": Unit("K", 1.0, _d(th=1)),
    # --- 运动学 -------------------------------------------------------
    "m/s": Unit("m/s", 1.0, _d(l=1, t=-1)),
    "m/s^2": Unit("m/s^2", 1.0, _d(l=1, t=-2)),
    "rad/s": Unit("rad/s", 1.0, _d(t=-1)),
    "rad/s^2": Unit("rad/s^2", 1.0, _d(t=-2)),
    "1/s": Unit("1/s", 1.0, _d(t=-1)),
    "Hz": Unit("Hz", 1.0, _d(t=-1)),
    "m^2": Unit("m^2", 1.0, _d(l=2)),
    "m^3": Unit("m^3", 1.0, _d(l=3)),
    "m^3/s": Unit("m^3/s", 1.0, _d(l=3, t=-1)),
    "1/m": Unit("1/m", 1.0, _d(l=-1)),
    "m^2/s": Unit("m^2/s", 1.0, _d(l=2, t=-1)),
    # --- 力学 ---------------------------------------------------------
    "N": Unit("N", 1.0, _d(m=1, l=1, t=-2)),
    "kN": Unit("kN", 1e3, _d(m=1, l=1, t=-2)),
    "MN": Unit("MN", 1e6, _d(m=1, l=1, t=-2)),
    "N*s": Unit("N*s", 1.0, _d(m=1, l=1, t=-1)),
    "N*m": Unit("N*m", 1.0, _d(m=1, l=2, t=-2)),
    "kN*m": Unit("kN*m", 1e3, _d(m=1, l=2, t=-2)),
    "N/(m/s)": Unit("N/(m/s)", 1.0, _d(m=1, t=-1)),
    "N/m": Unit("N/m", 1.0, _d(m=1, t=-2)),
    "Pa": Unit("Pa", 1.0, _d(m=1, l=-1, t=-2)),
    "kPa": Unit("kPa", 1e3, _d(m=1, l=-1, t=-2)),
    "MPa": Unit("MPa", 1e6, _d(m=1, l=-1, t=-2)),
    "Pa*s": Unit("Pa*s", 1.0, _d(m=1, l=-1, t=-1)),
    "kg/m^3": Unit("kg/m^3", 1.0, _d(m=1, l=-3)),
    "kg/m^2/s": Unit("kg/m^2/s", 1.0, _d(m=1, l=-2, t=-1)),
    "kg/s": Unit("kg/s", 1.0, _d(m=1, t=-1)),
    "kg*m^2": Unit("kg*m^2", 1.0, _d(m=1, l=2)),
    # --- 能量 / 功率 / 热 ---------------------------------------------
    "J": Unit("J", 1.0, _d(m=1, l=2, t=-2)),
    "kJ/kg": Unit("kJ/kg", 1e3, _d(l=2, t=-2)),
    "W": Unit("W", 1.0, _d(m=1, l=2, t=-3)),
    "kW": Unit("kW", 1e3, _d(m=1, l=2, t=-3)),
    "MW": Unit("MW", 1e6, _d(m=1, l=2, t=-3)),
    "W/m^2": Unit("W/m^2", 1.0, _d(m=1, t=-3)),
    "W/(m*K)": Unit("W/(m*K)", 1.0, _d(m=1, l=1, t=-3, th=-1)),
    "J/(m^2*K)": Unit("J/(m^2*K)", 1.0, _d(m=1, t=-2, th=-1)),
    "N^2/Hz": Unit("N^2/Hz", 1.0, _d(m=2, l=2, t=-3)),  # 力谱密度
    "m^2/Hz": Unit("m^2/Hz", 1.0, _d(l=2, t=1)),
}


def unit(name: str) -> Unit:
    try:
        return UNITS[name]
    except KeyError as exc:  # pragma: no cover - 由测试覆盖
        raise UnknownUnitError(f"单位未登记: {name!r} (拒绝字符近似匹配)") from exc


def dim_of(name: str) -> Dim:
    return unit(name).dim


def compatible(a: str, b: str) -> bool:
    """两个单位是否量纲兼容 (可直接相乘/相加)."""
    return dim_of(a) == dim_of(b)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """单位换算; 量纲不兼容 -> DimensionError (门禁判负而不是静默)."""
    ua, ub = unit(from_unit), unit(to_unit)
    if ua.dim != ub.dim:
        raise DimensionError(f"量纲不兼容: {from_unit} {ua.dim} -> {to_unit} {ub.dim}")
    return value * ua.scale / ub.scale


def conversion_factor(from_unit: str, to_unit: str) -> float:
    ua, ub = unit(from_unit), unit(to_unit)
    if ua.dim != ub.dim:
        raise DimensionError(f"量纲不兼容: {from_unit} -> {to_unit}")
    return ua.scale / ub.scale


def format_dim(d: Dim) -> str:
    names = ("M", "L", "T", "Th", "N", "I", "J")
    parts = [f"{n}^{e}" for n, e in zip(names, d) if e]
    return "·".join(parts) if parts else "1"


def check_homogeneous(terms) -> Dim:
    """一组 (coeff, unit) 相加必须同量纲; 返回公共量纲, 否则 DimensionError."""
    if not terms:
        raise DimensionError("空表达式")
    dims = {dim_of(u) for _, u in terms}
    if len(dims) != 1:
        raise DimensionError(f"加法/守恒式量纲不齐: {sorted(format_dim(d) for d in dims)}")
    return dims.pop()