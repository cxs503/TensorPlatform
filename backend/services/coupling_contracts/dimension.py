"""量纲代数 (dimensional algebra) — 跨求解器耦合契约的公共底座.

设计要点
--------
1. **纯 stdlib**: 只用 ``Fraction`` 精确整数指数, 不引入第三方依赖, 因此可以被
   CI 的 contracts job (只装 pytest) 与任何求解器仓直接复用。
2. **唯一事实来源**: 全仓只有这一处定义量纲向量与运算; 单位/坐标系/契约/迁移
   都从 ``Dim`` 派生, 避免"两套量纲表漂移"。
3. **可判负**: 任何量纲运算的非法组合 (加减不同量纲、非整数指数、缺量纲) 都抛
   :class:`DimensionError`, 绝不静默返回近似值。

量纲向量
--------
SI 七个基本量按固定顺序排列 (与 :func:`format_dim` 的符号一一对应):

    Dim = (M, L, T, I, Th, N, J)

    M 质量 kg | L 长度 m | T 时间 s | I 电流 A
    Th 温度 K | N 物质的量 mol | J 发光强度 cd

指数为 ``int`` (可为负); 组合运算在 :class:`Dim` 层用整数加法完成, 因此
``(N*s) / (kg*m)`` 这类复合不再依赖浮点。

工程约定: 平面角 (rad / deg) 在本平台按 **无量纲** 处理 (rad=1, deg=π/180),
这是数值求解器的通行做法; 需要"角度 vs 实数"的强类型区分时由契约层的
``tensor_order`` / ``semantic`` 字段表达, 而不是塞进量纲向量。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping, Sequence, Tuple

#: 基本量符号 (顺序即 Dim 元组顺序)
BASE_SYMBOLS: Tuple[str, ...] = ("M", "L", "T", "I", "Th", "N", "J")
BASE_NAMES: Tuple[str, ...] = (
    "mass",
    "length",
    "time",
    "electric_current",
    "thermodynamic_temperature",
    "amount_of_substance",
    "luminous_intensity",
)

#: 量纲元组类型
Dim = Tuple[int, int, int, int, int, int, int]

#: 无量纲量 (纯数/比值)
ZERO: Dim = (0, 0, 0, 0, 0, 0, 0)


class DimensionError(ValueError):
    """量纲不合法 / 不一致 (契约违约, 必须判负)."""


# ---------------------------------------------------------------------------
# 构造与运算
# ---------------------------------------------------------------------------
def dim(
    M: int = 0,
    L: int = 0,
    T: int = 0,
    I: int = 0,
    Th: int = 0,
    N: int = 0,
    J: int = 0,
) -> Dim:
    """按基本量名构造量纲向量 (关键字形式, 便于人读)."""
    return (int(M), int(L), int(T), int(I), int(Th), int(N), int(J))


def is_dim(value: object) -> bool:
    """是否为合法的 Dim (7 个整数)."""
    if not isinstance(value, tuple) or len(value) != 7:
        return False
    return all(isinstance(e, int) and not isinstance(e, bool) for e in value)


def require_dim(value: object, *, label: str = "dim") -> Dim:
    if not is_dim(value):
        raise DimensionError(f"{label} 不是合法量纲向量 (需 7 个整数): {value!r}")
    return value  # type: ignore[return-value]


def mul(a: Dim, b: Dim) -> Dim:
    """量纲相乘 = 指数相加."""
    return tuple(x + y for x, y in zip(require_dim(a), require_dim(b)))  # type: ignore[return-value]


def div(a: Dim, b: Dim) -> Dim:
    """量纲相除 = 指数相减."""
    return tuple(x - y for x, y in zip(require_dim(a), require_dim(b)))  # type: ignore[return-value]


def pow_dim(a: Dim, n: int) -> Dim:
    """量纲的整数次幂 (非整数幂会破坏 SI 基本量整数指数, 故拒绝)."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise DimensionError(f"量纲幂指数必须是整数, 收到 {n!r}")
    return tuple(x * n for x in require_dim(a))  # type: ignore[return-value]


def scale(a: Dim, k: Fraction) -> Dim:
    """量纲按有理数缩放 (仅用于 Buckingham π 的矩阵消元中间量).

    只在指数仍是整数时返回 Dim; 否则返回 ``None`` 表示"越出整数格"。
    """
    out = tuple(Fraction(x) * k for x in require_dim(a))
    if any(v.denominator != 1 for v in out):
        return None
    return tuple(int(v) for v in out)  # type: ignore[return-value]


def add_check(a: Dim, b: Dim, *, label: str = "加法") -> Dim:
    """同量纲才能相加/相减/比较; 返回公共量纲, 否则 :class:`DimensionError`."""
    if a != b:
        raise DimensionError(f"{label}量纲不齐: {format_dim(a)} vs {format_dim(b)}")
    return a


