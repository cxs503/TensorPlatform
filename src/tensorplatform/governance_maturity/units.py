"""单位字符串 → 量纲矢量 的登记表与解析器（治理成熟化节点的契约侧组件）。

为什么要自己解析单位
------------------
跨求解器耦合契约（``contracts/coupling/v*/``）里的端口只声明**单位字符串**
（``"m/s"``、``"kN*m"``、``"t/m^3"`` …）。这些字符串一旦写错，交换面的
``DimensionVector`` 就会与声明不符，而运行时 ``ContractValidator`` 比较的是
``DimensionVector``，**不会**发现"契约声明错了"这一类缺陷。所以要参与契约验证，
必须先把声明翻译成同一套量纲语言。

设计边界（IRON RULE：零领域语义）
--------------------------------
本模块只认识 SI 基本量纲 (M, L, T, Θ) 与工程单位的换算因子；不认识任何学科量。
新增单位必须加在 :data:`ATOMS` 里并写测试——不允许在调用点硬编码分支。

解析规则（够用即止，规则显式）
----------------------------
* 乘积用 ``*`` 或 ``·`` 连接: ``kN*m``；
* 商用 ``/`` 连接，右侧可带指数: ``kg/m^3``、``m/s^2``；
* 指数用 ``^`` 写整数（含负数）;
* 无单位用 ``"1"`` 表示;
* 不支持：括号、加号、开方——遇到就抛 :class:`UnitParseError`，**不静默降级**。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Tuple

from fractions import Fraction

# 基本量纲顺序固定，避免各处自定义顺序造成比较错误
DIM_ORDER: Tuple[str, ...] = ("M", "L", "T", "Theta")

# 原子单位：名字 -> (换算到 SI 的倍数, 量纲指数)
# 依据：SI 词头 + 常见工程单位定义（kn = 1852 m / 3600 s = 0.5144444… m/s）。
ATOMS: Dict[str, Tuple[float, Tuple[float, float, float, float]]] = {
    "1": (1.0, (0.0, 0.0, 0.0, 0.0)),
    "kg": (1.0, (1.0, 0.0, 0.0, 0.0)),
    "g": (1e-3, (1.0, 0.0, 0.0, 0.0)),
    "t": (1e3, (1.0, 0.0, 0.0, 0.0)),          # 工程制质量单位（吨）
    "m": (1.0, (0.0, 1.0, 0.0, 0.0)),
    "mm": (1e-3, (0.0, 1.0, 0.0, 0.0)),
    "km": (1e3, (0.0, 1.0, 0.0, 0.0)),
    "s": (1.0, (0.0, 0.0, 1.0, 0.0)),
    "K": (1.0, (0.0, 0.0, 0.0, 1.0)),
    "degC": (1.0, (0.0, 0.0, 0.0, 1.0)),       # 温差用；绝对温度偏移不在量纲里
    "Hz": (1.0, (0.0, 0.0, -1.0, 0.0)),
    "rpm": (1.0 / 60.0, (0.0, 0.0, -1.0, 0.0)),
    "rad": (1.0, (0.0, 0.0, 0.0, 0.0)),        # 无量纲角
    "deg": (1.0, (0.0, 0.0, 0.0, 0.0)),
    "N": (1.0, (1.0, 1.0, -2.0, 0.0)),
    "kN": (1e3, (1.0, 1.0, -2.0, 0.0)),
    "kn": (1852.0 / 3600.0, (0.0, 1.0, -1.0, 0.0)),  # knot
    "Pa": (1.0, (1.0, -1.0, -2.0, 0.0)),
    "kPa": (1e3, (1.0, -1.0, -2.0, 0.0)),
    "MPa": (1e6, (1.0, -1.0, -2.0, 0.0)),
    "J": (1.0, (1.0, 2.0, -2.0, 0.0)),
    "kJ": (1e3, (1.0, 2.0, -2.0, 0.0)),
    "W": (1.0, (1.0, 2.0, -3.0, 0.0)),
    "kW": (1e3, (1.0, 2.0, -3.0, 0.0)),
}

_EXP_RE = re.compile(r"^(?P<name>[A-Za-z]+)(?:\^(?P<exp>-?\d+))?$|^(?P<one>1)$")


class UnitParseError(ValueError):
    """单位字符串无法按 :data:`ATOMS` 解释（显式失败，不猜测）。"""


@dataclass(frozen=True)
class Unit:
    """解析结果：SI 换算倍数 + 量纲指数（Fraction 保证精确比较）。"""

    text: str
    scale: float
    dim: Tuple[Fraction, Fraction, Fraction, Fraction]

    def dimension_text(self) -> str:
        parts = []
        for name, exp in zip(DIM_ORDER, self.dim):
            if exp != 0:
                parts.append(f"{name}^{exp}")
        return "·".join(parts) if parts else "1"


def _parse_atom(token: str) -> Tuple[float, Tuple[Fraction, Fraction, Fraction, Fraction]]:
    token = token.strip()
    if not token:
        raise UnitParseError("空单位分量")
    m = _EXP_RE.match(token)
    if not m:
        raise UnitParseError(f"无法解析单位分量 {token!r}")
    if m.group("one"):
        name, exp = "1", 1
    else:
        name, exp = m.group("name"), int(m.group("exp") or 1)
    if name not in ATOMS:
        raise UnitParseError(f"未登记单位 {name!r}（请加进 units.ATOMS 并写测试）")
    scale, dim = ATOMS[name]
    return scale ** exp, tuple(Fraction(d).limit_denominator(60) * exp for d in dim)  # type: ignore[return-value]


def parse_unit(text: str) -> Unit:
    """把单位字符串解析成 :class:`Unit`；失败抛 :class:`UnitParseError`。"""
    if not isinstance(text, str) or not text.strip():
        raise UnitParseError(f"单位必须是非空字符串，得到 {text!r}")
    raw = text.strip().replace("·", "*").replace(" ", "")
    scale = 1.0
    dim = (Fraction(0), Fraction(0), Fraction(0), Fraction(0))
    # 先按 '/' 切分子/分母；分母内可能还有子乘积，仅允许单一分母段（规则见模块 docstring）
    has_div = "/" in raw
    numerator, _, denominator = raw.partition("/")
    if "/" in denominator:
        raise UnitParseError(f"不支持多级除法 {text!r}")
    if not numerator:
        raise UnitParseError(f"单位 {text!r} 缺少分子")
    segments = [(numerator, 1)] + ([(denominator, -1)] if has_div else [])
    for segment, sign in segments:
        if not segment:
            raise UnitParseError(f"单位 {text!r} 缺少分母")
        for token in segment.split("*"):
            atom_scale, atom_dim = _parse_atom(token)
            scale *= atom_scale ** sign
            dim = tuple(d + sign * e for d, e in zip(dim, atom_dim))  # type: ignore[assignment]
    return Unit(text=text, scale=scale, dim=dim)  # type: ignore[arg-type]


def to_dimension_vector(text: str):
    """单位字符串 → ``src.coupling.field_exchange.DimensionVector``（延迟导入避免循环依赖）。"""
    from src.coupling.field_exchange import DimensionVector  # 局部导入

    unit = parse_unit(text)
    return DimensionVector(
        M=float(unit.dim[0]), L=float(unit.dim[1]), T=float(unit.dim[2]), Theta=float(unit.dim[3])
    )


def same_dimension(a: str, b: str) -> bool:
    return parse_unit(a).dim == parse_unit(b).dim