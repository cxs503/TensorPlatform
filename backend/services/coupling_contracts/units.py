"""单位制登记表 + 单位表达式解析 + 可追溯换算 (unit systems & conversion).

覆盖三种"输入制式": ``SI`` / ``IMPERIAL`` (英制) / ``MIXED`` (混合, 例如
SI 几何 + 英制功率). 平台内部一律先归一化到 SI, 输出时再渲染到目标制式,
因此任意 ``A -> B -> A`` 往返只经过一次乘除对, 不产生累积误差。

为什么不用现成的单位库
----------------------
1. 需要**显式白名单**: 拼写错误 (``"kN"`` vs ``"kn"``) 必须报错而不是被前缀
   规则猜成同一个单位 —— 假通过比没有校验更危险。
2. 需要**换算链可追溯**: 每一步 (源值 -> SI 枢轴 -> 目标) 的系数、量纲都要能
   落盘进报告, 这是"跨求解器数据交换可审计"的硬要求。
3. 需要**温标偏置**支持: degC/degF 是仿射单位 (scale + offset), 与只乘系数的
   复合单位不能混用, 否则 20 degC 乘 2 得 40 degC 这种错误会静默溜过。

仿射单位规则
------------
``Unit(scale, offset)`` 定义 ``SI_value = value * scale + offset`` (offset 用 SI
单位表示)。复合表达式里出现 offset != 0 的单位直接 :class:`DimensionError`。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .dimension import (
    Dim,
    DimensionError,
    ZERO,
    dim as _dim,
    format_dim,
    mul,
    div,
    pow_dim,
)

class UnknownUnitError(KeyError):
    """单位未在登记表内 (显式白名单, 拒绝字符近似)."""


@dataclass(frozen=True)
class Unit:
    """一个已登记单位: ``SI_value = value * scale + offset``."""

    symbol: str
    #: 乘到 SI 基准的系数 (Fraction 语义的浮点, 误差 <= 1 ulp)
    scale: float
    dim: Dim
    system: str = "SI"  # SI / IMPERIAL / MIXED / NAUTICAL
    offset: float = 0.0  # 仿射偏置, 单位 = SI 单位
    note: str = ""

    @property
    def affine(self) -> bool:
        return self.offset != 0.0


def _u(symbol: str, scale: float, dim: Dim, system: str = "SI", *, offset: float = 0.0,
       note: str = "") -> Unit:
    return Unit(symbol, float(scale), dim, system, float(offset), note)


# SI 基本/导出单位 + 常用倍数
_D = _dim
_REGISTRY_LIST: List[Unit] = [
    # --- 无量纲 / 角度 ------------------------------------------------------
    _u("1", 1.0, ZERO, note="纯数 / 比值"),
    _u("-", 1.0, ZERO, note="对数比值"),
    _u("%", 0.01, ZERO, note="百分数"),
    _u("rad", 1.0, ZERO, note="平面角, 数值求解器按无量纲处理"),
    _u("deg", math.pi / 180.0, ZERO, note="角度"),
    _u("deg/s", math.pi / 180.0, _D(T=-1), note="角速度 (度制)"),
    _u("deg/s^2", math.pi / 180.0, _D(T=-2), note="角加速度 (度制)"),
    # --- 长度 --------------------------------------------------------------
    _u("m", 1.0, _D(L=1)),
    _u("mm", 1e-3, _D(L=1)),
    _u("cm", 1e-2, _D(L=1)),
    _u("um", 1e-6, _D(L=1)),
    _u("km", 1e3, _D(L=1)),
    _u("in", 0.0254, _D(L=1), "IMPERIAL", note="英寸"),
    _u("ft", 0.3048, _D(L=1), "IMPERIAL", note="英尺"),
    _u("yd", 0.9144, _D(L=1), "IMPERIAL"),
    _u("mi", 1609.344, _D(L=1), "IMPERIAL", note="法定英里"),
    _u("nmi", 1852.0, _D(L=1), "NAUTICAL", note="海里"),
    # --- 面积 / 体积 -------------------------------------------------------
    _u("m^2", 1.0, _D(L=2)),
    _u("m^3", 1.0, _D(L=3)),
    _u("cm^2", 1e-4, _D(L=2)),
    _u("mm^2", 1e-6, _D(L=2)),
    _u("ft^2", 0.09290304, _D(L=2), "IMPERIAL"),
    _u("in^2", 0.00064516, _D(L=2), "IMPERIAL"),
    _u("ft^3", 0.028316846592, _D(L=3), "IMPERIAL"),
    _u("L", 1e-3, _D(L=3), note="升"),
    # --- 时间 --------------------------------------------------------------
    _u("s", 1.0, _D(T=1)),
    _u("ms", 1e-3, _D(T=1)),
    _u("min", 60.0, _D(T=1)),
    _u("h", 3600.0, _D(T=1)),
    _u("day", 86400.0, _D(T=1)),
    # --- 质量 --------------------------------------------------------------
    _u("kg", 1.0, _D(M=1)),
    _u("g", 1e-3, _D(M=1)),
    _u("mg", 1e-6, _D(M=1)),
    _u("t", 1000.0, _D(M=1), note="公吨"),
    _u("lb", 0.45359237, _D(M=1), "IMPERIAL", note="磅 (质量)"),
    _u("oz", 0.028349523125, _D(M=1), "IMPERIAL"),
    _u("slug", 14.593902937206364, _D(M=1), "IMPERIAL"),
    # --- 运动学 ------------------------------------------------------------
    _u("m/s", 1.0, _D(L=1, T=-1)),
    _u("mm/s", 1e-3, _D(L=1, T=-1)),
    _u("km/h", 1000.0 / 3600.0, _D(L=1, T=-1)),
    _u("ft/s", 0.3048, _D(L=1, T=-1), "IMPERIAL"),
    _u("mph", 1609.344 / 3600.0, _D(L=1, T=-1), "IMPERIAL"),
    _u("kn", 1852.0 / 3600.0, _D(L=1, T=-1), "NAUTICAL", note="节 (knot)"),
    _u("m/s^2", 1.0, _D(L=1, T=-2)),
    _u("ft/s^2", 0.3048, _D(L=1, T=-2), "IMPERIAL"),
    _u("g0", 9.80665, _D(L=1, T=-2), note="标准重力加速度"),
    _u("rad/s", 1.0, _D(T=-1)),
    _u("rad/s^2", 1.0, _D(T=-2)),
    _u("1/s", 1.0, _D(T=-1)),
    _u("m^3/s", 1.0, _D(L=3, T=-1)),
    _u("m^2/s", 1.0, _D(L=2, T=-1)),
    _u("1/m", 1.0, _D(L=-1)),
    # --- 力 / 力矩 / 惯量 --------------------------------------------------
    _u("N", 1.0, _D(M=1, L=1, T=-2)),
    _u("mN", 1e-3, _D(M=1, L=1, T=-2)),
    _u("kN", 1e3, _D(M=1, L=1, T=-2)),
    _u("MN", 1e6, _D(M=1, L=1, T=-2)),
    _u("lbf", 4.4482216152605, _D(M=1, L=1, T=-2), "IMPERIAL"),
    _u("kip", 4448.2216152605, _D(M=1, L=1, T=-2), "IMPERIAL"),
    _u("kgf", 9.80665, _D(M=1, L=1, T=-2), "MIXED", note="千克力 (工程混合制)"),
    _u("N*m", 1.0, _D(M=1, L=2, T=-2)),
    _u("kN*m", 1e3, _D(M=1, L=2, T=-2)),
    _u("lbf*ft", 1.3558179483314004, _D(M=1, L=2, T=-2), "IMPERIAL"),
    _u("kg*m^2", 1.0, _D(M=1, L=2)),
    _u("slug*ft^2", 1.3558179483314004 * 0.3048, _D(M=1, L=2), "IMPERIAL"),
    _u("N*s", 1.0, _D(M=1, L=1, T=-1), note="冲量"),
    _u("N/m", 1.0, _D(M=1, T=-2), note="刚度/线载荷"),
    _u("N/(m/s)", 1.0, _D(M=1, T=-1), note="阻尼"),
    _u("N*m*s", 1.0, _D(M=1, L=2, T=-1), note="转动阻尼"),
    # --- 压力 / 应力 / 密度 / 粘性 ----------------------------------------
    _u("Pa", 1.0, _D(M=1, L=-1, T=-2)),
    _u("kPa", 1e3, _D(M=1, L=-1, T=-2)),
    _u("MPa", 1e6, _D(M=1, L=-1, T=-2)),
    _u("GPa", 1e9, _D(M=1, L=-1, T=-2)),
    _u("bar", 1e5, _D(M=1, L=-1, T=-2), "MIXED"),
    _u("atm", 101325.0, _D(M=1, L=-1, T=-2), "MIXED"),
    _u("psi", 6894.757293168361, _D(M=1, L=-1, T=-2), "IMPERIAL"),
    _u("ksi", 6894757.293168361, _D(M=1, L=-1, T=-2), "IMPERIAL"),
    _u("psf", 47.88025898033584, _D(M=1, L=-1, T=-2), "IMPERIAL"),
    _u("kg/m^3", 1.0, _D(M=1, L=-3)),
    _u("t/m^3", 1000.0, _D(M=1, L=-3), "MIXED"),
    _u("lb/ft^3", 16.018463373960138, _D(M=1, L=-3), "IMPERIAL"),
    _u("Pa*s", 1.0, _D(M=1, L=-1, T=-1), note="动力粘度"),
    _u("cP", 1e-3, _D(M=1, L=-1, T=-1), "MIXED"),
    _u("cSt", 1e-6, _D(L=2, T=-1), "MIXED", note="运动粘度 mm^2/s"),
    _u("kg/m^2/s", 1.0, _D(M=1, L=-2, T=-1)),
    _u("kg/s", 1.0, _D(M=1, T=-1)),
    # --- 能量 / 功率 ------------------------------------------------------
    _u("J", 1.0, _D(M=1, L=2, T=-2)),
    _u("kJ", 1e3, _D(M=1, L=2, T=-2)),
    _u("MJ", 1e6, _D(M=1, L=2, T=-2)),
    _u("kWh", 3.6e6, _D(M=1, L=2, T=-2), "MIXED"),
    _u("cal", 4.184, _D(M=1, L=2, T=-2), "MIXED"),
    _u("BTU", 1055.05585262, _D(M=1, L=2, T=-2), "IMPERIAL"),
    _u("kiloton", 4.184e12, _D(M=1, L=2, T=-2), "MIXED", note="TNT 当量"),
    _u("W", 1.0, _D(M=1, L=2, T=-3)),
    _u("kW", 1e3, _D(M=1, L=2, T=-3)),
    _u("MW", 1e6, _D(M=1, L=2, T=-3)),
    _u("hp", 745.6998715822702, _D(M=1, L=2, T=-3), "IMPERIAL"),
    _u("W/m^2", 1.0, _D(M=1, T=-3)),
    _u("W/(m*K)", 1.0, _D(M=1, L=1, T=-3, Th=-1)),
    _u("J/(m^2*K)", 1.0, _D(M=1, T=-2, Th=-1)),
    _u("kJ/kg", 1e3, _D(L=2, T=-2)),
    _u("J/kg", 1.0, _D(L=2, T=-2)),
    # --- 频率 / 谱 ---------------------------------------------------------
    _u("Hz", 1.0, _D(T=-1)),
    _u("kHz", 1e3, _D(T=-1)),
    _u("rpm", 2.0 * math.pi / 60.0, _D(T=-1), "MIXED", note="转/分 -> rad/s"),
    _u("N^2/Hz", 1.0, _D(M=2, L=2, T=-3), note="力谱密度"),
    _u("m^2/Hz", 1.0, _D(L=2, T=1), note="位移谱密度"),
    _u("Pa^2/Hz", 1.0, _D(M=2, L=-2, T=-3), note="声压谱密度"),
    # --- 温度 / 电磁 -------------------------------------------------------
    _u("K", 1.0, _D(Th=1)),
    _u("degC", 1.0, _D(Th=1), "MIXED", offset=273.15),
    _u("degF", 5.0 / 9.0, _D(Th=1), "IMPERIAL", offset=255.3722222222222),
    _u("A", 1.0, _D(I=1)),
    _u("mol", 1.0, _D(N=1)),
    _u("cd", 1.0, _D(J=1)),
]

#: 单位登记表 (symbol -> Unit); 同一 symbol 不得重复定义
UNITS: Dict[str, Unit] = {}
for _unit in _REGISTRY_LIST:
    if _unit.symbol in UNITS:  # pragma: no cover - 由测试覆盖
        raise RuntimeError(f"单位重复登记: {_unit.symbol!r}")
    UNITS[_unit.symbol] = _unit

SYSTEMS: Tuple[str, ...] = ("SI", "IMPERIAL", "MIXED", "NAUTICAL")


def unit_of(symbol: str) -> Unit:
    """取已登记单位; 未登记 -> :class:`UnknownUnitError`."""
    try:
        return UNITS[symbol]
    except KeyError as exc:
        raise UnknownUnitError(
            f"单位未登记: {symbol!r} (显式白名单, 拒绝字符近似/前缀猜测)"
        ) from exc


# ---------------------------------------------------------------------------
# 单位表达式解析 (支持括号 / 乘除 / 整数幂)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnitExpr:
    """解析后的单位表达式: 复合系数 + 量纲 + 规范化字符串."""

    text: str
    scale: float
    dim: Dim
    affine: bool = False
    offset: float = 0.0  # 仿射偏置 (SI 单位); 仅当 affine=True 时非零

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "scale_to_si": self.scale,
            "dim": format_dim(self.dim),
            "affine": self.affine,
            "offset": self.offset,
        }


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.tokens: List[str] = _tokenize(text)
        self.pos = 0
        self.used: List[str] = []

    # -- token 流 --
    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, expect: Optional[str] = None) -> str:
        tok = self.peek()
        if tok is None:
            raise DimensionError(f"单位表达式意外结束: {self.text!r}")
        if expect is not None and tok != expect:
            raise DimensionError(f"单位表达式期望 {expect!r} 得到 {tok!r}: {self.text!r}")
        self.pos += 1
        return tok

    def parse(self) -> Tuple[float, Dim, bool]:
        scale, d, affine = self.product()
        if self.peek() is not None:
            raise DimensionError(f"单位表达式尾部多余 token {self.peek()!r}: {self.text!r}")
        return scale, d, affine

    def product(self) -> Tuple[float, Dim, bool]:
        scale, d, affine = self.term()
        while self.peek() in ("*", "/"):
            op = self.take()
            s2, d2, a2 = self.term()
            if op == "*":
                scale *= s2
                d = mul(d, d2)
            else:
                scale /= s2
                d = div(d, d2)
            affine = affine or a2
        return scale, d, affine

    def term(self) -> Tuple[float, Dim, bool]:
        scale, d, affine = self.atom()
        if self.peek() == "^":
            self.take("^")
            exp_tok = self.take()
            try:
                exp = int(exp_tok)
            except ValueError as exc:
                raise DimensionError(f"单位幂指数必须为整数: {exp_tok!r} in {self.text!r}") from exc
            scale **= exp
            d = pow_dim(d, exp)
            if affine and exp != 1:
                raise DimensionError(f"仿射单位 (温标) 不可取幂: {self.text!r}")
        return scale, d, affine

    def atom(self) -> Tuple[float, Dim, bool]:
        tok = self.peek()
        if tok is None:
            raise DimensionError(f"单位表达式不完整: {self.text!r}")
        if tok == "(":
            self.take("(")
            scale, d, affine = self.product()
            self.take(")")
            return scale, d, affine
        if tok in ("*", "/", ")", "^"):
            raise DimensionError(f"单位表达式缺少单位符号: {self.text!r}")
        self.take()
        self.used.append(tok)
        try:
            num = float(tok)
        except ValueError:
            u = unit_of(tok)
            return u.scale, u.dim, u.affine
        # 纯数字因子 (例如 "1000*m") 允许, 视为无量纲系数
        return num, ZERO, False


def _tokenize(text: str) -> List[str]:
    out: List[str] = []
    buf = ""
    for ch in text:
        if ch in "*/()^":
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    if not out:
        raise DimensionError(f"单位表达式为空: {text!r}")
    return out


_PARSE_CACHE: Dict[str, UnitExpr] = {}


def parse_unit(text: str) -> UnitExpr:
    """解析单位表达式, 如 ``"kN*m"``、``"kg/(m*s^2)"``、``"deg/s"``.

    单 token 的已登记单位直接命中缓存 (最快路径); 复合表达式走递归下降。
    仿射单位出现在复合表达式里 -> :class:`DimensionError`。
    """
    if not isinstance(text, str) or not text.strip():
        raise DimensionError(f"单位表达式非法: {text!r}")
    key = text.replace(" ", "")
    cached = _PARSE_CACHE.get(key)
    if cached is not None:
        return cached
    if key in UNITS:
        u = UNITS[key]
        expr = UnitExpr(key, u.scale, u.dim, u.affine, u.offset)
    else:
        scale, d, affine = _Parser(key).parse()
        if affine and not _single_token(key):
            raise DimensionError(f"仿射单位 (温标) 不可参与复合: {text!r}")
        expr = UnitExpr(key, scale, d, affine)
    _PARSE_CACHE[key] = expr
    return expr


def _single_token(text: str) -> bool:
    return all(ch not in "*/()^" for ch in text)


def dim_of_unit(text: str) -> Dim:
    return parse_unit(text).dim


# ---------------------------------------------------------------------------
# 换算 (带可追溯链)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConversionStep:
    """换算链的一步: 记录方向、系数与量纲, 供审计溯源."""

    label: str
    value: float
    factor: float = 1.0
    offset_applied: float = 0.0
    dim: Dim = ZERO

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "value": self.value,
            "factor": self.factor,
            "offset_applied": self.offset_applied,
            "dim": format_dim(self.dim),
        }


@dataclass(frozen=True)
class ConversionTrace:
    """一次完整换算的溯源链 (源 -> SI 枢轴 -> 目标)."""

    source_unit: str
    target_unit: str
    source_value: float
    si_value: float
    target_value: float
    dim: Dim
    steps: Tuple[ConversionStep, ...] = ()

    @property
    def factor(self) -> float:
        if self.source_value == 0.0:
            return float("nan")
        return self.target_value / self.source_value

    def to_dict(self) -> dict:
        return {
            "source_unit": self.source_unit,
            "target_unit": self.target_unit,
            "source_value": self.source_value,
            "si_value": self.si_value,
            "target_value": self.target_value,
            "factor": self.factor,
            "dim": format_dim(self.dim),
            "steps": [s.to_dict() for s in self.steps],
        }


def to_si(value: float, from_unit: str) -> float:
    """任意登记单位 -> SI 数值 (仿射单位含偏置)."""
    u = parse_unit(from_unit)
    return float(value) * u.scale + u.offset


def from_si(value_si: float, to_unit: str) -> float:
    """SI 数值 -> 目标单位 (仿射单位含偏置)."""
    u = parse_unit(to_unit)
    return (float(value_si) - u.offset) / u.scale


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """单位换算; 量纲不兼容 -> :class:`DimensionError` (契约违约必须判负)."""
    a, b = parse_unit(from_unit), parse_unit(to_unit)
    if a.dim != b.dim:
        raise DimensionError(
            f"量纲不兼容: {from_unit!r} ({format_dim(a.dim)}) -> "
            f"{to_unit!r} ({format_dim(b.dim)})"
        )
    if not a.affine and not b.affine:
        # 纯比例: 一次乘除对, 往返相对误差 ~1 ulp
        return float(value) * a.scale / b.scale
    return from_si(to_si(value, from_unit), to_unit)


def conversion_factor(from_unit: str, to_unit: str) -> float:
    """换算系数 (仿射单位只对纯比例部分有意义, 故禁止)."""
    a, b = parse_unit(from_unit), parse_unit(to_unit)
    if a.dim != b.dim:
        raise DimensionError(f"量纲不兼容: {from_unit!r} -> {to_unit!r}")
    if a.affine or b.affine:
        raise DimensionError(f"仿射单位无单一换算系数: {from_unit!r} -> {to_unit!r}")
    return a.scale / b.scale


def convert_traced(value: float, from_unit: str, to_unit: str) -> ConversionTrace:
    """带溯源链的换算: 显式记录 SI 枢轴值与两级系数."""
    a, b = parse_unit(from_unit), parse_unit(to_unit)
    if a.dim != b.dim:
        raise DimensionError(
            f"量纲不兼容: {from_unit!r} ({format_dim(a.dim)}) -> "
            f"{to_unit!r} ({format_dim(b.dim)})"
        )
    si = float(value) * a.scale + a.offset
    target = (si - b.offset) / b.scale
    steps = (
        ConversionStep(f"source:{from_unit}", float(value), 1.0, 0.0, a.dim),
        ConversionStep("pivot:SI", si, a.scale, a.offset, a.dim),
        ConversionStep(f"target:{to_unit}", target, 1.0 / b.scale, -b.offset, b.dim),
    )
    return ConversionTrace(from_unit, to_unit, float(value), si, target, a.dim, steps)


def convert_between_systems(value: float, from_unit: str, system: str) -> Tuple[float, str]:
    """把 ``value`` 从任意单位渲染到目标制式下该量纲的首选单位.

    返回 ``(换算后数值, 目标单位符号)``; 该制式没有登记首选单位时退回 SI。
    """
    u = parse_unit(from_unit)
    target = preferred_unit(u.dim, system)
    return convert(value, from_unit, target), target


# ---------------------------------------------------------------------------
# 各制式的首选输出单位 (按量纲索引)
# ---------------------------------------------------------------------------
_PREFERRED: Dict[str, Dict[Dim, str]] = {
    "SI": {
        _D(M=1, L=1, T=-2): "N",
        _D(M=1, L=2, T=-2): "N*m",
        _D(M=1, L=-1, T=-2): "Pa",
        _D(M=1, L=-3): "kg/m^3",
        _D(M=1, L=-1, T=-1): "Pa*s",
        _D(L=1): "m",
        _D(L=2): "m^2",
        _D(L=3): "m^3",
        _D(L=1, T=-1): "m/s",
        _D(L=1, T=-2): "m/s^2",
        _D(T=-1): "1/s",
        _D(T=1): "s",
        _D(M=1): "kg",
        _D(M=1, L=2): "kg*m^2",
        _D(M=1, L=2, T=-3): "W",
        _D(M=1, L=2, T=-2): "J",
        _D(Th=1): "K",
        _D(L=3, T=-1): "m^3/s",
        _D(M=1, T=-1): "N/(m/s)",
        _D(M=1, L=1, T=-1): "N*s",
        _D(M=1, T=-2): "N/m",
    },
    "IMPERIAL": {
        _D(M=1, L=1, T=-2): "lbf",
        _D(M=1, L=2, T=-2): "lbf*ft",
        _D(M=1, L=-1, T=-2): "psi",
        _D(M=1, L=-3): "lb/ft^3",
        _D(L=1): "ft",
        _D(L=2): "ft^2",
        _D(L=3): "ft^3",
        _D(L=1, T=-1): "ft/s",
        _D(L=1, T=-2): "ft/s^2",
        _D(M=1): "lb",
        _D(M=1, L=2): "slug*ft^2",
        _D(M=1, L=2, T=-3): "hp",
        _D(M=1, L=2, T=-2): "BTU",
        _D(Th=1): "degF",
    },
    "NAUTICAL": {
        _D(L=1): "nmi",
        _D(L=1, T=-1): "kn",
        _D(L=3): "m^3",
        _D(M=1): "t",
        _D(M=1, L=1, T=-2): "kN",
        _D(M=1, L=-1, T=-2): "kPa",
    },
    "MIXED": {
        _D(M=1, L=1, T=-2): "kN",
        _D(M=1, L=-1, T=-2): "MPa",
        _D(M=1, L=-3): "t/m^3",
        _D(M=1, L=-1, T=-1): "cP",
        _D(L=2, T=-1): "cSt",
        _D(T=-1): "rpm",
        _D(M=1, L=2, T=-3): "kW",
        _D(L=1, T=-1): "kn",
        _D(Th=1): "degC",
        _D(L=1): "mm",
    },
}


def preferred_unit(d: Dim, system: str) -> str:
    """某制式下某量纲的首选单位; 未登记则退回 SI 首选, 再退回无量纲 ``1``."""
    if system not in _PREFERRED:
        raise DimensionError(f"未知单位制: {system!r} (可选 {SYSTEMS})")
    if d in _PREFERRED[system]:
        return _PREFERRED[system][d]
    if d in _PREFERRED["SI"]:
        return _PREFERRED["SI"][d]
    return "1"


def check_registry_consistency() -> List[str]:
    """自检: 每个首选单位都必须与索引量纲一致 (防止登记表内部漂移)."""
    problems: List[str] = []
    for system, table in _PREFERRED.items():
        for d, sym in table.items():
            try:
                got = dim_of_unit(sym)
            except UnknownUnitError as exc:
                problems.append(f"{system}: 首选单位 {sym!r} 未登记 ({exc})")
                continue
            if got != d:
                problems.append(
                    f"{system}: {sym!r} 量纲 {format_dim(got)} != 索引 {format_dim(d)}"
                )
    return problems


def units_of_system(system: str) -> List[Unit]:
    """列出某制式下的全部单位 (报告/审计用)."""
    return [u for u in UNITS.values() if u.system == system]

# ---------------------------------------------------------------------------
# SI 规范单位串 (由量纲合成, 不依赖首选表, 因此对任意量纲都成立)
# ---------------------------------------------------------------------------
def si_unit_name(d: Dim) -> str:
    """由量纲合成规范 SI 单位串, 如 ``M·L^2·T^-2`` -> ``"kg*m^2/s^2"``."""
    d = require_dim_(d)
    num: List[str] = []
    den: List[str] = []
    base_units = {
        "M": "kg",
        "L": "m",
        "T": "s",
        "I": "A",
        "Th": "K",
        "N": "mol",
        "J": "cd",
    }
    for sym, exp in zip(("M", "L", "T", "I", "Th", "N", "J"), d):
        if exp == 0:
            continue
        u = base_units[sym]
        e = abs(exp)
        tok = u if e == 1 else f"{u}^{e}"
        (num if exp > 0 else den).append(tok)
    if not num and not den:
        return "1"
    if not num:
        num = ["1"]
    body = "*".join(num)
    if den:
        body += "/" + ("*".join(den) if len(den) == 1 else f"({ '*'.join(den) })")
    return body


def require_dim_(d: Dim) -> Dim:
    from .dimension import require_dim as _r

    return _r(d)
