"""Buckingham π 定理的机器实现 (dimensional homogeneity checker).

做两件事
--------
1. **量纲一致性**: 给定一组物理量与一条"待验证关系" (幂次组合), 精确判断组合
   是否无量纲。非无量纲 -> :class:`DimensionError` 并报出具体变量与剩余量纲。
2. **无量纲群枚举**: 由量纲矩阵的**精确**零空间给出 π 群基, 群数
   ``k = n - rank``。这是"契约声明的无量纲判据 (如相似准则) 是否自洽"的机器门:
   * 声明了 k 个群但实际只有 k-1 个独立群 -> 声明冗余, 报 FAIL;
   * 声明某个群但它的幂次组合并非无量纲 -> 报 FAIL 并给出实际量纲。

为什么用 Fraction
-----------------
浮点高斯消元要用 eps 阈值判零, 对 ``M^1 L^1 T^-2`` 这类整数格上的矩阵会在
病态但合法的情形下产生"伪零" (秩多算一个), 直接导致群数错。指数全是整数,
用有理数消元可以**精确**判零, 结论与机器字长无关 (跨 ISA 可复现)。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .dimension import (
    BASE_SYMBOLS,
    Dim,
    DimensionError,
    ZERO,
    build_matrix,
    format_dim,
    mul,
    pow_dim,
    require_dim,
)


@dataclass(frozen=True)
class PiGroup:
    """一个无量纲群: ``exponents`` 为变量名 -> 有理指数."""

    name: str
    exponents: Tuple[Tuple[str, Fraction], ...]

    @property
    def exponent_map(self) -> Dict[str, Fraction]:
        return {k: v for k, v in self.exponents}

    def formula(self, sep: str = "·") -> str:
        """人类可读形式, 如 ``rho^1·v^1·L^1·mu^-1``."""
        parts = []
        for name, e in self.exponents:
            if e == 0:
                continue
            parts.append(f"{name}^{_fmt_frac(e)}" if e != 1 else name)
        return sep.join(parts) if parts else "1"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "exponents": {k: _fmt_frac(v) for k, v in self.exponents},
            "formula": self.formula(),
        }


def _fmt_frac(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


@dataclass(frozen=True)
class PiResult:
    """π 定理结果: 秩 / 群数 / 群基 / 量纲矩阵快照."""

    variables: Tuple[str, ...]
    dims: Tuple[Dim, ...]
    rank: int
    groups: Tuple[PiGroup, ...]

    @property
    def n(self) -> int:
        return len(self.variables)

    @property
    def n_groups(self) -> int:
        return len(self.groups)

    def to_dict(self) -> dict:
        return {
            "n_variables": self.n,
            "rank": self.rank,
            "n_groups": self.n_groups,
            "expected_groups": self.n - self.rank,
            "groups": [g.to_dict() for g in self.groups],
            "variables": [
                {"name": n, "dim": format_dim(d)} for n, d in zip(self.variables, self.dims)
            ],
        }


def _nullspace(rows: Sequence[Sequence[Fraction]], ncols: int) -> List[List[Fraction]]:
    """精确零空间基 (行阶梯 + 自由变量回代)."""
    mat = [list(r) for r in rows]
    nrows = len(mat)
    pivots: List[int] = []
    r = 0
    for c in range(ncols):
        pivot = None
        for rr in range(r, nrows):
            if mat[rr][c] != 0:
                pivot = rr
                break
        if pivot is None:
            continue
        mat[r], mat[pivot] = mat[pivot], mat[r]
        pv = mat[r][c]
        mat[r] = [v / pv for v in mat[r]]
        for rr in range(nrows):
            if rr != r and mat[rr][c] != 0:
                f = mat[rr][c]
                mat[rr] = [a - f * b for a, b in zip(mat[rr], mat[r])]
        pivots.append(c)
        r += 1
        if r == nrows:
            break
    free = [c for c in range(ncols) if c not in pivots]
    basis: List[List[Fraction]] = []
    for fc in free:
        vec = [Fraction(0)] * ncols
        vec[fc] = Fraction(1)
        for i, pc in enumerate(pivots):
            vec[pc] = -mat[i][fc]
        basis.append(vec)
    return basis


def _name_groups(exponents: List[List[Fraction]], variables: Sequence[str]) -> List[PiGroup]:
    """给群起稳定名字: π1..πn 按自由变量列顺序, 并附参与变量摘要."""
    groups: List[PiGroup] = []
    for i, vec in enumerate(exponents, start=1):
        pairs = tuple((variables[j], vec[j]) for j in range(len(variables)))
        groups.append(PiGroup(f"pi{i}", pairs))
    return groups


def buckingham_pi(variables: Iterable[Tuple[str, Dim]]) -> PiResult:
    """由量纲矩阵给出无量纲群基 ``k = n - rank``."""
    matrix = build_matrix(variables)
    rows = matrix.as_rows()
    rank = matrix.rank
    basis = _nullspace(rows, matrix.n)
    for vec in basis:  # 自检: 每个基向量按有理幂次组合后必须严格为零量纲
        residual = [
            sum(vec[j] * matrix.columns[j][i] for j in range(matrix.n))
            for i in range(len(BASE_SYMBOLS))
        ]
        if any(v != 0 for v in residual):  # pragma: no cover - 数学上不应发生
            raise DimensionError(f"零空间基产出非无量纲群: {residual}")
    groups = _name_groups(basis, matrix.names)
    return PiResult(matrix.names, matrix.columns, rank, tuple(groups))


def group_dim(variables: Iterable[Tuple[str, Dim]], exponents: Mapping[str, object]) -> Dim:
    """按声明幂次组合量纲; 未声明变量名 -> :class:`DimensionError`."""
    table = {}
    for name, d in variables:
        table[name] = require_dim(d, label=f"{name}.dim")
    unknown = set(exponents) - set(table)
    if unknown:
        raise DimensionError(f"π 群引用了不存在的变量: {sorted(unknown)}")
    out = ZERO
    for name, exp in exponents.items():
        e = Fraction(str(exp)) if not isinstance(exp, Fraction) else exp
        if e.denominator != 1:
            raise DimensionError(f"π 群指数必须为整数 (本平台只支持整数幂): {name}^{_fmt_frac(e)}")
        out = mul(out, pow_dim(table[name], int(e)))
    return out


@dataclass(frozen=True)
class GroupCheck:
    """一条 π 群声明的校验结果."""

    name: str
    dim: Dim
    ok: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "dim": format_dim(self.dim), "ok": self.ok, "reason": self.reason}


@dataclass(frozen=True)
class ConsistencyReport:
    """契约级量纲自洽报告: 群基 + 声明群逐条判定."""

    pi: PiResult
    declared: Tuple[GroupCheck, ...] = ()
    #: 声明的独立群数 (用于检测冗余/缺漏)
    declared_independent: int = 0
    #: True 时还要求声明群恰好构成完整群基 (k = n - rank)
    require_complete: bool = False

    @property
    def complete(self) -> bool:
        return self.declared_independent == self.pi.n_groups

    @property
    def redundant(self) -> bool:
        """声明里出现线性相关的群 (条数 > 独立数) => 冗余声明."""
        return sum(1 for c in self.declared if c.ok) > self.declared_independent

    @property
    def ok(self) -> bool:
        if not all(c.ok for c in self.declared):
            return False
        # 过度声明 (独立群多于 n-rank) 或组内线性相关 => 冗余, 判负
        if self.declared_independent > self.pi.n_groups or self.redundant:
            return False
        return self.complete if self.require_complete else True

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "n_variables": self.pi.n,
            "rank": self.pi.rank,
            "n_groups": self.pi.n_groups,
            "declared_independent": self.declared_independent,
            "complete": self.complete,
            "redundant": self.redundant,
            "require_complete": self.require_complete,
            "declared": [c.to_dict() for c in self.declared],
            "groups": [g.to_dict() for g in self.pi.groups],
        }


def check_declared_groups(
    variables: Iterable[Tuple[str, Dim]],
    declared: Mapping[str, Mapping[str, object]],
    *,
    require_complete: bool = False,
) -> ConsistencyReport:
    """校验契约里声明的 π 群: 逐个无量纲判定 + 独立性/数量核对."""
    var_list = list(variables)
    checks: List[GroupCheck] = []
    vectors: List[List[Fraction]] = []
    names = [n for n, _ in var_list]
    for gname, exps in declared.items():
        try:
            d = group_dim(var_list, exps)
        except DimensionError as exc:
            checks.append(GroupCheck(gname, ZERO, False, str(exc)))
            continue
        ok = d == ZERO
        checks.append(GroupCheck(gname, d, ok, "" if ok else f"组合量纲 {format_dim(d)} != 1"))
        if ok:
            vectors.append([Fraction(str(exps.get(n, 0))) for n in names])
    nd = _rank_of_fractions(vectors, len(names)) if vectors else 0
    return ConsistencyReport(
        pi=buckingham_pi(var_list),
        declared=tuple(checks),
        declared_independent=nd,
        require_complete=require_complete,
    )


def _rank_of_fractions(rows: List[List[Fraction]], ncols: int) -> int:
    from .dimension import rank_of

    return rank_of(rows, ncols)


def check_homogeneous_relation(
    variables: Iterable[Tuple[str, Dim]],
    lhs: Mapping[str, object],
    rhs: Mapping[str, object],
    *,
    label: str = "关系式",
) -> None:
    """校验两条幂次组合量纲相同 (如经验公式两端必须同量纲).

    不等 -> :class:`DimensionError`, 消息里同时给出两侧量纲与参与变量。
    """
    var_list = list(variables)
    d_l = group_dim(var_list, lhs)
    d_r = group_dim(var_list, rhs)
    if d_l != d_r:
        raise DimensionError(
            f"{label} 两端量纲不齐: 左 {format_dim(d_l)} vs 右 {format_dim(d_r)}"
        )