def is_dimensionless(a: Dim) -> bool:
    return require_dim(a) == ZERO


# ---------------------------------------------------------------------------
# 序列化 / 展示
# ---------------------------------------------------------------------------
def format_dim(a: Dim, *, space: bool = False) -> str:
    """量纲的可读串, 如 ``M·L·T^-2``; 无量纲返回 ``1``.

    ``space=True`` 时用 ``" "`` 分隔 (写进 JSON 报告时更易读)。
    """
    a = require_dim(a)
    parts = [f"{sym}^{e}" if e != 1 else sym for sym, e in zip(BASE_SYMBOLS, a) if e]
    if not parts:
        return "1"
    return (" " if space else "·").join(parts)


def dim_to_dict(a: Dim) -> dict:
    """转成 ``{"M": .., "L": ..}`` 的稀疏字典 (只列非零项)."""
    a = require_dim(a)
    return {sym: e for sym, e in zip(BASE_SYMBOLS, a) if e}


def dim_from_dict(d: Mapping[str, int]) -> Dim:
    """从稀疏字典构造量纲; 未知键 -> :class:`DimensionError`."""
    unknown = set(d) - set(BASE_SYMBOLS)
    if unknown:
        raise DimensionError(f"未知量纲符号: {sorted(unknown)}")
    return tuple(int(d.get(sym, 0)) for sym in BASE_SYMBOLS)  # type: ignore[return-value]


def parse_dim(text: str) -> Dim:
    """解析 :func:`format_dim` 产出的量纲串 (契约文件里的 ``declared_dim``)."""
    if not isinstance(text, str) or not text.strip():
        raise DimensionError(f"量纲串为空: {text!r}")
    s = text.strip()
    if s in ("1", "dimensionless"):
        return ZERO
    out = list(ZERO)
    for chunk in s.replace(" ", "·").split("·"):
        if not chunk:
            raise DimensionError(f"量纲串有空的乘子: {text!r}")
        if "^" in chunk:
            sym, _, exp_txt = chunk.partition("^")
            try:
                exp = int(exp_txt)
            except ValueError as exc:
                raise DimensionError(f"量纲指数非整数: {chunk!r}") from exc
        else:
            sym, exp = chunk, 1
        if sym not in BASE_SYMBOLS:
            raise DimensionError(f"未知量纲符号: {sym!r} in {text!r}")
        out[BASE_SYMBOLS.index(sym)] += exp
    return tuple(out)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 量纲矩阵 (Buckingham π 用) — 精确有理高斯消元
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DimensionMatrix:
    """``dims`` 按列排列的量纲矩阵 (7 x n), 提供精确秩/零空间. 见 buckingham.py."""

    names: Tuple[str, ...]
    columns: Tuple[Dim, ...]

    @property
    def n(self) -> int:
        return len(self.columns)

    def as_rows(self) -> Tuple[Tuple[Fraction, ...], ...]:
        return tuple(
            tuple(Fraction(self.columns[j][i]) for j in range(self.n))
            for i in range(len(BASE_SYMBOLS))
        )

    @property
    def rank(self) -> int:
        """精确秩 (Fraction 消元, 无浮点阈值)."""
        rows = [list(r) for r in self.as_rows()]
        return rank_of(rows, self.n)


def rank_of(rows: Sequence[Sequence[Fraction]], ncols: int) -> int:
    """精确秩: 有理数高斯消元, 主元精确比较, 无 eps 阈值."""
    mat = [list(r) for r in rows]
    rank = 0
    for col in range(ncols):
        pivot = None
        for r in range(rank, len(mat)):
            if mat[r][col] != 0:
                pivot = r
                break
        if pivot is None:
            continue
        mat[rank], mat[pivot] = mat[pivot], mat[rank]
        pv = mat[rank][col]
        mat[rank] = [v / pv for v in mat[rank]]
        for r in range(len(mat)):
            if r != rank and mat[r][col] != 0:
                f = mat[r][col]
                mat[r] = [a - f * b for a, b in zip(mat[r], mat[rank])]
        rank += 1
        if rank == len(mat):
            break
    return rank


def build_matrix(variables: Iterable[Tuple[str, Dim]]) -> DimensionMatrix:
    """把 ``[(name, dim), ...]`` 转成量纲矩阵; 名字重复/缺失直接报错."""
    names: list = []
    cols: list = []
    for item in variables:
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            raise DimensionError(f"量纲矩阵变量需为 (name, dim): {item!r}")
        name, d = item
        if not isinstance(name, str) or not name:
            raise DimensionError(f"变量名非法: {name!r}")
        if name in names:
            raise DimensionError(f"变量名重复: {name!r}")
        names.append(name)
        cols.append(require_dim(d, label=f"{name}.dim"))
    if not names:
        raise DimensionError("量纲矩阵为空")
    return DimensionMatrix(tuple(names), tuple(cols))