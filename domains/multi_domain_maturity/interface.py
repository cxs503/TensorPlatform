"""跨域场交换接口契约：守恒性 + 版本兼容性。

设计
----
- ``FieldGrid``：均匀网格上的分段常值场（cell-centered）。
- ``FieldExchange``：粗网格 → 细网格（整数倍加密）的保守传递：
  细网格每个 cell 取所属粗 cell 的值。分段常值 + 精确体积积分
  ⇒ 积分守恒到机器精度（残差 ~1e-16 << 1e-8）。
- ``InterfaceContract``：场交换接口契约（版本 major.minor.patch、
  单位、坐标系、形状、dtype、符号约定）。
- 版本兼容规则：major 相同 ⇒ 兼容（minor 向后兼容）；major 不同 ⇒ 不兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class FieldGrid:
    """均匀网格 + 分段常值场（cell-centered）。"""

    name: str
    shape: tuple  # (nx, ny)
    dx: float
    dy: float
    values: np.ndarray  # (nx, ny)

    def __post_init__(self) -> None:
        if len(self.shape) != 2:
            raise ValueError("shape 必须 (nx, ny)")
        if self.dx <= 0 or self.dy <= 0:
            raise ValueError("dx, dy > 0")
        v = np.asarray(self.values, dtype=float)
        if v.shape != tuple(self.shape):
            raise ValueError("values 形状与 shape 不符")
        object.__setattr__(self, "values", v)

    @property
    def nx(self) -> int:
        return self.shape[0]

    @property
    def ny(self) -> int:
        return self.shape[1]

    def integral(self) -> float:
        """场积分 ∫ f dA（精确，分段常值）。"""
        return float(self.values.sum() * self.dx * self.dy)


@dataclass(frozen=True)
class InterfaceContract:
    """场交换接口契约。"""

    field_name: str
    units: str
    frame: str  # 坐标系标识
    shape: tuple
    dtype: str
    sign_convention: str
    major: int = 1
    minor: int = 0
    patch: int = 0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.major < 0 or self.minor < 0 or self.patch < 0:
            raise ValueError("版本号必须非负")
        if not self.field_name:
            raise ValueError("field_name 非空")

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def is_compatible_with(self, other: "InterfaceContract") -> tuple[bool, str]:
        """版本兼容性：major 相同 ⇒ 兼容；否则不兼容。"""
        if self.field_name != other.field_name:
            return False, f"字段名不同: {self.field_name} != {other.field_name}"
        if self.units != other.units:
            return False, f"单位不同: {self.units} != {other.units}"
        if self.frame != other.frame:
            return False, f"坐标系不同: {self.frame} != {other.frame}"
        if self.major != other.major:
            return False, (
                f"major 版本不同: {self.major} != {other.major} "
                f"({self.version} vs {other.version})"
            )
        return True, f"兼容（{self.version} vs {other.version}）"


def refine_grid(g: FieldGrid, ratio: int) -> FieldGrid:
    """整数倍加密（保守：细 cell 继承粗 cell 值）。"""
    if ratio < 1 or int(ratio) != ratio:
        raise ValueError("ratio 必须正整数")
    ratio = int(ratio)
    nx, ny = g.shape
    new = np.repeat(np.repeat(g.values, ratio, axis=0), ratio, axis=1)
    return FieldGrid(
        name=g.name,
        shape=(nx * ratio, ny * ratio),
        dx=g.dx / ratio,
        dy=g.dy / ratio,
        values=new,
    )


def conservation_residual(g_coarse: FieldGrid, g_fine: FieldGrid) -> float:
    """守恒残差 |∫f_coarse - ∫f_fine| / |∫f_coarse|。"""
    ic = g_coarse.integral()
    if abs(ic) < 1e-300:
        raise ValueError("粗网格积分为零，残差无定义")
    return abs(ic - g_fine.integral()) / abs(ic)


def exchange_field(
    g: FieldGrid, ratio: int, contract: Optional[InterfaceContract] = None
) -> tuple[FieldGrid, float]:
    """场交换：返回 (细网格场, 守恒残差)。可选契约校验。"""
    gf = refine_grid(g, ratio)
    res = conservation_residual(g, gf)
    if contract is not None:
        if contract.shape != g.shape:
            raise ValueError(f"契约形状 {contract.shape} 与场 {g.shape} 不符")
        if contract.dtype != "float64":
            raise ValueError("契约 dtype 必须 float64")
    return gf, res


def check_contracts(
    a: InterfaceContract, b: InterfaceContract
) -> tuple[bool, str]:
    """双向版本兼容性检查。"""
    ok1, msg1 = a.is_compatible_with(b)
    ok2, msg2 = b.is_compatible_with(a)
    if ok1 and ok2:
        return True, msg1
    return False, f"{msg1}; {msg2}"
