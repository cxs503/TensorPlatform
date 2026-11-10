"""场交换契约检查 (field-exchange contract checks) — 量纲/张量阶/坐标系/语义.

检查层级 (逐层可判负, 每条违规都带机器可读的 code 与量化 detail):

1. ``DIM_MISMATCH``      两侧量纲向量不等; detail 给出差向量与逐基本量差异,
                         因此报告能直接说"差在 L 的 1 次幂", 而不是只说"不一致"。
2. ``QUANTITY_KIND``     两侧量纲相同但守恒量类别不同 —— 量纲只是必要条件。
                         典型: 力矩 (M L² T⁻²) 与能量 (M L² T⁻²) 同量纲,
                         把它们接在一起不会触发量纲错误, 却把力矩当能量送进
                         下游能量账。框架用 ``quantity`` 名 + 声明的量纲
                         联合校验, 不依赖任何领域公式。
3. ``TENSOR_ORDER``      端口张量阶不一致 (标量口接向量口).
4. ``FRAME_MISSING``     一阶及以上张量口未声明坐标系 —— 没有坐标系就无法
                         判定分量方向, 必须判负。
5. ``FRAME_MISMATCH``    两侧坐标系不同但调用方未提供变换 (``frames_connected``
                         由调用方按自己的坐标系图给出; 本模块不内置任何坐标系).
6. ``SIGN_CONVENTION``   两侧都声明 outward-positive (或都 inward-positive):
                         交接时符号会被静默翻转/加倍, 属于典型的"数值对但符号错"。
7. ``UNIT_INCONSISTENT`` 声明单位与声明量纲不相容 (单位解析失败或量纲不同).

零领域语义: 端口名/量名/坐标系名/单位全部来自入参或数据文件, 模块内不出现
任何领域词条或领域判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Literal, Optional, Tuple

from services.coupling_contracts.dimension import (
    BASE_SYMBOLS,
    Dim,
    DimensionError,
    format_dim,
    parse_dim,
)
from services.coupling_contracts.units import UnknownUnitError, dim_of_unit

Role = Literal["producer", "consumer"]

#: 向外为正 / 向内为正
OUTWARD = +1
INWARD = -1

#: 违规码 (稳定字符串, 供审计链与报告引用)
CODES = (
    "DIM_MISMATCH",
    "QUANTITY_KIND",
    "TENSOR_ORDER",
    "FRAME_MISSING",
    "FRAME_MISMATCH",
    "SIGN_CONVENTION",
    "UNIT_INCONSISTENT",
)


@dataclass(frozen=True)
class ExchangePort:
    """一个耦合端口对一个守恒量的交换声明."""

    name: str
    role: Role
    quantity: str
    dim: Dim
    unit: str = ""
    tensor_order: int = 0
    frame: Optional[str] = None
    orientation: int = OUTWARD

    def __post_init__(self) -> None:
        if self.role not in ("producer", "consumer"):
            raise ValueError(f"role 必须是 producer/consumer, 得到 {self.role!r}")
        if self.orientation not in (OUTWARD, INWARD):
            raise ValueError(f"orientation 必须是 +1/-1, 得到 {self.orientation!r}")
        if not isinstance(self.dim, tuple) or len(self.dim) != 7:
            raise DimensionError(f"端口 {self.name} 的 dim 非法: {self.dim!r}")


@dataclass(frozen=True)
class ExchangeViolation:
    code: str
    message: str
    detail: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


@dataclass(frozen=True)
class ExchangeCheck:
    """一次场交换检查的结果 (pass + 全部违规, 不只报第一条)."""

    passed: bool
    violations: Tuple[ExchangeViolation, ...] = ()
    dim_delta: Optional[Dim] = None
    producer: Optional[ExchangePort] = None
    consumer: Optional[ExchangePort] = None

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(v.code for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "codes": list(self.codes),
            "dim_delta": None if self.dim_delta is None else list(self.dim_delta),
            "producer": None if self.producer is None else self.producer.name,
            "consumer": None if self.consumer is None else self.consumer.name,
            "violations": [v.to_dict() for v in self.violations],
        }


def dim_delta(a: Dim, b: Dim) -> Dim:
    """逐基本量指数差 ``b - a`` (报告"错在哪一维"用)."""
    return tuple(y - x for x, y in zip(a, b))  # type: ignore[return-value]


def describe_dim_delta(delta: Dim) -> str:
    """把指数差翻成人读串, 如 ``L+1 T-1``; 全零返回 ``(identical)``."""
    parts = [f"{sym}{'+' if e > 0 else ''}{e}" for sym, e in zip(BASE_SYMBOLS, delta) if e]
    return " ".join(parts) if parts else "(identical)"


def _unit_dim(unit: str) -> Optional[Dim]:
    if not unit:
        return None
    try:
        return dim_of_unit(unit)
    except (UnknownUnitError, ValueError, KeyError):
        return None


def check_exchange(
    producer: ExchangePort,
    consumer: ExchangePort,
    *,
    frames_connected: bool = True,
    require_opposite_orientation: bool = True,
) -> ExchangeCheck:
    """校验一次 producer→consumer 场交换声明的全部契约维度.

    ``frames_connected`` 由调用方 (持有坐标系图的组件) 传入: 本模块不做坐标
    变换, 只裁决"若两侧坐标系不同, 是否有可达变换"。默认 True 表示调用方已
    确认可达 —— 单测里显式传 False 就能证明该检查确实在工作, 不会静默放行。
    """
    violations: list[ExchangeViolation] = []

    # 1. 量纲
    delta = dim_delta(producer.dim, consumer.dim)
    if delta != (0, 0, 0, 0, 0, 0, 0):
        violations.append(
            ExchangeViolation(
                code="DIM_MISMATCH",
                message=(
                    f"端口 {producer.name}→{consumer.name} 量纲不一致: "
                    f"{format_dim(producer.dim)} vs {format_dim(consumer.dim)} "
                    f"(差 {describe_dim_delta(delta)})"
                ),
                detail={
                    "producer_dim": list(producer.dim),
                    "consumer_dim": list(consumer.dim),
                    "delta": list(delta),
                    "delta_text": describe_dim_delta(delta),
                    "producer_dim_text": format_dim(producer.dim),
                    "consumer_dim_text": format_dim(consumer.dim),
                },
            )
        )

    # 2. 守恒量类别 (量纲相同 ≠ 物理量相同)
    if producer.quantity != consumer.quantity:
        violations.append(
            ExchangeViolation(
                code="QUANTITY_KIND",
                message=(
                    f"端口 {producer.name}→{consumer.name} 守恒量类别不同: "
                    f"{producer.quantity} vs {consumer.quantity}"
                    + (
                        " (两者量纲相同, 量纲检查无法发现此错误)"
                        if delta == (0, 0, 0, 0, 0, 0, 0)
                        else ""
                    )
                ),
                detail={
                    "producer_quantity": producer.quantity,
                    "consumer_quantity": consumer.quantity,
                    "same_dim": delta == (0, 0, 0, 0, 0, 0, 0),
                },
            )
        )

    # 3. 张量阶
    if producer.tensor_order != consumer.tensor_order:
        violations.append(
            ExchangeViolation(
                code="TENSOR_ORDER",
                message=(
                    f"端口 {producer.name}→{consumer.name} 张量阶不同: "
                    f"{producer.tensor_order} vs {consumer.tensor_order}"
                ),
                detail={
                    "producer_tensor_order": producer.tensor_order,
                    "consumer_tensor_order": consumer.tensor_order,
                },
            )
        )

    # 4/5. 坐标系
    for port in (producer, consumer):
        if port.tensor_order >= 1 and not port.frame:
            violations.append(
                ExchangeViolation(
                    code="FRAME_MISSING",
                    message=f"端口 {port.name} 为 {port.tensor_order} 阶张量但未声明坐标系",
                    detail={"port": port.name, "tensor_order": port.tensor_order},
                )
            )
    if producer.frame and consumer.frame and producer.frame != consumer.frame and not frames_connected:
        violations.append(
            ExchangeViolation(
                code="FRAME_MISMATCH",
                message=(
                    f"端口 {producer.name}({producer.frame}) → {consumer.name}"
                    f"({consumer.frame}) 坐标系不同且无可用变换"
                ),
                detail={"producer_frame": producer.frame, "consumer_frame": consumer.frame},
            )
        )

    # 6. 符号约定
    if require_opposite_orientation and producer.orientation == consumer.orientation:
        violations.append(
            ExchangeViolation(
                code="SIGN_CONVENTION",
                message=(
                    f"端口 {producer.name}→{consumer.name} 两侧都是 "
                    f"{'向外为正' if producer.orientation == OUTWARD else '向内为正'}, "
                    "交接后通量符号会被静默翻转"
                ),
                detail={
                    "producer_orientation": producer.orientation,
                    "consumer_orientation": consumer.orientation,
                },
            )
        )

    # 7. 声明单位与声明量纲相容
    for port in (producer, consumer):
        if port.unit:
            unit_dim = _unit_dim(port.unit)
            if unit_dim is None:
                violations.append(
                    ExchangeViolation(
                        code="UNIT_INCONSISTENT",
                        message=f"端口 {port.name} 单位 {port.unit!r} 无法解析",
                        detail={"port": port.name, "unit": port.unit},
                    )
                )
            elif unit_dim != port.dim:
                violations.append(
                    ExchangeViolation(
                        code="UNIT_INCONSISTENT",
                        message=(
                            f"端口 {port.name} 单位 {port.unit!r} 的量纲 "
                            f"{format_dim(unit_dim)} 与声明 {format_dim(port.dim)} 不符"
                        ),
                        detail={
                            "port": port.name,
                            "unit": port.unit,
                            "unit_dim": list(unit_dim),
                            "declared_dim": list(port.dim),
                        },
                    )
                )

    return ExchangeCheck(
        passed=not violations,
        violations=tuple(violations),
        dim_delta=delta,
        producer=producer,
        consumer=consumer,
    )


def check_exchange_set(
    pairs: Iterable[Tuple[ExchangePort, ExchangePort]],
    **kwargs,
) -> Dict[str, object]:
    """批量检查 (整包耦合边), 返回汇总: 通过数/失败数/逐条结果."""
    results = [check_exchange(p, c, **kwargs) for p, c in pairs]
    failed = [r for r in results if not r.passed]
    return {
        "n_checks": len(results),
        "n_passed": len(results) - len(failed),
        "n_failed": len(failed),
        "passed": not failed,
        "failed_details": [
            {"producer": r.producer.name, "consumer": r.consumer.name, "codes": list(r.codes),
             "violations": [v.to_dict() for v in r.violations]}
            for r in failed
        ],
    }


def port_from_dict(payload: dict) -> ExchangePort:
    """从数据文件 (JSON) 构造端口 —— 领域内容由数据注入, 代码零领域词条."""
    dim_field = payload.get("dim")
    if isinstance(dim_field, str):
        dim = parse_dim(dim_field)
    elif isinstance(dim_field, (list, tuple)) and len(dim_field) == 7:
        dim = tuple(int(x) for x in dim_field)  # type: ignore[assignment]
    else:
        raise ValueError(f"端口 {payload.get('name')!r} 缺少可解析的 dim 字段")
    return ExchangePort(
        name=str(payload["name"]),
        role=payload.get("role", "producer"),
        quantity=str(payload["quantity"]),
        dim=dim,
        unit=str(payload.get("unit", "")),
        tensor_order=int(payload.get("tensor_order", 0)),
        frame=payload.get("frame"),
        orientation=int(payload.get("orientation", OUTWARD)),
    )