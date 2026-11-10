"""守恒量通量台账 (exact-rational flux ledger) — 耦合界面守恒的公共底座.

为什么用 ``Fraction``
---------------------
守恒验证的判据是"界面残差 < 1e-10"甚至"链式残差与直达残差之差 < 1e-12"。
若通量本身用 float 累加, 残差里混进了求和顺序误差 (N 个面可达 N*eps*Σ|v|),
"检出 1e-13 量级的注入泄漏"与"浮点抖动"就无法区分。这里用 ``Fraction``
把**记账**做成精确算术: 只要输入是精确有理数, 残差就是精确有理数, 框架
自身贡献的误差恒为 0, 从而把判据数字的解释权完整交还给被验证的耦合过程。

符号约定 (domain-neutral)
-------------------------
``FluxSample.value`` 是**该侧向外流出界面**的通量 (outward positive)。闭合界面
守恒即 ``Σ_sides value == 0``。消费侧因此常以负值出现, 这是约定而非错误;
符号方向是否被正确声明由 :mod:`coupling_conservation.exchange` 的符号约定检查
负责, 与本模块的通量算术解耦。

本模块不含任何领域词条/领域公式/领域算例: 所有量名、量纲、单位都是入参。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable, Sequence, Tuple, Union

from services.coupling_contracts.dimension import Dim, DimensionError, format_dim

Number = Union[int, Fraction, float, str]

#: 默认守恒容差: 相对残差 1e-10 (机器判据 (b))
DEFAULT_TOLERANCE = Fraction(1, 10 ** 10)


# ---------------------------------------------------------------------------
# 数值规范化
# ---------------------------------------------------------------------------
def as_fraction(value: Number) -> Fraction:
    """把输入规范成精确有理数.

    - ``int`` / ``Fraction``: 精确保持;
    - ``float``: 走 ``Fraction(str(x))`` == 取该浮点数的**十进制字面值**,
      这样 ``0.1`` 得到 1/10 而不是二进制展开 (后者会把"人写的 1e-3 泄漏"
      变成一堆无意义的尾数);
    - ``str``: 支持 ``"1/3"`` 与 ``"1e-3"`` 两种写法。
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):  # bool 是 int 子类, 显式拒绝
        raise TypeError("bool 不是合法通量值")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"通量值必须有限: {value!r}")
        return Fraction(str(value))
    if isinstance(value, str):
        return Fraction(value)
    raise TypeError(f"不支持的通量数值类型: {type(value).__name__}")


def rel_residual(a: Number, b: Number) -> Fraction:
    """相对残差 ``|a-b| / max(|a|,|b|)`` (两者皆 0 时为 0).

    分母取两侧模的较大者而不是 ``a``, 避免"参考值恰好为 0"时的除零/爆量。
    返回精确有理数, 因此调用方可以直接和 1e-10 / 1e-12 这类阈值比较。
    """
    fa, fb = as_fraction(a), as_fraction(b)
    scale = max(abs(fa), abs(fb))
    if scale == 0:
        return Fraction(0)
    return abs(fa - fb) / scale


def abs_error(a: Number, b: Number) -> Fraction:
    return abs(as_fraction(a) - as_fraction(b))


# ---------------------------------------------------------------------------
# 通量样本与界面平衡
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FluxSample:
    """界面一侧在一个时间步上的守恒量通量 (精确有理数, SI 单位)."""

    quantity: str
    dim: Dim
    value: Fraction
    side: str
    unit: str = ""
    time: Fraction = Fraction(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", as_fraction(self.value))
        object.__setattr__(self, "time", as_fraction(self.time))
        if not isinstance(self.dim, tuple) or len(self.dim) != 7:
            raise DimensionError(f"FluxSample.dim 必须是 7 元量纲向量: {self.dim!r}")
        if not self.quantity:
            raise ValueError("FluxSample.quantity 不能为空")

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "dim": format_dim(self.dim),
            "value": float(self.value),
            "value_exact": str(self.value),
            "side": self.side,
            "unit": self.unit,
            "time": float(self.time),
        }


@dataclass(frozen=True)
class InterfaceBalance:
    """一个界面上某一守恒量的平衡结果 (可判负, 数字可溯源)."""

    quantity: str
    dim: Dim
    left_sum: Fraction
    right_sum: Fraction
    net: Fraction
    abs_sum: Fraction
    abs_residual: Fraction
    rel_residual: Fraction
    tolerance: Fraction
    passed: bool
    sides: Tuple[str, ...] = ()
    samples: Tuple[FluxSample, ...] = ()

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "dim": format_dim(self.dim),
            "sides": list(self.sides),
            "left_sum": float(self.left_sum),
            "right_sum": float(self.right_sum),
            "net": float(self.net),
            "net_exact": str(self.net),
            "abs_sum": float(self.abs_sum),
            "abs_residual": float(self.abs_residual),
            "rel_residual": float(self.rel_residual),
            "rel_residual_exact": str(self.rel_residual),
            "tolerance": float(self.tolerance),
            "passed": self.passed,
            "n_samples": len(self.samples),
        }

    @property
    def excess(self) -> Fraction:
        """超出容差多少倍 (``<=1`` 即通过); 供报告"量化违规程度"使用."""
        if self.tolerance <= 0:
            return Fraction(0) if self.rel_residual == 0 else Fraction(10 ** 9)
        return self.rel_residual / self.tolerance


