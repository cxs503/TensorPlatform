"""交换载荷封装 (exchange envelope): 值 + 单位 + 坐标系 + 量纲, 可归一化.

跨求解器交换的实际数据长这样::

    {"name": "force", "value": [12.0, -3.5, 0.4], "unit": "kN",
     "frame": "hull_fixed", "tensor_order": 1}

三种常见事故在 :func:`decode` 里被拦住:
  1. 单位拼错 / 量纲与声明不符 -> ``UNKNOWN_UNIT`` / ``DIM_MISMATCH``;
  2. 矢量缺坐标系 (下游不知道往哪个方向上投影) -> ``MISSING_FRAME``;
  3. 全零输入被当成正常物理量 -> 不崩, 但打 ``degenerate=True`` 并给出
     ``no physical meaning`` 结论 (验收负控要求)。

归一化一律走 "载荷单位 -> SI" 一步, 再按需要渲染到目标制式; 因此任意
往返 (载荷 -> SI -> 目标 -> 载荷) 只经过一次乘除对, 误差 ~1 ulp。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from .dimension import Dim, DimensionError, format_dim, parse_dim
from .frames import FrameGraph
from .units import (
    UnknownUnitError,
    convert,
    convert_traced,
    ConversionTrace,
    dim_of_unit,
    preferred_unit,
    si_unit_name,
    to_si,
)

SCHEMA_ID = "physical-quantity/v1"
_VECTOR_LEN = {1: 3, 2: 9}


class QuantityError(ValueError):
    """交换载荷不合法."""


@dataclass(frozen=True)
class Quantity:
    """一个带单位/坐标系/张量阶的物理量 (标量或矢量/张量)."""

    name: str
    value: object  # float | tuple[float, ...]
    unit: str
    frame: Optional[str] = None
    tensor_order: int = 0
    declared_dim: Optional[str] = None

    # -- 派生 --
    @property
    def dim(self) -> Dim:
        return dim_of_unit(self.unit)

    @property
    def components(self) -> Tuple[float, ...]:
        if isinstance(self.value, (list, tuple)):
            return tuple(float(v) for v in self.value)
        return (float(self.value),)  # type: ignore[arg-type]

    @property
    def is_zero(self) -> bool:
        """全部分量为 0 (含 -0.0)."""
        return all(c == 0.0 for c in self.components)

    @property
    def degenerate(self) -> bool:
        """全零但量纲非无量纲 -> 数值上合法、物理上无意义 (负控判据)."""
        return self.is_zero and self.dim != (0, 0, 0, 0, 0, 0, 0)

    @property
    def physical_meaning(self) -> str:
        if not self.degenerate:
            return "defined"
        return "undefined: all-zero magnitude, ratios/angles not computable"

    # -- 归一化 --
    def to_si(self) -> "Quantity":
        """归一化到 SI 规范单位 (标量/矢量逐分量)."""
        if isinstance(self.value, (list, tuple)):
            vals = tuple(to_si(v, self.unit) for v in self.components)
        else:
            vals = to_si(float(self.value), self.unit)  # type: ignore[arg-type]
        return Quantity(self.name, vals, si_unit_name(self.dim), self.frame,
                        self.tensor_order, format_dim(self.dim))

    def in_unit(self, unit: str) -> "Quantity":
        """渲染到指定单位 (量纲必须兼容)."""
        if isinstance(self.value, (list, tuple)):
            vals = tuple(convert(v, self.unit, unit) for v in self.components)
        else:
            vals = convert(float(self.value), self.unit, unit)  # type: ignore[arg-type]
        return Quantity(self.name, vals, unit, self.frame, self.tensor_order,
                        format_dim(self.dim))

    def in_system(self, system: str) -> "Quantity":
        """渲染到目标单位制的首选单位."""
        return self.in_unit(preferred_unit(self.dim, system))

    def trace_to(self, unit: str) -> ConversionTrace:
        if self.tensor_order != 0:
            raise QuantityError("转换链只对标量有单一定义 (矢量请逐分量)")
        return convert_traced(float(self.value), self.unit, unit)  # type: ignore[arg-type]

    def with_frame(self, frame: str, frames: FrameGraph) -> "Quantity":
        """坐标系变换 (仅一阶矢量); 记录经过的帧序列."""
        if self.tensor_order != 1:
            raise QuantityError("坐标系变换只支持一阶矢量 (tensor_order=1)")
        if not self.frame:
            raise QuantityError("源载荷未声明坐标系, 无法变换")
        out = frames.transform_vector(self.components, self.frame, frame)
        return Quantity(self.name, out, self.unit, frame, 1, self.declared_dim)

    def magnitude(self) -> float:
        """矢量模长 (标量取绝对值)."""
        if self.tensor_order == 0:
            return abs(self.components[0])
        return math.sqrt(sum(c * c for c in self.components))

    def unit_vector(self) -> Optional[Tuple[float, ...]]:
        """单位方向矢量; 全零 -> ``None`` (不抛异常, 负控要求不崩)."""
        m = self.magnitude()
        if m == 0.0:
            return None
        return tuple(c / m for c in self.components)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_ID,
            "name": self.name,
            "value": list(self.value) if isinstance(self.value, (list, tuple)) else self.value,
            "unit": self.unit,
            "frame": self.frame,
            "tensor_order": self.tensor_order,
            "resolved_dim": format_dim(self.dim),
            "declared_dim": self.declared_dim,
            "is_zero": self.is_zero,
            "physical_meaning": self.physical_meaning,
        }


def make(name: str, value: object, unit: str, *, frame: Optional[str] = None,
         tensor_order: Optional[int] = None, declared_dim: Optional[str] = None) -> Quantity:
    """构造载荷并做形状校验 (矢量必须 3 分量, 二阶张量必须 9 分量)."""
    order = tensor_order
    if order is None:
        order = 0 if not isinstance(value, (list, tuple)) else (1 if len(value) == 3 else 2)
    if order == 0:
        if isinstance(value, (list, tuple)):
            raise QuantityError(f"{name}: tensor_order=0 但给了 {len(value)} 个分量")
    else:
        need = _VECTOR_LEN.get(order)
        if need is None:
            raise QuantityError(f"{name}: 不支持的 tensor_order {order}")
        if not isinstance(value, (list, tuple)) or len(value) != need:
            raise QuantityError(
                f"{name}: tensor_order={order} 需要 {need} 个分量, 收到 {value!r}")
        for v in value:
            if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise QuantityError(f"{name}: 分量非法 {v!r}")
    if order >= 1 and not frame:
        raise QuantityError(f"{name}: tensor_order={order} 必须声明坐标系")
    return Quantity(name, value if order else float(value), unit, frame, order, declared_dim)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# JSON 封装 (交换格式)
# ---------------------------------------------------------------------------
def decode(payload: Mapping[str, object], *, frames: Optional[FrameGraph] = None,
           strict_dim: bool = True) -> Quantity:
    """解码交换载荷; 结构/单位/坐标系错误统一抛 :class:`QuantityError`."""
    if not isinstance(payload, Mapping):
        raise QuantityError(f"载荷必须是对象, 收到 {type(payload).__name__}")
    for key in ("name", "value", "unit"):
        if key not in payload:
            raise QuantityError(f"载荷缺少必填键 {key!r}: {payload!r}")
    name = str(payload["name"])
    unit = str(payload["unit"])
    try:
        dim = dim_of_unit(unit)
    except (UnknownUnitError, DimensionError) as exc:
        raise QuantityError(f"{name}: {exc}") from exc
    declared = payload.get("declared_dim")
    if declared not in (None, ""):
        try:
            dd = parse_dim(str(declared))
        except DimensionError as exc:
            raise QuantityError(f"{name}: declared_dim 不可解析 ({exc})") from exc
        if strict_dim and dd != dim:
            raise QuantityError(
                f"{name}: declared_dim={format_dim(dd)} 与单位 {unit!r} 推导 "
                f"{format_dim(dim)} 不一致")
    frame = payload.get("frame")
    frame = None if frame in (None, "", "n_a") else str(frame)
    order = payload.get("tensor_order")
    order = int(order) if order is not None else None  # type: ignore[arg-type]
    q = make(name, payload["value"], unit, frame=frame, tensor_order=order,
             declared_dim=None if declared in (None, "") else str(declared))
    if q.tensor_order >= 1 and frames is not None:
        if q.frame not in frames.frames:
            raise QuantityError(f"{name}: 坐标系 {q.frame!r} 未登记")
    return q


def encode(q: Quantity) -> dict:
    """编码为交换载荷 (只含契约字段, 便于落盘/传下游)."""
    return {
        "schema": SCHEMA_ID,
        "name": q.name,
        "value": list(q.value) if isinstance(q.value, (list, tuple)) else q.value,
        "unit": q.unit,
        "frame": q.frame,
        "tensor_order": q.tensor_order,
    }


def normalize_batch(
    payloads: Sequence[Mapping[str, object]],
    *,
    system: str = "SI",
    frames: Optional[FrameGraph] = None,
) -> Tuple[List[dict], List[dict]]:
    """批量归一化: 返回 ``(成功项, 失败项)``; 失败项带 name/reason.

    全零载荷不算失败 (负控), 但会在 ``physical_meaning`` 里显式声明无物理意义。
    """
    ok: List[dict] = []
    bad: List[dict] = []
    for item in payloads:
        try:
            q = decode(item, frames=frames)
            out = q.in_system(system).to_dict()
            out["source"] = {"unit": q.unit, "frame": q.frame}
            ok.append(out)
        except (QuantityError, DimensionError, UnknownUnitError) as exc:
            bad.append({"name": (item or {}).get("name", "?"), "reason": str(exc)})
    return ok, bad