"""量纲代数与单位标签校验 (纯 stdlib，零领域语义)。

单位标签是跨仓接口契约里最容易漂移的一环: 同一物理量在两个仓里分别写成
``kN`` 与 ``N``、``%`` 与 ``-``、``deg`` 与 ``rad``，字符串比较查不出来，
量纲比较才能。本模块把单位串解析成 (量纲向量, 尺度因子):

* 量纲向量: 基维 ``L/M/T/I/Th`` 上的 Fraction 指数 (支持 ``m^2``、``1/s``);
* 尺度因子: 换算到 SI 的倍数 (``kN`` → 1000)。

失败即抛 :class:`UnitError` (fail-closed): 未知单位标签不允许被静默当作
``dimensionless`` 放行，否则契约检查会把拼写错误洗成通过。
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

BASE_DIMS = ("L", "M", "T", "I", "Th")
DIMENSIONLESS = "1"

_L = {"L": Fraction(1)}
_M = {"M": Fraction(1)}
_T = {"T": Fraction(1)}
_I = {"I": Fraction(1)}
_TH = {"Th": Fraction(1)}


def _mul(a: dict[str, Fraction], b: dict[str, Fraction]) -> dict[str, Fraction]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, Fraction(0)) + v
    return {k: v for k, v in out.items() if v != 0}


def _scale(a: dict[str, Fraction], s: float) -> dict[str, Fraction]:
    return dict(a)


def _unit(dims: dict[str, Fraction], scale: float) -> tuple[dict[str, Fraction], float]:
    return (dims, float(scale))


_LENGTH = _L
_MASS = _M
_TIME = _T
_FORCE = _mul(_M, _mul(_L, {"T": Fraction(-2)}))
_ENERGY = _mul(_M, _mul(_mul(_L, _L), {"T": Fraction(-2)}))
_POWER = _mul(_M, _mul(_mul(_L, _L), {"T": Fraction(-3)}))
_PRESSURE = _mul(_M, _mul({"L": Fraction(-1)}, {"T": Fraction(-2)}))
_VOLT = _mul(_POWER, {"I": Fraction(-1)})
_SIEMENS = _mul({"I": Fraction(2)},
                _mul(_mul({"L": Fraction(-2)}, {"T": Fraction(3)}), {"M": Fraction(-1)}))

# 单位符号表: 只登记机制性换算, 不含任何领域量名。
UNITS: dict[str, tuple[dict[str, Fraction], float]] = {
    "1": _unit({}, 1.0),
    "-": _unit({}, 1.0),
    "": _unit({}, 1.0),
    # 长度
    "m": _unit(_LENGTH, 1.0),
    "km": _unit(_LENGTH, 1e3),
    "cm": _unit(_LENGTH, 1e-2),
    "mm": _unit(_LENGTH, 1e-3),
    "um": _unit(_LENGTH, 1e-6),
    "ft": _unit(_LENGTH, 0.3048),
    "nmi": _unit(_LENGTH, 1852.0),
    # 质量
    "kg": _unit(_MASS, 1.0),
    "g": _unit(_MASS, 1e-3),
    "t": _unit(_MASS, 1e3),
    # 时间
    "s": _unit(_TIME, 1.0),
    "ms": _unit(_TIME, 1e-3),
    "min": _unit(_TIME, 60.0),
    "h": _unit(_TIME, 3600.0),
    # 力 / 压强 / 能量 / 功率
    "N": _unit(_FORCE, 1.0),
    "kN": _unit(_FORCE, 1e3),
    "MN": _unit(_FORCE, 1e6),
    "Pa": _unit(_PRESSURE, 1.0),
    "kPa": _unit(_PRESSURE, 1e3),
    "MPa": _unit(_PRESSURE, 1e6),
    "J": _unit(_ENERGY, 1.0),
    "kJ": _unit(_ENERGY, 1e3),
    "W": _unit(_POWER, 1.0),
    "kW": _unit(_POWER, 1e3),
    "MW": _unit(_POWER, 1e6),
    # 电学
    "A": _unit(_I, 1.0),
    "mA": _unit(_I, 1e-3),
    "V": _unit(_VOLT, 1.0),
    "mV": _unit(_VOLT, 1e-3),
    "S": _unit(_SIEMENS, 1.0),
    # 角度与相对量
    "rad": _unit({}, 1.0),
    "deg": _unit({}, math.pi / 180.0),
    "%": _unit({}, 1e-2),
    # 温度
    "K": _unit(_TH, 1.0),
    # 频率
    "Hz": _unit({"T": Fraction(-1)}, 1.0),
}

_FACTOR_RE = re.compile(r"^([A-Za-z%_]+|1)(?:\^\(?(-?\d+(?:/\d+)?)\)?)?$")


class UnitError(ValueError):
    """单位标签不可解析 (或量纲不可比)。"""


def _strip_outer(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(expr) - 1:
                    balanced = False
                    break
        if not balanced:
            break
        expr = expr[1:-1].strip()
    return expr


def _split_top(expr: str, sep: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p for p in parts if p != ""]


def parse_unit(unit: str) -> tuple[dict[str, Fraction], float]:
    """解析单位串 → (量纲向量, SI 尺度因子)。未知符号抛 :class:`UnitError`。"""
    if not isinstance(unit, str):
        raise UnitError(f"unit must be str, got {type(unit).__name__}")
    expr = unit.strip().replace("·", "*").replace("⋅", "*").replace("×", "*")
    expr = expr.replace(" ", "")
    if expr in ("", "1", "-"):
        return ({}, 1.0)
    expr = _strip_outer(expr)
    div = _split_top(expr, "/")
    if len(div) > 1:
        dims, scale = parse_unit(div[0])
        for d in div[1:]:
            d_dims, d_scale = parse_unit(d)
            dims = _mul(dims, {k: -v for k, v in d_dims.items()})
            scale = scale / d_scale
        return (dims, scale)
    mul = _split_top(expr, "*")
    if len(mul) > 1:
        dims: dict[str, Fraction] = {}
        scale = 1.0
        for part in mul:
            p_dims, p_scale = parse_unit(part)
            dims = _mul(dims, p_dims)
            scale *= p_scale
        return (dims, scale)
    m = _FACTOR_RE.match(expr)
    if not m:
        raise UnitError(f"cannot parse unit {unit!r}")
    sym, exp = m.group(1), m.group(2)
    if sym not in UNITS:
        raise UnitError(f"unknown unit symbol {sym!r} in {unit!r}")
    dims, scale = UNITS[sym]
    e = Fraction(exp) if exp else Fraction(1)
    out: dict[str, Fraction] = {}
    for k, v in dims.items():
        nv = v * e
        if nv != 0:
            out[k] = nv
    return (out, scale ** float(e))


def dimension_signature(unit: str) -> str:
    """规范量纲签名 (用于跨仓比对): ``L^1 M^1 T^-2``。"""
    dims, _ = parse_unit(unit)
    if not dims:
        return DIMENSIONLESS
    return " ".join(f"{k}^{_fmt(frac)}" for k, frac in sorted(dims.items()))


def _fmt(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def same_dimension(a: str, b: str) -> bool:
    try:
        return dimension_signature(a) == dimension_signature(b)
    except UnitError:
        return False


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """同量纲换算: ``convert(3, "kN", "N") == 3000.0``。量纲不同抛错。"""
    d_from, s_from = parse_unit(from_unit)
    d_to, s_to = parse_unit(to_unit)
    if _norm(d_from) != _norm(d_to):
        raise UnitError(f"dimension mismatch: {from_unit!r} vs {to_unit!r}")
    return value * (s_from / s_to)


def _norm(d: dict[str, Fraction]) -> tuple:
    return tuple(sorted((k, v) for k, v in d.items() if v != 0))