def check_balance(
    samples: Iterable[FluxSample],
    *,
    tolerance: Number = DEFAULT_TOLERANCE,
    group_by: str = "side",
) -> InterfaceBalance:
    """检查一组界面通量样本是否闭合 (``Σ outward == 0``).

    ``group_by`` 决定左右两侧如何划分; 默认按 ``side`` 字段。两侧各自求和后
    的差即净通量, 残差 = ``|net| / Σ|v|`` (尺度无关, 可直接跨问题比较)。

    守卫: 同一界面的所有样本必须同量名同量纲 —— 把 kg/s 和 J/s 混进一个
    平衡里求和是无意义的, 这里直接抛 :class:`DimensionError` 而不是给个
    看起来很小的残差。
    """
    items = tuple(samples)
    if not items:
        raise ValueError("check_balance 需要至少一个 FluxSample")
    quantities = {s.quantity for s in items}
    if len(quantities) != 1:
        raise ValueError(f"同一界面平衡的守恒量名必须唯一, 得到 {sorted(quantities)}")
    dims = {s.dim for s in items}
    if len(dims) != 1:
        raise DimensionError(
            "同一界面平衡内量纲不一致: " + ", ".join(sorted(format_dim(d) for d in dims))
        )
    sides: list[str] = []
    for s in items:
        key = getattr(s, group_by)
        if key not in sides:
            sides.append(key)
    left = sum((s.value for s in items if getattr(s, group_by) == sides[0]), Fraction(0))
    right = sum((s.value for s in items if getattr(s, group_by) != sides[0]), Fraction(0))
    net = left + right
    abs_sum = sum((abs(s.value) for s in items), Fraction(0))
    rel = abs(net) / abs_sum if abs_sum > 0 else Fraction(0)
    tol = as_fraction(tolerance)
    return InterfaceBalance(
        quantity=items[0].quantity,
        dim=items[0].dim,
        left_sum=left,
        right_sum=right,
        net=net,
        abs_sum=abs_sum,
        abs_residual=abs(net),
        rel_residual=rel,
        tolerance=tol,
        passed=rel <= tol,
        sides=tuple(sides),
        samples=items,
    )


def split_conserved(value: Number, weights: Sequence[Number]) -> Tuple[Fraction, ...]:
    """把一个守恒通量按权重拆分到多个面 (权重必须精确求和为 1).

    这是"界面重分布"的通用原语: DEM 的接触面、LBM 的边界网格面、FEM 的
    单元面都可以用它表达, 而框架不认识其中任何一种。
    """
    v = as_fraction(value)
    ws = tuple(as_fraction(w) for w in weights)
    if not ws:
        raise ValueError("权重列表不能为空")
    total = sum(ws, Fraction(0))
    if total != 1:
        raise ValueError(f"重分布权重必须精确求和为 1, 得到 {total}")
    return tuple(v * w for w in ws)


@dataclass
class FluxLedger:
    """按时间步累积的守恒量台账, 用于"多步累积守恒"与漂移审计."""

    quantity: str
    dim: Dim
    tolerance: Fraction = DEFAULT_TOLERANCE
    steps: list = field(default_factory=list)  # list[InterfaceBalance]

    def record(self, samples: Iterable[FluxSample], *, tolerance: Number | None = None) -> InterfaceBalance:
        balance = check_balance(
            samples, tolerance=self.tolerance if tolerance is None else tolerance
        )
        if balance.quantity != self.quantity or balance.dim != self.dim:
            raise DimensionError(
                f"台账 {self.quantity}{format_dim(self.dim)} 收到 "
                f"{balance.quantity}{format_dim(balance.dim)}"
            )
        self.steps.append(balance)
        return balance

    @property
    def cumulative_net(self) -> Fraction:
        return sum((b.net for b in self.steps), Fraction(0))

    @property
    def cumulative_abs_sum(self) -> Fraction:
        return sum((b.abs_sum for b in self.steps), Fraction(0))

    @property
    def cumulative_rel_residual(self) -> Fraction:
        if self.cumulative_abs_sum == 0:
            return Fraction(0)
        return abs(self.cumulative_net) / self.cumulative_abs_sum

    @property
    def all_passed(self) -> bool:
        return all(b.passed for b in self.steps)

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "dim": format_dim(self.dim),
            "n_steps": len(self.steps),
            "cumulative_net": float(self.cumulative_net),
            "cumulative_rel_residual": float(self.cumulative_rel_residual),
            "worst_rel_residual": float(
                max((b.rel_residual for b in self.steps), default=Fraction(0))
            ),
            "all_passed": self.all_passed,
            "steps": [b.to_dict() for b in self.steps],
        }