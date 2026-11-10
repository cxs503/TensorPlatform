"""Safe, declarative expression compiler for optimizer problem definitions.

The optimisation DSL describes objective / constraint / gradient entries as
plain strings (``"100*(y - x**2)**2 + (1 - x)**2"``).  Strings are parsed with
:mod:`ast` and evaluated against a whitelist of node types and functions, so a
problem file can never execute arbitrary code.  This is the only place where
expressions are turned into callables.

Conventions
-----------
* Every expression is evaluated over a mapping ``{variable_name: float}``.
* ``x[i]`` style access is intentionally **not** supported (variables are
  addressed by name); this keeps the DSL free of implicit ordering.
* Allowed: literals, names of declared variables, unary +/- , + - * / // % **,
  parentheses, and whitelisted pure-math calls.
"""

from __future__ import annotations

import ast
import math
from typing import Callable, Dict, Mapping, Sequence

import numpy as np

__all__ = ["compile_expression", "ExpressionError", "MATH_NAMESPACE"]


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a forbidden construct."""


# --- pure-math whitelist ----------------------------------------------------

_PI = math.pi
_E = math.e

MATH_NAMESPACE: Dict[str, object] = {
    "pi": _PI,
    "e": _E,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "abs": abs,
    "pow": pow,
    "min": min,
    "max": max,
    "sign": lambda v: (v > 0) - (v < 0),
    "floor": math.floor,
    "ceil": math.ceil,
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.FloorDiv,
    ast.UAdd,
    ast.USub,
    ast.Tuple,
)


def _check(node: ast.AST, allowed_names: Sequence[str]) -> None:
    for sub in ast.walk(node):
        if not isinstance(sub, _ALLOWED_NODES):
            raise ExpressionError(
                f"forbidden syntax {type(sub).__name__!r} in expression "
                f"(only arithmetic + whitelisted math calls are allowed)"
            )
        if isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name):
                raise ExpressionError("only direct calls to whitelisted functions are allowed")
            if sub.func.id not in MATH_NAMESPACE:
                raise ExpressionError(f"unknown function {sub.func.id!r}")
            if sub.keywords:
                raise ExpressionError("keyword arguments are not supported")
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            if sub.id not in MATH_NAMESPACE and sub.id not in allowed_names:
                raise ExpressionError(
                    f"unknown symbol {sub.id!r}; declared variables are {list(allowed_names)}"
                )


def compile_expression(
    expression: str, variable_names: Sequence[str]
) -> Callable[[Mapping[str, float]], float]:
    """Compile ``expression`` into ``f(values: Mapping[str, float]) -> float``.

    Compilation (parse + whitelist check) happens once; evaluation is a plain
    ``eval`` of the pre-compiled code object over a merged namespace.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("expression must be a non-empty string")
    declared = tuple(variable_names)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - surfaced as DSL error
        raise ExpressionError(f"cannot parse expression {expression!r}: {exc}") from exc
    _check(tree, declared)
    code = compile(tree, filename="<optimizer-dsl>", mode="eval")

    def evaluate(values: Mapping[str, float]) -> float:
        local = dict(MATH_NAMESPACE)
        for name in declared:
            local[name] = float(values[name])
        return float(eval(code, {"__builtins__": {}}, local))  # noqa: S307 - whitelisted AST

    evaluate.expression = expression  # type: ignore[attr-defined]
    return evaluate


def compile_vector_expression(
    expressions: Sequence[str], variable_names: Sequence[str]
) -> Callable[[Mapping[str, float]], np.ndarray]:
    """Compile a list of expressions into a single vector-valued callable."""
    parts = [compile_expression(e, variable_names) for e in expressions]
    if not parts:
        raise ExpressionError("gradient expression list must not be empty")

    def evaluate(values: Mapping[str, float]) -> np.ndarray:
        return np.array([p(values) for p in parts], dtype=float)

    evaluate.expressions = tuple(expressions)  # type: ignore[attr-defined]
    return evaluate