"""安全算术表达式求值器（判据侧的 ``lhs`` / ``rhs`` 求值）。

设计边界
========
* **零领域语义**：本模块只认识算术运算、常量 ``pi``、函数 ``abs/sqrt/min/max``，
  以及"名字 → 响应字段"的解析；不含任何学科词条、算例或阈值。
* **安全**：用 :mod:`ast` 白名单遍历，禁止属性访问 / 下标 / 导入 / lambda，
  因此配置文件里的表达式无法逃逸成任意代码执行。
* **可解释**：求值结果附带**参与求值的符号表**，判据表里能看出这个数字是用哪些字段算的。

支持的语法（已声明，非"尽力而为"）::

    数字            1.5 / -0.2 / 1e-3
    常量            pi
    字段名          J / KT / KQ / speed_ms          （响应顶层字段）
    嵌套字段        field("gz_curve")  取字段本身；嵌套用 field("a.b.c")
    函数            abs(x) / sqrt(x) / min(a, b) / max(a, b)
    运算            + - * / ** 与括号
"""
from __future__ import annotations

import ast
import math
from typing import Any, Mapping

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_FUNCS = {
    "abs": abs,
    "sqrt": math.sqrt,
    "min": min,
    "max": max,
}
_CONSTS = {"pi": math.pi, "e": math.e}


class ExprError(ValueError):
    """表达式非法或求值失败（不可求值 ≠ 判据失败，调用方必须区分）。"""


def _dotted(payload: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            raise ExprError(f"field not found: {dotted!r} (missing {part!r})")
    return cur


def _eval(node: ast.AST, payload: Mapping[str, Any], symbols: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, payload, symbols)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ExprError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.Name):
        name = node.id
        if name in _CONSTS:
            return _CONSTS[name]
        if name in payload and isinstance(payload[name], (int, float)):
            val = float(payload[name])
            symbols[name] = val
            return val
        raise ExprError(f"name not resolvable as numeric response field: {name!r}")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise ExprError(f"operator not allowed: {type(node.op).__name__}")
        left = _eval(node.left, payload, symbols)
        right = _eval(node.right, payload, symbols)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ExprError("division by zero")
            return left / right
        return left ** right
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise ExprError(f"operator not allowed: {type(node.op).__name__}")
        val = _eval(node.operand, payload, symbols)
        return val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExprError("only plain function names are allowed")
        fname = node.func.id
        if fname == "field":
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
                raise ExprError("field() takes exactly one string literal")
            dotted = str(node.args[0].value)
            raw = _dotted(payload, dotted)
            symbols[dotted] = raw
            return raw
        fn = _FUNCS.get(fname)
        if fn is None:
            raise ExprError(f"function not allowed: {fname!r}")
        args = [_eval(a, payload, symbols) for a in node.args]
        return float(fn(*args))
    raise ExprError(f"syntax node not allowed: {type(node).__name__}")


def evaluate(expression: str, payload: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """求值并返回 ``(值, 符号表)``；非法表达式抛 :class:`ExprError`。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ExprError("empty expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - 配置写错时的兜底
        raise ExprError(f"syntax error: {exc}") from exc
    symbols: dict[str, Any] = {}
    value = _eval(tree, payload, symbols)
    if not isinstance(value, (int, float)) or value != value:  # NaN 拒绝
        raise ExprError(f"non-finite result: {value!r}")
    return float(value), symbols


def rel_error(lhs: float, rhs: float, eps: float = 1e-12) -> float:
    """相对误差 ``|lhs-rhs| / max(|rhs|, eps)``（口径与平台度量门一致）。"""
    return abs(lhs - rhs) / max(abs(rhs), eps)