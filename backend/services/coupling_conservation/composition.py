"""多对耦合的组合验证 (composition of coupling pairs) — 传递性裁决.

问题
----
三场耦合 A↔B、B↔C、A↔C 里, A→C 有两条路径: 直达, 或 A→B→C 转接。
如果框架自己的组合不引入误差, 两条路径算出的**守恒残差**必须相同
(机器判据 (e): 差 < 1e-12)。这条性质 (传递性) 是把"两两验证都过"升级为
"整体可信"的关键 —— 否则两两全绿但组合后泄漏, 没人能定位。

实现要点
--------
1. 界面转接法则 :class:`TransferLaw` 只允许三种**精确可记账**的操作:
   单位换算 (``unit_scale``, 有理数)、面重分布 (``weights``, 必须精确求和为 1)、
   显式损失 (``loss``, 有理数)。三者都用 ``Fraction`` 完成, 因此守恒转接是
   **精确**守恒, 残差不会掺入框架抖动。
2. 链式残差用与直达残差**同一套** :func:`rel_residual` 定义, 保证两者同量纲
   同尺度, 可直接相减。
3. 非守恒的中间界面 (``loss != 0`` 或 ``Σweights != 1``) 会破坏传递性, 框架
   量化报告失配量, 而不是静默放过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.coupling_contracts.dimension import Dim, DimensionError, format_dim

from .flux import as_fraction, rel_residual


@dataclass(frozen=True)
class TransferLaw:
    """一个耦合界面上的守恒量转接法则 (精确有理数)."""

    source: str
    target: str
    quantity: str
    dim_in: Dim
    dim_out: Dim
    unit_scale: Fraction = Fraction(1)
    weights: Tuple[Fraction, ...] = (Fraction(1),)
    loss: Fraction = Fraction(0)
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_scale", as_fraction(self.unit_scale))
        object.__setattr__(self, "loss", as_fraction(self.loss))
        object.__setattr__(
            self, "weights", tuple(as_fraction(w) for w in self.weights)
        )
        if not self.weights:
            raise ValueError(f"{self.source}→{self.target} 权重不能为空")
        if self.unit_scale == 0:
            raise ValueError(f"{self.source}→{self.target} unit_scale 不能为 0")

    @property
    def weight_sum(self) -> Fraction:
        return sum(self.weights, Fraction(0))

    @property
    def redistributes(self) -> bool:
        return self.weights != (Fraction(1),)

    def apply(self, value) -> Fraction:
        """把上游守恒通量映射到下游 (先按权重分配到面, 汇总后减损失)."""
        v = as_fraction(value) * self.unit_scale
        total = v * self.weight_sum
        return total - self.loss

    @property
    def is_exactly_conservative(self) -> bool:
        return self.weight_sum == 1 and self.loss == 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "quantity": self.quantity,
            "dim_in": format_dim(self.dim_in),
            "dim_out": format_dim(self.dim_out),
            "unit_scale": float(self.unit_scale),
            "weights": [float(w) for w in self.weights],
            "weight_sum": float(self.weight_sum),
            "loss": float(self.loss),
            "exactly_conservative": self.is_exactly_conservative,
            "label": self.label,
        }


@dataclass(frozen=True)
class TransitivityResult:
    """一条"直达 vs 链式"的残差一致性裁决."""

    upstream: str
    downstream: str
    path: Tuple[str, ...]
    conserved_value: Fraction      # 上游守恒通量 (SI)
    measured_downstream: Fraction  # 下游实测守恒通量 (SI)
    direct_residual: Fraction
    chain_residual: Fraction
    mismatch: Fraction             # |chain - direct| (判据 (e) 的误差量)
    transitivity_tol: Fraction
    conservation_tol: Fraction
    conservation_ok: bool
    transitive: bool

    @property
    def passed(self) -> bool:
        return self.transitive and self.conservation_ok

    def to_dict(self) -> dict:
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "path": list(self.path),
            "conserved_value": float(self.conserved_value),
            "measured_downstream": float(self.measured_downstream),
            "direct_residual": float(self.direct_residual),
            "chain_residual": float(self.chain_residual),
            "direct_residual_exact": str(self.direct_residual),
            "chain_residual_exact": str(self.chain_residual),
            "mismatch": float(self.mismatch),
            "mismatch_exact": str(self.mismatch),
            "transitivity_tol": float(self.transitivity_tol),
            "conservation_tol": float(self.conservation_tol),
            "conservation_ok": self.conservation_ok,
            "transitive": self.transitive,
            "passed": self.passed,
        }


@dataclass
class CouplingChain:
    """耦合图的一条可传递路径 (节点名由调用方给, 框架不认识其物理含义)."""

    nodes: Tuple[str, ...]
    laws: Dict[Tuple[str, str], TransferLaw] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.nodes) < 2:
            raise ValueError("耦合链至少需要 2 个节点")
        if len(set(self.nodes)) != len(self.nodes):
            raise ValueError(f"耦合链节点名重复: {self.nodes}")

    def add_law(self, law: TransferLaw) -> None:
        if (law.source, law.target) in self.laws:
            raise ValueError(f"重复的耦合边 {law.source}→{law.target}")
        self.laws[(law.source, law.target)] = law

    def path(self, start: str, end: str) -> Tuple[str, ...]:
        """按 ``nodes`` 声明顺序返回 start→end 的子路径 (不支持绕行)."""
        try:
            i, j = self.nodes.index(start), self.nodes.index(end)
        except ValueError as exc:  # pragma: no cover - 参数错误
            raise KeyError(f"节点 {start!r}/{end!r} 不在链 {self.nodes} 中") from exc
        if i >= j:
            raise ValueError(f"{start}→{end} 不是链上正向路径")
        return self.nodes[i : j + 1]

    def propagate(self, start: str, end: str, value) -> Fraction:
        """沿链把守恒通量从 start 精确推到 end."""
        v = as_fraction(value)
        path = self.path(start, end)
        for a, b in zip(path, path[1:]):
            try:
                law = self.laws[(a, b)]
            except KeyError as exc:
                raise KeyError(f"缺少耦合边 {a}→{b} 的转接法则") from exc
            if law.quantity != self.laws[(path[0], path[1])].quantity:
                raise ValueError(f"链上守恒量不一致: {law.quantity}")
            v = law.apply(v)
        return v

    def check_dims(self) -> List[str]:
        """链上相邻法则的量纲接口必须逐一相接, 否则返回问题清单."""
        problems: List[str] = []
        for (a, b), law in self.laws.items():
            if law.dim_in != law.dim_out:
                problems.append(
                    f"{a}→{b} 量纲变化 {format_dim(law.dim_in)}→{format_dim(law.dim_out)}"
                )
        path = self.nodes
        for (a, b), (b2, c) in zip(zip(path, path[1:]), zip(path[1:], path[2:])):
            left, right = self.laws.get((a, b)), self.laws.get((b2, c))
            if left and right and left.dim_out != right.dim_in:
                problems.append(
                    f"节点 {b} 处量纲不连续: {format_dim(left.dim_out)} vs "
                    f"{format_dim(right.dim_in)}"
                )
        return problems


def check_transitivity(
    chain: CouplingChain,
    *,
    upstream: str,
    downstream: str,
    conserved_value,
    measured_downstream,
    direct_law: Optional[TransferLaw] = None,
    conservation_tol=Fraction(1, 10 ** 10),
    transitivity_tol=Fraction(1, 10 ** 12),
) -> TransitivityResult:
    """裁决 "链式残差是否等于直达残差" (判据 (e)).

    残差在**下游单位**下比较, 否则 1000 g/s 与 1 kg/s 会被算成 999 的差值 ——
    这正是"单位换算是耦合方的责任、验证框架只负责记账"的体现:

    * 直达路径: ``direct_law`` (上游→下游的单位换算/重分布法则, 缺省为单位换算
      恒等, 即两侧同单位) 作用于上游实测值, 再与下游实测值比;
    * 链式路径: 依次施加 ``upstream→...→downstream`` 各界面法则。

    两条路径都用同一 :func:`rel_residual` 定义, 所以 ``mismatch`` 直接就是
    框架组合引入的相对误差 (判据要求 < 1e-12)。
    """
    v_up = as_fraction(conserved_value)
    v_down = as_fraction(measured_downstream)
    c_tol = as_fraction(conservation_tol)
    t_tol = as_fraction(transitivity_tol)

    propagated = chain.propagate(upstream, downstream, v_up)
    direct_value = v_up if direct_law is None else direct_law.apply(v_up)
    direct = rel_residual(direct_value, v_down)
    chain_res = rel_residual(propagated, v_down)
    mismatch = abs(chain_res - direct)
    return TransitivityResult(
        upstream=upstream,
        downstream=downstream,
        path=chain.path(upstream, downstream),
        conserved_value=v_up,
        measured_downstream=v_down,
        direct_residual=direct,
        chain_residual=chain_res,
        mismatch=mismatch,
        transitivity_tol=t_tol,
        conservation_tol=c_tol,
        conservation_ok=chain_res <= c_tol and direct <= c_tol,
        transitive=mismatch <= t_tol,
    )


def additivity_report(
    residuals: Mapping[str, Fraction],
    *,
    total_key: str,
    partial_keys: Sequence[str],
    tol=Fraction(1, 10 ** 9),
) -> dict:
    """逐界面残差 vs 总残差的加性审计 (定位泄漏发生在哪一对).

    对 k 个界面并联的耦合块, 精确记账下总残差 == Σ 分残差 (线性尺度相加)。
    若两者不符, 说明存在未被记账的通量 (例如某条边漏记), 这里量化差量,
    并给出各界面占比, 便于报告直接引用。
    """
    t = as_fraction(tol)
    total = as_fraction(residuals[total_key])
    parts = [as_fraction(residuals[k]) for k in partial_keys]
    s = sum(parts, Fraction(0))
    diff = abs(total - s)
    share = {
        k: float(as_fraction(residuals[k]) / s) if s else 0.0 for k in partial_keys
    }
    return {
        "total_key": total_key,
        "total": float(total),
        "sum_partials": float(s),
        "diff": float(diff),
        "consistent": diff <= t,
        "tolerance": float(t),
        "share": share,
    }