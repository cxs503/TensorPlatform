"""代数检查器 (algebra checker) — 数值运算的量纲一致性规则.

规则
----
1. **加减/比较**: 量纲向量必须完全相同 (含角度制式).
   - rad 与 deg 直接相加 -> ``ANGLE_MODE_MISMATCH`` (必须显式换算).
2. **乘除**: 量纲向量相加/相减 (自动推导结果量纲).
3. **幂运算**: 指数必须为整数; 底数无量纲时任意指数允许; 底数有量纲时
   指数必须为整数 (否则破坏 SI 基本量整数指数).
4. **指数/对数**: 输入必须无量纲 (``exp(x)`` / ``log(x)`` 要求 x 无量纲).
5. **角度制折叠**: rad/deg 属同量纲 (无量纲) 不同刻度, 必须显式换算因子.

每条检查返回 :class:`AlgebraResult`, 含通过/失败状态与原因码.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from services.coupling_contracts.dimension import (
    Dim,
    DimensionError,
    ZERO,
    add_check,
    dim,
    div,
    format_dim,
    is_dimensionless,
    mul,
    pow_dim,
)


class AlgebraError(ValueError):
    """代数运算量纲不合法."""


@dataclass(frozen=True)
class AlgebraResult:
    """一次代数检查的结果."""
    ok: bool
    code: str = ""
    message: str = ""
    result_dim: Optional[Dim] = None

    def to_dict(self) -> dict:
        d: dict = {"ok": self.ok}
        if self.code:
            d["code"] = self.code
        if self.message:
            d["message"] = self.message
        if self.result_dim is not None:
            d["result_dim"] = format_dim(self.result_dim)
        return d


def _fail(code: str, message: str) -> AlgebraResult:
    return AlgebraResult(ok=False, code=code, message=message)


def _ok(result_dim: Optional[Dim] = None) -> AlgebraResult:
    return AlgebraResult(ok=True, result_dim=result_dim)


# ---------------------------------------------------------------------------
# 加减: 量纲必须相同
# ---------------------------------------------------------------------------
def check_add(a_dim: Dim, b_dim: Dim, *,
              a_angle: str = "rad", b_angle: str = "rad",
              label: str = "加法") -> AlgebraResult:
    """检查 a + b 的量纲一致性.

    Parameters
    ----------
    a_dim, b_dim : Dim
        操作数的量纲向量.
    a_angle, b_angle : str
        角度制式 (仅当量纲为无量纲且字段为角度时有效).
    label : str
        操作标签 (用于错误消息).
    """
    if a_dim != b_dim:
        return _fail(
            "DIM_MISMATCH_ADD",
            f"{label}量纲不齐: {format_dim(a_dim)} vs {format_dim(b_dim)}"
        )
    # 角度制式检查: 无量纲字段若声明了角度模式, 必须一致
    if is_dimensionless(a_dim) and a_angle != b_angle:
        return _fail(
            "ANGLE_MODE_MISMATCH",
            f"{label}角度制式不一致: {a_angle!r} vs {b_angle!r} "
            f"(rad 与 deg 不可直接相加, 须显式换算)"
        )
    return _ok(result_dim=a_dim)


def check_sub(a_dim: Dim, b_dim: Dim, *,
              a_angle: str = "rad", b_angle: str = "rad",
              label: str = "减法") -> AlgebraResult:
    """检查 a - b 的量纲一致性 (规则同加法)."""
    return check_add(a_dim, b_dim, a_angle=a_angle, b_angle=b_angle, label=label)


# ---------------------------------------------------------------------------
# 比较: 量纲必须相同
# ---------------------------------------------------------------------------
def check_compare(a_dim: Dim, b_dim: Dim, *,
                  a_angle: str = "rad", b_angle: str = "rad",
                  label: str = "比较") -> AlgebraResult:
    """检查 a == b / a > b 等比较操作的量纲一致性."""
    if a_dim != b_dim:
        return _fail(
            "DIM_MISMATCH_COMPARE",
            f"{label}量纲不齐: {format_dim(a_dim)} vs {format_dim(b_dim)}"
        )
    if is_dimensionless(a_dim) and a_angle != b_angle:
        return _fail(
            "ANGLE_MODE_MISMATCH",
            f"{label}角度制式不一致: {a_angle!r} vs {b_angle!r}"
        )
    return _ok(result_dim=ZERO)


# ---------------------------------------------------------------------------
# 乘除: 量纲相加/相减
# ---------------------------------------------------------------------------
def check_mul(a_dim: Dim, b_dim: Dim, *, label: str = "乘法") -> AlgebraResult:
    """检查 a * b, 返回结果量纲."""
    try:
        result = mul(a_dim, b_dim)
    except DimensionError as exc:
        return _fail("MUL_ERROR", f"{label}量纲运算失败: {exc}")
    return _ok(result_dim=result)


def check_div(a_dim: Dim, b_dim: Dim, *, label: str = "除法") -> AlgebraResult:
    """检查 a / b, 返回结果量纲."""
    try:
        result = div(a_dim, b_dim)
    except DimensionError as exc:
        return _fail("DIV_ERROR", f"{label}量纲运算失败: {exc}")
    return _ok(result_dim=result)


# ---------------------------------------------------------------------------
# 幂运算: 底数无量纲时任意指数; 有量纲时指数必须为整数
# ---------------------------------------------------------------------------
def check_pow(base_dim: Dim, exponent: float, *, label: str = "幂运算") -> AlgebraResult:
    """检查 base ^ exponent 的量纲一致性.

    规则:
    - 底数无量纲: 任意指数允许, 结果无量纲.
    - 底数有量纲: 指数必须为整数, 结果量纲 = base_dim * exponent.
    """
    if is_dimensionless(base_dim):
        return _ok(result_dim=ZERO)

    # 有量纲底数: 指数必须为整数
    if not isinstance(exponent, (int, float)):
        return _fail(
            "POW_NON_NUMERIC_EXPONENT",
            f"{label}指数必须为数值, 收到 {type(exponent).__name__}"
        )
    if isinstance(exponent, float) and not exponent.is_integer():
        return _fail(
            "POW_NON_INTEGER_EXPONENT",
            f"{label}有量纲底数的指数必须为整数, 收到 {exponent!r} "
            f"(量纲 {format_dim(base_dim)} 的 {exponent} 次幂破坏 SI 基本量整数指数)"
        )
    try:
        result = pow_dim(base_dim, int(exponent))
    except DimensionError as exc:
        return _fail("POW_ERROR", f"{label}量纲幂运算失败: {exc}")
    return _ok(result_dim=result)


# ---------------------------------------------------------------------------
# 指数函数: 输入必须无量纲
# ---------------------------------------------------------------------------
def check_exp(x_dim: Dim, *, label: str = "指数函数") -> AlgebraResult:
    """检查 exp(x) 的量纲一致性 (x 必须无量纲)."""
    if not is_dimensionless(x_dim):
        return _fail(
            "EXP_DIMENSIONED_INPUT",
            f"{label}输入必须无量纲, 收到 {format_dim(x_dim)} "
            f"(exp 的泰勒展开要求各项量纲一致, 仅无量纲满足)"
        )
    return _ok(result_dim=ZERO)


# ---------------------------------------------------------------------------
# 对数函数: 输入必须无量纲
# ---------------------------------------------------------------------------
def check_log(x_dim: Dim, *, label: str = "对数函数") -> AlgebraResult:
    """检查 log(x) 的量纲一致性 (x 必须无量纲)."""
    if not is_dimensionless(x_dim):
        return _fail(
            "LOG_DIMENSIONED_INPUT",
            f"{label}输入必须无量纲, 收到 {format_dim(x_dim)} "
            f"(log 的泰勒展开要求各项量纲一致, 仅无量纲满足)"
        )
    return _ok(result_dim=ZERO)


# ---------------------------------------------------------------------------
# 批量检查: 一次检查多个运算
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BatchAlgebraResult:
    """批量代数检查结果."""
    results: List[AlgebraResult]
    total: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


def check_batch(operations: List[dict]) -> BatchAlgebraResult:
    """批量检查代数运算.

    每个 operation 是一个字典, 含:
    - ``op``: "add" / "sub" / "compare" / "mul" / "div" / "pow" / "exp" / "log"
    - ``a_dim`` / ``b_dim``: 操作数量纲 (tuple 或 list of 7 ints)
    - ``exponent``: 幂运算的指数 (仅 pow 需要)
    - ``a_angle`` / ``b_angle``: 角度制式 (可选, 默认 "rad")
    - ``label``: 操作标签 (可选)
    """
    results: List[AlgebraResult] = []
    passed = 0
    failed = 0

    for op_def in operations:
        op = op_def.get("op", "")
        label = op_def.get("label", op)

        def _to_dim(v) -> Dim:
            if isinstance(v, (list, tuple)) and len(v) == 7:
                return tuple(int(x) for x in v)  # type: ignore[return-value]
            return v  # already Dim

        a_dim = _to_dim(op_def.get("a_dim", ZERO))
        b_dim = _to_dim(op_def.get("b_dim", ZERO))
        a_angle = op_def.get("a_angle", "rad")
        b_angle = op_def.get("b_angle", "rad")

        if op in ("add", "+"):
            r = check_add(a_dim, b_dim, a_angle=a_angle, b_angle=b_angle, label=label)
        elif op in ("sub", "-"):
            r = check_sub(a_dim, b_dim, a_angle=a_angle, b_angle=b_angle, label=label)
        elif op in ("compare", "==", ">", "<", ">=", "<="):
            r = check_compare(a_dim, b_dim, a_angle=a_angle, b_angle=b_angle, label=label)
        elif op in ("mul", "*"):
            r = check_mul(a_dim, b_dim, label=label)
        elif op in ("div", "/"):
            r = check_div(a_dim, b_dim, label=label)
        elif op in ("pow", "**"):
            r = check_pow(a_dim, op_def.get("exponent", 0), label=label)
        elif op in ("exp",):
            r = check_exp(a_dim, label=label)
        elif op in ("log", "ln"):
            r = check_log(a_dim, label=label)
        else:
            r = _fail("UNKNOWN_OP", f"未知运算: {op!r}")

        results.append(r)
        if r.ok:
            passed += 1
        else:
            failed += 1

    return BatchAlgebraResult(
        results=results,
        total=len(operations),
        passed=passed,
        failed=failed,
    )
